"""Vector helpers for arrays of 2D image points shaped (..., 2), with y pointing down.

Missing points are NaN and propagate to NaN results.
"""

import warnings

import numpy as np


def angle_at(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Angle ABC in degrees (180 means A, B and C are in a straight line)."""
    return vector_angle(a - b, c - b)


def vector_angle(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Unsigned angle between two vectors in degrees."""
    dot = np.sum(u * v, axis=-1)
    norm = np.linalg.norm(u, axis=-1) * np.linalg.norm(v, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.clip(dot / norm, -1.0, 1.0)
    return np.degrees(np.arccos(cos))


def direction_deg(start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """Direction of start->end in degrees clockwise from straight up on screen.

    0 is up, 90 is right, 180 is down, -90 is left.
    """
    d = end - start
    return np.degrees(np.arctan2(d[..., 0], -d[..., 1]))


def wrap_deg(a: np.ndarray | float) -> np.ndarray | float:
    """Wrap angles into [-180, 180)."""
    return (np.asarray(a) + 180.0) % 360.0 - 180.0


def unwrap_deg(a: np.ndarray) -> np.ndarray:
    """Unwrap a series of directions so it accumulates past +/-180 (NaNs are bridged)."""
    out = np.full_like(a, np.nan, dtype=float)
    finite = np.isfinite(a)
    if finite.sum() == 0:
        return out
    out[finite] = np.degrees(np.unwrap(np.radians(a[finite])))
    return out


def distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.linalg.norm(a - b, axis=-1)


def nanmedian(a: np.ndarray) -> float:
    """Median ignoring NaN; NaN (without a warning) when nothing is finite."""
    a = np.asarray(a, dtype=float)
    if not np.isfinite(a).any():
        return float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return float(np.nanmedian(a))


def nanpercentile(a: np.ndarray, q: float) -> float:
    a = np.asarray(a, dtype=float)
    if not np.isfinite(a).any():
        return float("nan")
    return float(np.nanpercentile(a, q))
