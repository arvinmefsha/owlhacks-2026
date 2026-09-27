"""Calibrated envelope measurements on source timestamps; no numeric grading."""
from collections.abc import Sequence
import numpy as np

from analysis.pipeline import AnalysisError

MAX_GAP = 0.20
MIN_CONFIDENCE = 0.30
TITLES = {
    "EXCESSIVE_TRAVEL": "Excessive board travel",
    "LOW_APEX": "Low flight apex",
    "LOOSE_SHAPE": "Loose tuck or pike envelope",
    "UNDER_ROTATION": "Short of vertical at entry",
    "OVER_ROTATION": "Past vertical at entry",
    "ENTRY_LINE_OFF_VERTICAL": "Entry line off vertical",
    "FOLDED_ENTRY": "Still folded at entry",
}

ENTRY_MIN_DEVIATION_DEG = 10.0
ENTRY_WINDOW_FRAMES = 8
ENTRY_AXIS_POINTS = (0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16)

FAULT_PRIORITY = {
    "FOLDED_ENTRY": 5,
    "UNDER_ROTATION": 4,
    "OVER_ROTATION": 4,
    "ENTRY_LINE_OFF_VERTICAL": 4,
    "EXCESSIVE_TRAVEL": 3,
    "LOOSE_SHAPE": 2,
    "LOW_APEX": 2,
}


def _pose(frame, width, height):
    raw = frame.get("evidence_lm", frame.get("lm"))
    if raw is None:
        return np.full((17, 2), np.nan)
    a = np.asarray(raw, dtype=float)
    if a.shape != (17, 4):
        return np.full((17, 2), np.nan)
    xy = a[:, :2] * [width, height]
    xy[(a[:, 3] < .5) | ~np.isfinite(a).all(axis=1)] = np.nan
    return xy


def _first_keypoint_contact(frames, t, start, width, height, water):
    """First measured keypoint crossing, with an interpolated water-contact time."""
    for index in range(max(1, start), len(frames)):
        if t[index] - t[index - 1] > MAX_GAP:
            continue
        previous, current = _pose(frames[index - 1], width, height), _pose(frames[index], width, height)
        crossing = np.flatnonzero(
            np.isfinite(previous[:, 1]) & np.isfinite(current[:, 1])
            & (previous[:, 1] < water) & (current[:, 1] >= water)
        )
        if crossing.size:
            keypoint = int(crossing[np.argmax(current[crossing, 1])])
            fraction = (water - previous[keypoint, 1]) / (current[keypoint, 1] - previous[keypoint, 1])
            return index, float(t[index - 1] + np.clip(fraction, 0, 1) * (t[index] - t[index - 1]))
    return None, None


def _first_box_contact(boxes: np.ndarray, valid: np.ndarray, t: np.ndarray,
                       start: int, water: float):
    """Fallback contact time when the leading joint vanishes at the surface.

    This only locates contact from two consecutive measured detections.  It is
    never used as the body axis, so a tall box cannot manufacture a vertical
    entry score.
    """
    for index in range(max(1, start), len(boxes)):
        if not valid[index - 1] or not valid[index] or t[index] - t[index - 1] > MAX_GAP:
            continue
        previous, current = boxes[index - 1, 3], boxes[index, 3]
        if previous < water <= current and current > previous:
            fraction = (water - previous) / (current - previous)
            return index, float(t[index - 1] + np.clip(fraction, 0, 1) * (t[index] - t[index - 1]))
    return None, None


def _project_box_contact(boxes: np.ndarray, valid: np.ndarray, t: np.ndarray,
                         start: int, water: float, scale: float):
    """Short momentum fallback when detection ends immediately above water."""
    candidates = np.flatnonzero(valid & (np.arange(len(t)) >= start) & (boxes[:, 3] < water))
    if len(candidates) < 3:
        return None, None
    # Use the closest measured approach, not the last detection: once hands hit
    # the surface, partial-person/splash boxes can jump upward again.
    closest = int(candidates[np.argmax(boxes[candidates, 3])])
    window = candidates[(candidates <= closest) & (t[candidates] >= t[closest] - .18)]
    if len(window) < 3 or np.max(np.diff(t[window])) > MAX_GAP:
        return None, None
    y = boxes[window, 3]
    slopes = np.diff(y) / np.diff(t[window])
    descending = slopes[slopes > .25 * scale]
    early = max(2, len(window) // 2)
    early_y = float(np.median(y[:early]))
    early_t = float(np.median(t[window[:early]]))
    net_descent = float(y[-1] - early_y)
    if len(descending) < 2 or net_descent < .12 * scale or t[closest] <= early_t:
        return None, None
    # Net motion is more stable than adjacent box edges, which jitter when an
    # arm is the last visible segment. Positive local steps are required above.
    speed = net_descent / (t[closest] - early_t)
    distance = float(water - boxes[closest, 3])
    delay = distance / speed
    if distance < 0 or distance > .30 * scale or not 0 <= delay <= MAX_GAP:
        return None, None
    # This index is a boundary for selecting pre-contact evidence and may equal
    # len(t); the projected instant itself is always passed explicitly.
    return closest + 1, float(t[closest] + delay)


def _robust_axis(points: np.ndarray, water: float, standing: float):
    """A line through the visible body, rejecting arms or joints outside that line."""
    points = points[list(ENTRY_AXIS_POINTS)]
    points = points[np.isfinite(points).all(axis=1) & (points[:, 1] < water)]
    if len(points) < 4:
        return None
    best = None
    for first in range(len(points) - 1):
        for second in range(first + 1, len(points)):
            axis = points[second] - points[first]
            length = np.linalg.norm(axis)
            if length < .30 * standing:
                continue
            unit = axis / length
            residual = np.abs((points - points[first])[:, 0] * unit[1] - (points - points[first])[:, 1] * unit[0])
            inliers = residual <= max(4.0, .075 * standing)
            extent = np.ptp(points[inliers, 1]) if inliers.any() else 0.0
            candidate = (int(inliers.sum()), float(extent), -float(np.median(residual[inliers])) if inliers.any() else -np.inf, inliers)
            if best is None or candidate[:3] > best[:3]:
                best = candidate
    if best is None or best[0] < 4 or best[1] < .35 * standing:
        return None
    fit = points[best[3]]
    centered = fit - np.median(fit, axis=0)
    _, _, vectors = np.linalg.svd(centered, full_matrices=False)
    axis = vectors[0]
    if axis[1] < 0:
        axis = -axis
    # Signed lean is mirror-sensitive by design; magnitude is the score.
    return float(np.degrees(np.arctan2(axis[0], axis[1])))


def _entry_line(frames, t, contact, width, height, water, standing):
    """Robust median body-line deviation in the measured frames before first contact."""
    first = max(0, contact - ENTRY_WINDOW_FRAMES)
    samples: list[tuple[float, float]] = []
    for index in range(first, contact):
        if index and t[index] - t[index - 1] > MAX_GAP:
            samples.clear()
        lean = _robust_axis(_pose(frames[index], width, height), water, standing)
        if lean is not None:
            samples.append((float(t[index]), lean))
    if len(samples) < 3:
        if len(samples) < 2:
            return None, None, "At least two clear pre-contact body-line frames are required."
    times = np.array([sample[0] for sample in samples])
    leans = np.array([sample[1] for sample in samples])
    last_dry_time = t[min(contact - 1, len(t) - 1)]
    if last_dry_time - times[-1] > .12:
        return None, None, "The last measured body line is too far from water contact."
    if times[-1] - times[0] > .35:
        return None, None, "Pre-contact body-line frames are too far apart."
    deviation = float(np.median(np.abs(leans)))
    # Classify direction only when consecutive estimates agree about whether
    # the body is moving toward or away from vertical. Otherwise give the
    # concrete line measurement with no speculative cause.
    delta = np.diff(leans)
    movement = np.sign(leans[:-1] * delta)
    directional = movement[np.abs(delta) >= 1.0]
    if deviation <= ENTRY_MIN_DEVIATION_DEG or len(directional) < 2 or abs(np.median(directional)) < .5:
        return deviation, None, None
    return deviation, ("OVER_ROTATION" if np.median(directional) > 0 else "UNDER_ROTATION"), None


def _entry_extension(frames, t, boxes, valid, contact, entry_time, width, height, water, standing, scale):
    """Observed shoulder-to-ankle reach divided by the articulated body length.

    This is orientation independent: a straight inverted or horizontal diver has
    a long chord, while a diver still folded has a much shorter one. Require
    repeated, plausible raw joints near the surface; a pose prediction or a
    compact detection box alone cannot establish a technique fault.
    """
    samples = []
    for index in range(max(0, contact - ENTRY_WINDOW_FRAMES), min(contact, len(frames))):
        if not valid[index] or entry_time - t[index] > .22:
            continue
        if boxes[index, 3] >= water or water - boxes[index, 3] > .65 * scale:
            continue
        points = _pose(frames[index], width, height)
        sides = []
        for chain in ((5, 11, 13, 15), (6, 12, 14, 16)):
            joints = points[list(chain)]
            if not np.isfinite(joints).all() or np.any(joints[:, 1] >= water):
                continue
            segments = np.linalg.norm(np.diff(joints, axis=0), axis=1)
            if (np.any(segments < .08 * standing) or np.any(segments > .65 * standing)
                    or not .45 * standing <= segments.sum() <= 1.3 * standing):
                continue
            sides.append(float(np.linalg.norm(joints[-1] - joints[0]) / segments.sum()))
        if sides and (len(sides) == 1 or max(sides) - min(sides) <= .25):
            samples.append((index, float(np.median(sides))))
    if len(samples) < 2 or t[samples[-1][0]] - t[samples[-2][0]] > MAX_GAP:
        return None, None, "At least two clear, near-water body poses are required."
    recent = samples[-3:]
    return float(np.median([ratio for _, ratio in recent])), recent[-1][0], None


def analyze_dive(frames: Sequence[dict], width: int, height: int, setup: dict,
                 calibration: dict, height_cm: float | None = None) -> dict:
    """Evaluate 1 m springboard macro metrics without synthesizing missing data."""
    if not frames:
        raise AnalysisError("No frames were recorded.")
    if width <= 0 or height <= 0:
        raise AnalysisError("Invalid frame dimensions.")
    unique = {}
    for frame in frames:
        ts = float(frame["t"])
        if np.isfinite(ts):
            unique.setdefault(ts, frame)
    frames = [unique[key] for key in sorted(unique)]
    if not frames:
        raise AnalysisError("No valid frame timestamps.")
    t = np.array([f["t"] for f in frames], dtype=float)
    boxes = np.full((len(frames), 4), np.nan)
    for i, frame in enumerate(frames):
        b = frame.get("box")
        confidence = float(frame.get("box_confidence", 0))
        if b is None or not np.isfinite(confidence) or confidence < MIN_CONFIDENCE or frame.get("box_predicted", False):
            continue
        b = np.asarray(b, dtype=float)
        if b.shape == (4,) and np.isfinite(b).all() and np.all(b >= 0) and np.all(b <= 1) and np.all(b[2:] > b[:2]):
            boxes[i] = b * [width, height, width, height]
    valid = np.isfinite(boxes).all(axis=1)
    result = {"version": 3, "method": "macro-observations-v2",
              "metrics": [], "faults": [], "info": [], "warnings": [],
              "phases": {"takeoff": None, "apex": None, "entry": None, "entry_method": "unavailable"},
              "scale": {"source": "unavailable", "px_per_m": None},
              "head_first": round(float(setup.get("somersaults", .5)) * 2) % 2 == 1,
              "rotation": {"measured_deg": None, "expected_deg": float(setup.get("somersaults", .5)) * 360},
              "quality": {"tracking_coverage": float(valid.mean()), "frames": len(t)},
              "series": {"t": [], "box_height_ratio": [], "box_top_height_m": []},
              "envelopes": [], "standing_height_px": None}
    definitions = [
        ("travel_m", "Board travel at entry", "entry", "m", "≤ 1.80 m"),
        ("apex_height_m", "Top of body above board at apex", "flight", "m", "≥ 0.80 m"),
        ("compaction_ratio", "Minimum envelope height / standing height", "flight", "ratio", "≤ 0.55"),
        ("entry_deviation_deg", "Entry line from vertical", "entry", "°", "≤ 10° from vertical"),
        ("entry_extension_ratio", "Body extension at entry", "entry", "ratio", "≥ 0.85 (straight body)"),
    ]
    for key, label, phase, unit, target in definitions:
        result["metrics"].append(dict(key=key, label=label, phase=phase, unit=unit, target=target,
            value=None, score=None, fault=None, t=None, status="unavailable", reason="Milestone evidence unavailable."))
    def set_metric(index, value, at, flag=None, reason=None, status=None, timestamp=None):
        m = result["metrics"][index]
        resolved_status = status or ("flagged" if flag else "within_target")
        m.update(value=float(value) if value is not None else None,
                 t=timestamp if timestamp is not None else float(t[at]) if at is not None else None,
                 fault=flag, status=resolved_status, reason=reason, score=None)
        if flag:
            severity = "major" if FAULT_PRIORITY[flag] >= 4 else "minor"
            result["faults"].append(dict(id=flag, title=TITLES[flag], phase=m["phase"], metric=m["key"],
                label=m["label"], value=m["value"], unit=m["unit"], target=m["target"],
                severity=severity, t=m["t"], priority=FAULT_PRIORITY[flag]))
    board, water = calibration.get("board_tip"), calibration.get("water_y")
    if board is None or water is None:
        result["warnings"].append("Mark the resting board tip and waterline; no assumed body-height scale is used.")
        return result
    bx, by, water = float(board["x"]) * width, float(board["y"]) * height, float(water) * height
    if not np.isfinite([bx, by, water]).all() or not 0 <= bx <= width or not 0 <= by < water <= height or water - by < 10:
        raise AnalysisError("Mark the waterline below the resting board tip, at least 10 pixels apart.")
    if setup.get("apparatus", "springboard") != "springboard" or not np.isclose(float(setup.get("board_height_m", 1)), 1):
        result["warnings"].append("These thresholds apply only to 1 m springboard dives.")
        return result
    scale = water - by
    result["scale"] = dict(source="board_to_water", px_per_m=scale, reference_height_m=1.)
    result["warnings"].append("Feedback is based only on supported side-view observations. Unavailable evidence is excluded.")
    bw, bh = boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1]
    cx, cy = (boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2
    # Reject image-edge truncation as evidence; preserve the original water crossing.
    valid &= (boxes[:, 0] > 1) & (boxes[:, 1] > 1) & (boxes[:, 2] < width - 1) & (boxes[:, 3] < height - 1)
    standing = valid & (np.abs(boxes[:, 3] - by) <= .12 * scale) & (bh >= 1.8 * bw) & (np.abs(cx - bx) <= 1.5 * scale)
    standing_idx = np.flatnonzero(standing)
    # Require an upright stable run, not one convenient frame in the somersault.
    baseline = None
    start_index = None
    for end in standing_idx:
        run = standing_idx[(standing_idx <= end) & (t[standing_idx] >= t[end] - .3)]
        if len(run) >= 2 and t[run[-1]] - t[run[0]] >= .025 and np.max(np.diff(t[run])) <= MAX_GAP:
            if np.ptp(bh[run]) <= .22 * np.median(bh[run]) and np.ptp(cx[run]) <= .35 * scale:
                baseline = run
                break
    shape_reference_reliable = baseline is not None
    if baseline is None:
        # Use several loose upright detections near the board when there is no
        # perfectly stationary setup segment. This supports scale only; it does
        # not create takeoff or body-joint measurements.
        early_limit = max(3, int(.30 * len(t)))
        likely_upright = np.flatnonzero(
            valid & (np.arange(len(t)) < early_limit)
            & (bh >= 1.35 * bw)
            & (np.abs(boxes[:, 3] - by) <= .45 * scale)
            & (np.abs(cx - bx) <= 2.5 * scale)
        )
        if len(likely_upright) >= 2:
            baseline = likely_upright
            start_index = int(likely_upright[-1])
            shape_reference_reliable = True
            result["warnings"].append("Standing scale is estimated from visible upright frames; some measurements may be unavailable.")
        elif len(likely_upright) == 1:
            baseline = likely_upright
            start_index = int(likely_upright[-1])
            shape_reference_reliable = False
            result["warnings"].append("Standing scale uses one upright frame; shape measurements are less certain.")
        else:
            # Keep going if the diver is visible but does not pause on the board.
            # The high-percentile envelope in the beginning of the clip is a
            # scale reference only; it is never treated as a technical fault.
            early = np.flatnonzero(valid & (np.arange(len(t)) < early_limit) & (bh > .25 * scale))
            if not len(early):
                remaining_valid = np.flatnonzero(valid)
                if not len(remaining_valid):
                    result["warnings"].append("The diver was not detected clearly enough to measure this dive.")
                    return result
                # Some uploads start with the diver already moving. Use the
                # upper part of the observed box-height distribution as a loose
                # body-size reference and retain independent flight/entry data.
                reference_height = float(np.percentile(bh[remaining_valid], 80))
                baseline = remaining_valid[bh[remaining_valid] >= reference_height]
                start_index = int(remaining_valid[0])
                result["warnings"].append("No board-side stance was visible; body scale uses detected flight envelopes and some height estimates may be approximate.")
            else:
                baseline = early
                start_index = int(early[-1])
                shape_reference_reliable = False
                result["warnings"].append("Scale is estimated from early visible body detections; height and shape feedback may be approximate.")
    standing_height = float(np.percentile(bh[baseline], 80)) if start_index == (int(np.flatnonzero(valid)[0]) if len(np.flatnonzero(valid)) else -1) else float(np.median(bh[baseline]))
    result["standing_height_px"] = standing_height
    start = int(start_index if start_index is not None else baseline[-1])
    airborne = np.flatnonzero(valid & (np.arange(len(t)) > start) & (boxes[:, 3] < by - .06 * scale))
    if not len(airborne):
        remaining = np.flatnonzero(valid & (np.arange(len(t)) > start))
        if not len(remaining):
            result["warnings"].append("The diver was visible briefly, but flight milestones could not be located.")
            return result
        airborne = remaining
        result["warnings"].append("Takeoff was not clear; flight measurements use the visible post-board sequence.")
    # Entry begins at the first confident keypoint touching water, not when the
    # whole detection box reaches it. This preserves the dry body line at entry.
    contact, entry_time = _first_keypoint_contact(frames, t, int(airborne[0]), width, height, water)
    entry_method = "keypoint_water_crossing"
    if contact is None:
        contact, entry_time = _first_box_contact(boxes, valid, t, int(airborne[0]), water)
        entry_method = "measured_box_water_crossing"
    if contact is None:
        contact, entry_time = _project_box_contact(boxes, valid, t, int(airborne[0]), water, scale)
        entry_method = "projected_box_water_crossing"
    stop = min(contact, len(t) - 1) if contact is not None else len(t) - 1
    flight = np.flatnonzero(valid & (np.arange(len(t)) >= airborne[0]) & (np.arange(len(t)) <= stop) & (boxes[:, 1] < by))
    if not len(flight):
        # A partly cropped diver can still produce usable travel/entry feedback.
        # Keep detections with visible top edge as weaker apex evidence.
        flight = np.flatnonzero(valid & (np.arange(len(t)) >= airborne[0]) & (np.arange(len(t)) <= stop))
    if not len(flight):
        return result
    apex = int(flight[np.argmin(cy[flight])])
    supports = np.flatnonzero(valid[:apex] & (np.abs(boxes[:apex, 3] - by) <= .25 * scale))
    takeoff = int(supports[-1]) + 1 if len(supports) else int(airborne[0])
    if takeoff >= apex or not valid[takeoff] or t[takeoff] - t[max(0, takeoff-1)] > MAX_GAP:
        takeoff = int(airborne[0])
        if takeoff >= apex:
            takeoff = int(flight[0]) if len(flight) else apex
        result["warnings"].append("Takeoff timing is approximate; visible flight measurements are retained.")
    result["phases"].update(takeoff=float(t[takeoff]), apex=float(t[apex]))
    if contact is not None:
        result["phases"].update(entry=entry_time, entry_method=entry_method)
        # Interpolate the observed box center to the same instant only when both
        # adjacent boxes are measured. A predicted crop never becomes travel data.
        if 0 < contact < len(t) and valid[contact - 1] and valid[contact]:
            fraction = (entry_time - t[contact - 1]) / (t[contact] - t[contact - 1])
            entry_center = cx[contact - 1] + fraction * (cx[contact] - cx[contact - 1])
            travel = abs(entry_center - bx) / scale
            set_metric(0, travel, contact, "EXCESSIVE_TRAVEL" if travel > 1.8 else None, timestamp=entry_time)
        else:
            result["metrics"][0]["reason"] = "Measured boxes on both sides of first water contact are required."
    else:
        result["warnings"].append("First water contact is missing or obscured; entry metrics are unavailable.")
    coverage = valid[takeoff:stop+1].mean()
    result["quality"]["tracking_coverage"] = float(coverage)
    # An observed minimum is only a supported apex if the surrounding flight is covered.
    gaps = np.diff(t[flight])
    if coverage >= .20 and len(flight) >= 3 and (not len(gaps) or np.median(gaps) <= MAX_GAP) and apex >= takeoff:
        value = (by - boxes[apex, 1]) / scale
        set_metric(1, value, apex, "LOW_APEX" if value < .8 else None)
    eligible = np.flatnonzero(valid & (np.arange(len(t)) >= takeoff) & (np.arange(len(t)) < stop))
    if len(eligible):
        compact = int(eligible[np.argmin(bh[eligible])])
        ratio = float(bh[compact] / standing_height)
        if setup.get("position") in ("tuck", "pike"):
            # Still export the requested minimum, but a wide horizontal straight body
            # cannot prove tightness; missing flight cannot prove failure to compress.
            reliable = (shape_reference_reliable and coverage >= .45 and len(eligible) >= 3
                        and (not len(gaps) or np.median(gaps) <= MAX_GAP))
            square = .6 <= bw[compact] / bh[compact] <= 1.6
            flag = "LOOSE_SHAPE" if reliable and ratio > .55 else None
            status = "flagged" if flag else "within_target" if reliable and square else "uncertain"
            set_metric(2, ratio, compact, flag, None if status != "uncertain" else "Rotation or missing flight prevents a reliable shape judgment.", status)
        else:
            set_metric(2, None, None, reason="Compaction applies only to tuck and pike.", status="not_applicable")
    if contact is not None:
        deviation, rotation_flag, reason = _entry_line(frames, t, contact, width, height, water, standing_height)
        if deviation is not None:
            flag = rotation_flag or ("ENTRY_LINE_OFF_VERTICAL" if deviation > ENTRY_MIN_DEVIATION_DEG else None)
            set_metric(3, deviation, contact, flag, status="flagged" if flag else "within_target", timestamp=entry_time)
        else:
            result["metrics"][3]["reason"] = reason
    elif len(t):
        # If tracking ends just before splash, score the last short, visible
        # pre-water segment when the measured envelope is close to the surface.
        last_valid = np.flatnonzero(valid & (np.arange(len(t)) >= int(airborne[0])))
        if len(last_valid):
            last = int(last_valid[-1])
            near_water = boxes[last, 3] < water and water - boxes[last, 3] <= .60 * scale
            if near_water:
                deviation, rotation_flag, reason = _entry_line(frames, t, last + 1, width, height, water, standing_height)
                if deviation is not None:
                    flag = rotation_flag or ("ENTRY_LINE_OFF_VERTICAL" if deviation > ENTRY_MIN_DEVIATION_DEG else None)
                    set_metric(3, deviation, last, flag, status="flagged" if flag else "within_target", timestamp=float(t[last]))
                    result["phases"].update(entry=float(t[last]), entry_method="last_visible_precontact")
                else:
                    result["metrics"][3]["reason"] = reason
    if contact is not None and entry_time is not None:
        extension, extension_frame, reason = _entry_extension(
            frames, t, boxes, valid, contact, entry_time, width, height, water, standing_height, scale)
        if extension is not None:
            folded = extension < .68
            if folded:
                # A folded diver has no meaningful straight entry axis. Do not
                # let a coincidental line through a few joints earn a high score.
                result["faults"] = [f for f in result["faults"] if f["metric"] != "entry_deviation_deg"]
                set_metric(3, None, None,
                           reason="The body was folded; a straight entry line cannot be measured.",
                           status="unavailable")
            set_metric(4, extension, extension_frame, "FOLDED_ENTRY" if folded else None,
                       reason="The observed body remained folded immediately before water contact." if folded else None,
                       timestamp=entry_time)
        else:
            result["metrics"][4]["reason"] = reason
    # Diagnostic envelopes are clipped independently of the contact calculation.
    for i in range(len(t)):
        b = boxes[i].copy()
        visible = valid[i] and i <= stop and b[1] < water
        if visible:
            b[3] = min(b[3], water)
        result["envelopes"].append({"t": float(t[i]), "box": (b / [width, height, width, height]).tolist() if visible else None})
    for i in eligible:
        result["series"]["t"].append(float(t[i]))
        result["series"]["box_height_ratio"].append(float(bh[i] / standing_height))
        result["series"]["box_top_height_m"].append(float((by - boxes[i, 1]) / scale))
    return result
