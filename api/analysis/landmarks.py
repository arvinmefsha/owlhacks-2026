"""COCO-17 pose indices and the combined body points used by dive analysis."""

from dataclasses import dataclass

import numpy as np

NUM_LANDMARKS = 17

NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16


def center_of_mass(
    shoulder: np.ndarray,
    hip: np.ndarray,
    knee: np.ndarray,
    ankle: np.ndarray,
    wrist: np.ndarray,
) -> np.ndarray:
    """Whole-body centre of mass from segment masses (simplified Dempster proportions).

    In flight this point follows a true parabola whatever the body shape, unlike the hips.
    COCO-17 has no toe landmark, so the ankle represents the foot segment.
    """
    arms = np.where(np.isfinite(wrist), shoulder + 0.4 * (wrist - shoulder), shoulder)
    return (
        0.578 * (hip + 0.6 * (shoulder - hip))  # head and trunk
        + 0.100 * arms
        + 0.200 * (hip + 0.43 * (knee - hip))  # thighs
        + 0.093 * (knee + 0.43 * (ankle - knee))  # shanks
        + 0.029 * ankle
    )


def _weighted_mid(xy: np.ndarray, vis: np.ndarray, left: int, right: int) -> np.ndarray:
    """Visibility-weighted midpoint of a left/right pair.

    From a side-on camera the two sides overlap, so squaring confidence leans on the
    side the detector can see most clearly.
    """
    pts = xy[:, [left, right], :]
    w = np.nan_to_num(vis[:, [left, right]], nan=0.0) ** 2
    w = np.where(np.isfinite(pts[..., 0]), w, 0.0)
    total = w.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        mid = np.nansum(pts * w[..., None], axis=1) / total[:, None]
    mid[total <= 0] = np.nan
    return mid


@dataclass
class Body:
    """Resampled, smoothed body points in pixels (y down), one row per time step."""

    t: np.ndarray
    nose: np.ndarray
    shoulder: np.ndarray
    hip: np.ndarray
    knee: np.ndarray
    ankle: np.ndarray
    wrist: np.ndarray
    l_ankle: np.ndarray
    r_ankle: np.ndarray
    com: np.ndarray

    @classmethod
    def from_landmarks(cls, t: np.ndarray, xy: np.ndarray, vis: np.ndarray) -> "Body":
        shoulder = _weighted_mid(xy, vis, L_SHOULDER, R_SHOULDER)
        hip = _weighted_mid(xy, vis, L_HIP, R_HIP)
        knee = _weighted_mid(xy, vis, L_KNEE, R_KNEE)
        ankle = _weighted_mid(xy, vis, L_ANKLE, R_ANKLE)
        wrist = _weighted_mid(xy, vis, L_WRIST, R_WRIST)
        return cls(
            t=t,
            nose=xy[:, NOSE, :],
            shoulder=shoulder,
            hip=hip,
            knee=knee,
            ankle=ankle,
            wrist=wrist,
            l_ankle=xy[:, L_ANKLE, :],
            r_ankle=xy[:, R_ANKLE, :],
            com=center_of_mass(shoulder, hip, knee, ankle, wrist),
        )

    def extremities(self) -> list[np.ndarray]:
        """Points that can lead the entry: hands for head-first, feet for feet-first."""
        return [self.nose, self.wrist, self.ankle]
