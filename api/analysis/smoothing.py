"""Turn raw, unevenly spaced landmark samples into an even, smoothed time series."""

import numpy as np
from scipy.signal import savgol_filter


def true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(start, stop) index pairs for each run of True values."""
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[::2], edges[1::2]))


def interpolate_gaps(t: np.ndarray, values: np.ndarray, max_gap_s: float) -> np.ndarray:
    """Linearly fill NaN gaps no longer than max_gap_s in each column of an (N, K) array."""
    out = values.copy()
    for j in range(out.shape[1]):
        col = out[:, j]
        idx = np.flatnonzero(np.isfinite(col))
        for a, b in zip(idx[:-1], idx[1:]):
            if b - a > 1 and t[b] - t[a] <= max_gap_s:
                col[a + 1 : b] = np.interp(t[a + 1 : b], [t[a], t[b]], [col[a], col[b]])
    return out


def resample_uniform(t: np.ndarray, values: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """Resample (N, K) values onto an even grid at fs Hz.

    Grid points that fall inside a gap that wasn't filled stay NaN rather than being bridged.
    """
    grid = np.arange(t[0], t[-1] + 1e-9, 1.0 / fs)
    out = np.full((grid.size, values.shape[1]), np.nan)
    right = np.clip(np.searchsorted(t, grid, side="left"), 0, t.size - 1)
    left = np.clip(right - 1, 0, t.size - 1)
    exact = np.isclose(t[right], grid)
    for j in range(values.shape[1]):
        col = values[:, j]
        finite = np.isfinite(col)
        if finite.sum() < 2:
            continue
        ok = (finite[left] & finite[right]) | (exact & finite[right])
        out[ok, j] = np.interp(grid[ok], t[finite], col[finite])
    return grid, out


def smooth(values: np.ndarray, fs: float, window_s: float = 0.15, polyorder: int = 2) -> np.ndarray:
    """Savitzky-Golay smoothing of each column, applied separately to each unbroken run."""
    window = max(5, int(round(window_s * fs)))
    if window % 2 == 0:
        window += 1
    out = values.copy()
    for j in range(values.shape[1]):
        col = values[:, j]
        for start, stop in true_runs(np.isfinite(col)):
            if stop - start >= window:
                out[start:stop, j] = savgol_filter(col[start:stop], window, polyorder)
    return out
