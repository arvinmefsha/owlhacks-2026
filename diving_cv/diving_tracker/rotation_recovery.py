"""Carry successful image orientation through flight, reacquiring when needed."""
import numpy as np

from .dive_context import angular_delta, orientation
from .refinement import pose_quality


def recovery_score(pose):
    """Include distal arms so good legs cannot entirely hide absent wrists."""
    if pose is None:
        return 0.
    arms=pose.confidence[[7,8,9,10]]
    arms=np.where(np.isfinite(pose.keypoints[[7,8,9,10]]).all(axis=1),arms,0.)
    return .8*pose_quality(pose)+.2*float(np.mean(arms))


class OrientationRecovery:
    def __init__(self, frame_count):
        self.turn=0
        self.last_scan=-10
        self.scan_angle=None
        self.budget=max(3,frame_count//3)
        self.used=0
        self.airborne=False

    def alternatives(self, pose, box, timestamp_index, board, water_y, needs_recovery):
        if box is None or box[1]>=water_y:
            return []
        angle=orientation(pose)
        # Detector geometry can establish lift-off even if a tight tuck has
        # already erased every reliable shoulder/ankle observation.
        if box[3] < board[1]-.05*max(box[3]-box[1],1.):
            self.airborne=True
        if angle is not None and abs(angular_delta(angle,0))>.65:
            self.airborne=True
        if pose is not None:
            feet=[j for j in [15,16] if pose.confidence[j]>.5 and np.isfinite(pose.keypoints[j]).all()]
            scale=max(np.linalg.norm(box[2:]-box[:2]),1.)
            if feet and all(pose.keypoints[j,1]<board[1]-.15*scale for j in feet):
                self.airborne=True
        arms_missing=pose is None or np.mean(pose.confidence[[7,8,9,10]])<.6
        moved=angle is not None and (self.scan_angle is None or abs(angular_delta(angle,self.scan_angle))>.35)
        interval=3 if self.airborne else 12
        if timestamp_index-self.last_scan<interval or self.used>=self.budget:
            return []
        if not (self.airborne and (moved or arms_missing or needs_recovery)):
            # Before flight retain cheap ordinary tracking; do not burn the
            # flight recovery budget on arms raised above the board.
            return []
        turns=[turn for turn in range(4) if turn!=self.turn][:self.budget-self.used]
        self.used+=len(turns)
        self.last_scan=timestamp_index
        self.scan_angle=angle
        return turns

    def accept(self, candidates):
        usable=[(turn,p) for turn,p in candidates if p is not None]
        if not usable:
            return None
        turn,pose=max(usable,key=lambda pair:recovery_score(pair[1]))
        if recovery_score(pose)>=.4:
            self.turn=turn
        return pose
