# Dive Form Analyzer handoff

## Current state

- Repository: arvinmefsha/owlhacks-2026
- Current branch: main
- Current commit: ed0a657 (Merge derek5.1 into main)
- main, derek5.1, and origin/main currently point to the same commit.
- Working tree is clean.
- The latest experimental height graph work was reverted. The committed review page still shows the existing analysis graphs.
- The local app is intended to run at http://127.0.0.1:3000 with FastAPI on http://127.0.0.1:8000.

## Product flows

There are two separate user flows:

1. Record (/record): live camera session. The diver completes a dive, the app detects the dive, analyzes it, and gives feedback after the dive.
2. Upload (/upload): prerecorded side-view video. The user marks the board tip and waterline, presses Analyze with YOLO, waits for the asynchronous job, and reviews the skeleton, measurements, feedback, workouts, and video playback.

/progress shows growth over time and has the delete-all-dives/data control.

The top navigation is folder-like colored tabs on the left:

- Record: blue
- Upload: green
- Progress: purple

The active tab is lighter/highlighted. Record and Upload are intentionally separate routes and do not share a mode toggle. The upload flow has an accessible Upload another dive button, and completed dive pages have the same action.

## Architecture

- api/: FastAPI backend.
- web/: Next.js 16, React 19 frontend.
- diving_cv/: YOLO11 top-down detector/pose tracker, temporal filtering, calibrated kinematics, rendering, and CSV export.
- Uploaded analysis is asynchronous through POST /analysis/jobs; the frontend polls /analysis/jobs/{id} until completion.
- Browser requests use /api/*; Next rewrites them to API_URL, normally http://127.0.0.1:8000.
- All pose tracking is YOLO based. MediaPipe was intentionally removed from the product and should not be reintroduced.
- Offline/upload analysis uses the quality YOLO profile. Live uses a smaller, faster YOLO pose model.
- Current analysis method for new uploads is macro-observations-v2, designed to favor supported observations and avoid false numeric claims.
- Gemini supplies optional coaching/visual feedback. Rule-based feedback remains the fallback so a dive can still receive feedback if Gemini fails.
- ElevenLabs supplies optional live spoken feedback, with browser speech as fallback.

## Tracking and feedback decisions

The project evolved from unreliable joint-angle scoring to evidence-based macro observations. Important requirements:

- Do not use numerical scoring as the main product experience. Progress and coaching observations are preferred.
- Do not give a perfect 10; older scoring code may still exist for legacy data.
- A dive with partial tracking should receive supported feedback instead of being rejected too aggressively.
- Missing or submerged landmarks must be treated as unavailable. Never turn missing points into false technical faults.
- The waterline is a hard visual boundary: visible skeleton segments are clipped at the waterline, and submerged parts may be unavailable.
- Entry deviation is measured from the first water contact and should favor a robust vertical interpretation. Avoid reintroducing the old 100°/175° false-entry-angle behavior.
- Tight tuck/pike dives and fast arm/leg motion are known difficult cases. Preserve existing tracking behavior when changing review or feedback UI.

## Video review

web/components/UploadVideoPlayer.tsx handles review playback.

- Scrubbing and frame stepping use tracked frame times and decoded presentation timing.
- Previous/next frame buttons are gray translucent circles over the sides of the video.
- Play/pause is overlaid on the video.
- Fullscreen is a single expand icon in the top-left corner. It preserves the current skeleton visibility state.
- The separate fullscreen skeleton/video buttons were removed.
- The normal skeleton visibility control remains below the video.
- Video exports are MP4-only in web/lib/export-video.ts.

## Environment

- The backend reads the ignored repository-root .env.
- The configured services are Gemini, ElevenLabs, and TigerData PostgreSQL through DATABASE_URL.
- Never copy secrets into this handoff, commits, logs, screenshots, or chat output. Existing credentials are in the local ignored .env.
- The TigerData URL must include the password. The host resolves and a direct SELECT 1 connection was verified during the last setup.
- web/.env.local should contain API_URL=http://127.0.0.1:8000.

## Running locally

From the repository root:

    cd api
    .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000

In another terminal:

    cd web
    PATH=/Users/Derek/Documents/Codex/.tools/node-v26.10.0-darwin-arm64/bin:$PATH npm run build
    PATH=/Users/Derek/Documents/Codex/.tools/node-v26.10.0-darwin-arm64/bin:$PATH npm run start -- --hostname 127.0.0.1 --port 3000

Use the production server for stable local testing. Next dev previously produced repeated EMFILE: too many open files, watch errors on this machine. The production server avoids that watcher problem.

Health checks:

    curl http://127.0.0.1:8000/health
    curl -I http://127.0.0.1:3000/record
    curl -I http://127.0.0.1:3000/upload

The API needs write access to api/uploads for video analysis. A failed Analyze request previously returned a generic Request failed because the backend received a 500 while trying to write the uploaded .mov file there.

## Verification

Frontend checks:

    cd web
    PATH=/Users/Derek/Documents/Codex/.tools/node-v26.10.0-darwin-arm64/bin:$PATH npm test
    PATH=/Users/Derek/Documents/Codex/.tools/node-v26.10.0-darwin-arm64/bin:$PATH npm exec tsc -- --noEmit
    PATH=/Users/Derek/Documents/Codex/.tools/node-v26.10.0-darwin-arm64/bin:$PATH npm run build

At the last completed verification, the frontend tests had 17 passing tests and the production build completed successfully. The experimental graph branch briefly added five tests, but those files were removed when the graph work was reverted.

Backend tests are under api/tests. Use the project virtual environment and the repository's existing pytest setup when changing backend behavior.

## Branch history and major work

- arvin: original working branch from the upstream repository.
- derek2: created from arvin; improved uploaded-video tracking and continuity while preserving live feedback.
- derek3: tracking latency, tuck/pike continuity, arm recovery, and water-entry behavior were investigated and improved.
- derek4.0: scoring/feedback work moved toward evidence-based coaching and better entry handling; merged into main.
- derek5.1: MP4 export and the current navigation/review UI changes; merged into main.

Important commits:

- 615e8c9: export annotated dive videos as MP4.
- 03f6c86: separate Record and Upload navigation flows.
- ed0a657: merge derek5.1 into main.

## Known issues and next work

- The requested feet-height and hip/center-of-mass graphs were designed in an uncommitted experiment and then reverted at the user's request. If revisiting them, first inspect the current committed UI and add the new work on a fresh branch.
- The graph design should use available YOLO ankle and hip landmarks, show height above water versus clip time, mark takeoff/apex/entry, clearly label uncertainty gaps, and fit a parabola only when the flight data is sufficiently complete. It must not invent values for submerged or low-confidence points.
- Gemini visual review has a generic error message. Direct Gemini text and image requests were verified with the configured model. If this fails again, inspect the API process logs and confirm the running process loaded the current .env.
- If uploads fail with a generic request error, inspect backend logs first. Confirm api/uploads is writable and that the API is reachable through the Next proxy.
- Do not modify the live flow while making uploaded-video changes unless the user explicitly asks for both.

## Agent operating notes

- Preserve user changes in a dirty worktree.
- Use apply_patch for source edits.
- Do not print or commit .env values.
- Verify the branch and status before committing.
- The user commonly asks for commit/push/merge after implementation. Do not commit automatically unless requested.
