"""Offline pose cleanup with no causal lag or open-ended extrapolation."""
import numpy as np


def refine_poses(times, raw, confidence, water_y, max_gap_seconds=0.085):
    points = np.asarray(raw, dtype=float).copy()
    scores = np.asarray(confidence, dtype=float).copy()
    valid = np.isfinite(points).all(axis=2) & (scores >= 0.35)
    points[~valid] = np.nan
    scores[~valid] = 0.0
    estimated = np.zeros(scores.shape, dtype=bool)
    # A centered quadratic fit only nudges uncertain observations. Strong
    # observations remain exact, including during rapid rotation and extension.
    for joint in range(points.shape[1]):
        for i in range(2, len(times) - 2):
            if not valid[i, joint] or scores[i, joint] >= 0.6:
                continue
            sl = slice(i-2, i+3)
            local_t = times[sl] - times[i]
            if not valid[sl, joint].all() or np.max(np.diff(times[sl])) > max_gap_seconds:
                continue
            fit = np.polynomial.polynomial.polyfit(local_t, raw[sl, joint], 2, w=confidence[sl, joint])
            points[i, joint] = 0.75 * raw[i, joint] + 0.25 * fit[0]
        indices = np.flatnonzero(valid[:, joint])
        for left, right in zip(indices[:-1], indices[1:]):
            dt = times[right] - times[left]
            if right == left+1 or dt > max_gap_seconds:
                continue
            a, b = points[left, joint], points[right, joint]
            # Do not bridge into or back out of the water, or over an implausible jump.
            body = raw[left, 5:17]
            finite = body[np.isfinite(body).all(axis=1)]
            extent = max(float(np.linalg.norm(np.ptp(finite, axis=0))), 1.) if len(finite) else 1.
            if max(a[1], b[1]) >= water_y or np.linalg.norm(b-a) > extent:
                continue
            for i in range(left+1, right):
                weight = (times[i]-times[left])/dt
                points[i, joint] = (1-weight)*a + weight*b
                scores[i, joint] = min(0.29, scores[left, joint], scores[right, joint])
                estimated[i, joint] = True
    return points, scores, estimated


def pose_quality(pose):
    if pose is None:
        return 0.0
    scores = pose.confidence[5:17]
    finite = np.isfinite(pose.keypoints[5:17]).all(axis=1)
    return float(np.mean(np.where(finite, scores, 0)))


def reliable_pose(pose):
    return (pose_quality(pose) >= 0.55 and np.count_nonzero(pose.confidence[5:17] >= 0.4) >= 8
            and not suspicious_geometry(pose))


def suspicious_geometry(pose):
    """Trigger recovery for extreme geometry; do not force hidden limbs straight.

    Only upper bounds are used: valid projected limbs can become arbitrarily
    short during foreshortening or a tuck. Recovery is preferable to inventing
    coordinates when these coarse checks fail.
    """
    if pose is None:
        return True
    diagonal = max(float(np.linalg.norm(pose.box[2:]-pose.box[:2])), 1.)
    for a,b in ((5,7),(7,9),(6,8),(8,10),(11,13),(13,15),(12,14),(14,16)):
        if min(pose.confidence[a],pose.confidence[b]) < .35:
            continue
        if np.linalg.norm(pose.keypoints[a]-pose.keypoints[b]) > .85*diagonal:
            return True
    return False
