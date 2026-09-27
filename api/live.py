"""Live session endpoints: per-frame pose for the dive trigger, a quick spoken tip, and its audio."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from feedback.speech import SpeechClient, SpeechError
from live_pose import LivePoseDetector, LivePoseUnavailable
from pydantic import BaseModel, Field, StringConstraints

log = logging.getLogger("api.live")

router = APIRouter(prefix="/live", tags=["live"])

FRAME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_FRAME_BYTES = 1024 * 1024


class LiveTipRequest(BaseModel):
    dive_id: UUID
    previous_dive_id: UUID | None = None
    dive_number: int = Field(1, ge=1)


class SpeechRequest(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]


def _live_pose(request: Request) -> LivePoseDetector:
    detector = getattr(request.app.state, "live_pose", None)
    if detector is None:
        detector = LivePoseDetector(request.app.state.settings)
        request.app.state.live_pose = detector
    return detector


def _speech(request: Request) -> SpeechClient:
    speech = getattr(request.app.state, "speech", None)
    if speech is None:
        speech = SpeechClient(request.app.state.settings)
        request.app.state.speech = speech
    return speech


@router.post("/pose")
def live_pose(request: Request, frame: Annotated[UploadFile, File(description="One camera frame")]):
    mime = (frame.content_type or "").split(";")[0].strip().lower()
    if mime not in FRAME_TYPES:
        raise HTTPException(415, "Frames must be JPEG, PNG or WebP images.")
    data = frame.file.read(MAX_FRAME_BYTES + 1)
    if len(data) > MAX_FRAME_BYTES:
        raise HTTPException(413, "Each frame must be under 1 MB.")
    try:
        return _live_pose(request).detect(data)
    except ValueError:
        raise HTTPException(415, "The frame could not be read as an image.") from None
    except LivePoseUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


@router.post("/tip")
def live_tip(body: LiveTipRequest, request: Request):
    db = request.app.state.db
    dive = db.get_dive_meta(body.dive_id)
    if dive is None:
        raise HTTPException(404, "Dive not found.")
    previous = db.get_dive_meta(body.previous_dive_id) if body.previous_dive_id else None
    tip, source = request.app.state.coach.quick_tip(
        dive["analysis"], dive["setup"], previous["analysis"] if previous else None, body.dive_number
    )
    return {"tip": tip, "source": source}


@router.post("/speech")
def live_speech(body: SpeechRequest, request: Request):
    speech = _speech(request)
    if not speech.configured:
        return Response(status_code=204)
    try:
        audio = speech.synthesize(body.text)
    except SpeechError as exc:
        log.warning("ElevenLabs speech failed (%s)", exc)
        raise HTTPException(502, "The coach's voice isn't available right now. Try again in a moment.") from None
    return Response(audio, media_type="audio/mpeg")
