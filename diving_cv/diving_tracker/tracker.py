from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .backend import BackendConfig, UltralyticsTopDownBackend
from .calibration import CalibrationData
from .filters import KeypointFilter
from .kinematics import KinematicsAnalyzer, anthropometric_com, stabilize_left_right
from .models import AnalysisResult, FrameTrack


@dataclass
class TrackerConfig:
    backend: BackendConfig = field(default_factory=BackendConfig)
    keypoint_confidence: float = 0.4
    max_keypoint_prediction_seconds: float = 0.35
    max_box_prediction_seconds: float = 0.45
    process_variance: float = 900.0
    measurement_variance: float = 16.0


class DiverBoxTracker:
    """Extrapolates the detection crop using the diver's CoM/hip trajectory."""

    def __init__(self, max_prediction_seconds: float) -> None:
        self.max_prediction_seconds = float(max_prediction_seconds)
        self.box: np.ndarray | None = None
        self.anchor: np.ndarray | None = None
        self.velocity = np.zeros(2, dtype=float)
        self.timestamp: float | None = None
        self.last_measurement_timestamp: float | None = None

    def predict(self, timestamp: float) -> np.ndarray | None:
        if (
            self.box is None
            or self.anchor is None
            or self.timestamp is None
            or self.last_measurement_timestamp is None
        ):
            return None
        if timestamp - self.last_measurement_timestamp > self.max_prediction_seconds:
            return None
        dt = max(0.0, timestamp - self.timestamp)
        shift = self.velocity * dt
        return self.box + np.array(
            [shift[0], shift[1], shift[0], shift[1]], dtype=float
        )

    def update(
        self, box: np.ndarray, anchor: np.ndarray | None, timestamp: float
    ) -> None:
        box = np.asarray(box, dtype=float)
        measured_anchor = (
            np.asarray(anchor, dtype=float)
            if anchor is not None and np.isfinite(anchor).all()
            else 0.5 * (box[:2] + box[2:])
        )
        if self.anchor is not None and self.timestamp is not None:
            dt = timestamp - self.timestamp
            if dt > 1e-4:
                measured_velocity = (measured_anchor - self.anchor) / dt
                self.velocity = 0.65 * self.velocity + 0.35 * measured_velocity
        self.box = box.copy()
        self.anchor = measured_anchor.copy()
        self.timestamp = float(timestamp)
        self.last_measurement_timestamp = float(timestamp)

    def advance(self, timestamp: float) -> None:
        predicted = self.predict(timestamp)
        if (
            predicted is not None
            and self.anchor is not None
            and self.timestamp is not None
        ):
            dt = max(0.0, timestamp - self.timestamp)
            self.anchor = self.anchor + self.velocity * dt
            self.box = predicted
            self.timestamp = float(timestamp)


class BoardTipTracker:
    """Tracks local board texture to estimate springboard depression."""

    def __init__(self, point: tuple[float, float]) -> None:
        self.point = np.asarray(point, dtype=np.float32).reshape(1, 1, 2)
        self.previous_gray: np.ndarray | None = None

    def step(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self.previous_gray is None:
            self.previous_gray = gray
            return self.point.reshape(2).astype(float)
        next_point, status, _ = cv2.calcOpticalFlowPyrLK(
            self.previous_gray,
            gray,
            self.point,
            None,
            winSize=(25, 25),
            maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
        )
        accepted = bool(next_point is not None and status is not None and status[0, 0])
        if accepted:
            reverse, reverse_status, _ = cv2.calcOpticalFlowPyrLK(
                gray, self.previous_gray, next_point, None, winSize=(25, 25), maxLevel=3
            )
            error = (
                float(np.linalg.norm(reverse - self.point))
                if reverse is not None
                and reverse_status is not None
                and reverse_status[0, 0]
                else np.inf
            )
            jump = float(np.linalg.norm(next_point - self.point))
            accepted = error <= 2.0 and jump <= 25.0
        if accepted:
            self.point = next_point
        self.previous_gray = gray
        return self.point.reshape(2).astype(float)


class DivingTracker:
    """Complete two-stage detector, pose, temporal filtering, and analysis pipeline."""

    def __init__(
        self,
        config: TrackerConfig | None = None,
        backend: UltralyticsTopDownBackend | None = None,
    ) -> None:
        self.config = config or TrackerConfig()
        self.backend = backend

    def process(
        self,
        video_path: str | Path,
        calibration: CalibrationData,
        progress: Callable[[int, int], None] | None = None,
    ) -> tuple[list[FrameTrack], AnalysisResult]:
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise FileNotFoundError(f"Could not open video: {video_path}")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not np.isfinite(fps) or fps <= 1:
            fps = 30.0
        calibration.validate(width, height)

        backend = self.backend or UltralyticsTopDownBackend(self.config.backend)
        keypoint_filter = KeypointFilter(
            confidence_threshold=self.config.keypoint_confidence,
            max_prediction_seconds=self.config.max_keypoint_prediction_seconds,
            process_variance=self.config.process_variance,
            measurement_variance=self.config.measurement_variance,
        )
        box_tracker = DiverBoxTracker(self.config.max_box_prediction_seconds)
        board_tracker = BoardTipTracker(calibration.board_tip)
        tracks: list[FrameTrack] = []
        filtered_reference: np.ndarray | None = None
        index = 0
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                timestamp = index / fps
                board_tip = board_tracker.step(frame)
                predicted_box = box_tracker.predict(timestamp)
                detected_box, detection_confidence = backend.detect(
                    frame, calibration.roi, predicted_box
                )
                pose = None
                if detected_box is not None:
                    pose = backend.estimate_pose(
                        frame, detected_box, detection_confidence
                    )
                elif predicted_box is not None:
                    pose = backend.estimate_pose(frame, predicted_box, 0.0)

                if pose is not None:
                    raw_points, raw_confidence = stabilize_left_right(
                        pose.keypoints.copy(),
                        pose.confidence.copy(),
                        filtered_reference,
                    )
                    filtered, confidence, predicted = keypoint_filter.step(
                        timestamp, raw_points, raw_confidence
                    )
                else:
                    filtered, confidence, predicted = keypoint_filter.step(
                        timestamp, None, None
                    )
                com = anthropometric_com(filtered)
                if np.isfinite(filtered).any():
                    filtered_reference = filtered.copy()

                update_box = pose.box if pose is not None else detected_box
                if update_box is not None:
                    box_tracker.update(
                        update_box, com if np.isfinite(com).all() else None, timestamp
                    )
                else:
                    box_tracker.advance(timestamp)
                stored_box = None if box_tracker.box is None else box_tracker.box.copy()
                tracks.append(
                    FrameTrack(
                        frame=index,
                        timestamp=timestamp,
                        keypoints=filtered,
                        confidence=confidence,
                        predicted=predicted,
                        box=stored_box,
                        detection_confidence=float(detection_confidence),
                        com=com,
                        board_tip=board_tip,
                    )
                )
                index += 1
                if progress:
                    progress(index, frame_count)
        finally:
            capture.release()
        if not tracks:
            raise RuntimeError("The input video contained no readable frames.")
        analysis = KinematicsAnalyzer(calibration, fps).analyze(tracks)
        return tracks, analysis
