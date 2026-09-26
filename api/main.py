"""Dive Form Analyzer API.

The Next.js app proxies /api/* to this server, so browsers never call it directly and the
Gemini and database credentials stay on this machine. Run with: uvicorn main:app --reload
"""

import logging
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError

from analysis import AnalysisError, analyze_dive
from config import Settings, get_settings
from db.database import Database
from feedback.catalog import load_catalog
from feedback.gemini import GeminiCoach
from models import DivePayload, NewDiver, Readiness

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

VIDEO_TYPES = {"video/webm": ".webm", "video/mp4": ".mp4", "video/quicktime": ".mov"}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_KEYFRAME_BYTES = 4 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_url.get_secret_value())
    db.init_schema(load_catalog())
    db.open()
    app.state.settings = settings
    app.state.db = db
    app.state.coach = GeminiCoach(settings.gemini_api_key.get_secret_value(), settings.gemini_model)
    log.info("Ready. Gemini model: %s", settings.gemini_model)
    try:
        yield
    finally:
        db.close()


app = FastAPI(title="Dive Form Analyzer API", lifespan=lifespan)


def _db(request: Request) -> Database:
    return request.app.state.db


def _coach(request: Request) -> GeminiCoach:
    return request.app.state.coach


def _settings(request: Request) -> Settings:
    return request.app.state.settings


DB = Annotated[Database, Depends(_db)]
Coach = Annotated[GeminiCoach, Depends(_coach)]
AppSettings = Annotated[Settings, Depends(_settings)]


def _mime(upload: UploadFile) -> str:
    return (upload.content_type or "").split(";")[0].strip().lower()


def _video_file(settings: Settings, name: str | None) -> Path | None:
    if not name:
        return None
    path = (settings.upload_dir / name).resolve()
    return path if path.parent == settings.upload_dir.resolve() and path.is_file() else None


def _metric_rows(dive_id: UUID, diver_id: UUID, at: datetime, analysis: dict) -> list[tuple]:
    rows = [(dive_id, diver_id, at, m["phase"], m["key"], m["value"], m["score"]) for m in analysis["metrics"]]
    rows += [(dive_id, diver_id, at, "info", i["key"], i["value"], None) for i in analysis["info"]]
    rows += [(dive_id, diver_id, at, phase, f"score_{phase}", s, s) for phase, s in analysis["scores"].items()]
    return rows


@app.get("/health")
def health(db: DB):
    return {"ok": db.ping()}


@app.get("/divers")
def list_divers(db: DB):
    return db.list_divers()


@app.post("/divers", status_code=201)
def create_diver(body: NewDiver, db: DB):
    return db.create_diver(body.name, body.height_cm)


@app.post("/dives", status_code=201)
def create_dive(
    payload: Annotated[UploadFile, File(description="JSON matching DivePayload, sent as a file part")],
    db: DB,
    coach: Coach,
    settings: AppSettings,
    video: Annotated[UploadFile | None, File(description="The recorded clip")] = None,
):
    try:
        body = DivePayload.model_validate_json(payload.file.read())
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False, include_input=False)) from None

    mime = _mime(video) if video else None
    if video:
        if mime not in VIDEO_TYPES:
            raise HTTPException(415, "Unsupported video type. Use WebM, MP4 or MOV.")
        if video.size is not None and video.size > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"The video is larger than {settings.max_upload_mb} MB.")

    diver = db.get_diver(body.diver_id)
    if diver is None:
        raise HTTPException(404, "Diver not found.")

    setup = body.setup.model_dump()
    frames = [f.model_dump() for f in body.frames]
    try:
        analysis = analyze_dive(
            frames, body.video.width, body.video.height, setup, body.calibration.model_dump(), diver["height_cm"]
        )
    except AnalysisError as exc:
        raise HTTPException(422, str(exc)) from None
    feedback, feedback_source = coach.feedback(analysis, setup)

    dive_id = uuid4()
    recorded_at = datetime.now(UTC)
    video_name = None
    if video:
        video_name = f"{dive_id}{VIDEO_TYPES[mime]}"
        with (settings.upload_dir / video_name).open("wb") as out:
            shutil.copyfileobj(video.file, out)
    try:
        db.insert_dive(
            {
                "id": dive_id,
                "diver_id": body.diver_id,
                "recorded_at": recorded_at,
                "setup": setup,
                "calibration": body.calibration.model_dump(),
                "source": body.video.source,
                "video_path": video_name,
                "video_mime": mime,
                "video_width": body.video.width,
                "video_height": body.video.height,
                "fps": body.video.fps,
                "overall_score": analysis["scores"]["overall"],
                "scores": analysis["scores"],
                "analysis": analysis,
                "feedback": feedback.model_dump(),
                "feedback_source": feedback_source,
            },
            frames,
            _metric_rows(dive_id, body.diver_id, recorded_at, analysis),
        )
    except Exception:
        if video_name:
            (settings.upload_dir / video_name).unlink(missing_ok=True)
        raise
    return {"id": dive_id, "scores": analysis["scores"], "feedback_source": feedback_source}


@app.get("/dives")
def list_dives(db: DB, diver_id: UUID, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    return db.list_dives(diver_id, limit)


@app.get("/dives/{dive_id}")
def get_dive(dive_id: UUID, db: DB, settings: AppSettings):
    dive = db.get_dive(dive_id)
    if dive is None:
        raise HTTPException(404, "Dive not found.")
    dive["has_video"] = _video_file(settings, dive.pop("video_path")) is not None
    frames = dive.pop("frames")
    # Landmarks are flattened: 33 points x (x, y, z, visibility) per frame, or null.
    dive["frames"] = {"t": [f["t"] for f in frames], "lm": [f["landmarks"] for f in frames]}
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
    if db.get_diver(body.diver_id) is None:
        raise HTTPException(404, "Diver not found.")
    return db.save_readiness(body.diver_id, datetime.now(UTC), body.heart_rate, body.breathing_rate)


@app.get("/readiness")
def latest_readiness(db: DB, diver_id: UUID):
    return db.latest_readiness(diver_id)


@app.get("/progress")
def progress(db: DB, diver_id: UUID):
    return db.progress(diver_id)


@app.get("/workouts")
def workouts():
    return load_catalog()
