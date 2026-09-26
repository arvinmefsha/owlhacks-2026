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

```bash
cd api && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q
```
