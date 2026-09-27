"""Request bodies accepted by the API."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


class RegionOfInterest(BaseModel):
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _inside_frame(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("ROI must fit inside the normalized video frame")
        return self


class AnalysisCalibration(Calibration):
    roi: RegionOfInterest | None = None


class AnalysisJobPayload(BaseModel):
    setup: DiveSetup
    calibration: AnalysisCalibration
    source: Literal["live", "upload"]
    profile: Literal["fast", "quality"]


class Readiness(BaseModel):
    heart_rate: float | None = Field(None, ge=30, le=220)
    breathing_rate: float | None = Field(None, ge=3, le=60)
