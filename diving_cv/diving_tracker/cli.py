from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from .annotator import VideoAnnotator
from .backend import BackendConfig
from .calibration import CalibrationData, KinematicCalibrator
from .kinematics import csv_ready_rows
from .tracker import DivingTracker, TrackerConfig
from .dive_context import DiveContext


def parser() -> argparse.ArgumentParser:
    output = argparse.ArgumentParser(
        description="Track and analyze a 1 m springboard dive from a static side-view video."
    )
    output.add_argument("video", type=Path, help="Input video path")
    output.add_argument("--position", choices=["tuck", "pike", "straight"], help="Optional soft dive context")
    output.add_argument("--direction", choices=["forward", "back", "inward", "reverse"], help="Recorded context; image spin is inferred")
    output.add_argument("--somersaults", type=float, help="Recorded context; does not force rotation")
    output.add_argument(
        "--output-dir",
        type=Path,
        default=Path("diving-output"),
        help="Directory for video, CSV, JSON, and calibration",
    )
    output.add_argument(
        "--calibration",
        type=Path,
        help="Reuse a calibration JSON instead of opening the click UI",
    )
    output.add_argument(
        "--reference-frame",
        type=int,
        default=0,
        help="Frame shown by the calibration UI",
    )
    output.add_argument(
        "--detector",
        default="yolo11n.pt",
        help="Ultralytics person detector model or local weight path",
    )
    output.add_argument(
        "--pose",
        default="yolo11m-pose.pt",
        help="Ultralytics pose model or local weight path",
    )
    output.add_argument(
        "--device",
        help="Inference device, such as cpu, 0, or mps; omit to select available acceleration",
    )
    output.add_argument(
        "--detector-size", type=int, default=640, help="Detector inference image size"
    )
    output.add_argument(
        "--pose-size", type=int, default=768, help="Pose inference image size"
    )
    output.add_argument(
        "--padding",
        type=float,
        default=0.30,
        help="Crop padding per side; must be at least 0.20",
    )
    output.add_argument(
        "--show",
        action="store_true",
        help="Show the annotated render while writing it; Esc stops preview",
    )
    output.add_argument(
        "--no-video", action="store_true", help="Skip annotated video rendering"
    )
    return output


def main() -> None:
    args = parser().parse_args()
    if not args.video.is_file():
        raise SystemExit(f"Input video does not exist: {args.video}")
    if args.padding < 0.20:
        raise SystemExit("--padding must be at least 0.20 to avoid clipping limbs.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    calibration_path = args.output_dir / "calibration.json"
    if args.calibration:
        calibration = CalibrationData.load(args.calibration)
    else:
        print(
            "Calibration: click board tip, water level, then draw the flight-path ROI."
        )
        calibration = KinematicCalibrator().calibrate(args.video, args.reference_frame)
    calibration.save(calibration_path)
    print(f"Saved calibration: {calibration_path}")

    config = TrackerConfig(
        backend=BackendConfig(
            detector_model=args.detector,
            pose_model=args.pose,
            device=args.device,
            detector_size=args.detector_size,
            pose_size=args.pose_size,
            crop_padding=args.padding,
        )
    )
    tracker = DivingTracker(config)
    last_update = 0.0

    def progress(current: int, total: int) -> None:
        nonlocal last_update
        now = time.monotonic()
        if now - last_update >= 1.0 or (total and current == total):
            suffix = f"/{total}" if total else ""
            print(f"\rTracking frame {current}{suffix}", end="", flush=True)
            last_update = now

    try:
        tracks, analysis = tracker.process(args.video, calibration, progress,
            context=DiveContext(args.position, args.direction, args.somersaults))
        print()
        stem = args.video.stem
        csv_path = args.output_dir / f"{stem}_kinematics.csv"
        summary_path = args.output_dir / f"{stem}_summary.json"
        video_path = args.output_dir / f"{stem}_tracked.mp4"
        pd.DataFrame(csv_ready_rows(analysis.rows)).to_csv(csv_path, index=False)
        summary_path.write_text(
            json.dumps(analysis.summary, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        if not args.no_video:
            print("Rendering annotated video...")
            VideoAnnotator().render(
                args.video, video_path, tracks, analysis, calibration, args.show
            )
        print(f"CSV:     {csv_path}")
        print(f"Summary: {summary_path}")
        if not args.no_video:
            print(f"Video:   {video_path}")
        fit = analysis.summary["flight"]["ballistic_fit"]
        print(
            f"Ballistic check: R^2={fit['r_squared']}, inferred g={fit['gravity_m_s2']} m/s^2"
        )
    except KeyboardInterrupt:
        raise SystemExit("Cancelled.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
