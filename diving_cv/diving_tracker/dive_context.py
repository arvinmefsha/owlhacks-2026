"""Soft dive context and bounded selection of observed pose candidates.

No expected rotation count is imposed on the coordinates. Named direction is
camera-dependent; angular velocity is always learned from observed motion.
"""
from dataclasses import dataclass

import numpy as np

from .refinement import suspicious_geometry


@dataclass(frozen=True)
class DiveContext:
    position: str | None = None
    direction: str | None = None
    somersaults: float | None = None


def orientation(pose):
    if pose is None:
        return None
    ids = [5, 6, 11, 12]
    if np.min(pose.confidence[ids]) < .45 or not np.isfinite(pose.keypoints[ids]).all():
        return None
    vector = pose.keypoints[[5, 6]].mean(axis=0)-pose.keypoints[[11, 12]].mean(axis=0)
    scale = max(np.linalg.norm(pose.box[2:]-pose.box[:2]), 1.)
    if np.linalg.norm(vector) < .08*scale:
        return None
    return float(np.arctan2(vector[0], -vector[1]))


def angular_delta(a, b):
    return float(np.arctan2(np.sin(a-b), np.cos(a-b)))


class RotationState:
    def __init__(self):
        self.angle = None
        self.timestamp = None
        self.omega = 0.

    def predict(self, timestamp):
        if self.angle is None or timestamp-self.timestamp > .12:
            return None
        return self.angle+self.omega*max(0., timestamp-self.timestamp)

    def update(self, pose, timestamp):
        angle = orientation(pose)
        if angle is None:
            return
        if self.angle is not None and 0 < timestamp-self.timestamp <= .12:
            velocity = angular_delta(angle, self.angle)/(timestamp-self.timestamp)
            # Implausible torso flips reduce trust; never force the expected spin.
            if abs(velocity) > 12*np.pi:
                return
            self.omega = .5*self.omega+.5*velocity
        else:
            self.omega = 0.
        self.angle, self.timestamp = angle, timestamp


def phase_hypothesis(pose, board_tip, water_y, previous_phase):
    """Observed broad phase, with unknown instead of a scripted dive schedule."""
    if pose is None:
        return "unknown"
    valid=(pose.confidence>.45)&np.isfinite(pose.keypoints).all(axis=1)
    if valid.sum()<5:
        return "unknown"
    if np.median(pose.keypoints[valid,1])>=water_y:
        return "entry"
    ankles=[j for j in [15,16] if valid[j]]
    scale=max(np.linalg.norm(pose.box[2:]-pose.box[:2]),1.)
    if ankles and min(np.linalg.norm(pose.keypoints[j]-board_tip) for j in ankles)<.15*scale:
        return "takeoff"
    compact=compactness(pose)
    if compact is not None and compact<1.25:
        return "compact"
    if compact is not None and previous_phase in {"compact","opening"}:
        return "opening"
    return "flight"


def compactness(pose):
    if pose is None or min(pose.confidence[[5,6,11,12,13,14]]) < .4:
        return None
    chest=pose.keypoints[[5,6]].mean(axis=0)
    hip=pose.keypoints[[11,12]].mean(axis=0)
    knee=pose.keypoints[[13,14]].mean(axis=0)
    length=np.linalg.norm(chest-hip)
    return float(np.linalg.norm(knee-chest)/length) if length>1 else None


def recovery_needed(pose, previous, predicted_angle, context):
    if pose is None or suspicious_geometry(pose):
        return True
    angle=orientation(pose)
    if angle is not None and predicted_angle is not None and abs(angular_delta(angle,predicted_angle))>.6:
        return True
    valid=pose.confidence>.5
    p=pose.keypoints[valid]
    if len(p):
        margin=.025*max(np.linalg.norm(pose.box[2:]-pose.box[:2]),1.)
        if np.any(p<pose.box[:2]-margin) or np.any(p>pose.box[2:]+margin):
            return True
    if previous is not None:
        valid &= previous.confidence>.5
        if valid.sum()>=4:
            displacement=np.linalg.norm(pose.keypoints[valid]-previous.keypoints[valid],axis=1)
            scale=max(np.linalg.norm(previous.box[2:]-previous.box[:2]),1.)
            if np.median(displacement)>.3*scale:
                return True
    # Context changes when we spend retry compute, not the desired body shape.
    compact=compactness(pose)
    return bool(context.position=='tuck' and angle is not None and
                (abs(angular_delta(angle,0))>.65 or (compact is not None and compact<1.25)))


def transition_cost(a, b, dt):
    if a is None or b is None or dt>.12:
        return 0.
    valid=(a.confidence>.4)&(b.confidence>.4)
    if valid.sum()<4:
        return 0.
    scale=max(np.linalg.norm(a.box[2:]-a.box[:2]),np.linalg.norm(b.box[2:]-b.box[:2]),1.)
    displacement=np.linalg.norm(a.keypoints[valid]-b.keypoints[valid],axis=1)/scale
    # Allow rapid legitimate motion. Capped penalties cannot overwhelm strong
    # visual evidence, and do not penalize genuine early opening/under-rotation.
    return .12*min(float(np.median(displacement))/max(dt*12,.08),1.)


def select_sequence(candidates, times):
    from .rotation_recovery import recovery_score
    costs=[]; parents=[]
    for i, row in enumerate(candidates):
        unary=np.array([1-recovery_score(p)+(.15 if p is not None and suspicious_geometry(p) else 0) for p in row])
        if not i:
            costs.append(unary);parents.append(np.zeros(len(row),dtype=int));continue
        edges=np.array([[costs[-1][j]+transition_cost(prev,p,times[i]-times[i-1])
                         for j,prev in enumerate(candidates[i-1])] for p in row])
        parent=np.argmin(edges,axis=1)
        costs.append(unary+edges[np.arange(len(row)),parent]);parents.append(parent)
    chosen=[int(np.argmin(costs[-1]))]
    for i in range(len(candidates)-1,0,-1):chosen.append(int(parents[i][chosen[-1]]))
    return chosen[::-1]
