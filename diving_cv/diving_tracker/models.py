from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PoseMeasurement:
    keypoints: np.ndarray
    confidence: np.ndarray
    box: np.ndarray
    detection_confidence: float
    box_confidence: float | None = None


@dataclass
class FrameTrack:
    frame: int
    timestamp: float
    keypoints: np.ndarray
    confidence: np.ndarray
    predicted: np.ndarray
    box: np.ndarray | None
    detection_confidence: float
    com: np.ndarray
    board_tip: np.ndarray
    raw_keypoints: np.ndarray | None = None
    raw_confidence: np.ndarray | None = None
    measured_box: np.ndarray | None = None
    box_confidence: float = 0.0


@dataclass
class AnalysisResult:
    rows: list[dict]
    summary: dict
