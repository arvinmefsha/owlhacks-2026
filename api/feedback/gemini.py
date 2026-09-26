"""Coaching feedback from Gemini, with a rule-based fallback so every dive gets a result."""

import base64
import json
import logging
from typing import Literal

from google import genai
from pydantic import BaseModel, Field

from analysis.scoring import FAULT_TITLES
from feedback.catalog import catalog_by_id, load_catalog, workouts_for

log = logging.getLogger(__name__)

PhaseName = Literal["takeoff", "flight", "entry"]


class FeedbackFault(BaseModel):
    title: str = Field(description="Short name of the fault, e.g. 'Short of vertical at entry'.")
    phase: PhaseName
    timestamp_s: float | None = Field(None, description="Clip time where it shows, copied from the measurements.")
    detail: str = Field(description="One or two sentences on what happened, using the measured numbers.")
    severity: Literal["minor", "major"]


class WorkoutPick(BaseModel):
    id: str = Field(description="Workout id from the catalog.")
    reason: str = Field(description="One sentence on why this workout helps this diver.")


class DiveFeedback(BaseModel):
    summary: str = Field(description="Two or three sentences: overall impression, biggest strength, most important fix.")
    faults: list[FeedbackFault] = Field(max_length=4, description="Most important faults first.")
    cues: list[str] = Field(min_length=1, max_length=4, description="Short coaching cues for the next attempt.")
    workouts: list[WorkoutPick] = Field(min_length=1, max_length=5, description="Dryland workouts from the catalog.")


class VisionNote(BaseModel):
    phase: PhaseName
    note: str = Field(description="One sentence about something visible in this frame.")


class VisionReview(BaseModel):
    summary: str = Field(description="Two sentences on what the frames show beyond the measurements.")
    notes: list[VisionNote] = Field(min_length=1, max_length=5, description="Observations, in dive order.")


SYSTEM_INSTRUCTION = """You are an experienced springboard and platform diving coach reviewing one dive.
You receive measurements from a pose-estimation system that watched the dive from the side, each already scored out of 10 against a coaching target, plus the faults it detected and a catalog of dryland workouts.

- Only state what the measurements support. Quote numbers where they help, for example "hips opened to 150°, aim for 165°".
- The measurements are approximate. If there are warnings about tracking or calibration, mention the most important one briefly.
- Lead with what went well, then the most important fix. Be encouraging and specific, for a club-level diver.
- Cues are short phrases to think about on the next attempt, under 12 words each.
- Choose 2 to 5 workouts from the catalog, by id, that target the detected faults. With no faults, choose ones that build on strengths.
- Copy timestamps from the measurements. Never invent them."""

VISION_PROMPT = """These three frames come from the same dive: takeoff, top of the flight, and entry, with the tracked skeleton drawn on.
The measurements below were computed from the skeleton. Point out things you can see that they can't capture well, such as head position, hand and arm shape, how tight the body looks, twisting, and how clean the entry and splash look.
Don't repeat the measured numbers, and say so if a frame is too unclear to judge."""

CUES: dict[str, str] = {
    "takeoff_knees_bent": "Push through the board until the legs are straight.",
    "takeoff_hips_closed": "Stand tall at takeoff, hips forward.",
    "takeoff_lean": "Jump up first, then rotate.",
    "takeoff_arms_low": "Arms finish high as you leave the board.",
    "low_height": "Press the board down, then explode up.",
    "too_close_to_board": "Take off a touch further out.",
    "too_far_from_board": "Jump up, not out.",
    "board_clearance": "Give the board more room on the way down.",
    "loose_position": "Grab and squeeze: pull the position tight.",
    "bent_knees": "Lock the knees, point the toes.",
    "body_not_straight": "Stay long and tight through the flight.",
    "legs_apart": "Squeeze the ankles together.",
    "flexed_feet": "Point the toes the whole way.",
    "entry_angle": "Line up straight before the water.",
    "under_rotation": "Kick out a little earlier and reach for the water.",
    "over_rotation": "Open up sooner and stop at vertical.",
    "entry_body_line": "Squeeze glutes and core as you enter.",
    "entry_arms": "Lock the arms overhead in a flat-hand grab.",
}


def format_value(value: float, unit: str) -> str:
    return f"{value:g}{unit}" if unit.startswith(("°", "%")) else f"{value:g} {unit}"


def _context(analysis: dict, setup: dict) -> dict:
    return {
        "dive": setup,
        "scores": analysis["scores"],
        "measurements": [
            {k: m[k] for k in ("label", "phase", "value", "unit", "target", "score", "t")} for m in analysis["metrics"]
        ],
        "other_measurements": analysis["info"],
        "faults": [
            {k: f[k] for k in ("id", "title", "phase", "value", "unit", "target", "score", "severity", "t")}
            for f in analysis["faults"]
        ],
        "warnings": analysis["warnings"],
    }


def _feedback_schema(workout_ids: list[str]) -> dict:
    schema = DiveFeedback.model_json_schema()
    schema["$defs"]["WorkoutPick"]["properties"]["id"]["enum"] = workout_ids
    return schema


def rule_based_feedback(analysis: dict) -> DiveFeedback:
    scores = analysis["scores"]
    faults = analysis["faults"][:4]
    phases = {p: s for p, s in scores.items() if p != "overall"}
    summary = f"Scored {scores['overall']}/10 by the numbers."
    if phases:
        best = max(phases, key=phases.get)
        summary += f" Strongest phase: {best} ({phases[best]}/10)."
    summary += f" Main thing to fix: {faults[0]['title'].lower()}." if faults else " No major faults detected."

    picks = workouts_for([f["id"] for f in faults])
    if not picks:
        catalog = catalog_by_id()
        picks = [(catalog["hollow_hold"], ""), (catalog["box_jumps"], "")]
    return DiveFeedback(
        summary=summary,
        faults=[
            FeedbackFault(
                title=f["title"],
                phase=f["phase"],
                timestamp_s=f["t"],
                detail=f"{f['label']} measured {format_value(f['value'], f['unit'])}; target {f['target']}.",
                severity=f["severity"],
            )
            for f in faults
        ],
        cues=[CUES[f["id"]] for f in faults if f["id"] in CUES][:4] or ["Keep it tight and aim for a vertical entry."],
        workouts=[
            WorkoutPick(id=w["id"], reason=f"Targets: {FAULT_TITLES[fault].lower()}." if fault else "Builds body tension and height.")
            for w, fault in picks
        ],
    )


class GeminiCoach:
    def __init__(self, api_key: str, model: str, timeout_s: float = 45.0):
        self._client = genai.Client(api_key=api_key)
        self._api_key = api_key
        self.model = model
        self.timeout_s = timeout_s

    def describe_error(self, exc: Exception) -> str:
        """An exception summary that is safe to log."""
        return f"{type(exc).__name__}: {str(exc).replace(self._api_key, '***')[:300]}"

    def feedback(self, analysis: dict, setup: dict) -> tuple[DiveFeedback, str]:
        """Feedback and its source ("gemini" or "rules" if the Gemini call failed)."""
        try:
            return self._ask_feedback(analysis, setup), "gemini"
        except Exception as exc:  # network, quota, bad JSON: never lose the dive over feedback
            log.warning("Gemini feedback failed (%s); using rule-based feedback", self.describe_error(exc))
            return rule_based_feedback(analysis), "rules"

    def _ask_feedback(self, analysis: dict, setup: dict) -> DiveFeedback:
        catalog = load_catalog()
        ids = [w["id"] for w in catalog]
        payload = _context(analysis, setup)
        payload["workout_catalog"] = [{k: w[k] for k in ("id", "name", "targets", "description")} for w in catalog]
        interaction = self._client.interactions.create(
            model=self.model,
            system_instruction=SYSTEM_INSTRUCTION,
            input=json.dumps(payload),
            response_format={"type": "text", "mime_type": "application/json", "schema": _feedback_schema(ids)},
            generation_config={"thinking_level": "low"},
            store=False,
            timeout=self.timeout_s,
        )
        feedback = DiveFeedback.model_validate_json(interaction.output_text)
        known = set(ids)
        feedback.workouts = [w for w in feedback.workouts if w.id in known] or rule_based_feedback(analysis).workouts
        return feedback

    def review_keyframes(self, analysis: dict, setup: dict, frames: list[tuple[str, bytes, str]]) -> VisionReview:
        """Ask Gemini to look at keyframes given as (label, image bytes, mime type). Raises on failure."""
        content: list[dict] = [{"type": "text", "text": VISION_PROMPT + "\n\n" + json.dumps(_context(analysis, setup))}]
        for label, image, mime in frames:
            content.append({"type": "text", "text": f"Frame: {label}"})
            content.append({"type": "image", "data": base64.b64encode(image).decode(), "mime_type": mime})
        interaction = self._client.interactions.create(
            model=self.model,
            system_instruction=SYSTEM_INSTRUCTION,
            input=content,
            response_format={"type": "text", "mime_type": "application/json", "schema": VisionReview.model_json_schema()},
            generation_config={"thinking_level": "low"},
            store=False,
            timeout=self.timeout_s,
        )
        return VisionReview.model_validate_json(interaction.output_text)
