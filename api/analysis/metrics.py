"""Raw form measurements for each phase of the dive.

Everything is measured in the image plane, which is what a judge sees from the side.
Values are returned unscored; scoring.py decides what is good.
"""

from dataclasses import dataclass, field

import numpy as np

from analysis.geometry import (
    angle_at,
    direction_deg,
    distance,
    nanmedian,
    nanpercentile,
    unwrap_deg,
    vector_angle,
    wrap_deg,
)
from analysis.landmarks import Body
from analysis.phases import Phases

# The first moments after takeoff still look like takeoff; skip them for flight metrics.
FLIGHT_SKIP_S = 0.1
# Near the board the feet are expected to be close, so clearance is checked after this.
CLEARANCE_SKIP_S = 0.2
# Entry form is judged over the frames just before the first water contact.
ENTRY_WINDOW_S = 0.1


@dataclass
class Measurement:
    value: float
    t: float | None = None


@dataclass
class RawMetrics:
    values: dict[str, Measurement] = field(default_factory=dict)
    head_first: bool = True
    rotation_deg: float = float("nan")
    warnings: list[str] = field(default_factory=list)

    def put(self, key: str, value: float, t: float | None = None) -> None:
        if np.isfinite(value):
            self.values[key] = Measurement(float(value), None if t is None else float(t))


def _window_median(series: np.ndarray, lo: int, hi: int) -> float:
    return nanmedian(series[max(lo, 0) : hi + 1])


def compute_metrics(
    body: Body,
    phases: Phases,
    fs: float,
    px_per_m: float,
    board_tip_px: np.ndarray | None,
    expected_head_first: bool,
) -> RawMetrics:
    out = RawMetrics()
    t = body.t
    to, apex, contact = phases.takeoff, phases.apex, phases.contact

    hip_angle = angle_at(body.shoulder, body.hip, body.knee)
    knee_angle = angle_at(body.hip, body.knee, body.ankle)
    torso_dir = direction_deg(body.hip, body.shoulder)

    # Takeoff: legs and hips should be fully extended, torso close to upright, arms up.
    arm_lift = vector_angle(body.wrist - body.shoulder, body.hip - body.shoulder)
    out.put("takeoff_knee_angle", _window_median(knee_angle, to - 1, to + 1), t[to])
    out.put("takeoff_hip_angle", _window_median(hip_angle, to - 1, to + 1), t[to])
    out.put("takeoff_lean_deg", abs(_window_median(wrap_deg(torso_dir), to - 1, to + 1)), t[to])
    out.put("takeoff_arm_angle", _window_median(arm_lift, to - 1, to + 1), t[to])

    # Flight
    out.put("jump_height_m", (body.com[to, 1] - body.com[apex, 1]) / px_per_m, t[apex])
    out.put("flight_time_s", t[contact] - t[to])

    ref_x = board_tip_px[0] if board_tip_px is not None else body.hip[to, 0]
    out.put("entry_distance_m", abs(body.hip[contact, 0] - ref_x) / px_per_m, t[contact])

    flight = np.arange(min(to + int(FLIGHT_SKIP_S * fs), contact), contact + 1)
    if board_tip_px is not None:
        late = flight[flight >= to + int(CLEARANCE_SKIP_S * fs)]
        if late.size:
            gaps = np.stack([distance(p[late], board_tip_px) for p in (body.nose, body.wrist, body.foot)])
            closest_per_frame = np.fmin.reduce(gaps, axis=0)
            if np.isfinite(closest_per_frame).any():
                closest = int(np.nanargmin(closest_per_frame))
                out.put("board_clearance_m", closest_per_frame[closest] / px_per_m, t[late[closest]])

    if flight.size >= 3:
        flight_hip = hip_angle[flight]
        tightest_value = nanpercentile(flight_hip, 5)
        if np.isfinite(tightest_value):
            tight = int(flight[np.nanargmin(np.abs(flight_hip - tightest_value))])
            out.put("min_hip_angle", tightest_value, t[tight])
            out.put("position_knee_angle", _window_median(knee_angle, tight - 2, tight + 2), t[tight])
            out.put("position_hold_s", np.sum(flight_hip <= tightest_value + 20) / fs)
        out.put("flight_knee_angle", nanpercentile(knee_angle[flight], 5), t[apex])

        leg_len = nanmedian(distance(body.hip, body.knee) + distance(body.knee, body.ankle))
        split = distance(body.l_ankle[flight], body.r_ankle[flight]) / leg_len
        out.put("leg_split_pct", 100 * nanmedian(split), t[apex])
        out.put("toe_point_deg", nanmedian(angle_at(body.knee, body.ankle, body.foot)[flight]), t[apex])

    # Rotation of the torso from takeoff to entry, signed (clockwise on screen is positive).
    span = np.arange(to, contact + 1)
    torso_unwrapped = unwrap_deg(torso_dir[span])
    finite = np.flatnonzero(np.isfinite(torso_unwrapped))
    if finite.size >= 2:
        out.rotation_deg = float(torso_unwrapped[finite[-1]] - torso_unwrapped[finite[0]])
        out.put("rotation_deg", abs(out.rotation_deg))
        speed = np.abs(np.gradient(torso_unwrapped[finite], t[span][finite]))
        out.put("peak_rotation_dps", float(np.nanmax(speed)))

    # Entry: judged on the last frames before the leading hand or foot touches the water.
    lo = max(to, contact - max(1, int(ENTRY_WINDOW_S * fs)))
    head_below_feet = _window_median(body.nose[:, 1] - body.ankle[:, 1], lo, contact)
    out.head_first = bool(head_below_feet > 0) if np.isfinite(head_below_feet) else expected_head_first
    if out.head_first != expected_head_first:
        out.warnings.append(
            f"The entry looked {'head' if out.head_first else 'feet'}-first, but the selected dive "
            f"enters {'head' if expected_head_first else 'feet'}-first. Check the dive selection."
        )

    body_dir = direction_deg(body.ankle, body.shoulder)
    target_dir = 180.0 if out.head_first else 0.0
    offset = _window_median(wrap_deg(body_dir - target_dir), lo, contact)
    out.put("entry_angle_deg", abs(offset), t[contact])
    # Negative means the diver still had rotation left to do (short), positive means past vertical.
    if np.isfinite(offset) and abs(out.rotation_deg) > 45:
        out.put("entry_rotation_error_deg", offset * np.sign(out.rotation_deg), t[contact])

    out.put("entry_body_line_deg", _window_median(angle_at(body.shoulder, body.hip, body.ankle), lo, contact), t[contact])

    arm = body.wrist - body.shoulder
    torso_up = body.shoulder - body.hip
    if out.head_first:
        arm_error = vector_angle(arm, torso_up)
    else:
        arm_error = np.fmin(vector_angle(arm, torso_up), vector_angle(arm, -torso_up))
    out.put("entry_arm_angle_deg", _window_median(arm_error, lo, contact), t[contact])

    return out
