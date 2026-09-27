# 1 m Diving Computer-Vision Analyzer

This is a standalone, offline Python application for a **static, side-view camera**. It uses a two-stage top-down architecture:

1. A YOLO person detector searches only the user-selected flight-path ROI.
2. YOLO11 pose runs on a motion-associated crop with 30% padding.
3. A constant-acceleration Kalman filter tracks each COCO keypoint independently. Measurements below 0.4 confidence are rejected; short gaps use predicted motion and are flagged as predictions.
4. Anthropometric segment weights estimate whole-body center of mass (CoM). The flight CoM is checked against a ballistic parabola.

The same tracker powers the web app's local asynchronous analysis jobs. Practice recordings use a faster inference profile; uploaded footage uses the higher-resolution profile.

## Installation

Python 3.10–3.14 is supported. From the repository root:

```bash
cd diving_cv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

`requirements.txt` is also provided for environments that do not use editable package installs.

The first run downloads the requested Ultralytics weights. The defaults are `yolo11n.pt` for person detection and `yolo11m-pose.pt` for pose. For maximum pose accuracy, substitute `--pose yolo11x-pose.pt`; it is slower and uses more memory.

## Run

```bash
diving-track /absolute/path/to/dive.mp4 --output-dir diving-output
```

Or without installing the console entry point:

```bash
python -m diving_tracker /absolute/path/to/dive.mp4 --output-dir diving-output
```

Calibration opens before inference:

1. Left-click **A**, the springboard tip.
2. Left-click **B**, the water level vertically aligned with the board. Right-click undoes a point.
3. Press Enter, then draw a loose ROI covering the complete flight path and press Enter again.

For the 1 m CLI workflow, the scale is `abs(B.y - A.y)` pixels per meter. Saved calibration also records the physical vertical reference, allowing the integrated app to divide by its selected board height. Keep the camera level, static, perpendicular to the dive plane, and far enough away to minimize perspective distortion. Use `--reference-frame N` if frame 0 does not show the board and water clearly.

The saved calibration can be reused without opening a GUI:

```bash
diving-track dive.mp4 \
  --calibration diving-output/calibration.json \
  --output-dir second-run \
  --device mps
```

Common inference devices are `--device cpu`, `--device mps` on Apple Silicon, or `--device 0` for the first CUDA GPU. Omit the option to let Ultralytics choose. `--show` previews the rendered output, and `--no-video` produces only CSV and JSON.

## Outputs

For `dive.mp4`, the output directory contains:

- `dive_tracked.mp4`: confidence-colored skeleton, predicted-joint dashes, CoM trail, board/water references, and live telemetry.
- `dive_kinematics.csv`: one row per source frame. Keypoints are JSON arrays in `keypoints_x_y`; confidence and prediction flags are parallel arrays.
- `dive_summary.json`: calibration, phase frames, takeoff, flight, entry, ballistic-fit, and tracking-quality metrics.
- `calibration.json`: reusable board, water, and ROI selections.

CSV vertical position and velocity use world convention: height and `vy_m_s` are positive upward. Pixel coordinates retain image convention (y positive downward). Rotation is the unwrapped shoulder-to-hip body-axis angle and is differentiated into degrees/second.

## Metric definitions

- **Maximum board depression:** greatest downward displacement of a Lucas–Kanade feature track initialized at the clicked board tip, up to takeoff.
- **Takeoff:** first frame after the final ankle/board contact; a sustained upward-velocity fallback is used when the feet are occluded.
- **Hurdle height:** maximum pre-depression CoM height above the board line.
- **Apex:** maximum calibrated CoM height.
- **Tuck/pike tightness:** minimum hip angle and minimum knee-to-chest distance during flight.
- **Entry:** first post-apex nose, wrist, or ankle surface crossing while the CoM is descending.
- **Entry angle:** absolute body-axis deviation from vertical; 0° is the target.
- **Ballistic verification:** a quadratic fit of flight CoM height. The summary reports fit R², inferred gravity, and a conservative verified flag.

## Failure handling and interpretation

- Detector misses use a CoM-driven predicted crop for up to 0.45 s.
- Joint occlusions use per-keypoint predictions for up to 0.35 s, then become missing instead of drifting indefinitely.
- A temporal assignment check suppresses whole-body left/right label flips during inversion.
- The renderer clips limbs at the water surface after entry and stops drawing lost splash-exit joints.
- The CSV distinguishes measured and predicted keypoints. Predicted points should not be treated as independent observations.

All metrics are 2-D image-plane estimates. Motion toward the camera, camera roll, zoom, lens distortion, an oblique view, an incorrectly marked water line, or a moving camera will bias distances and velocities. Board depression depends on visible texture near the selected tip and should be visually inspected in the rendered video. This tool is suitable for coaching and research review, not certified judging or clinical use.

## Tests

```bash
cd diving_cv
python -m unittest discover -s tests -v
```
