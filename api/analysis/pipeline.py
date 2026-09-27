"""Legacy v1 helpers for historical data/tests. Production entry point is analysis.macro."""

from collections.abc import Sequence

import numpy as np

from analysis.geometry import (
    angle_at,
    direction_deg,
    distance,
    nanpercentile,
    unwrap_deg,
)
from analysis.landmarks import NUM_LANDMARKS, Body
from analysis.metrics import compute_metrics
from analysis.phases import find_phases
from analysis.scoring import score_dive
from analysis.smoothing import interpolate_gaps, resample_uniform, smooth

MIN_VISIBILITY = 0.3
MAX_GAP_S = 0.25
MIN_TRACKED_FRAMES = 10
DEFAULT_HEIGHT_CM = 170.0
# Shoulder-to-hip + hip-to-knee + knee-to-ankle as a fraction of standing height
# (Drillis and Contini segment proportions: 0.288 + 0.245 + 0.246).
SEGMENTS_FRACTION_OF_HEIGHT = 0.779
MAX_SERIES_POINTS = 300


class AnalysisError(ValueError):
    """The frames can't be analysed (for example, no diver was tracked)."""


def _to_arrays(frames: Sequence[dict], width: int, height: int):
    """Sorted times, (N, 17, 2) pixel positions and confidence, NaN where missing."""
    frames = sorted(frames, key=lambda f: f["t"])
    t = np.array([f["t"] for f in frames], dtype=float)
    keep = np.concatenate([[True], np.diff(t) > 1e-6])
    frames = [f for f, k in zip(frames, keep) if k]
    t = t[keep]

    raw = np.full((t.size, NUM_LANDMARKS, 4), np.nan)
    for i, f in enumerate(frames):
        if f.get("lm") is not None:
            raw[i] = np.asarray(f["lm"], dtype=float)[:, :4]

    vis = raw[..., 3]
    xy = raw[..., :2] * np.array([width, height], dtype=float)
    xy[vis < MIN_VISIBILITY] = np.nan
    return t, xy, vis


def _sample_rate(t: np.ndarray) -> float:
    dt = np.diff(t)
    return float(np.clip(round(1.0 / np.median(dt)), 15, 120)) if dt.size else 30.0


def _estimate_scale(body: Body, height_cm: float | None) -> tuple[float, dict]:
    segments = (
        distance(body.shoulder, body.hip) + distance(body.hip, body.knee) + distance(body.knee, body.ankle)
    )
    # From the side, limbs rarely point at the camera, and a high percentile would be inflated by
    # tracking jitter (which also skews the gravity parabola used for takeoff), so use the median.
    segments_px = nanpercentile(segments, 50)
    if not np.isfinite(segments_px) or segments_px <= 0:
        raise AnalysisError("Couldn't see the diver's shoulders, hips, knees and ankles clearly enough to measure.")
    used_cm = height_cm or DEFAULT_HEIGHT_CM
    px_per_m = segments_px / (SEGMENTS_FRACTION_OF_HEIGHT * used_cm / 100.0)
    return px_per_m, {
        "px_per_m": round(px_per_m, 2),
        "height_cm": used_cm,
        "source": "diver_height" if height_cm else "default_height",
    }


def _calibrated_scale(
    calibration: dict, height: int, board_height_m: float
) -> tuple[float, dict] | None:
    board = calibration.get("board_tip")
    water_y = calibration.get("water_y")
    if board is None or water_y is None:
        return None
    pixel_distance = abs(float(water_y) - float(board["y"])) * height
    if pixel_distance < 10:
        raise AnalysisError("Board tip and water line must be at least 10 pixels apart.")
    px_per_m = pixel_distance / board_height_m
    return px_per_m, {
        "px_per_m": round(px_per_m, 2),
        "reference_height_m": board_height_m,
        "source": "board_to_water",
    }


def _series(body: Body, takeoff: int, px_per_m: float) -> dict[str, list]:
    step = max(1, int(np.ceil(body.t.size / MAX_SERIES_POINTS)))
    idx = np.arange(0, body.t.size, step)

    def clean(a: np.ndarray, digits: int) -> list:
        return [None if not np.isfinite(v) else round(float(v), digits) for v in a[idx]]

    return {
        "t": clean(body.t, 3),
        "hip_angle": clean(angle_at(body.shoulder, body.hip, body.knee), 1),
        "knee_angle": clean(angle_at(body.hip, body.knee, body.ankle), 1),
        "height_m": clean((body.com[takeoff, 1] - body.com[:, 1]) / px_per_m, 3),
        "rotation_deg": clean(unwrap_deg(direction_deg(body.hip, body.shoulder)), 1),
    }


def analyze_dive(
    frames: Sequence[dict],
    width: int,
    height: int,
    setup: dict,
    calibration: dict,
    height_cm: float | None,
) -> dict:
    """Analyse one dive.

    frames: [{"t": seconds, "lm": 17 x [x, y, z, confidence] in normalized image units, or None}]
    setup: {"position", "direction", "somersaults", "apparatus", "board_height_m"}
    calibration: {"board_tip": {"x", "y"} or None, "water_y": float or None}, normalized units
    """
    if not frames:
        raise AnalysisError("No frames were recorded.")
    t, xy, vis = _to_arrays(frames, width, height)
    tracked = np.isfinite(xy[:, :, 0]).sum(axis=1) >= 8
    if tracked.sum() < MIN_TRACKED_FRAMES:
        raise AnalysisError(
            "The pose tracker didn't find a diver in enough frames. Film side-on, with the whole body in view."
        )

    fs = _sample_rate(t[tracked])
    flat = interpolate_gaps(t, xy.reshape(t.size, -1), MAX_GAP_S)
    grid, flat = resample_uniform(t, flat, fs)
    flat = smooth(flat, fs)
    _, vis_grid = resample_uniform(t, interpolate_gaps(t, vis, MAX_GAP_S), fs)
    body = Body.from_landmarks(grid, flat.reshape(grid.size, NUM_LANDMARKS, 2), vis_grid)

    if np.isfinite(body.com[:, 1]).sum() < MIN_TRACKED_FRAMES:
        raise AnalysisError("The diver's trunk and legs weren't visible for long enough to follow the dive.")

    calibrated = _calibrated_scale(calibration, height, float(setup["board_height_m"]))
    px_per_m, scale = calibrated or _estimate_scale(body, height_cm)
    warnings: list[str] = []
    if scale["source"] == "default_height":
        warnings.append(
            "No board-to-water scale was available, so distances use an approximate body scale."
        )

    board_tip = calibration.get("board_tip")
    board_tip_px = np.array([board_tip["x"] * width, board_tip["y"] * height]) if board_tip else None
    water_y_px, water_method = None, "none"
    if calibration.get("water_y") is not None:
        water_y_px, water_method = calibration["water_y"] * height, "water_line"
    elif board_tip_px is not None:
        estimate = board_tip_px[1] + setup["board_height_m"] * px_per_m
        if estimate <= height * 1.02:
            water_y_px, water_method = estimate, "board_height"

    lowest_y = np.fmax.reduce(np.stack([p[:, 1] for p in body.extremities()]), axis=0)
    phases = find_phases(grid, fs, body.com[:, 1], lowest_y, height, px_per_m, water_y_px, water_method)
    if phases.entry_method in ("last_tracked", "landing", "frame_edge") and water_method == "none":
        warnings.append("No water line was marked, so the entry moment is estimated. Tap the water line next time.")

    somersaults = float(setup["somersaults"])
    expected_head_first = round(somersaults * 2) % 2 == 1
    raw = compute_metrics(body, phases, fs, px_per_m, board_tip_px, expected_head_first)
    warnings += raw.warnings

    expected_rotation = somersaults * 360.0
    if np.isfinite(raw.rotation_deg) and abs(abs(raw.rotation_deg) - expected_rotation) > 120:
        warnings.append(
            f"Measured about {abs(raw.rotation_deg):.0f}° of rotation, but the selected dive has "
            f"{expected_rotation:.0f}°. Check the dive selection, or tracking may have slipped."
        )

    in_flight = (t >= grid[phases.takeoff]) & (t <= grid[phases.contact])
    coverage = float(tracked[in_flight].mean()) if in_flight.any() else 0.0
    if coverage < 0.7:
        warnings.append(
            f"The tracker lost the diver for {100 * (1 - coverage):.0f}% of the flight, so some numbers may be off."
        )

    scored = score_dive(raw, setup["position"], setup["apparatus"])
    return {
        "version": 1,
        "fs": fs,
        "scale": scale,
        "phases": {
            "takeoff": round(float(grid[phases.takeoff]), 3),
            "apex": round(float(grid[phases.apex]), 3),
            "entry": round(float(grid[phases.contact]), 3),
            "entry_method": phases.entry_method,
        },
        "head_first": raw.head_first,
        "rotation": {
            "measured_deg": None if not np.isfinite(raw.rotation_deg) else round(abs(raw.rotation_deg), 1),
            "expected_deg": expected_rotation,
        },
        "quality": {"tracking_coverage": round(coverage, 3), "frames": int(t.size)},
        "warnings": warnings,
        "series": _series(body, phases.takeoff, px_per_m),
        **scored,
    }
