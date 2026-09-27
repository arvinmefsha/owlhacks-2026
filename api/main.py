"""Dive Form Analyzer API.

The Next.js app proxies /api/* to this server, so browsers never call it directly and the
Gemini and database credentials stay on this machine. Run with: uvicorn main:app --reload
"""

import logging
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from analysis_jobs import AnalysisJobManager
from config import Settings, get_settings
from db.database import Database
from db.local import LocalDatabase
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from feedback.catalog import load_catalog
from feedback.gemini import GeminiCoach
from models import AnalysisJobPayload, Readiness
from pydantic import ValidationError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

VIDEO_TYPES = {"video/webm": ".webm", "video/mp4": ".mp4", "video/quicktime": ".mov"}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_KEYFRAME_BYTES = 4 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.database_url is None:
        db = LocalDatabase(settings.local_db_path)
        log.info("DATABASE_URL is not set; using local SQLite at %s", settings.local_db_path)
    else:
        db = Database(settings.database_url.get_secret_value())
    db.init_schema(load_catalog())
    db.open()
    app.state.settings = settings
    app.state.db = db
    gemini_key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
    app.state.coach = GeminiCoach(gemini_key, settings.gemini_model)
    app.state.analysis_jobs = AnalysisJobManager(db, app.state.coach, settings)
    log.info("Ready. Gemini model: %s", settings.gemini_model)
    try:
        yield
    finally:
        app.state.analysis_jobs.shutdown()
        db.close()


app = FastAPI(title="Dive Form Analyzer API", lifespan=lifespan)


def _db(request: Request) -> Any:
    return request.app.state.db


def _coach(request: Request) -> GeminiCoach:
    return request.app.state.coach


def _settings(request: Request) -> Settings:
    return request.app.state.settings


DB = Annotated[Any, Depends(_db)]
Coach = Annotated[GeminiCoach, Depends(_coach)]
AppSettings = Annotated[Settings, Depends(_settings)]


def _mime(upload: UploadFile) -> str:
    return (upload.content_type or "").split(";")[0].strip().lower()


def _video_file(settings: Settings, name: str | None) -> Path | None:
    if not name:
        return None
    path = (settings.upload_dir / name).resolve()
    return path if path.parent == settings.upload_dir.resolve() and path.is_file() else None


@app.get("/health")
def health(db: DB):
    return {"ok": db.ping()}


def _analysis_jobs(request: Request) -> AnalysisJobManager:
    manager = getattr(request.app.state, "analysis_jobs", None)
    if manager is None:
        manager = AnalysisJobManager(request.app.state.db, request.app.state.coach, request.app.state.settings)
        request.app.state.analysis_jobs = manager
    return manager


@app.post("/analysis/jobs", status_code=202)
def create_analysis_job(
    request: Request,
    payload: Annotated[UploadFile, File(description="JSON analysis-job configuration")],
    video: Annotated[UploadFile, File(description="Recorded or uploaded dive video")],
    db: DB,
    settings: AppSettings,
):
    try:
        body = AnalysisJobPayload.model_validate_json(payload.file.read())
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False, include_input=False)) from None

    mime = _mime(video)
    if mime not in VIDEO_TYPES:
        raise HTTPException(415, "Unsupported video type. Use WebM, MP4 or MOV.")
    if video.size is not None and video.size > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"The video is larger than {settings.max_upload_mb} MB.")
    if body.calibration.board_tip is None or body.calibration.water_y is None:
        raise HTTPException(422, "Mark the board tip and visible air-water surface before analysis.")
    diver = db.get_or_create_default_diver()
    job_file = settings.upload_dir / f"job-{uuid4()}{VIDEO_TYPES[mime]}"
    with job_file.open("wb") as out:
        shutil.copyfileobj(video.file, out)
    manager = _analysis_jobs(request)
    job_payload = body.model_dump(mode="json")
    job_payload["diver_id"] = str(diver["id"])
    job_id = manager.submit(job_payload, job_file, mime)
    return {"id": str(job_id), "status": "queued"}


@app.get("/analysis/jobs/{job_id}")
def get_analysis_job(job_id: UUID, request: Request):
    job = _analysis_jobs(request).get(job_id)
    if job is None:
        raise HTTPException(404, "Analysis job not found.")
    return job


@app.delete("/analysis/jobs/{job_id}", status_code=204)
def cancel_analysis_job(job_id: UUID, request: Request):
    if not _analysis_jobs(request).cancel(job_id):
        raise HTTPException(409, "The job is already running or finished.")


@app.get("/dives")
def list_dives(db: DB, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    return db.list_dives(db.get_or_create_default_diver()["id"], limit)


@app.get("/dives/{dive_id}")
def get_dive(dive_id: UUID, db: DB, settings: AppSettings):
    dive = db.get_dive(dive_id)
    if dive is None:
        raise HTTPException(404, "Dive not found.")
    dive["has_video"] = _video_file(settings, dive.pop("video_path")) is not None
    frames = dive.pop("frames")
    # Landmarks are flattened: 17 COCO points x (x, y, z, confidence) per frame, or null.
    dive["frames"] = {
        "t": [f["t"] for f in frames],
        "lm": [
            f["landmarks"]
            if f["landmarks"] is None or len(f["landmarks"]) == 68
            else None
            for f in frames
        ],
    }
    return dive


@app.get("/dives/{dive_id}/video")
def get_video(dive_id: UUID, db: DB, settings: AppSettings):
    dive = db.get_dive_meta(dive_id)
    path = _video_file(settings, dive["video_path"]) if dive else None
    if path is None:
        raise HTTPException(404, "No video for this dive.")
    return FileResponse(path, media_type=dive["video_mime"])


@app.delete("/dives/{dive_id}", status_code=204)
def delete_dive(dive_id: UUID, db: DB, settings: AppSettings):
    row = db.delete_dive(dive_id)
    if row is None:
        raise HTTPException(404, "Dive not found.")
    path = _video_file(settings, row["video_path"])
    if path:
        path.unlink(missing_ok=True)
        path.with_suffix(".tracking.npz").unlink(missing_ok=True)


@app.post("/dives/{dive_id}/vision-review")
def vision_review(
    dive_id: UUID,
    db: DB,
    coach: Coach,
    takeoff: Annotated[UploadFile, File()],
    apex: Annotated[UploadFile, File()],
    entry: Annotated[UploadFile, File()],
):
    dive = db.get_dive_meta(dive_id)
    if dive is None:
        raise HTTPException(404, "Dive not found.")
    images = []
    for label, upload in (("takeoff", takeoff), ("top of the flight", apex), ("entry", entry)):
        mime = _mime(upload)
        data = upload.file.read(MAX_KEYFRAME_BYTES + 1)
        if mime not in IMAGE_TYPES:
            raise HTTPException(415, "Keyframes must be JPEG, PNG or WebP images.")
        if len(data) > MAX_KEYFRAME_BYTES:
            raise HTTPException(413, "Each keyframe must be under 4 MB.")
        images.append((label, data, mime))
    try:
        review = coach.review_keyframes(dive["analysis"], dive["setup"], images).model_dump()
    except Exception as exc:
        log.warning("Gemini vision review failed (%s)", coach.describe_error(exc))
        raise HTTPException(502, "Gemini couldn't review the frames right now. Try again in a moment.") from None
    db.save_vision_review(dive_id, review)
    return review


@app.post("/readiness", status_code=201)
def save_readiness(body: Readiness, db: DB):
    if body.heart_rate is None and body.breathing_rate is None:
        raise HTTPException(422, "Send a heart rate, a breathing rate, or both.")
    diver_id = db.get_or_create_default_diver()["id"]
    return db.save_readiness(diver_id, datetime.now(UTC), body.heart_rate, body.breathing_rate)


@app.get("/readiness")
def latest_readiness(db: DB):
    return db.latest_readiness(db.get_or_create_default_diver()["id"])


@app.get("/progress")
def progress(db: DB):
    return db.progress(db.get_or_create_default_diver()["id"])


@app.get("/workouts")
def workouts():
    return load_catalog()
