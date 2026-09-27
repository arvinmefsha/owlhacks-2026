from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np

from .calibration import CalibrationData
from .constants import SKELETON
from .models import AnalysisResult, FrameTrack


def _point(value: np.ndarray) -> tuple[int, int]:
    return round(float(value[0])), round(float(value[1]))


def _draw_dashed(
    frame: np.ndarray,
    start: tuple[int, int],
    end: tuple[int, int],
    color: tuple[int, int, int],
    width: int = 2,
) -> None:
    vector = np.asarray(end, dtype=float) - np.asarray(start, dtype=float)
    length = float(np.linalg.norm(vector))
    if length < 1:
        return
    direction = vector / length
    for distance in np.arange(0, length, 12):
        a = np.asarray(start) + direction * distance
        b = np.asarray(start) + direction * min(distance + 7, length)
        cv2.line(frame, _point(a), _point(b), color, width, cv2.LINE_AA)


def _clip_to_water(
    a: np.ndarray, b: np.ndarray, water_y: float
) -> tuple[np.ndarray, np.ndarray] | None:
    above_a, above_b = a[1] <= water_y, b[1] <= water_y
    if not above_a and not above_b:
        return None
    if above_a and above_b:
        return a, b
    upper, lower = (a, b) if above_a else (b, a)
    denominator = lower[1] - upper[1]
    if abs(denominator) < 1e-6:
        return None
    intersection = upper + (water_y - upper[1]) / denominator * (lower - upper)
    return (upper, intersection) if above_a else (intersection, upper)


class VideoAnnotator:
    def render(
        self,
        input_path: str | Path,
        output_path: str | Path,
        tracks: list[FrameTrack],
        analysis: AnalysisResult,
        calibration: CalibrationData,
        show: bool = False,
    ) -> None:
        capture = cv2.VideoCapture(str(input_path))
        if not capture.isOpened():
            raise FileNotFoundError(
                f"Could not reopen video for rendering: {input_path}"
            )
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(
            str(target), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
        )
        if not writer.isOpened():
            capture.release()
            raise RuntimeError(f"Could not create output video: {target}")

        entry_frame = int(analysis.summary["phases"]["entry_frame"])
        trajectory: list[tuple[int, int]] = []
        index = 0
        try:
            while index < len(tracks):
                ok, frame = capture.read()
                if not ok:
                    break
                track, row = tracks[index], analysis.rows[index]
                board_y = round(calibration.board_tip[1])
                water_y = round(calibration.water_y)
                cv2.line(frame, (0, board_y), (width - 1, board_y), (0, 165, 255), 2)
                cv2.line(frame, (0, water_y), (width - 1, water_y), (255, 190, 0), 2)
                cv2.putText(
                    frame,
                    "BOARD HEIGHT",
                    (12, max(22, int(calibration.board_tip[1]) - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.52,
                    (0, 165, 255),
                    2,
                    cv2.LINE_AA,
                )
                cv2.putText(
                    frame,
                    "WATER",
                    (12, max(22, int(calibration.water_y) - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.52,
                    (255, 190, 0),
                    2,
                    cv2.LINE_AA,
                )
                cv2.circle(
                    frame, _point(track.board_tip), 5, (0, 230, 255), -1, cv2.LINE_AA
                )

                if np.isfinite(track.com).all() and (
                    index <= entry_frame or track.com[1] <= calibration.water_y
                ):
                    trajectory.append(_point(track.com))
                recent_trajectory = trajectory[-90:]
                for start, end in pairwise(recent_trajectory):
                    cv2.line(frame, start, end, (255, 90, 255), 2, cv2.LINE_AA)
                if trajectory:
                    cv2.circle(
                        frame, trajectory[-1], 6, (255, 90, 255), -1, cv2.LINE_AA
                    )

                for first, second in SKELETON:
                    a, b = track.keypoints[first], track.keypoints[second]
                    if not (np.isfinite(a).all() and np.isfinite(b).all()):
                        continue
                    segment = (
                        _clip_to_water(a, b, calibration.water_y)
                        if index >= entry_frame
                        else (a, b)
                    )
                    if segment is None:
                        continue
                    score = min(track.confidence[first], track.confidence[second])
                    estimated = bool(track.predicted[first] or track.predicted[second])
                    color = (
                        (0, 210, 70)
                        if score >= 0.7 and not estimated
                        else (0, 190, 255)
                        if score >= 0.4
                        else (90, 90, 255)
                    )
                    if estimated:
                        _draw_dashed(
                            frame, _point(segment[0]), _point(segment[1]), color
                        )
                    else:
                        cv2.line(
                            frame,
                            _point(segment[0]),
                            _point(segment[1]),
                            color,
                            3,
                            cv2.LINE_AA,
                        )
                for point, score, predicted in zip(
                    track.keypoints, track.confidence, track.predicted
                ):
                    if not np.isfinite(point).all() or (
                        index >= entry_frame and point[1] > calibration.water_y
                    ):
                        continue
                    color = (
                        (0, 220, 70)
                        if score >= 0.7 and not predicted
                        else (0, 190, 255)
                        if score >= 0.4
                        else (90, 90, 255)
                    )
                    cv2.circle(frame, _point(point), 4, color, -1, cv2.LINE_AA)

                self._dashboard(frame, row)
                writer.write(frame)
                if show:
                    cv2.imshow("Diving analysis", frame)
                    if cv2.waitKey(1) & 0xFF == 27:
                        break
                index += 1
        finally:
            capture.release()
            writer.release()
            if show:
                cv2.destroyAllWindows()

    @staticmethod
    def _dashboard(frame: np.ndarray, row: dict) -> None:
        overlay = frame.copy()
        cv2.rectangle(
            overlay,
            (max(0, frame.shape[1] - 320), 12),
            (frame.shape[1] - 12, 157),
            (12, 18, 28),
            -1,
        )
        cv2.addWeighted(overlay, 0.76, frame, 0.24, 0, frame)
        x = max(12, frame.shape[1] - 304)

        def value(key: str, suffix: str) -> str:
            current = row.get(key)
            return "--" if current is None else f"{current:.2f}{suffix}"

        lines = (
            (f"PHASE  {row['phase'].replace('_', ' ').upper()}", (255, 255, 255)),
            (f"HEIGHT {value('height_m', ' m')}", (120, 230, 255)),
            (f"SPEED  {value('velocity_m_s', ' m/s')}", (130, 255, 160)),
            (f"ROT    {value('rotation_deg_s', ' deg/s')}", (255, 170, 255)),
            (f"HIP    {value('hip_angle_deg', ' deg')}", (255, 220, 140)),
        )
        for line_index, (text, color) in enumerate(lines):
            cv2.putText(
                frame,
                text,
                (x, 38 + line_index * 26),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                color,
                1,
                cv2.LINE_AA,
            )
