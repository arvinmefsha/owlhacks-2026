"""Split a dive into takeoff, apex and entry."""

from dataclasses import dataclass

import numpy as np

G = 9.81
# How far before the apex to look for takeoff. A 10 m platform dive rises for well under a second.
TAKEOFF_SEARCH_S = 1.2
# The flight parabola is fitted to the part of the flight within this distance of the apex.
FIT_DEPTH_M = 0.25
# How far the centre of mass may stray from the flight parabola before we call it board contact.
BALLISTIC_TOLERANCE_M = 0.03
# If the body finishes this far below takeoff level, the diver went into water below the board
# rather than landing on the same floor they jumped from.
DROP_TO_WATER_M = 1.0
LANDING_TOLERANCE_M = 0.02


@dataclass
class Phases:
    takeoff: int
    apex: int
    contact: int
    entry_method: str


def _fit_flight_parabola(t: np.ndarray, y: np.ndarray, idx: np.ndarray, g_px: float) -> np.ndarray | None:
    """Least-squares fit of y = g/2 t^2 + c1 t + c0 (gravity fixed) over the given samples."""
    idx = idx[np.isfinite(y[idx])]
    if idx.size < 3:
        return None
    a = np.stack([t[idx], np.ones(idx.size)], axis=1)
    (c1, c0), *_ = np.linalg.lstsq(a, y[idx] - 0.5 * g_px * t[idx] ** 2, rcond=None)
    return 0.5 * g_px * t**2 + c1 * t + c0


def _takeoff_and_apex(
    t: np.ndarray, com_y: np.ndarray, first_valid: int, fs: float, px_per_m: float
) -> tuple[int, int]:
    apex = int(np.nanargmin(com_y))
    start = max(first_valid, apex - int(TAKEOFF_SEARCH_S * fs))
    upward_speed = -np.gradient(com_y) * fs
    window = upward_speed[start : apex + 1]
    if apex <= start or not np.isfinite(window).any():
        return apex, apex
    # After the feet leave the board upward speed can only fall, so its peak is a first guess.
    rough = start + int(np.nanargmax(window))

    # Refine: once airborne the centre of mass follows a gravity parabola. Fit it to the top of the
    # flight (certainly airborne), then walk back to where the real path leaves it: before takeoff
    # the board was still pushing, so the body sits above the backward-extrapolated parabola.
    depth = min(FIT_DEPTH_M * px_per_m, 0.6 * (com_y[rough] - com_y[apex]))
    if not depth > 2.0:
        return rough, apex
    near_top = com_y <= com_y[apex] + depth
    lo, hi = apex, apex
    while lo - 1 > start and near_top[lo - 1]:
        lo -= 1
    while hi + 1 < com_y.size and near_top[hi + 1]:
        hi += 1
    fit_idx = np.arange(lo, hi + 1)
    parabola = _fit_flight_parabola(t, com_y, fit_idx, G * px_per_m)
    if parabola is None:
        return rough, apex
    apex = int(fit_idx[np.argmin(parabola[fit_idx])])

    residual = com_y - parabola
    noise = float(np.sqrt(np.nanmean(residual[fit_idx] ** 2)))
    grounded = residual < -max(BALLISTIC_TOLERANCE_M * px_per_m, 3 * noise, 2.0)
    for i in range(lo - 1, start, -1):
        if grounded[i] and grounded[i - 1]:
            return i + 1, apex
    return rough, apex


def find_phases(
    t: np.ndarray,
    fs: float,
    com_y: np.ndarray,
    lowest_y: np.ndarray,
    frame_height: float,
    px_per_m: float,
    water_y_px: float | None,
    water_method: str,
) -> Phases:
    """Indices of takeoff, apex and first water contact on the resampled grid.

    com_y and lowest_y are image rows (y down). lowest_y is the lowest tracked body point in each
    frame, which is what touches the water first.
    """
    valid = np.flatnonzero(np.isfinite(com_y))
    first_valid, last_valid = int(valid[0]), int(valid[-1])
    n = com_y.size

    takeoff, apex = _takeoff_and_apex(t, com_y, first_valid, fs, px_per_m)

    after = np.arange(apex + 1, n)
    contact: int | None = None
    method = water_method

    if water_y_px is not None:
        hits = after[lowest_y[after] >= water_y_px]
        if hits.size:
            contact = int(hits[0])

    if contact is None:
        drop_m = (com_y[last_valid] - com_y[takeoff]) / px_per_m
        if drop_m > DROP_TO_WATER_M:
            leaves_frame = after[lowest_y[after] >= frame_height * 0.98]
            contact, method = (int(leaves_frame[0]), "frame_edge") if leaves_frame.size else (last_valid, "last_tracked")
        else:
            landed = after[com_y[after] >= com_y[takeoff] - LANDING_TOLERANCE_M * px_per_m]
            contact = int(landed[0]) if landed.size else last_valid
            method = "landing"

    contact = min(max(contact, apex + 1), n - 1)
    return Phases(takeoff=takeoff, apex=apex, contact=contact, entry_method=method)
