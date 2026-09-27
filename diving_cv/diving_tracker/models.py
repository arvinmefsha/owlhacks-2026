from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PoseMeasurement:
    keypoints: np.ndarray
    confidence: np.ndarray
    box: np.ndarray
    detection_confidence: float


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


@dataclass
class AnalysisResult:
    rows: list[dict]
    summary: dict
