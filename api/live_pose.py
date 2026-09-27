"""Single-frame YOLO pose for the live session's dive trigger.

A small pose model of its own, separate from the analysis worker's, so live frames wait at most
for the worker's current YOLO call, never for a whole dive. Ultralytics and torch are imported on
first use, so the API runs (and its tests pass) without them.
"""

import logging
from time import perf_counter
from typing import Any

import cv2
import numpy as np
from config import Settings
from diving_tracker.backend import INFERENCE_LOCK

log = logging.getLogger(__name__)


class LivePoseUnavailable(RuntimeError):
    """The YOLO stack is not installed, or the live pose model could not be loaded or run."""


class FrameDecodeError(ValueError):
    """The uploaded bytes are not a decodable image."""


def _numpy(values: Any) -> np.ndarray:
    return values.cpu().numpy() if hasattr(values, "cpu") else np.asarray(values)


def parse_pose_result(result: Any) -> dict:
    """The most confident person in an Ultralytics pose result, in normalized frame coordinates."""
    boxes, keypoints = result.boxes, result.keypoints
    if boxes is None or keypoints is None or keypoints.xyn is None:
        return {"person": False, "keypoints": None, "box": None}
    scores = _numpy(boxes.conf).astype(float)
    if scores.size == 0:
        return {"person": False, "keypoints": None, "box": None}
    index = int(np.argmax(scores))
    points = np.nan_to_num(_numpy(keypoints.xyn)[index].astype(float))
    confidence = np.ones(len(points)) if keypoints.conf is None else _numpy(keypoints.conf)[index].astype(float)
    # Ultralytics zeroes the coordinates of keypoints it considers not visible; (0, 0) is not a real position.
    confidence = np.where((points == 0).all(axis=1), 0.0, np.nan_to_num(confidence))
    box = np.nan_to_num(_numpy(boxes.xyxyn)[index].astype(float))
    return {
        "person": True,
        "keypoints": [
            [round(float(x), 4), round(float(y), 4), round(float(c), 4)]
            for (x, y), c in zip(points, confidence)
        ],
        "box": [round(float(v), 4) for v in box],
    }


class LivePoseDetector:
    """Loads settings.live_pose_model on the first frame and finds the diver in single frames."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model: Any = None
        self._device: str | None = None
        self._worked = False

    def _load(self) -> None:
        try:
            import torch
            from ultralytics import YOLO
        except (ImportError, OSError):  # a broken torch install on Windows fails with OSError (missing DLL)
            raise LivePoseUnavailable(
                "Live pose detection needs the YOLO stack (ultralytics and torch). "
                "Install diving_cv's dependencies into the API environment and restart the API."
            ) from None
        name = self.settings.live_pose_model
        try:
            device = self.settings.yolo_device or (
                "cuda:0" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
            )
            model = YOLO(name)
        except Exception as exc:
            log.warning("Loading the live pose model %s failed (%s: %s)", name, type(exc).__name__, str(exc)[:300])
            raise LivePoseUnavailable(f"The live pose model {name} could not be loaded.") from None
        log.info("Live pose model %s loaded on %s", name, device)
        self._model, self._device = model, device

    def detect(self, image_bytes: bytes) -> dict:
        """Pose of the most confident person in one JPEG, PNG or WebP frame.

        Raises FrameDecodeError if the bytes are not a decodable image, and LivePoseUnavailable if
        the model cannot be loaded or fails before it has ever worked (a setup problem such as an
        invalid YOLO_DEVICE). Later failures propagate unchanged, as they may be transient.
        """
        try:
            frame = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR) if image_bytes else None
        except cv2.error:
            frame = None
        if frame is None:
            raise FrameDecodeError("The frame could not be decoded as an image.")
        # Shared with the analysis worker; parsing copies tensors off the GPU, so it stays inside too.
        with INFERENCE_LOCK:
            if self._model is None:
                self._load()
            started = perf_counter()
            try:
                result = self._model.predict(
                    source=frame,
                    imgsz=self.settings.live_pose_size,
                    classes=[0],
                    device=self._device,
                    verbose=False,
                )[0]
                parsed = parse_pose_result(result)
            except Exception as exc:
                if self._worked:
                    raise
                reason = f"{type(exc).__name__}: {str(exc)[:200]}"
                log.warning("The live pose model failed on its first frame (%s)", reason)
                raise LivePoseUnavailable(f"Live pose detection could not run on device {self._device} ({reason}).") from None
            elapsed_ms = (perf_counter() - started) * 1000
            self._worked = True
        return {**parsed, "inference_ms": round(elapsed_ms, 1)}
