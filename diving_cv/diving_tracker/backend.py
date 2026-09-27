from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import PoseMeasurement


def box_iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (
        max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
        + max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
        - intersection
    )
    return float(intersection / union) if union > 0 else 0.0


def padded_box(
    box: np.ndarray, width: int, height: int, padding: float = 0.3
) -> np.ndarray:
    center_x = 0.5 * (box[0] + box[2])
    center_y = 0.5 * (box[1] + box[3])
    box_width = max(2.0, box[2] - box[0]) * (1.0 + 2.0 * padding)
    box_height = max(2.0, box[3] - box[1]) * (1.0 + 2.0 * padding)
    # A mildly square crop protects extended arms during an inverted pose.
    side = max(box_width, box_height * 0.72)
    box_width = max(box_width, side)
    box_height = max(box_height, side)
    return np.array(
        [
            max(0.0, center_x - box_width / 2),
            max(0.0, center_y - box_height / 2),
            min(float(width - 1), center_x + box_width / 2),
            min(float(height - 1), center_y + box_height / 2),
        ],
        dtype=float,
    )


@dataclass
class BackendConfig:
    detector_model: str = "yolo11n.pt"
    pose_model: str = "yolo11m-pose.pt"
    device: str | None = None
    detector_confidence: float = 0.2
    pose_confidence: float = 0.15
    detector_size: int = 960
    pose_size: int = 960
    crop_padding: float = 0.3


class UltralyticsTopDownBackend:
    """YOLO person detector followed by a separate pose pass on a padded crop."""

    def __init__(self, config: BackendConfig) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "Ultralytics is not installed. Run `python -m pip install -e .` in diving_cv."
            ) from exc
        self.config = config
        self.detector = YOLO(config.detector_model)
        self.pose = YOLO(config.pose_model)

    def detect(
        self,
        frame: np.ndarray,
        roi: tuple[int, int, int, int],
        predicted_box: np.ndarray | None,
    ) -> tuple[np.ndarray | None, float]:
        x, y, w, h = roi
        roi_frame = frame[y : y + h, x : x + w]
        options: dict[str, object] = {
            "source": roi_frame,
            "classes": [0],
            "conf": self.config.detector_confidence,
            "iou": 0.55,
            "imgsz": self.config.detector_size,
            "verbose": False,
        }
        if self.config.device:
            options["device"] = self.config.device
        result = self.detector.predict(**options)[0]
        if result.boxes is None or len(result.boxes) == 0:
            return None, 0.0
        boxes = result.boxes.xyxy.cpu().numpy().astype(float)
        scores = result.boxes.conf.cpu().numpy().astype(float)
        boxes[:, [0, 2]] += x
        boxes[:, [1, 3]] += y

        if predicted_box is None:
            areas = np.maximum(1.0, boxes[:, 2] - boxes[:, 0]) * np.maximum(
                1.0, boxes[:, 3] - boxes[:, 1]
            )
            ranks = scores + 0.03 * np.log1p(areas / max(w * h, 1))
        else:
            predicted_center = 0.5 * (predicted_box[:2] + predicted_box[2:])
            centers = 0.5 * (boxes[:, :2] + boxes[:, 2:])
            diagonal = max(
                float(np.linalg.norm(predicted_box[2:] - predicted_box[:2])), 1.0
            )
            distance = np.linalg.norm(centers - predicted_center, axis=1) / diagonal
            overlaps = np.array(
                [box_iou(candidate, predicted_box) for candidate in boxes]
            )
            ranks = 0.65 * scores + 0.45 * overlaps - 0.50 * np.minimum(distance, 3.0)
        index = int(np.argmax(ranks))
        return boxes[index], float(scores[index])

    def estimate_pose(
        self, frame: np.ndarray, detection_box: np.ndarray, detection_confidence: float
    ) -> PoseMeasurement | None:
        height, width = frame.shape[:2]
        crop_box = padded_box(detection_box, width, height, self.config.crop_padding)
        x1, y1, x2, y2 = np.rint(crop_box).astype(int)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        options: dict[str, object] = {
            "source": crop,
            "conf": self.config.pose_confidence,
            "iou": 0.6,
            "imgsz": self.config.pose_size,
            "verbose": False,
        }
        if self.config.device:
            options["device"] = self.config.device
        result = self.pose.predict(**options)[0]
        if (
            result.keypoints is None
            or result.keypoints.xy is None
            or len(result.keypoints.xy) == 0
        ):
            return None
        all_points = result.keypoints.xy.cpu().numpy().astype(float)
        if result.keypoints.conf is None:
            all_confidence = np.ones(all_points.shape[:2], dtype=float)
        else:
            all_confidence = result.keypoints.conf.cpu().numpy().astype(float)
        person_confidence = (
            result.boxes.conf.cpu().numpy().astype(float)
            if result.boxes is not None and len(result.boxes)
            else np.nanmean(all_confidence, axis=1)
        )
        quality = 0.6 * np.nanmedian(all_confidence, axis=1) + 0.4 * person_confidence
        index = int(np.nanargmax(quality))
        points = all_points[index]
        points[:, 0] += x1
        points[:, 1] += y1
        points[all_confidence[index] <= 0] = np.nan
        return PoseMeasurement(
            keypoints=points,
            confidence=all_confidence[index],
            box=np.asarray(detection_box, dtype=float),
            detection_confidence=float(detection_confidence),
        )
