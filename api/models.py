"""Request bodies accepted by the API."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from analysis.landmarks import NUM_LANDMARKS


class Point(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class DiveSetup(BaseModel):
    position: Literal["straight", "pike", "tuck", "free"] = "straight"
    direction: Literal["forward", "back", "reverse", "inward"] = "forward"
    somersaults: float = Field(0.5, ge=0, le=4.5, multiple_of=0.5)
    apparatus: Literal["springboard", "platform"] = "springboard"
    board_height_m: float = Field(1.0, gt=0, le=10)


class Calibration(BaseModel):
    """Optional taps on a paused frame, in normalized image coordinates."""

    board_tip: Point | None = None
    water_y: float | None = Field(None, ge=0, le=1)


class VideoInfo(BaseModel):
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    fps: float | None = Field(None, gt=0, le=480)
    source: Literal["live", "upload"] = "live"


class Frame(BaseModel):
    t: float = Field(ge=0)
    lm: list[list[float]] | None = None  # 33 x [x, y, z, visibility], or None if no pose was found

    @field_validator("lm")
    @classmethod
    def _shape(cls, lm: list[list[float]] | None) -> list[list[float]] | None:
        if lm is not None and (len(lm) != NUM_LANDMARKS or any(len(p) != 4 for p in lm)):
            raise ValueError(f"expected {NUM_LANDMARKS} landmarks of [x, y, z, visibility]")
        return lm


class DivePayload(BaseModel):
    diver_id: UUID
    setup: DiveSetup
    calibration: Calibration = Calibration()
    video: VideoInfo
    frames: list[Frame] = Field(min_length=1, max_length=20000)


class NewDiver(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    height_cm: float | None = Field(None, ge=100, le=230)


class Readiness(BaseModel):
    diver_id: UUID
    heart_rate: float | None = Field(None, ge=30, le=220)
    breathing_rate: float | None = Field(None, ge=3, le=60)
