import json
import logging
from types import SimpleNamespace

from analysis import analyze_dive
from analysis.scoring import FAULT_TITLES
from feedback.catalog import load_catalog, workouts_for
from feedback.gemini import CUES, GeminiCoach, format_value, rule_based_feedback
from tests.synthetic import HEIGHT, WIDTH, DiveSpec, generate

TUCK_103C = {"position": "tuck", "direction": "forward", "somersaults": 1.5, "apparatus": "springboard", "board_height_m": 1.0}


def sloppy_analysis() -> dict:
    spec = DiveSpec(v0=3.2, position_hip=100, position_knee=100, toe=100, rotation_deg=515, entry_arm_lift=130, leg_gap_m=0.4)
    frames, truth = generate(spec)
    return analyze_dive(frames, WIDTH, HEIGHT, TUCK_103C, {"water_y": truth["water_y"]}, height_cm=170)


class FakeInteractions:
    def __init__(self, output_text=None, error=None):
        self.output_text, self.error, self.kwargs = output_text, error, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(output_text=self.output_text)


def coach_with(interactions: FakeInteractions) -> GeminiCoach:
    coach = GeminiCoach(api_key="test-secret-key", model="gemini-3.8-flash")
    coach._client = SimpleNamespace(interactions=interactions)
    return coach


def test_every_fault_has_a_cue_and_a_workout():
    ids = {w["id"] for w in load_catalog()}
    assert len(ids) == len(load_catalog()), "workout ids must be unique"
    for fault in FAULT_TITLES:
        assert fault in CUES, fault
        assert workouts_for([fault]), fault
    for workout in load_catalog():
        assert set(workout["targets"]) <= set(FAULT_TITLES), workout["id"]


def test_rule_feedback_for_a_sloppy_dive():
    analysis = sloppy_analysis()
    feedback = rule_based_feedback(analysis)
    assert analysis["faults"][0]["title"].lower() in feedback.summary
    assert 1 <= len(feedback.faults) <= 4
    assert feedback.cues and feedback.workouts
    assert feedback.faults[0].timestamp_s == analysis["faults"][0]["t"]


def test_format_value():
    assert format_value(150.0, "°") == "150°"
    assert format_value(0.45, "m") == "0.45 m"
    assert format_value(30.0, "% of leg length") == "30% of leg length"


def test_gemini_feedback_is_constrained_to_the_catalog():
    analysis = sloppy_analysis()
    reply = {
        "summary": "Good effort.",
        "faults": [],
        "cues": ["Squeeze the tuck."],
        "workouts": [{"id": "tuck_snaps", "reason": "Tighter tuck."}, {"id": "made_up_drill", "reason": "?"}],
    }
    interactions = FakeInteractions(output_text=json.dumps(reply))
    feedback, source = coach_with(interactions).feedback(analysis, TUCK_103C)

    assert source == "gemini"
    assert [w.id for w in feedback.workouts] == ["tuck_snaps"]
    kwargs = interactions.kwargs
    assert kwargs["store"] is False
    assert kwargs["generation_config"] == {"thinking_level": "low"}
    assert "temperature" not in kwargs
    assert kwargs["response_format"]["mime_type"] == "application/json"
    enum = kwargs["response_format"]["schema"]["$defs"]["WorkoutPick"]["properties"]["id"]["enum"]
    assert set(enum) == {w["id"] for w in load_catalog()}
    assert json.loads(kwargs["input"])["faults"][0]["id"] == analysis["faults"][0]["id"]


def test_gemini_errors_fall_back_to_rules_without_logging_the_key(caplog):
    analysis = sloppy_analysis()
    interactions = FakeInteractions(error=RuntimeError("401 for key test-secret-key"))
    with caplog.at_level(logging.WARNING):
        feedback, source = coach_with(interactions).feedback(analysis, TUCK_103C)
    assert source == "rules"
    assert feedback.workouts
    assert "test-secret-key" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_unparseable_gemini_reply_falls_back_to_rules():
    feedback, source = coach_with(FakeInteractions(output_text="not json")).feedback(sloppy_analysis(), TUCK_103C)
    assert source == "rules"
    assert feedback.cues
