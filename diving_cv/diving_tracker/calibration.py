from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2


@dataclass(frozen=True)
class CalibrationData:
    board_tip: tuple[float, float]
    water_point: tuple[float, float]
    roi: tuple[int, int, int, int]
    reference_frame: int = 0
    vertical_reference_m: float = 1.0

    @property
    def water_y(self) -> float:
        return float(self.water_point[1])

    @property
    def pixels_per_meter(self) -> float:
        pixel_distance = abs(float(self.water_point[1]) - float(self.board_tip[1]))
        if pixel_distance < 10:
            raise ValueError(
                "Board tip and water line must be at least 10 pixels apart."
            )
        if self.vertical_reference_m <= 0:
            raise ValueError("The physical board-to-water distance must be positive.")
        return pixel_distance / self.vertical_reference_m

    def validate(self, width: int, height: int) -> None:
        for name, point in (
            ("board_tip", self.board_tip),
            ("water_point", self.water_point),
        ):
            if not (0 <= point[0] < width and 0 <= point[1] < height):
                raise ValueError(f"{name} is outside the video frame: {point}")
        x, y, w, h = self.roi
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
            raise ValueError(f"ROI is invalid for {width}x{height}: {self.roi}")
        _ = self.pixels_per_meter

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> CalibrationData:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            board_tip=tuple(map(float, data["board_tip"])),
            water_point=tuple(map(float, data["water_point"])),
            roi=tuple(map(int, data["roi"])),
            reference_frame=int(data.get("reference_frame", 0)),
            vertical_reference_m=float(data.get("vertical_reference_m", 1.0)),
        )


class KinematicCalibrator:
    """Collect board, water, and flight-path ROI calibration from an OpenCV window."""

    WINDOW = "Diving calibration"

    def calibrate(
        self, video_path: str | Path, reference_frame: int = 0
    ) -> CalibrationData:
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise FileNotFoundError(f"Could not open video: {video_path}")
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if reference_frame < 0 or (count and reference_frame >= count):
            capture.release()
            raise ValueError(f"Reference frame {reference_frame} is outside the video.")
        capture.set(cv2.CAP_PROP_POS_FRAMES, reference_frame)
        ok, frame = capture.read()
        capture.release()
        if not ok:
            raise RuntimeError(f"Could not read reference frame {reference_frame}.")

        points: list[tuple[int, int]] = []

        def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
            if event == cv2.EVENT_LBUTTONDOWN and len(points) < 2:
                points.append((x, y))
            elif event == cv2.EVENT_RBUTTONDOWN and points:
                points.pop()

        cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.WINDOW, on_mouse)
        while True:
            display = frame.copy()
            title = (
                "Click A: board tip"
                if not points
                else "Click B: water level"
                if len(points) == 1
                else "Enter: continue"
            )
            cv2.rectangle(display, (8, 8), (440, 46), (15, 15, 15), -1)
            cv2.putText(
                display,
                title + " | right-click: undo | Esc: cancel",
                (18, 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            if points:
                cv2.circle(display, points[0], 7, (0, 165, 255), -1)
                cv2.putText(
                    display,
                    "A board",
                    (points[0][0] + 10, points[0][1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 165, 255),
                    2,
                )
            if len(points) == 2:
                cv2.circle(display, points[1], 7, (255, 180, 0), -1)
                cv2.line(
                    display,
                    (0, points[1][1]),
                    (display.shape[1] - 1, points[1][1]),
                    (255, 180, 0),
                    1,
                )
                cv2.putText(
                    display,
                    "B water",
                    (points[1][0] + 10, points[1][1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 180, 0),
                    2,
                )
            cv2.imshow(self.WINDOW, display)
            key = cv2.waitKey(20) & 0xFF
            if key in (10, 13) and len(points) == 2:
                break
            if key == 27:
                cv2.destroyWindow(self.WINDOW)
                raise KeyboardInterrupt("Calibration cancelled.")

        cv2.destroyWindow(self.WINDOW)
        roi = cv2.selectROI(
            "Select loose flight-path ROI, then press Enter",
            frame,
            showCrosshair=True,
            fromCenter=False,
        )
        cv2.destroyAllWindows()
        if roi[2] <= 0 or roi[3] <= 0:
            raise ValueError("A non-empty flight-path ROI is required.")
        result = CalibrationData(
            board_tip=(float(points[0][0]), float(points[0][1])),
            water_point=(float(points[1][0]), float(points[1][1])),
            roi=tuple(map(int, roi)),
            reference_frame=reference_frame,
        )
        result.validate(frame.shape[1], frame.shape[0])
        return result
