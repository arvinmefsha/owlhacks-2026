"""Local asynchronous YOLO analysis jobs.

The worker is deliberately local for now. It serializes GPU access, keeps model weights
loaded between dives, and exposes progress without tying inference to an HTTP request.
"""

from __future__ import annotations

import logging
import json
import shutil
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import cv2
import numpy as np
from analysis import analyze_dive
from diving_tracker.backend import BackendConfig, UltralyticsTopDownBackend
from diving_tracker.calibration import CalibrationData
from diving_tracker.tracker import DivingTracker, TrackerConfig
from diving_tracker.dive_context import DiveContext
from feedback.gemini import rule_based_feedback

log = logging.getLogger(__name__)


def _metric_rows(dive_id: UUID, diver_id: UUID, at: datetime, analysis: dict) -> list[tuple]:
    rows = [(dive_id, diver_id, at, m["phase"], m["key"], m["value"], None) for m in analysis["metrics"] if m["value"] is not None]
    rows += [(dive_id, diver_id, at, "info", i["key"], i["value"], None) for i in analysis["info"]]
    return rows


class AnalysisJobManager:
    """One warm model pair and one inference worker for predictable local resource use."""

    def __init__(self, db: Any, coach: Any, settings: Any) -> None:
        self.db = db
        self.coach = coach
        self.settings = settings
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yolo-analysis")
        self.feedback_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dive-feedback")
        self.jobs: dict[UUID, dict] = {}
        self.futures: dict[UUID, Future] = {}
        self.lock = Lock()
        self.backend: UltralyticsTopDownBackend | None = None

    def submit(self, payload: dict, video_path: Path, mime: str) -> UUID:
        job_id = uuid4()
        with self.lock:
            self.jobs[job_id] = {
                "id": str(job_id),
                "status": "queued",
                "stage": "Waiting for the local analysis worker",
                "progress": 0.0,
                "frames_processed": 0,
                "total_frames": None,
                "dive_id": None,
                "error": None,
                "profile": payload["profile"],
            }
            self.futures[job_id] = self.executor.submit(self._run, job_id, payload, video_path, mime)
        return job_id

    def get(self, job_id: UUID) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def cancel(self, job_id: UUID) -> bool:
        with self.lock:
            future = self.futures.get(job_id)
            job = self.jobs.get(job_id)
            if future is None or job is None or future.running() or future.done():
                return False
            if not future.cancel():
                return False
            job.update(status="cancelled", stage="Cancelled", error=None)
            return True

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.feedback_executor.shutdown(wait=False, cancel_futures=True)

    def _finish_feedback(self, dive_id: UUID, analysis: dict, setup: dict) -> None:
        try:
            feedback, source = self.coach.feedback(analysis, setup)
            self.db.save_feedback(dive_id, feedback.model_dump(), source)
        except Exception:
            # The initial rule-based feedback is already saved with the dive.
            log.exception("Coaching update for dive %s failed", dive_id)
            try:
                self.db.finish_pending_feedback(dive_id)
            except Exception:
                log.exception("Could not finalize rule-based feedback for dive %s", dive_id)

    def _update(self, job_id: UUID, **values: Any) -> None:
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id].update(values)

    def _backend(self, profile: str) -> tuple[UltralyticsTopDownBackend, TrackerConfig]:
        size = 640 if profile == "fast" else 768
        backend_config = BackendConfig(
            detector_model=self.settings.yolo_detector_model,
            pose_model=self.settings.yolo_pose_model,
            device=self.settings.yolo_device,
            detector_size=640,
            pose_size=size,
            crop_padding=0.30,
        )
        if self.backend is None:
            self.backend = UltralyticsTopDownBackend(backend_config)
        else:
            # Jobs are serialized, so one loaded model pair can safely use profile-specific sizes.
            backend_config.device = self.backend.config.device
            self.backend.config = backend_config
        return self.backend, TrackerConfig(backend=backend_config, calculate_legacy_kinematics=False)

    def _run(self, job_id: UUID, payload: dict, video_path: Path, mime: str) -> None:
        try:
            self._update(job_id, status="running", stage="Opening video", progress=0.02)
            capture = cv2.VideoCapture(str(video_path))
            if not capture.isOpened():
                raise ValueError("The uploaded video could not be decoded.")
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            capture.release()
            if width <= 0 or height <= 0:
                raise ValueError("The uploaded video has invalid dimensions.")

            calibration_payload = payload["calibration"]
            board = calibration_payload.get("board_tip")
            water_y = calibration_payload.get("water_y")
            if board is None or water_y is None:
                raise ValueError("Mark the board tip and visible air-water surface before analysis.")
            roi_payload = calibration_payload.get("roi")
            if roi_payload:
                roi = (
                    round(roi_payload["x"] * width),
                    round(roi_payload["y"] * height),
                    round(roi_payload["width"] * width),
                    round(roi_payload["height"] * height),
                )
            else:
                roi = (0, 0, width, height)
            calibration = CalibrationData(
                board_tip=(board["x"] * width, board["y"] * height),
                water_point=(board["x"] * width, water_y * height),
                roi=roi,
                vertical_reference_m=float(payload["setup"]["board_height_m"]),
            )
            calibration.validate(width, height)

            backend, tracker_config = self._backend(payload["profile"])
            tracker = DivingTracker(tracker_config, backend=backend)

            def progress(current: int, frame_count: int) -> None:
                denominator = frame_count or total or max(current, 1)
                self._update(
                    job_id,
                    stage="Tracking diver with YOLO",
                    progress=min(0.82, 0.05 + 0.77 * current / denominator),
                    frames_processed=current,
                    total_frames=frame_count or total or None,
                )

            setup = payload["setup"]
            tracks, kinematics = tracker.process(video_path, calibration, progress,
                context=DiveContext(position=setup.get("position"), direction=setup.get("direction"),
                                    somersaults=setup.get("somersaults")))
            self._update(job_id, stage="Calculating dive metrics", progress=0.86)
            frames = []
            for track in tracks:
                landmarks = []
                for point, confidence, predicted in zip(track.keypoints, track.confidence, track.predicted):
                    if not np.isfinite(point).all():
                        landmarks.append([0.0, 0.0, 0.0, 0.0])
                    else:
                        score = min(float(confidence), 0.29) if predicted else float(confidence)
                        landmarks.append(
                            [float(point[0] / width), float(point[1] / height), 0.0, score]
                        )
                measured_box = track.measured_box
                raw = track.raw_keypoints
                raw_conf = track.raw_confidence
                evidence = None if raw is None or raw_conf is None else [
                    [float(p[0] / width), float(p[1] / height), 0.0, float(c)]
                    if np.isfinite(p).all() else [0., 0., 0., 0.]
                    for p, c in zip(raw, raw_conf)
                ]
                frames.append({"t": track.timestamp, "lm": landmarks,
                    "evidence_lm": evidence,
                    "box": (measured_box / np.array([width, height, width, height])).tolist()
                        if measured_box is not None and np.isfinite(measured_box).all() else None,
                    "box_confidence": track.box_confidence})

            diver = self.db.get_diver(UUID(payload["diver_id"]))
            if diver is None:
                raise ValueError("The selected diver no longer exists.")
            setup = payload["setup"]
            analysis = analyze_dive(
                frames,
                width,
                height,
                setup,
                calibration_payload,
                diver["height_cm"],
            )
            analysis["model"] = {
                "family": "YOLO11-Pose",
                "profile": payload["profile"],
                "schema": "coco17-v1",
                "pipeline": "pts-rotation-carry-v4",
            }
            analysis["processing"] = tracker.diagnostics
            if payload["source"] == "live":
                self._update(job_id, stage="Writing coaching feedback", progress=0.92)
                initial_feedback, feedback_source = self.coach.feedback(analysis, setup)
                coaching_pending = False
            else:
                self._update(job_id, stage="Saving video analysis", progress=0.92)
                initial_feedback = rule_based_feedback(analysis)
                coaching_pending = self.coach.enabled and analysis.get("method") != "macro-envelope-v1"
                feedback_source = "pending" if coaching_pending else "rules"

            dive_id = uuid4()
            recorded_at = datetime.now(UTC)
            extension = video_path.suffix.lower()
            final_name = f"{dive_id}{extension}"
            final_path = self.settings.upload_dir / final_name
            diagnostics_path = final_path.with_suffix(".tracking.npz")
            shutil.move(str(video_path), final_path)
            try:
                save_started = perf_counter()
                # Local audit data supports comparisons and future reprocessing
                # without another model pass. Never sent to an external service.
                np.savez_compressed(diagnostics_path,
                    frame=np.array([t.frame for t in tracks]),
                    times=np.array([t.timestamp for t in tracks]),
                    raw=np.array([t.raw_keypoints for t in tracks]),
                    raw_confidence=np.array([t.raw_confidence for t in tracks]),
                    points=np.array([t.keypoints for t in tracks]),
                    confidence=np.array([t.confidence for t in tracks]),
                    predicted=np.array([t.predicted for t in tracks]),
                    boxes=np.array([t.measured_box if t.measured_box is not None else np.full(4, np.nan) for t in tracks]),
                    box_confidence=np.array([t.box_confidence for t in tracks]),
                    candidate_diagnostics=np.array(json.dumps(tracker.candidate_diagnostics)))
                analysis["processing"]["seconds"]["save_artifact"] = perf_counter()-save_started
                self.db.insert_dive(
                    {
                        "id": dive_id,
                        "diver_id": UUID(payload["diver_id"]),
                        "recorded_at": recorded_at,
                        "setup": setup,
                        "calibration": calibration_payload,
                        "source": payload["source"],
                        "video_path": final_name,
                        "video_mime": mime,
                        "video_width": width,
                        "video_height": height,
                        "fps": fps if np.isfinite(fps) and fps > 0 else None,
                        "overall_score": None,
                        "scores": {},
                        "analysis": analysis,
                        "feedback": initial_feedback.model_dump(),
                        "feedback_source": feedback_source,
                    },
                    frames,
                    _metric_rows(dive_id, UUID(payload["diver_id"]), recorded_at, analysis),
                )
            except Exception:
                final_path.unlink(missing_ok=True)
                diagnostics_path.unlink(missing_ok=True)
                raise
            self._update(
                job_id,
                status="complete",
                stage="Video analysis complete",
                progress=1.0,
                dive_id=str(dive_id),
            )
            if coaching_pending:
                try:
                    self.feedback_executor.submit(self._finish_feedback, dive_id, analysis, setup)
                except Exception:
                    log.exception("Could not start coaching for dive %s", dive_id)
                    try:
                        self.db.finish_pending_feedback(dive_id)
                    except Exception:
                        log.exception("Could not finalize rule-based feedback for dive %s", dive_id)
        except Exception as exc:
            log.exception("YOLO analysis job %s failed", job_id)
            video_path.unlink(missing_ok=True)
            self._update(job_id, status="failed", stage="Analysis failed", error=str(exc))
