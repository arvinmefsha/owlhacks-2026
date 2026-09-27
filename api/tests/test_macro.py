"""Independent envelope fixtures, including adversarial missing-data cases."""
from copy import deepcopy
import json

import numpy as np
import pytest

from analysis import AnalysisError, analyze_dive
from feedback.gemini import GeminiCoach, rule_based_feedback, rule_based_tip
from analysis_jobs import _metric_rows
from uuid import uuid4
from datetime import datetime, UTC

SETUP = dict(position="tuck", direction="forward", somersaults=1.5, apparatus="springboard", board_height_m=1)
CAL = dict(board_tip=dict(x=.24, y=.4), water_y=.5)


def clip(deviation=0., mirror=False, trend=0.):
    centers = [320]*6 + [295, 270, 245, 222, 202, 190, 185, 190, 202, 220, 242, 265, 290, 315, 340, 360, 380, 400, 420]
    heights = [160]*6 + [150, 140, 120, 100, 80, 70, 64, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 160, 160]
    frames = []
    for i, (cy, h) in enumerate(zip(centers, heights)):
        cx = 220 if i < 6 else 240 + (i-6)*200/18
        w = 60 if 9 <= i <= 15 else 40
        box = np.array([cx-w/2, cy-h/2, cx+w/2, cy+h/2]) / 1000
        lm = np.zeros((17, 4))
        if i >= 15:
            # A long, measured body line: lowest point first reaches the water
            # at frame 24. ``deviation`` is degrees away from vertical.
            theta = np.radians(deviation + (i - 24) * trend)
            axis = np.array([np.sin(theta), np.cos(theta)])
            leading = 80 / max(np.cos(theta), .2)
            for a, offset in ((0, -92), (5, -55), (6, -55), (7, -28), (8, -28),
                              (9, -82), (10, -82), (11, -5), (12, -5),
                              (13, 32), (14, 32), (15, leading), (16, leading)):
                lm[a, :2] = (np.array([cx, cy]) + offset*axis)/1000
                lm[a, 3] = .95
        if mirror:
            box[[0, 2]] = 1-box[[2, 0]]
            lm[:, 0] = 1-lm[:, 0]
        frames.append(dict(t=i/30, box=box.tolist(), box_confidence=.9, lm=lm.tolist()))
    cal = deepcopy(CAL)
    if mirror:
        cal["board_tip"]["x"] = 1-cal["board_tip"]["x"]
    return frames, cal


def run(frames=None, cal=None, setup=None):
    if frames is None:
        frames, default = clip()
        cal = default if cal is None else cal
    return analyze_dive(frames, 1000, 1000, setup or SETUP, cal or CAL)


def test_exact_source_milestones_scale_and_metrics():
    result = run()
    assert result["phases"] == dict(takeoff=6/30, apex=12/30, entry=24/30, entry_method="keypoint_water_crossing")
    assert result["scale"]["px_per_m"] == 100
    values = {m["key"]: m["value"] for m in result["metrics"]}
    assert values == pytest.approx(dict(travel_m=2, apex_height_m=2.47, compaction_ratio=.4,
                                        entry_deviation_deg=0, entry_extension_ratio=1))
    assert [f["id"] for f in result["faults"]] == ["EXCESSIVE_TRAVEL"]
    assert "scores" not in result
    assert "hip_angle" not in result["series"]
    json.dumps(result, allow_nan=False)


def test_clean_vertical_entry_cannot_be_inverted_to_180_degrees_or_flagged():
    result = run()
    entry = next(m for m in result["metrics"] if m["key"] == "entry_deviation_deg")
    assert entry["value"] == pytest.approx(0)
    assert entry["status"] == "within_target"
    assert entry["fault"] is None
    assert {m["key"] for m in result["metrics"]} == {
        "travel_m", "apex_height_m", "compaction_ratio", "entry_deviation_deg", "entry_extension_ratio"
    }


def _fold_near_contact(frames, indices=(21, 22, 23)):
    for i in indices:
        cx = (frames[i]["box"][0] + frames[i]["box"][2]) * 500
        cy = (frames[i]["box"][1] + frames[i]["box"][3]) * 500
        for side, shift in ((0, -3), (1, 3)):
            for joint, dx, dy in ((5, -25, -35), (11, 15, -5), (13, 35, -35), (15, -15, -5)):
                point = frames[i]["lm"][joint + side]
                point[:2] = [(cx + dx + shift) / 1000, (cy + dy) / 1000]
                point[3] = .95


def test_folded_body_at_entry_is_flagged_and_takes_priority_over_travel():
    frames, cal = clip()
    _fold_near_contact(frames)
    result = run(frames, cal)
    entry = next(m for m in result["metrics"] if m["key"] == "entry_extension_ratio")
    assert entry["value"] < .68
    assert entry["fault"] == "FOLDED_ENTRY"
    assert result["metrics"][3]["value"] is None
    feedback = rule_based_feedback(result)
    assert feedback.faults[0].title == "Still folded at entry"
    assert "Open from the tuck" in feedback.cues[0]
    assert "macro_kickout_freeze" in [w.id for w in feedback.workouts]


def test_one_suspect_entry_pose_does_not_create_a_folded_entry_fault():
    frames, cal = clip()
    _fold_near_contact(frames, indices=(23,))
    result = run(frames, cal)
    assert result["metrics"][4]["fault"] is None
    assert result["metrics"][4]["value"] is not None


@pytest.mark.parametrize("deviation,flag", [(0, None), (8, None), (10, None), (15, "ENTRY_LINE_OFF_VERTICAL")])
@pytest.mark.parametrize("mirror", [False, True])
def test_entry_deviation_is_mirror_invariant(deviation, flag, mirror):
    frames, cal = clip(deviation, mirror)
    result = run(frames, cal)
    entry = result["metrics"][3]
    assert entry["value"] == pytest.approx(deviation)
    assert entry["fault"] == flag
    assert entry["status"] == ("flagged" if flag else "within_target")


def test_directional_labels_require_consistent_precontact_rotation():
    result = run(*clip(30, trend=3))
    assert result["metrics"][3]["fault"] == "OVER_ROTATION"
    result = run(*clip(18, trend=-3))
    assert result["metrics"][3]["fault"] == "UNDER_ROTATION"


def test_first_measured_node_sets_entry_before_box_reaches_water():
    frames, cal = clip()
    # A leading hand touches first while the rest of the detection envelope is dry.
    frames[22]["lm"][9][1] = .5
    frames[22]["lm"][9][3] = .95
    result = run(frames, cal)
    assert result["phases"]["entry"] == pytest.approx(22 / 30)
    assert result["phases"]["entry_method"] == "keypoint_water_crossing"
    assert frames[22]["box"][3] < .5


def test_no_calibration_or_boxes_never_recreates_measurements_from_joints():
    frames, cal = clip()
    for f in frames:
        f.pop("box")
    result = run(frames, cal)
    assert all(m["value"] is None for m in result["metrics"])
    assert not result["faults"]
    result = analyze_dive(clip()[0], 1000, 1000, SETUP, {}, height_cm=170)
    assert result["scale"]["source"] == "unavailable"
    assert not result["faults"]


def test_lost_contact_and_predicted_boxes_are_not_measurements():
    frames, cal = clip()
    frames[-2]["box_predicted"] = True
    result = run(frames, cal)
    assert result["phases"]["entry"] == pytest.approx(.8)
    assert result["metrics"][0]["value"] is None
    assert result["metrics"][3]["value"] == pytest.approx(0)
    assert result["metrics"][2]["status"] == "within_target"


def test_horizontal_straight_body_does_not_earn_tight_tuck_result():
    frames, cal = clip()
    b = frames[12]["box"]
    b[0], b[2] = .22, .38
    result = run(frames, cal)
    assert result["metrics"][2]["value"] == pytest.approx(.4)
    assert result["metrics"][2]["status"] == "uncertain"


def test_full_observed_loose_shape_maps_workout_without_joint_angles():
    frames, cal = clip()
    for f in frames[6:]:
        b = f["box"]
        cy = (b[1]+b[3])/2
        h = max(b[3]-b[1], .1)
        b[1], b[3] = cy-h/2, cy+h/2
    result = run(frames, cal)
    assert result["metrics"][2]["fault"] == "LOOSE_SHAPE"
    coach = GeminiCoach(None, "unused")
    feedback, source = coach.feedback(result, SETUP)
    assert source == "rules"
    assert "macro_tuck_snaps" in [w.id for w in feedback.workouts]
    assert "out of 10" not in rule_based_tip(result)


def test_all_five_faults_have_deterministic_workout_mapping():
    from analysis.macro import TITLES
    from feedback.catalog import workouts_for
    for flag in TITLES:
        picks = workouts_for([flag], per_fault=1)
        assert len(picks) == 1
        assert picks[0][0]["target"]


def test_missing_contact_node_uses_box_for_timing_but_visible_body_for_angle():
    frames, cal = clip()
    frames[-1]["lm"] = None
    result = run(frames, cal)
    assert result["phases"]["entry_method"] == "measured_box_water_crossing"
    assert result["metrics"][3]["value"] == pytest.approx(0)


def test_box_contact_never_manufactures_an_entry_axis():
    frames, cal = clip()
    for frame in frames[-6:]:
        frame["lm"] = None
    result = run(frames, cal)
    assert result["phases"]["entry_method"] == "measured_box_water_crossing"
    assert result["metrics"][3]["value"] is None


def test_short_contact_projection_preserves_visible_preentry_line():
    frames, cal = clip()
    # The detector stops 10 px (0.1 m) above water while moving down quickly,
    # as in the real tuck clip; the contact joint itself is no longer visible.
    frames[-1]["box"][3] = .49
    frames[-1]["lm"] = None
    result = run(frames, cal)
    assert result["phases"]["entry_method"] == "projected_box_water_crossing"
    assert result["phases"]["entry"] > frames[-1]["t"]
    assert result["phases"]["entry"] - frames[-1]["t"] <= .12
    assert result["metrics"][3]["value"] == pytest.approx(0)


def test_bad_calibration_and_unsupported_apparatus():
    with pytest.raises(AnalysisError):
        run(cal={**CAL, "water_y": .3})
    result = run(setup={**SETUP, "board_height_m": 3})
    assert not result["faults"]
    assert result["metrics"][0]["value"] is None


def test_splash_after_contact_is_excluded_and_envelope_is_clipped():
    frames, cal = clip()
    frames += [dict(t=.9, box=[.1, .05, .9, .95], box_confidence=.99, lm=None)]
    result = run(frames, cal)
    assert result["phases"]["entry"] == .8
    assert result["envelopes"][-1]["box"] is None
    assert all(e["box"] is None or e["box"][3] <= .5 for e in result["envelopes"])


def test_no_fabricated_workouts_or_metric_rows_on_missing_data():
    result = analyze_dive(clip()[0], 1000, 1000, SETUP, {})
    feedback = rule_based_feedback(result)
    assert feedback.workouts == []
    assert "unavailable" in feedback.summary
    assert _metric_rows(uuid4(), uuid4(), datetime.now(UTC), result) == []


def test_timestamp_irregularity_and_duplicate_frames():
    frames, cal = clip()
    frames[10]["t"] += .002
    frames += [deepcopy(frames[10])]
    result = run(frames, cal)
    assert result["phases"]["entry"] == .8
    assert result["quality"]["frames"] == 25
