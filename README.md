# Dive Form Analyzer

Record a dive with your camera, see a live skeleton overlay, and get form scores, coaching feedback (Gemini) and dryland workouts. Dives and per-frame pose data are stored in Tiger Data (TimescaleDB).

- `web/`: Next.js app. Runs MediaPipe pose tracking in the browser and forwards `/api/*` to the backend.
- `api/`: FastAPI backend. Analyses the pose frames, scores the dive, asks Gemini for feedback and stores everything in Tiger Data.

## Setup

1. Secrets: copy `.env.example` to `.env` in the repo root and fill in `GEMINI_API_KEY` and `DATABASE_URL` (a Tiger Cloud service connection string). `.env` is gitignored; never commit it.
2. Backend (Python 3.13):
   ```bash
   cd api
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/uvicorn main:app --reload
   ```
   On startup it creates the tables, hypertables, continuous aggregate and policies in Tiger Data.
3. Web app (Node 20+):
   ```bash
   cd web
   npm install   # also copies the MediaPipe runtime and downloads the pose models into public/
   npm run dev
   ```
4. Open http://localhost:3000.

The web app holds no secrets. The browser only talks to Next.js, which forwards `/api` to the backend on `127.0.0.1:8000` (change with `API_URL` in `web/.env.local`).

## Filming tips

Film side-on, level with the board, with the whole dive from takeoff to entry in view, ideally from a tripod. Set the diver's height and tap the board tip and water line before recording for accurate distances and entry timing.

## Tests

Uploaded-video tracking can be reviewed locally without the API: open **Record → Upload video**, choose a clip, optionally select the diver in its first frame, then choose **Track diver**. Review the result with the timeline, frame buttons, and playback speed controls before **Save dive & get feedback**. Saving still requires the configured API/database.

Uploads use the heavy MediaPipe model with a fresh VIDEO tracker per clip. [Mediabunny](https://mediabunny.dev/guide/media-sinks) decodes native source frames sequentially, including variable-rate and high-frame-rate clips, instead of seeking at an assumed 60 FPS. A two-candidate tracker avoids MediaPipe's single-pose temporal smoothing; spatial association selects the diver. Low-confidence frames get independent cropped/rotated IMAGE recovery passes. This does not change the live camera tracker.

Only gaps bounded by measurements within 80 ms are interpolated, without smoothing measured positions or extrapolating lost limbs. Estimated joint confidence stays below the backend's measured-joint threshold. If the water line is calibrated, lower-body contact starts an entry state: submerged joints are suppressed, limb lines stop at the surface, and a short 80 ms amber carryover avoids splash flicker while MediaPipe reacquires the upper body. Cyan lines show higher-confidence joints; dashed amber lines show uncertain/estimated joints. These are still model estimates, not ground truth. Occlusion, blur, small subjects, and other people can cause errors. Old saved dives keep their original tracking; rerun the original clip to use the new tracker.

Video decoding requires a browser with support for the clip's codec (Chrome/Edge and H.264 MP4 are useful fallbacks). Unsupported files show an actionable error. The 20,000-frame API limit is enforced without downsampling; trim longer clips to the dive. Recovery is slower than ordinary inference. All decoding/model resources are released on completion or cancellation.

Frontend checks:

```bash
cd web
npm run test:tracking
npx tsc --noEmit
npm run lint
```

```bash
cd api && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q
```
