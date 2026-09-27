# Dive Form Analyzer

Record a dive or upload existing footage, then review YOLO-based pose tracking, dive scores, coaching feedback, and recommended dryland workouts. Dives and per-frame pose data are stored in Tiger Data (TimescaleDB).

- `web/`: Next.js app for camera recording, uploads, calibration, job progress, and review.
- `api/`: FastAPI backend with a local asynchronous YOLO analysis worker, scoring, Gemini coaching, and persistence.
- `diving_cv/`: Reusable YOLO11 top-down tracker, temporal filtering, calibrated kinematics, rendering, and CSV export.

## Processing flows

- **Practice camera:** the browser records the dive, then sends the completed clip to the local `fast` YOLO profile. Feedback appears after analysis; inference does not compete with camera capture.
- **Uploaded footage:** the browser sends the original clip to the local `quality` profile, which uses a larger inference size for more precise review.

Both profiles use a person detector followed by YOLO11 pose on a padded diver crop. Detection runs periodically, with immediate recovery when the crop fails; pose inference still covers each decodable source frame while a diver crop is available. Offline centered refinement preserves strong observations and fills short, bracketed occlusions. The review clips submerged limb segments at the water surface.

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
- Strong observations (confidence ≥0.6) retain the model's exact positions; uncertain observations receive a small centered correction instead of a trailing causal filter.
- Gaps are interpolated only between valid observations at most 85 ms apart, above the water. There is no open-ended joint extrapolation.
- Estimated points are visibly distinguished and stored below the measured-joint confidence threshold.
- The detector runs at 640 pixels; quality pose inference uses 768, fast uses 640. Difficult crops may receive a bounded 960-pixel rotated/expanded retry.
- Source presentation timestamps and frame order are validated with PyAV. Missing or ambiguous timestamps produce an actionable error instead of a guessed VFR timeline.
- Playback draws video pixels and the matching pose together in one canvas, using decoded `mediaTime`. Scrubbing uses the frame's presentation interval, never a future nearest neighbor.
- Per-stage processing times and the actual device are saved in `analysis.processing`. Raw and refined poses are retained locally in `api/uploads/<dive-id>.tracking.npz` for comparison.
- Entry is inferred from the calibrated water surface. Limbs are shortened at that line so visible body parts remain drawn without inventing underwater locations.

Pose estimates are not ground truth. Blur, strong reflections, severe occlusion, small subjects, camera motion, and other people can still reduce accuracy.

### Dive-aware recovery

Analysis jobs pass the selected dive position, direction, and somersault count into the tracker. Tuck context increases recovery attention during compact or inverted poses. The image-space spin is inferred from observed torso motion, not the named dive direction. The expected somersault count never forces an orientation, phase duration, or final pose.

High-confidence but inconsistent motion can trigger rotated/expanded crop recovery. Once airborne, recovery frames are spaced at least three frames apart and compare the three remaining quarter-turn orientations. A successful orientation is reused on following frames instead of reverting to an upright input. Extra inference is capped at one third of the source frame count; it is not spent on ordinary board preparation. Missing elbows/wrists trigger recovery, and pose scoring includes distal-arm confidence so reliable legs cannot conceal missing arms. Candidates are compared across the completed sequence using confidence, extreme-geometry checks, and a small capped continuity cost. Selected poses are actual model observations, not synthesized ideal dives. The existing short-gap and waterline rules still apply.

The local `.tracking.npz` includes `candidate_diagnostics` (JSON text containing candidates, selected indices, and observed phase hypotheses). Processing metadata identifies `pts-rotation-carry-v4`. Reanalyze the original clip to use this pipeline; existing saved poses are not silently rewritten. The standalone tracker accepts optional `DiveContext` and remains usable without a declared dive.

## Existing reviews and verification

Install the updated backend dependencies and restart the API before testing. Existing Python workers retain previously imported code. `YOLO_DEVICE` can override automatic CUDA/MPS/CPU selection; availability is checked at runtime.

For the local SQLite database, repair verified legacy `frame_index / average_fps` timestamps without running YOLO again:

```bash
api/.venv/bin/python api/repair_timestamps.py          # dry run
api/.venv/bin/python api/repair_timestamps.py --apply  # backup, then atomic update
```

The repair checks the schema, frame count/order, and known timestamp sequence. It refuses ambiguous mappings, records the video SHA-256, recomputes time-dependent metrics and rule-based feedback, and is idempotent. A full SQLite backup is placed beside the database before writes. PostgreSQL is not modified by this local repair command. Legacy filtered coordinates cannot be undone from their timestamps: upload the original clip again to get the new limb refinement. Original reviews remain available.

Browser regression checks can be run on a saved dive with the app running:

```bash
cd web
node tests/browser-review.mjs <dive-uuid>
# Open http://localhost:3000/_sync-check.html and read the results.
```

The harness exercises the real review page, playback speeds, scrubbing, frame steps, and decoded-frame skeleton coordinates. The generated `public/_sync-check.html` is a temporary local test artifact and should not be deployed.

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
