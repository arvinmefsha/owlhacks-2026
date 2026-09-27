# Agent Handoff: Diving Pose Tracking on `derek3`

## Mission and current user request

The active issue is that YOLO pose tracking still loses the diver during tight tuck/pike rotations, and the arms are often missing while the diver opens out of the tuck on the way to the water. The next agent should improve recovery and continuity without sacrificing the currently good tracking behavior elsewhere.

The user wants:

- YOLO only. Do not add or restore MediaPipe.
- No Vultr/cloud processing for now; keep processing local.
- Preserve the two product flows: live feedback after a completed dive, and upload/analyze/review for prerecorded video.
- Keep review playback synchronized with the source video and avoid introducing skeleton lag.
- Improve tuck/pike continuity, spinning/occlusion handling, and arm reacquisition before water entry.
- Do not claim the problem is solved without visual validation on real tuck and pike clips.

## Repository and Git state

- Repository: `/Users/Derek/Documents/Codex/owlhacks-2026`
- Current branch: `derek3`
- Latest committed revision: `8d3b142 Improve diving pose synchronization and analysis performance`
- The working tree is intentionally dirty with the tuck/rotation work listed below. Do not discard it.
- No commit or push has been made for the current uncommitted rotation-recovery changes.

Current status at handoff:

```text
 M README.md
 M api/analysis_jobs.py
 M diving_cv/diving_tracker/__init__.py
 M diving_cv/diving_tracker/cli.py
 M diving_cv/diving_tracker/tracker.py
?? diving_cv/TUCK_VALIDATION.md
?? diving_cv/diving_tracker/dive_context.py
?? diving_cv/diving_tracker/rotation_recovery.py
?? diving_cv/tests/test_dive_context.py
?? diving_cv/tests/test_rotation_recovery.py
```

The earlier performance/synchronization work is already committed in `8d3b142`. Parent history includes the merge from `derek2` and the timestamp/playback fixes.

## Architecture to preserve

### Backend and tracking

- FastAPI entry point: `api/main.py`
- Async analysis job pipeline: `api/analysis_jobs.py`
- Main tracker: `diving_cv/diving_tracker/tracker.py`
- YOLO backend: `diving_cv/diving_tracker/backend.py`
- Timing/PyAV handling: `diving_cv/diving_tracker/timing.py`
- Offline refinement: `diving_cv/diving_tracker/refinement.py`
- API storage and job status: `api/`

The pose pipeline is top-down YOLO:

- Person detector: `yolo11n.pt`
- Pose model: `yolo11m-pose.pt`
- Detector default size: 640
- Pose default size: 768
- Retry size: 960
- Actual available device is selected automatically (CPU/MPS/CUDA).
- Detection is periodic; pose inference is attempted on every source frame.
- Timing is driven by source timestamps rather than assuming a fixed FPS.
- The refinement path is offline/non-causal so it should not create trailing playback lag.

There must be no MediaPipe dependency or code path. The user explicitly asked to wipe legacy MediaPipe.

### Review playback

- `web/components/UploadVideoPlayer.tsx`
- `web/lib/video-sync.ts`

The canvas is drawn from the video frame and the skeleton in the same `requestVideoFrameCallback` cycle, using `mediaTime`; a fallback path exists for browsers without that API. Scrubbing and frame stepping are already supported. Avoid changing tracking coordinates or timestamp semantics to fix a frontend sync issue unless a regression is demonstrated.

### Timestamp/database repair already completed

The timestamp work repaired legacy local records and did not alter old pose coordinates:

- `api/repair_timestamps.py` supports dry-run/apply.
- A local backup was created under `api/local-data/` and is ignored by Git.
- Do not run destructive database migrations or reset local data without explicit authorization.

## Current uncommitted implementation

### Dive context

`diving_cv/diving_tracker/dive_context.py` adds a soft dive-context model:

- Tracks body orientation, angular delta, compactness, and phase hypothesis.
- Uses optional position/direction/somersault metadata from the CLI.
- Provides transition costs and sequence selection helpers.
- It is intended to bias continuity, not hard-code a single dive type or force implausible landmarks.

### Rotation recovery

`diving_cv/diving_tracker/rotation_recovery.py` adds:

- `OrientationRecovery` state across frames.
- Carry-forward of a successful orientation/turn during airborne motion.
- Candidate orientation alternatives when the primary pose is weak.
- A bounded recovery budget (`max(3, frame_count // 3)`) to avoid unlimited extra inference.
- Recovery scoring with body coverage, arm confidence, continuity, compactness, and transition cost.

### Tracker integration

`diving_cv/diving_tracker/tracker.py` now:

- Builds candidates from the primary pose, carried rotation, and alternative rotations.
- Uses sequence selection to prefer temporally coherent candidates.
- Retains diagnostics for candidate/recovery decisions.
- Applies the existing smoothing/refinement after the selected raw sequence is assembled.

### API/CLI/docs/tests

- `api/analysis_jobs.py` identifies the pipeline as `pts-rotation-carry-v4` and stores candidate diagnostics in the tracking NPZ.
- `diving_cv/diving_tracker/__init__.py` exports `DiveContext`.
- `diving_cv/diving_tracker/cli.py` accepts `--position`, `--direction`, and `--somersaults`.
- `README.md` documents the rotation-carry pipeline.
- `diving_cv/TUCK_VALIDATION.md` records the validation procedure and results.
- New focused tests are in `diving_cv/tests/test_dive_context.py` and `diving_cv/tests/test_rotation_recovery.py`.

## Evidence so far

Last verified test command:

```bash
api/.venv/bin/python -m pytest -q api/tests diving_cv/tests
```

Result: **54 passed, 2 warnings**.

Also verify before committing:

```bash
git diff --check
```

Local real-video checks used these clips:

- Tuck clip: `api/uploads/e551ca14-45a8-4c1a-8021-039bcbc2d75d.mov` — 465 frames.
- Pike control: `api/uploads/61d54a6d-4157-4f28-9be7-4a6a40143d1f.mov` — 270 frames.

Temporary diagnostic outputs are outside the repository under `/Users/Derek/Documents/Codex/2026-09-26/pu/work/`. Relevant files include `rotationcarry-*.npz/json`, `carry-comparison.jpg`, `rotations.jpg`, and earlier baseline/optimized/context outputs. They are diagnostics only and should not be committed unless the user explicitly wants fixtures.

Observed measurements from the latest experiments:

- Earlier optimized 768 baseline on the 465-frame tuck clip: about 40.65 seconds CPU.
- Context-sequence version: about 42.65 seconds; manually sampled mean error about 40 px, median about 26.7 px, body coverage about 91.9%.
- Rotation-carry version: about 49.88 seconds CPU; manually sampled mean error about 16 px, median about 11.2 px, opening-arm coverage about 94.2%, overall sampled flight body coverage about 91.5%.
- Pike control with rotation carry: about 26.55 seconds; measured joint coverage about 92.44% versus about 91.45% in the prior comparison; timestamps remained unchanged.
- A four-orientation diagnostic around frame 330 showed that turn 3 produced the visually correct extended limbs while turn 0 did not. This is why orientation carry was added.

These are promising spot checks, not a full accuracy benchmark. The implementation is **not yet proven to solve** full tuck loss or all arm reacquisition cases.

## Remaining technical risks and next work

Work in this order:

1. Read this file, then inspect the full diff and run the test suite. Do not start by rewriting the tracker.
2. Re-run the tuck and pike clips with diagnostics enabled. Generate frame-by-frame contact sheets around initial tuck, deepest tuck, first opening frame, arm extension, and water entry.
3. Visually compare raw YOLO pose, carried orientation, selected candidate, and final refined pose. Check both arms and both legs separately. A high confidence score alone is not sufficient.
4. Audit candidate selection. The current recovery path scores all candidates and uses transition cost, but the next agent must confirm that the selected candidate is the one actually written into the final track before smoothing/refinement. Check duplicate primary/retry candidates and tie behavior.
5. Tune airborne detection and waterline phase boundaries. Ensure going below the waterline does not cause the remaining above-water limbs to be dropped, while underwater/occluded landmarks are allowed to be carried or marked missing instead of snapping to zero.
6. Add a targeted reacquisition path for arms coming out of tuck. Prefer temporal prediction plus a bounded alternate orientation/retry before accepting a low-confidence arm. Keep the other limb joints stable.
7. Add tests for missing arm in the deepest tuck, orientation change during spin, arm reappearance after tuck, no-landmark frame, waterline transition, and candidate continuity. Synthetic tests should cover logic; real clips must cover visual behavior.
8. Only after accuracy is acceptable, profile CPU time. The extra rotation candidates increased runtime. Reduce cost with bounded retries, frame/phase gating, cached crops, and avoiding duplicate inference. Do not lower pose quality or skip frames in a way that reintroduces lag.
9. Update `diving_cv/TUCK_VALIDATION.md`; its older lower section may still describe a source/8 recovery budget and should be reconciled with the current one-third budget.
10. Run the complete tests, `git diff --check`, and a real-video render. Report exact clips, timings, coverage, and known misses.

Potential design direction: maintain a short-lived multi-hypothesis track during airborne rotation. When the primary orientation is weak, propagate the last successful orientation using angular velocity, score a small set of nearby turns, and keep the best sequence with hysteresis. When an arm reappears, allow confidence to recover gradually rather than replacing the entire skeleton abruptly. Keep this bounded and phase-aware so ordinary dives do not pay a large runtime cost.

Do not use a causal trailing filter to hide the problem: playback must stay synchronized with the source frame. Do not use hard-coded per-frame coordinates or video-specific hacks.

## How to run locally

Backend:

```bash
cd /Users/Derek/Documents/Codex/owlhacks-2026/api
.venv/bin/uvicorn main:app --reload
```

Frontend:

```bash
cd /Users/Derek/Documents/Codex/owlhacks-2026/web
npm run dev
```

Review URL: `http://localhost:3000/record`

The currently running local server may be stale or unavailable; verify health and restart only the needed process. Do not assume browser verification succeeded just because the URL exists.

Useful checks:

```bash
curl -s http://localhost:8000/api/health
api/.venv/bin/python -m pytest -q api/tests diving_cv/tests
```

The browser review harness is `web/tests/browser-review.mjs` if the frontend dependencies are available.

## Delivery expectations

Before handing work back:

- Keep all edits on `derek3` unless explicitly told otherwise.
- Do not merge, push, or commit unless the user asks.
- Preserve the YOLO-only architecture and the live-feedback semantics.
- Include a concise summary of files changed, tests run, real-video results, remaining limitations, and runtime impact.
- If committing is later requested, commit the current tuck/rotation changes as a focused commit and push only after verifying the remote/branch.
