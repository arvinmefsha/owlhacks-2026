# Dive Form Analyzer

Record a dive or upload existing footage, then review YOLO-based pose tracking, dive scores, coaching feedback, and recommended dryland workouts. Dives and per-frame pose data are stored in Tiger Data (TimescaleDB).

- `web/`: Next.js app for camera recording, uploads, calibration, job progress, and review.
- `api/`: FastAPI backend with a local asynchronous YOLO analysis worker, scoring, Gemini coaching, and persistence.
- `diving_cv/`: Reusable YOLO11 top-down tracker, temporal filtering, calibrated kinematics, rendering, and CSV export.

## Processing flows

- **Practice camera:** the browser records the dive, then sends the completed clip to the local `fast` YOLO profile. Feedback appears after analysis; inference does not compete with camera capture.
- **Uploaded footage:** the browser sends the original clip to the local `quality` profile, which uses a larger inference size for more precise review.

Both profiles use a person detector followed by YOLO11 pose on a padded diver crop. Per-joint constant-acceleration filters carry short occlusions through tuck and pike positions, motion association keeps the crop on the diver, and temporal left/right correction reduces limb swaps during inversion. The review overlay clips submerged limb segments at the water surface while preserving visible joints above it.

## Setup

1. Copy `.env.example` to `.env` in the repository root and set `GEMINI_API_KEY` and `DATABASE_URL`. The real `.env` is ignored by Git.
2. Start the backend (Python 3.13):

   ```bash
   cd api
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/uvicorn main:app --reload
   ```

   Model weights download on first use unless `YOLO_DETECTOR_MODEL` and `YOLO_POSE_MODEL` point to local files. Set `YOLO_DEVICE=mps`, `0`, or `cpu` to force Apple Silicon, CUDA device 0, or CPU.

3. Start the web app (Node 20+):

   ```bash
   cd web
   npm install
   npm run dev
   ```

4. Open [http://localhost:3000/record](http://localhost:3000/record).

The browser only talks to Next.js. Next.js forwards `/api/*` to FastAPI on `127.0.0.1:8000`; override this with `API_URL` in `web/.env.local`.

## Use

Film side-on with a static camera and keep the board, complete flight, and water surface visible. Before recording or analyzing an upload, mark the springboard tip and the visible boundary between air and water.

The interface is configured for a 1 m springboard and uses one anonymous local history. It does not ask for an athlete name or height.

The API queues inference and reports its stage, frame count, and progress. A completed job opens the normal review page, including slow motion, frame stepping, timeline scrubbing, skeleton confidence, and dive metrics.

## Tracking behavior

- Detection is restricted to the flight path and associated with the predicted center of mass.
- The pose crop includes 30% padding so extended limbs are not cut off.
- Measurements below 0.4 confidence are rejected instead of snapping to `(0, 0)`.
- A constant-acceleration Kalman filter predicts individual joints for short gaps, while longer gaps are marked missing to prevent drift.
- Predicted points are visibly distinguished and stored below the measured-joint confidence threshold.
- Entry is inferred from the calibrated water surface. Limbs are shortened at that line so visible body parts remain drawn without inventing underwater locations.

Pose estimates are not ground truth. Blur, strong reflections, severe occlusion, small subjects, camera motion, and other people can still reduce accuracy.

## Tests

```bash
cd web
npx tsc --noEmit
npm run lint
npm run build
```

```bash
cd api
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

```bash
cd diving_cv
.venv/bin/python -m unittest discover -s tests -v
```

For the standalone calibrated CLI and rendered-video/CSV outputs, see [`diving_cv/README.md`](diving_cv/README.md).
