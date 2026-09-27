"""Coaching feedback from Gemini, with a rule-based fallback so every dive gets a result."""

import base64
import json
import logging
from typing import Literal

from analysis.scoring import FAULT_TITLES
from google import genai
from pydantic import BaseModel, Field

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


class LiveTip(BaseModel):
    tip: str = Field(description="One or two short spoken sentences, at most 35 words, with exactly one fix.")


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

LIVE_TIP_MAX_WORDS = 35
LIVE_TIP_TIMEOUT_S = 12.0

LIVE_TIP_INSTRUCTION = """You are a diving coach standing poolside. The diver just climbed out of the water and you give them one quick spoken pointer before the next dive.
You receive the dive setup, scores out of 10 from a pose-estimation system, the top faults with their measurements and a suggested cue, any tracking warnings, and sometimes the same numbers for the diver's previous dive.

- One or two short sentences, 35 words at most, in second person. It is read aloud, so no lists, markdown, symbols or emoji.
- Open with a few words of encouragement about something that went well, then give exactly one concrete fix for the first (highest-impact) fault.
- Compare with the previous dive only when the numbers clearly support it, for example "your entry was straighter than last time".
- Never invent numbers or faults. With no faults, tell the diver what to keep doing."""

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


PHASE_PRAISE: dict[str, tuple[str, ...]] = {
    "takeoff": ("Strong takeoff on that one.", "Great push off the board."),
    "flight": ("Nice shape in the air.", "Good work in the air on that one."),
    "entry": ("Nice entry on that one.", "Good line into the water."),
}


def _top_faults(analysis: dict, limit: int) -> list[dict]:
    return sorted(analysis.get("faults") or [], key=lambda f: f.get("impact", 0), reverse=True)[:limit]


def rule_based_tip(analysis: dict, previous_analysis: dict | None = None, dive_number: int = 1) -> str:
    """A short spoken tip: praise, the change since the last dive if it moved, and one fix."""
    scores = analysis["scores"]
    overall = scores["overall"]
    fix = next(iter(_top_faults(analysis, 1)), None)
    phases = {p: s for p, s in scores.items() if p in PHASE_PRAISE and (fix is None or p != fix["phase"])}
    best = max(phases, key=phases.get) if phases else None
    if best is not None and phases[best] >= 7:
        variants = PHASE_PRAISE[best]
        parts = [variants[(dive_number - 1) % len(variants)]]
    else:
        parts = [f"Good effort, that one scored {overall:g} out of 10."]

    previous = ((previous_analysis or {}).get("scores") or {}).get("overall")
    if previous is not None:
        change = round(overall - previous, 1)
        if abs(change) >= 0.3:
            parts.append(f"That's {'up' if change > 0 else 'down'} {abs(change):g} from your last dive.")

    if fix is None:
        parts.append("No faults flagged, so keep doing exactly that.")
    else:
        cue = CUES.get(fix["id"], f"Work on this: {fix['title'].lower()}.")
        parts.append(f"Next time, {cue[0].lower()}{cue[1:]}")
    return " ".join(parts)


def _tip_numbers(analysis: dict) -> dict:
    return {
        "scores": analysis.get("scores"),
        "top_faults": [
            {**{k: f.get(k) for k in ("id", "title", "phase", "value", "unit", "target", "score", "severity")},
             "cue": CUES.get(f["id"])}
            for f in _top_faults(analysis, 3)
        ],
    }


def _tip_context(analysis: dict, setup: dict, previous_analysis: dict | None, dive_number: int) -> dict:
    payload = {"dive_number": dive_number, "dive": setup, **_tip_numbers(analysis), "warnings": analysis.get("warnings") or []}
    if previous_analysis:
        payload["previous_dive"] = _tip_numbers(previous_analysis)
    return payload


class GeminiCoach:
    def __init__(self, api_key: str | None, model: str, timeout_s: float = 45.0):
        self._client = genai.Client(api_key=api_key) if api_key else None
        self._api_key = api_key
        self.model = model
        self.timeout_s = timeout_s

    def describe_error(self, exc: Exception) -> str:
        """An exception summary that is safe to log."""
        message = str(exc)
        if self._api_key:
            message = message.replace(self._api_key, "***")
        return f"{type(exc).__name__}: {message[:300]}"

    def feedback(self, analysis: dict, setup: dict) -> tuple[DiveFeedback, str]:
        """Feedback and its source ("gemini" or "rules" if the Gemini call failed)."""
        if self._client is None:
            return rule_based_feedback(analysis), "rules"
        try:
            return self._ask_feedback(analysis, setup), "gemini"
        except Exception as exc:  # network, quota, bad JSON: never lose the dive over feedback
            log.warning("Gemini feedback failed (%s); using rule-based feedback", self.describe_error(exc))
            return rule_based_feedback(analysis), "rules"

    def _ask_feedback(self, analysis: dict, setup: dict) -> DiveFeedback:
        if self._client is None:
            raise RuntimeError("Gemini is not configured.")
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

    def quick_tip(
        self, analysis: dict, setup: dict, previous_analysis: dict | None = None, dive_number: int = 1
    ) -> tuple[str, str]:
        """A short spoken tip and its source ("gemini", or "rules" if Gemini is off or failed)."""
        if self._client is not None:
            try:
                return self._ask_tip(analysis, setup, previous_analysis, dive_number), "gemini"
            except Exception as exc:  # a live session never waits on or fails over a tip
                log.warning("Gemini live tip failed (%s); using the rule-based tip", self.describe_error(exc))
        return rule_based_tip(analysis, previous_analysis, dive_number), "rules"

    def _ask_tip(self, analysis: dict, setup: dict, previous_analysis: dict | None, dive_number: int) -> str:
        if self._client is None:
            raise RuntimeError("Gemini is not configured.")
        interaction = self._client.interactions.create(
            model=self.model,
            system_instruction=LIVE_TIP_INSTRUCTION,
            input=json.dumps(_tip_context(analysis, setup, previous_analysis, dive_number)),
            response_format={"type": "text", "mime_type": "application/json", "schema": LiveTip.model_json_schema()},
            generation_config={"thinking_level": "low"},
            store=False,
            timeout=min(self.timeout_s, LIVE_TIP_TIMEOUT_S),
        )
        tip = " ".join(LiveTip.model_validate_json(interaction.output_text).tip.split())
        if not tip or len(tip.split()) > LIVE_TIP_MAX_WORDS:
            raise ValueError(f"the tip was empty or longer than {LIVE_TIP_MAX_WORDS} words")
        return tip

    def review_keyframes(self, analysis: dict, setup: dict, frames: list[tuple[str, bytes, str]]) -> VisionReview:
        """Ask Gemini to look at keyframes given as (label, image bytes, mime type). Raises on failure."""
        if self._client is None:
            raise RuntimeError("Gemini is not configured.")
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
