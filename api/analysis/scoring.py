"""Score raw measurements out of 10 and turn the weak ones into faults.

Targets are coaching rules of thumb, not official judging criteria. Each spec says what value
earns a 10 (good) and what earns a 0 (bad), with a straight line in between. Tune them here.
"""

from dataclasses import dataclass
from typing import Literal

import numpy as np

from analysis.metrics import RawMetrics

Phase = Literal["takeoff", "flight", "entry"]

PHASE_WEIGHTS: dict[str, float] = {"takeoff": 0.25, "flight": 0.40, "entry": 0.35}
FAULT_BELOW = 7.0
MAJOR_BELOW = 4.0

FAULT_TITLES: dict[str, str] = {
    "takeoff_knees_bent": "Knees not fully extended at takeoff",
    "takeoff_hips_closed": "Hips not open at takeoff",
    "takeoff_lean": "Leaning at takeoff",
    "takeoff_arms_low": "Arms low at takeoff",
    "low_height": "Low jump height",
    "too_close_to_board": "Entry too close to the board",
    "too_far_from_board": "Travelling too far from the board",
    "board_clearance": "Too close to the board in flight",
    "loose_position": "Position not tight enough",
    "bent_knees": "Bent knees",
    "body_not_straight": "Body not straight in flight",
    "legs_apart": "Feet apart",
    "entry_angle": "Entry not vertical",
    "under_rotation": "Short of vertical at entry",
    "over_rotation": "Past vertical at entry",
    "entry_body_line": "Body line broken at entry",
    "entry_arms": "Arms not locked at entry",
}


@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    phase: Phase
    unit: str
    good: float | tuple[float, float]
    bad: float | tuple[float, float]
    weight: float
    fault: str
    target: str


def score_value(value: float, spec: Spec) -> float:
    if isinstance(spec.good, tuple):
        (lo, hi), (lo_bad, hi_bad) = spec.good, spec.bad
        if lo <= value <= hi:
            return 10.0
        frac = (value - lo_bad) / (lo - lo_bad) if value < lo else (hi_bad - value) / (hi_bad - hi)
    else:
        frac = (value - spec.bad) / (spec.good - spec.bad)
    return round(10.0 * float(np.clip(frac, 0.0, 1.0)), 1)


def specs_for(position: str, apparatus: str, head_first: bool) -> list[Spec]:
    specs = [
        Spec("takeoff_knee_angle", "Knee extension at takeoff", "takeoff", "°", 170, 135, 1.0,
             "takeoff_knees_bent", "170° or more (straight legs)"),
        Spec("takeoff_hip_angle", "Hip extension at takeoff", "takeoff", "°", 165, 120, 1.0,
             "takeoff_hips_closed", "165° or more"),
        Spec("takeoff_lean_deg", "Torso lean at takeoff", "takeoff", "°", 15, 45, 1.0,
             "takeoff_lean", "within 15° of upright"),
        Spec("takeoff_arm_angle", "Arm lift at takeoff", "takeoff", "°", 150, 70, 0.5,
             "takeoff_arms_low", "arms above the head (150° or more)"),
    ]

    if apparatus == "platform":
        specs.append(Spec("jump_height_m", "Jump height above takeoff", "flight", "m", 0.6, 0.15, 1.5,
                          "low_height", "0.6 m or more"))
    else:
        specs.append(Spec("jump_height_m", "Jump height above takeoff", "flight", "m", 1.0, 0.3, 1.5,
                          "low_height", "1.0 m or more"))

    specs += [
        Spec("entry_distance_m", "Distance from the board at entry", "flight", "m", (0.5, 1.8), (0.0, 3.0), 0.75,
             "board_distance", "0.5 to 1.8 m"),
        Spec("board_clearance_m", "Closest approach to the board", "flight", "m", 0.6, 0.2, 1.0,
             "board_clearance", "at least 0.6 m"),
    ]

    if position == "tuck":
        specs += [
            Spec("min_hip_angle", "Tuck tightness (hip angle)", "flight", "°", 60, 110, 1.5,
                 "loose_position", "60° or less"),
            Spec("position_knee_angle", "Knee bend in the tuck", "flight", "°", 60, 110, 1.0,
                 "loose_position", "60° or less"),
        ]
    elif position == "pike":
        specs += [
            Spec("min_hip_angle", "Pike depth (hip angle)", "flight", "°", 60, 110, 1.5,
                 "loose_position", "60° or less"),
            Spec("position_knee_angle", "Straight knees in the pike", "flight", "°", 165, 130, 1.0,
                 "bent_knees", "165° or more"),
        ]
    elif position == "straight":
        specs += [
            Spec("min_hip_angle", "Straight body in flight (hip angle)", "flight", "°", 160, 120, 1.5,
                 "body_not_straight", "160° or more throughout"),
            Spec("flight_knee_angle", "Straight knees in flight", "flight", "°", 165, 130, 1.0,
                 "bent_knees", "165° or more throughout"),
        ]

    specs += [
        Spec("leg_split_pct", "Feet together", "flight", "% of leg length", 15, 50, 0.75,
             "legs_apart", "15% or less"),
        Spec("entry_angle_deg", "Entry angle from vertical", "entry", "°", 5, 30, 2.0,
             "entry_angle", "within 5° of vertical"),
        Spec("entry_body_line_deg", "Straight body at entry", "entry", "°", 170, 140, 1.5,
             "entry_body_line", "170° or more"),
    ]

    if head_first:
        specs.append(Spec("entry_arm_angle_deg", "Arms locked overhead at entry", "entry", "°", 15, 60, 1.0,
                          "entry_arms", "within 15° of the body line"))
    else:
        specs.append(Spec("entry_arm_angle_deg", "Arms tight to the body at entry", "entry", "°", 20, 60, 1.0,
                          "entry_arms", "within 20° of the body line"))
    return specs


INFO_LABELS: dict[str, tuple[str, str]] = {
    "flight_time_s": ("Flight time", "s"),
    "rotation_deg": ("Total rotation", "°"),
    "peak_rotation_dps": ("Peak rotation speed", "°/s"),
    "position_hold_s": ("Time held in position", "s"),
    "entry_rotation_error_deg": ("Rotation error at entry (negative is short)", "°"),
}


def _fault_id(spec: Spec, value: float, raw: RawMetrics) -> str:
    if spec.fault == "board_distance":
        return "too_close_to_board" if value < spec.good[0] else "too_far_from_board"  # type: ignore[index]
    if spec.fault == "entry_angle" and "entry_rotation_error_deg" in raw.values:
        return "under_rotation" if raw.values["entry_rotation_error_deg"].value < 0 else "over_rotation"
    return spec.fault


def score_dive(raw: RawMetrics, position: str, apparatus: str) -> dict:
    metrics, faults = [], []
    for spec in specs_for(position, apparatus, raw.head_first):
        m = raw.values.get(spec.key)
        if m is None:
            continue
        score = score_value(m.value, spec)
        fault = _fault_id(spec, m.value, raw) if score < FAULT_BELOW else None
        metrics.append({
            "key": spec.key,
            "label": spec.label,
            "phase": spec.phase,
            "value": round(m.value, 2),
            "unit": spec.unit,
            "target": spec.target,
            "score": score,
            "weight": spec.weight,
            "fault": fault,
            "t": None if m.t is None else round(m.t, 3),
        })
        if fault:
            faults.append({
                "id": fault,
                "title": FAULT_TITLES[fault],
                "phase": spec.phase,
                "metric": spec.key,
                "label": spec.label,
                "value": round(m.value, 2),
                "unit": spec.unit,
                "target": spec.target,
                "score": score,
                "severity": "major" if score < MAJOR_BELOW else "minor",
                "t": None if m.t is None else round(m.t, 3),
                "impact": round(spec.weight * (10.0 - score), 2),
            })

    phase_scores: dict[str, float] = {}
    for phase in PHASE_WEIGHTS:
        rows = [m for m in metrics if m["phase"] == phase]
        if rows:
            total = sum(m["weight"] for m in rows)
            phase_scores[phase] = round(sum(m["score"] * m["weight"] for m in rows) / total, 1)

    weight_sum = sum(PHASE_WEIGHTS[p] for p in phase_scores)
    overall = sum(PHASE_WEIGHTS[p] * s for p, s in phase_scores.items()) / weight_sum if weight_sum else 0.0

    info = [
        {"key": key, "label": label, "value": round(raw.values[key].value, 2), "unit": unit}
        for key, (label, unit) in INFO_LABELS.items()
        if key in raw.values
    ]

    faults.sort(key=lambda f: f["impact"], reverse=True)
    return {
        "scores": {"overall": round(overall, 1), **phase_scores},
        "metrics": metrics,
        "faults": faults,
        "info": info,
    }
