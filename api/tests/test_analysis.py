import pytest

from analysis import AnalysisError, analyze_dive
from tests.synthetic import HEIGHT, WIDTH, DiveSpec, generate

TUCK_103C = {"position": "tuck", "direction": "forward", "somersaults": 1.5, "apparatus": "springboard", "board_height_m": 1.0}


def run(spec: DiveSpec, setup: dict, calibrate: str = "water"):
    frames, truth = generate(spec)
    calibration = {}
    if calibrate in ("water", "both"):
        calibration["water_y"] = truth["water_y"]
    if calibrate in ("board", "both"):
        calibration["board_tip"] = truth["board_tip"]
    result = analyze_dive(frames, WIDTH, HEIGHT, setup, calibration, height_cm=spec.height_m * 100)
    return result, truth


def metric(result: dict, key: str) -> dict:
    return next(m for m in result["metrics"] if m["key"] == key)


def fault_ids(result: dict) -> set[str]:
    return {f["id"] for f in result["faults"]}


def test_good_tuck_dive_matches_ground_truth():
    result, truth = run(DiveSpec(), TUCK_103C, calibrate="both")

    phases = result["phases"]
    assert phases["takeoff"] == pytest.approx(truth["takeoff"], abs=0.05)
    assert phases["apex"] == pytest.approx(truth["apex"], abs=0.05)
    assert phases["entry"] == pytest.approx(truth["contact"], abs=0.05)
    assert phases["entry_method"] == "water_line"

    assert metric(result, "jump_height_m")["value"] == pytest.approx(truth["jump_height_m"], abs=0.08)
    assert metric(result, "min_hip_angle")["value"] == pytest.approx(55, abs=6)
    assert metric(result, "position_knee_angle")["value"] == pytest.approx(50, abs=8)
    assert metric(result, "entry_angle_deg")["value"] < 6
    assert metric(result, "toe_point_deg")["value"] > 160
    assert result["head_first"] is True
    assert result["rotation"]["measured_deg"] == pytest.approx(530, abs=25)

    assert result["scores"]["overall"] >= 8.0
    assert not {"low_height", "loose_position", "under_rotation", "flexed_feet"} & fault_ids(result)
    assert result["warnings"] == []


def test_sloppy_dive_produces_the_expected_faults():
    spec = DiveSpec(v0=3.2, position_hip=100, position_knee=100, toe=100, rotation_deg=515, entry_arm_lift=130, leg_gap_m=0.4)
    result, _ = run(spec, TUCK_103C)

    faults = fault_ids(result)
    assert {"low_height", "loose_position", "flexed_feet", "under_rotation", "entry_arms", "legs_apart"} <= faults
    assert result["scores"]["overall"] < 6.0
    impacts = [f["impact"] for f in result["faults"]]
    assert impacts == sorted(impacts, reverse=True)


def test_over_rotation_is_distinguished_from_short():
    result, _ = run(DiveSpec(rotation_deg=565), TUCK_103C)
    assert "over_rotation" in fault_ids(result)
    assert "under_rotation" not in fault_ids(result)


def test_board_tip_alone_estimates_the_water_line():
    result, truth = run(DiveSpec(), TUCK_103C, calibrate="board")
    assert result["phases"]["entry_method"] == "board_height"
    assert result["phases"]["entry"] == pytest.approx(truth["contact"], abs=0.06)


def test_floor_jump_without_calibration_uses_landing():
    setup = {"position": "straight", "direction": "forward", "somersaults": 0, "apparatus": "springboard", "board_height_m": 1.0}
    result, truth = run(DiveSpec(floor_jump=True, v0=2.5), setup, calibrate="none")
    assert result["phases"]["entry_method"] == "landing"
    assert result["phases"]["takeoff"] == pytest.approx(truth["takeoff"], abs=0.05)
    assert result["phases"]["entry"] == pytest.approx(truth["contact"], abs=0.06)
    assert result["head_first"] is False
    assert any("water line" in w for w in result["warnings"])


def test_missing_height_is_flagged():
    frames, truth = generate(DiveSpec())
    result = analyze_dive(frames, WIDTH, HEIGHT, TUCK_103C, {"water_y": truth["water_y"]}, height_cm=None)
    assert result["scale"]["source"] == "default_height"
    assert any("height" in w for w in result["warnings"])


def test_no_pose_raises_a_clear_error():
    frames = [{"t": i / 30, "lm": None} for i in range(60)]
    with pytest.raises(AnalysisError):
        analyze_dive(frames, WIDTH, HEIGHT, TUCK_103C, {}, height_cm=170)


def test_uneven_live_sampling_is_handled():
    frames, truth = generate(DiveSpec(fps=60))
    uneven = [f for i, f in enumerate(frames) if i % 5 not in (1, 3)]  # about 24 fps, irregular
    result = analyze_dive(uneven, WIDTH, HEIGHT, TUCK_103C, {"water_y": truth["water_y"]}, height_cm=170)
    assert result["phases"]["apex"] == pytest.approx(truth["apex"], abs=0.06)
    assert metric(result, "jump_height_m")["value"] == pytest.approx(truth["jump_height_m"], abs=0.1)
