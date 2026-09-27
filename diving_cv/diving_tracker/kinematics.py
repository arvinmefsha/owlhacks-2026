from __future__ import annotations

import json
from dataclasses import asdict

import numpy as np

from .calibration import CalibrationData
from .constants import (
    ENTRY_INDICES,
    LEFT_ANKLE,
    LEFT_ELBOW,
    LEFT_HIP,
    LEFT_KNEE,
    LEFT_RIGHT_PAIRS,
    LEFT_SHOULDER,
    LEFT_WRIST,
    RIGHT_ANKLE,
    RIGHT_ELBOW,
    RIGHT_HIP,
    RIGHT_KNEE,
    RIGHT_SHOULDER,
    RIGHT_WRIST,
)
from .models import AnalysisResult, FrameTrack


def _valid(point: np.ndarray) -> bool:
    return np.asarray(point).shape == (2,) and bool(np.isfinite(point).all())


def _mean_points(points: np.ndarray) -> np.ndarray:
    valid = np.isfinite(points).all(axis=1)
    return np.mean(points[valid], axis=0) if np.any(valid) else np.full(2, np.nan)


def _segment_center(
    points: np.ndarray, first: int, second: int, fraction: float = 0.5
) -> np.ndarray:
    a, b = points[first], points[second]
    if _valid(a) and _valid(b):
        return a + fraction * (b - a)
    if _valid(a):
        return a.copy()
    if _valid(b):
        return b.copy()
    return np.full(2, np.nan)


def anthropometric_com(points: np.ndarray) -> np.ndarray:
    """Whole-body CoM from segment centers and standard approximate mass fractions."""

    shoulder = _mean_points(points[[LEFT_SHOULDER, RIGHT_SHOULDER]])
    hip = _mean_points(points[[LEFT_HIP, RIGHT_HIP]])
    head = _mean_points(points[:5])
    trunk = _mean_points(np.vstack([shoulder, hip]))
    segments: list[tuple[float, np.ndarray]] = [(0.081, head), (0.497, trunk)]
    for shoulder_i, elbow_i, wrist_i in (
        (LEFT_SHOULDER, LEFT_ELBOW, LEFT_WRIST),
        (RIGHT_SHOULDER, RIGHT_ELBOW, RIGHT_WRIST),
    ):
        segments.append((0.028, _segment_center(points, shoulder_i, elbow_i, 0.436)))
        segments.append((0.022, _segment_center(points, elbow_i, wrist_i, 0.682)))
    for hip_i, knee_i, ankle_i in (
        (LEFT_HIP, LEFT_KNEE, LEFT_ANKLE),
        (RIGHT_HIP, RIGHT_KNEE, RIGHT_ANKLE),
    ):
        segments.append((0.100, _segment_center(points, hip_i, knee_i, 0.433)))
        segments.append((0.061, _segment_center(points, knee_i, ankle_i, 0.433)))
    total = sum(weight for weight, center in segments if _valid(center))
    if total <= 0:
        return np.full(2, np.nan)
    return sum(weight * center for weight, center in segments if _valid(center)) / total


def stabilize_left_right(
    points: np.ndarray, confidence: np.ndarray, reference: np.ndarray | None
) -> tuple[np.ndarray, np.ndarray]:
    """Reject a global left/right label flip when temporal geometry strongly disagrees."""

    if reference is None:
        return points, confidence
    direct, swapped, count = 0.0, 0.0, 0
    for left, right in LEFT_RIGHT_PAIRS:
        if confidence[left] < 0.4 or confidence[right] < 0.4:
            continue
        if not (
            _valid(points[left])
            and _valid(points[right])
            and _valid(reference[left])
            and _valid(reference[right])
        ):
            continue
        direct += float(
            np.sum((points[left] - reference[left]) ** 2)
            + np.sum((points[right] - reference[right]) ** 2)
        )
        swapped += float(
            np.sum((points[left] - reference[right]) ** 2)
            + np.sum((points[right] - reference[left]) ** 2)
        )
        count += 1
    if count >= 2 and swapped < 0.72 * direct:
        points, confidence = points.copy(), confidence.copy()
        for left, right in LEFT_RIGHT_PAIRS:
            points[[left, right]] = points[[right, left]]
            confidence[[left, right]] = confidence[[right, left]]
    return points, confidence


def _angle(a: np.ndarray, vertex: np.ndarray, c: np.ndarray) -> float:
    if not (_valid(a) and _valid(vertex) and _valid(c)):
        return float("nan")
    first, second = a - vertex, c - vertex
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator < 1e-6:
        return float("nan")
    return float(
        np.degrees(np.arccos(np.clip(np.dot(first, second) / denominator, -1.0, 1.0)))
    )


def _nanmean(values: list[float]) -> float:
    array = np.asarray(values, dtype=float)
    return float(np.nanmean(array)) if np.isfinite(array).any() else float("nan")


def _local_derivative(
    values: np.ndarray, times: np.ndarray, window: int = 7
) -> np.ndarray:
    output = np.full_like(values, np.nan, dtype=float)
    radius = max(1, window // 2)
    for index in range(len(values)):
        lo, hi = max(0, index - radius), min(len(values), index + radius + 1)
        valid = np.isfinite(values[lo:hi]) & np.isfinite(times[lo:hi])
        if np.count_nonzero(valid) < 3:
            continue
        local_t = times[lo:hi][valid] - times[index]
        degree = min(2, len(local_t) - 1)
        output[index] = np.polyfit(local_t, values[lo:hi][valid], degree)[-2]
    return output


def _json_points(points: np.ndarray) -> list[list[float | None]]:
    return [
        [
            round(float(x), 3) if np.isfinite(x) else None,
            round(float(y), 3) if np.isfinite(y) else None,
        ]
        for x, y in points
    ]


def _finite_or_none(value: float) -> float | None:
    return round(float(value), 6) if np.isfinite(value) else None


class KinematicsAnalyzer:
    def __init__(self, calibration: CalibrationData, fps: float) -> None:
        self.calibration = calibration
        self.fps = float(fps)

    def analyze(self, tracks: list[FrameTrack]) -> AnalysisResult:
        if not tracks:
            raise ValueError("No frames were tracked.")
        times = np.array([track.timestamp for track in tracks], dtype=float)
        points = np.stack([track.keypoints for track in tracks])
        confidence = np.stack([track.confidence for track in tracks])
        com = np.stack([track.com for track in tracks])
        scale = self.calibration.pixels_per_meter

        x_m = (com[:, 0] - self.calibration.board_tip[0]) / scale
        height_m = (self.calibration.water_y - com[:, 1]) / scale
        board_relative_height_m = (self.calibration.board_tip[1] - com[:, 1]) / scale
        vx = _local_derivative(x_m, times)
        vy = _local_derivative(height_m, times)
        speed = np.hypot(vx, vy)

        shoulder = np.array(
            [_mean_points(frame[[LEFT_SHOULDER, RIGHT_SHOULDER]]) for frame in points]
        )
        hip = np.array([_mean_points(frame[[LEFT_HIP, RIGHT_HIP]]) for frame in points])
        body_angle = np.degrees(
            np.arctan2(hip[:, 0] - shoulder[:, 0], hip[:, 1] - shoulder[:, 1])
        )
        valid_angle = np.isfinite(body_angle)
        unwrapped = np.full(len(tracks), np.nan)
        if np.any(valid_angle):
            unwrapped[valid_angle] = np.degrees(
                np.unwrap(np.radians(body_angle[valid_angle]))
            )
        angular_velocity = _local_derivative(unwrapped, times)

        hip_angle = np.full(len(tracks), np.nan)
        knee_angle = np.full(len(tracks), np.nan)
        knee_to_chest = np.full(len(tracks), np.nan)
        for i, frame in enumerate(points):
            hip_angle[i] = _nanmean(
                [
                    _angle(frame[LEFT_SHOULDER], frame[LEFT_HIP], frame[LEFT_KNEE]),
                    _angle(frame[RIGHT_SHOULDER], frame[RIGHT_HIP], frame[RIGHT_KNEE]),
                ]
            )
            knee_angle[i] = _nanmean(
                [
                    _angle(frame[LEFT_HIP], frame[LEFT_KNEE], frame[LEFT_ANKLE]),
                    _angle(frame[RIGHT_HIP], frame[RIGHT_KNEE], frame[RIGHT_ANKLE]),
                ]
            )
            chest = shoulder[i]
            distances = [
                np.linalg.norm(frame[knee] - chest)
                for knee in (LEFT_KNEE, RIGHT_KNEE)
                if _valid(frame[knee]) and _valid(chest)
            ]
            knee_to_chest[i] = min(distances) / scale if distances else np.nan

        apex = int(np.nanargmax(height_m)) if np.isfinite(height_m).any() else 0
        entry = self._entry_frame(points, confidence, vy, apex)
        takeoff = self._takeoff_frame(points, vy, apex)
        if entry <= takeoff:
            entry = min(len(tracks) - 1, max(apex + 1, takeoff + 1))
        board_y = np.array([track.board_tip[1] for track in tracks], dtype=float)
        depression_search_end = min(takeoff + 1, len(tracks))
        max_depression = (
            int(np.nanargmax(board_y[:depression_search_end]))
            if np.isfinite(board_y[:depression_search_end]).any()
            else takeoff
        )

        phases = np.full(len(tracks), "pre_takeoff", dtype=object)
        phases[takeoff : apex + 1] = "ascent"
        phases[apex + 1 : entry] = "descent"
        phases[entry:] = "entry_or_exit"
        fit = self._ballistic_fit(times, board_relative_height_m, takeoff, entry)

        flight = slice(takeoff, entry + 1)
        rows: list[dict] = []
        for index, track in enumerate(tracks):
            rows.append(
                {
                    "frame": track.frame,
                    "timestamp": round(track.timestamp, 6),
                    "keypoints_x_y": _json_points(track.keypoints),
                    "keypoint_confidences": [
                        round(float(value), 4) for value in track.confidence
                    ],
                    "predicted_keypoints": [bool(value) for value in track.predicted],
                    "com_x": _finite_or_none(track.com[0]),
                    "com_y": _finite_or_none(track.com[1]),
                    "height_m": _finite_or_none(height_m[index]),
                    "vx_m_s": _finite_or_none(vx[index]),
                    "vy_m_s": _finite_or_none(vy[index]),
                    "velocity_m_s": _finite_or_none(speed[index]),
                    "body_angle_deg": _finite_or_none(unwrapped[index]),
                    "rotation_deg_s": _finite_or_none(angular_velocity[index]),
                    "hip_angle_deg": _finite_or_none(hip_angle[index]),
                    "knee_angle_deg": _finite_or_none(knee_angle[index]),
                    "knee_to_chest_m": _finite_or_none(knee_to_chest[index]),
                    "phase": str(phases[index]),
                    "detection_confidence": round(float(track.detection_confidence), 4),
                }
            )

        entry_angle = self._entry_angle(unwrapped, entry)
        flight_measured = (
            ~np.stack([track.predicted for track in tracks])[takeoff : entry + 1]
        ) & (confidence[takeoff : entry + 1] >= 0.4)
        tracking_coverage = float(np.mean(np.sum(flight_measured, axis=1) >= 8))
        summary = {
            "calibration": {
                **asdict(self.calibration),
                "pixels_per_meter": round(scale, 4),
            },
            "video": {"fps": self.fps, "frames": len(tracks)},
            "phases": {
                "maximum_board_depression_frame": max_depression,
                "takeoff_frame": takeoff,
                "apex_frame": apex,
                "entry_frame": entry,
            },
            "takeoff": {
                "board_depression_m": _finite_or_none(
                    max(
                        0.0,
                        (board_y[max_depression] - self.calibration.board_tip[1])
                        / scale,
                    )
                ),
                "hurdle_height_m_above_board": _finite_or_none(
                    np.nanmax(board_relative_height_m[: max_depression + 1])
                ),
                "vx_m_s": _finite_or_none(
                    np.nanmedian(vx[takeoff : min(takeoff + 4, len(tracks))])
                ),
                "vy_m_s": _finite_or_none(
                    np.nanmedian(vy[takeoff : min(takeoff + 4, len(tracks))])
                ),
            },
            "flight": {
                "peak_height_m_above_board": _finite_or_none(
                    np.nanmax(board_relative_height_m[flight])
                ),
                "minimum_hip_angle_deg": _finite_or_none(np.nanmin(hip_angle[flight])),
                "minimum_knee_to_chest_m": _finite_or_none(
                    np.nanmin(knee_to_chest[flight])
                ),
                "peak_rotation_deg_s": _finite_or_none(
                    np.nanmax(np.abs(angular_velocity[flight]))
                ),
                "ballistic_fit": fit,
            },
            "entry": {
                "angle_from_vertical_deg": _finite_or_none(entry_angle),
                "velocity_m_s": _finite_or_none(
                    np.nanmedian(speed[max(takeoff, entry - 2) : entry + 1])
                ),
                "vx_m_s": _finite_or_none(
                    np.nanmedian(vx[max(takeoff, entry - 2) : entry + 1])
                ),
                "vy_m_s": _finite_or_none(
                    np.nanmedian(vy[max(takeoff, entry - 2) : entry + 1])
                ),
            },
            "quality": {
                "flight_tracking_coverage": round(tracking_coverage, 4),
                "measured_keypoint_fraction": round(
                    float(np.mean(~np.stack([track.predicted for track in tracks]))), 4
                ),
            },
            "notes": [
                "Coordinates and 2-D kinematics are image-plane estimates from a static side view.",
                "Predicted keypoints bridge short occlusions and are explicitly flagged in the CSV.",
                "Board depression requires visible texture near the clicked board tip; inspect the rendered marker.",
            ],
        }
        return AnalysisResult(rows=rows, summary=summary)

    def _takeoff_frame(
        self, points: np.ndarray, vertical_velocity: np.ndarray, apex: int
    ) -> int:
        board = np.asarray(self.calibration.board_tip)
        scale = self.calibration.pixels_per_meter
        ankles = points[:, [LEFT_ANKLE, RIGHT_ANKLE], :]
        vertical_gap = np.abs(ankles[..., 1] - board[1])
        horizontal_gap = np.abs(ankles[..., 0] - board[0])
        contact = np.any(
            (vertical_gap < 0.12 * scale) & (horizontal_gap < 1.20 * scale), axis=1
        )
        candidates = np.flatnonzero(contact[: max(apex, 1)])
        if candidates.size:
            return min(int(candidates[-1] + 1), apex)
        upward = np.isfinite(vertical_velocity) & (vertical_velocity > 0.15)
        for index in range(max(0, apex)):
            if np.count_nonzero(upward[index : min(index + 3, apex + 1)]) >= 2:
                return index
        return max(0, apex - max(2, round(0.35 * self.fps)))

    def _entry_frame(
        self,
        points: np.ndarray,
        confidence: np.ndarray,
        vertical_velocity: np.ndarray,
        apex: int,
    ) -> int:
        for index in range(apex + 1, len(points)):
            leading = points[index, ENTRY_INDICES, 1]
            score = confidence[index, ENTRY_INDICES]
            crossed_surface = np.any(
                (score >= 0.2)
                & np.isfinite(leading)
                & (leading >= self.calibration.water_y)
            )
            descending = (
                not np.isfinite(vertical_velocity[index])
                or vertical_velocity[index] < 0
            )
            if crossed_surface and descending:
                return index
        return len(points) - 1

    @staticmethod
    def _entry_angle(unwrapped_angle: np.ndarray, entry: int) -> float:
        lo = max(0, entry - 2)
        angle = float(np.nanmedian(unwrapped_angle[lo : entry + 1]))
        if not np.isfinite(angle):
            return float("nan")
        wrapped = abs(((angle + 90.0) % 180.0) - 90.0)
        return float(wrapped)

    @staticmethod
    def _ballistic_fit(
        times: np.ndarray, heights: np.ndarray, takeoff: int, entry: int
    ) -> dict:
        local_t = times[takeoff : entry + 1] - times[takeoff]
        local_y = heights[takeoff : entry + 1]
        valid = np.isfinite(local_t) & np.isfinite(local_y)
        if np.count_nonzero(valid) < 6:
            return {"r_squared": None, "gravity_m_s2": None, "verified": False}
        coefficients = np.polyfit(local_t[valid], local_y[valid], 2)
        fitted = np.polyval(coefficients, local_t[valid])
        residual = float(np.sum((local_y[valid] - fitted) ** 2))
        total = float(np.sum((local_y[valid] - np.mean(local_y[valid])) ** 2))
        r_squared = 1.0 - residual / total if total > 1e-12 else float("nan")
        gravity = -2.0 * float(coefficients[0])
        return {
            "r_squared": _finite_or_none(r_squared),
            "gravity_m_s2": _finite_or_none(gravity),
            "verified": bool(
                np.isfinite(r_squared) and r_squared >= 0.85 and 5.0 <= gravity <= 15.0
            ),
            "model": "y(t)=y0+v0*t-0.5*g*t^2",
        }


def csv_ready_rows(rows: list[dict]) -> list[dict]:
    output = []
    for row in rows:
        item = row.copy()
        item["keypoints_x_y"] = json.dumps(item["keypoints_x_y"], separators=(",", ":"))
        item["keypoint_confidences"] = json.dumps(
            item["keypoint_confidences"], separators=(",", ":")
        )
        item["predicted_keypoints"] = json.dumps(
            item["predicted_keypoints"], separators=(",", ":")
        )
        output.append(item)
    return output
