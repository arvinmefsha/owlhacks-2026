from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from .backend import BackendConfig, UltralyticsTopDownBackend
from .calibration import CalibrationData
from .refinement import refine_poses, reliable_pose, pose_quality
from .timing import read_timeline
from .dive_context import DiveContext, RotationState, recovery_needed, select_sequence, phase_hypothesis
from .rotation_recovery import OrientationRecovery
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
    detection_interval: int = 4
    retry_interval: int = 3


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
        self.diagnostics: dict = {}

    def process(
        self,
        video_path: str | Path,
        calibration: CalibrationData,
        progress: Callable[[int, int], None] | None = None,
        context: DiveContext | None = None,
    ) -> tuple[list[FrameTrack], AnalysisResult]:
        started = perf_counter()
        context = context or DiveContext()
        rotation_state = RotationState()
        previous_pose = None
        candidate_rows = []
        self.candidate_diagnostics = []
        phases = []
        previous_phase = "unknown"
        timeline = read_timeline(video_path)
        recovery = OrientationRecovery(len(timeline.times))
        timings = {"timeline": perf_counter()-started, "decode": 0., "detect": 0., "pose": 0., "board": 0.}
        calls = {"detect": 0, "pose": 0, "retry": 0}
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
        box_tracker = DiverBoxTracker(self.config.max_box_prediction_seconds)
        board_tracker = BoardTipTracker(calibration.board_tip)
        tracks: list[FrameTrack] = []
        filtered_reference: np.ndarray | None = None
        index = 0
        last_detection = -self.config.detection_interval
        previous_reliable = False

        def detect(frame, predicted_box):
            start = perf_counter()
            result = backend.detect(frame, calibration.roi, predicted_box)
            timings["detect"] += perf_counter()-start
            calls["detect"] += 1
            return result

        def estimate(frame, box, confidence, **kwargs):
            start = perf_counter()
            result = backend.estimate_pose(frame, box, confidence, **kwargs)
            timings["pose"] += perf_counter()-start
            calls["pose"] += 1
            return result

        try:
            while True:
                start = perf_counter()
                ok, frame = capture.read()
                timings["decode"] += perf_counter()-start
                if not ok:
                    break
                if index >= len(timeline.times):
                    raise ValueError("Video decoders disagree on frame count; cannot align poses safely.")
                timestamp = float(timeline.times[index])
                start = perf_counter()
                board_tip = board_tracker.step(frame)
                timings["board"] += perf_counter()-start
                predicted_box = box_tracker.predict(timestamp)
                do_detect = (predicted_box is None or not previous_reliable or index-last_detection >= self.config.detection_interval)
                detected_box, detection_confidence = (None, 0.)
                if do_detect:
                    detected_box, detection_confidence = detect(frame, predicted_box)
                    last_detection = index
                crop_box = detected_box if detected_box is not None else predicted_box
                pose = None
                candidates = []
                oriented_candidates = []
                primary_turn = recovery.turn
                if crop_box is not None:
                    pose = estimate(frame, crop_box, detection_confidence, rotation=primary_turn)
                    if pose is not None: candidates.append(pose)
                    oriented_candidates.append((primary_turn, pose))
                # Reacquire immediately if the cheap propagated crop fails.
                if not reliable_pose(pose) and not do_detect:
                    detected_box, detection_confidence = detect(frame, predicted_box)
                    last_detection = index
                    if detected_box is not None:
                        crop_box = detected_box
                        candidate = estimate(frame, crop_box, detection_confidence, rotation=primary_turn)
                        if candidate is not None: candidates.append(candidate)
                        oriented_candidates.append((primary_turn, candidate))
                        if pose_quality(candidate) > pose_quality(pose):
                            pose = candidate
                predicted_angle = rotation_state.predict(timestamp)
                for rotation in recovery.alternatives(pose, crop_box, index, board_tip, calibration.water_y,
                        not reliable_pose(pose) or recovery_needed(pose, previous_pose, predicted_angle, context)):
                    candidate = estimate(frame, crop_box, detection_confidence,
                                         rotation=rotation, padding=.5, pose_size=960)
                    if candidate is not None:candidates.append(candidate)
                    oriented_candidates.append((rotation,candidate))
                    calls["retry"] += 1
                pose = recovery.accept(oriented_candidates)

                candidate_rows.append(candidates or [None])
                rotation_state.update(pose, timestamp)
                previous_pose = pose
                phase = phase_hypothesis(pose, board_tip, calibration.water_y, previous_phase)
                phases.append(phase)
                if phase != "unknown": previous_phase = phase

                raw_points = np.full((17,2), np.nan)
                raw_confidence = np.zeros(17)
                if pose is not None:
                    raw_points, raw_confidence = stabilize_left_right(
                        pose.keypoints.copy(),
                        pose.confidence.copy(),
                        filtered_reference,
                    )
                filtered = raw_points.copy()
                filtered[raw_confidence < 0.35] = np.nan
                confidence = np.where(np.isfinite(filtered).all(axis=1), raw_confidence, 0.)
                predicted = np.zeros(17, dtype=bool)
                com = anthropometric_com(filtered)
                if np.isfinite(filtered).any():
                    filtered_reference = filtered.copy()

                previous_reliable = reliable_pose(pose)
                update_box = pose.box if pose is not None and pose_quality(pose) >= 0.35 else detected_box
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
                        raw_keypoints=raw_points,
                        raw_confidence=raw_confidence,
                    )
                )
                index += 1
                if progress:
                    progress(index, frame_count)
        finally:
            capture.release()
        if not tracks:
            raise RuntimeError("The input video contained no readable frames.")
        if len(tracks) != len(timeline.times):
            raise ValueError("Video decoders disagree on frame count; cannot align poses safely.")
        start = perf_counter()
        selected = select_sequence(candidate_rows, timeline.times)
        reference = None
        for i, choice in enumerate(selected):
            pose = candidate_rows[i][choice]
            if pose is not None:
                tracks[i].raw_keypoints, tracks[i].raw_confidence = stabilize_left_right(
                    pose.keypoints.copy(), pose.confidence.copy(), reference)
                reference = tracks[i].raw_keypoints.copy()
                reference[tracks[i].raw_confidence < .4] = np.nan
            else:
                reference = None
            self.candidate_diagnostics.append({"frame": i, "selected": choice,
                "phase_hypothesis": phases[i],
                "candidates": [{"points": [[float(v) if np.isfinite(v) else None for v in xy] for xy in p.keypoints],
                                "confidence": p.confidence.tolist(), "box": p.box.tolist()}
                               if p is not None else None for p in candidate_rows[i]]})
        points, scores, predicted = refine_poses(timeline.times,
            np.array([t.raw_keypoints for t in tracks]), np.array([t.raw_confidence for t in tracks]), calibration.water_y)
        for i, track in enumerate(tracks):
            track.keypoints, track.confidence, track.predicted = points[i], scores[i], predicted[i]
            track.com = anthropometric_com(points[i])
        timings["refine"] = perf_counter()-start
        start = perf_counter()
        analysis = KinematicsAnalyzer(calibration, fps).analyze(tracks)
        timings["kinematics"] = perf_counter()-start
        timings["total"] = perf_counter()-started
        self.diagnostics = {"timing": timeline.metadata(), "seconds": timings, "calls": calls,
                            "context": {"position": context.position, "direction": context.direction,
                                        "somersaults": context.somersaults},
                            "selection": "bounded-sequence-v1",
                            "retry_budget": recovery.budget,
                            "alternative_frames": sum(len(row)>1 for row in candidate_rows),
                            "device": backend.config.device, "filter": "offline-centered-v1"}
        analysis.summary["processing"] = self.diagnostics
        return tracks, analysis
