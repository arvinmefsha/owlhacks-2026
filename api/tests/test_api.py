import json

import pytest
from fastapi.testclient import TestClient

import main
from config import Settings
from tests.fakes import FakeCoach, FakeDatabase
from tests.synthetic import HEIGHT, WIDTH, DiveSpec, generate

TUCK_103C = {"position": "tuck", "direction": "forward", "somersaults": 1.5, "apparatus": "springboard", "board_height_m": 1.0}


@pytest.fixture
def client(tmp_path):
    main.app.state.settings = Settings(
        gemini_api_key="test-key", database_url="postgres://user:pw@localhost/test", upload_dir=tmp_path
    )
    main.app.state.db = FakeDatabase()
    main.app.state.coach = FakeCoach()
    return TestClient(main.app)


def new_diver(client, height_cm=170) -> str:
    response = client.post("/divers", json={"name": "  Sam  ", "height_cm": height_cm})
    assert response.status_code == 201
    assert response.json()["name"] == "Sam"
    return response.json()["id"]


def post_dive(client, diver_id, frames, calibration=None, video=b"fake-webm-bytes", mime="video/webm;codecs=vp9"):
    payload = {
        "diver_id": diver_id,
        "setup": TUCK_103C,
        "calibration": calibration or {},
        "video": {"width": WIDTH, "height": HEIGHT, "fps": 60, "source": "upload"},
        "frames": frames,
    }
    files = {"payload": ("payload.json", json.dumps(payload), "application/json")}
    if video is not None:
        files["video"] = ("dive.webm", video, mime)
    return client.post("/dives", files=files)


def test_dive_round_trip(client, tmp_path):
    diver_id = new_diver(client)
    frames, truth = generate(DiveSpec())
    response = post_dive(client, diver_id, frames, {"water_y": truth["water_y"], "board_tip": truth["board_tip"]})
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["scores"]["overall"] >= 8.0
    assert created["feedback_source"] == "rules"

    db = main.app.state.db
    metric_names = {row[4] for row in next(iter(db.metrics.values()))}
    assert {"jump_height_m", "entry_angle_deg", "score_overall", "score_entry", "rotation_deg"} <= metric_names

    dive = client.get(f"/dives/{created['id']}").json()
    assert dive["has_video"] is True
    assert "video_path" not in dive
    assert dive["diver_name"] == "Sam"
    assert len(dive["frames"]["t"]) == len(frames)
    assert all(lm is None or len(lm) == 132 for lm in dive["frames"]["lm"])
    assert dive["feedback"]["workouts"]
    assert dive["analysis"]["phases"]["entry_method"] == "water_line"

    video = client.get(f"/dives/{created['id']}/video")
    assert video.status_code == 200
    assert video.content == b"fake-webm-bytes"
    assert video.headers["content-type"] == "video/webm"

    listed = client.get("/dives", params={"diver_id": diver_id}).json()
    assert [d["id"] for d in listed] == [created["id"]]

    assert client.delete(f"/dives/{created['id']}").status_code == 204
    assert list(tmp_path.iterdir()) == []
    assert client.get(f"/dives/{created['id']}").status_code == 404


def test_dive_without_a_tracked_pose_is_rejected(client, tmp_path):
    diver_id = new_diver(client)
    response = post_dive(client, diver_id, [{"t": i / 30, "lm": None} for i in range(60)])
    assert response.status_code == 422
    assert "pose tracker" in response.json()["detail"]
    assert list(tmp_path.iterdir()) == []


def test_unknown_diver_is_404(client):
    frames, _ = generate(DiveSpec())
    response = post_dive(client, "00000000-0000-4000-8000-000000000000", frames)
    assert response.status_code == 404


def test_unsupported_video_type_is_415(client):
    diver_id = new_diver(client)
    frames, _ = generate(DiveSpec())
    assert post_dive(client, diver_id, frames, video=b"GIF89a", mime="image/gif").status_code == 415


def test_malformed_payload_is_422_without_echoing_input(client):
    diver_id = new_diver(client)
    response = post_dive(client, diver_id, [{"t": 0.0, "lm": [[0.5, 0.5, 0.0, 1.0]] * 5}], video=None)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any("33 landmarks" in err["msg"] for err in detail)
    assert all("input" not in err for err in detail)


def test_dive_without_video(client):
    diver_id = new_diver(client)
    frames, truth = generate(DiveSpec())
    created = post_dive(client, diver_id, frames, {"water_y": truth["water_y"]}, video=None).json()
    assert client.get(f"/dives/{created['id']}").json()["has_video"] is False
    assert client.get(f"/dives/{created['id']}/video").status_code == 404


def test_vision_review_is_saved(client):
    diver_id = new_diver(client)
    frames, truth = generate(DiveSpec())
    created = post_dive(client, diver_id, frames, {"water_y": truth["water_y"]}).json()
    jpeg = b"\xff\xd8\xff\xe0fake-jpeg"
    files = {name: (f"{name}.jpg", jpeg, "image/jpeg") for name in ("takeoff", "apex", "entry")}
    response = client.post(f"/dives/{created['id']}/vision-review", files=files)
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == "Clean line at entry."
    assert [label for label, _, _ in main.app.state.coach.keyframes] == ["takeoff", "top of the flight", "entry"]
    assert client.get(f"/dives/{created['id']}").json()["vision_review"]["notes"][0]["phase"] == "entry"


def test_readiness(client):
    diver_id = new_diver(client)
    assert client.post("/readiness", json={"diver_id": diver_id}).status_code == 422
    response = client.post("/readiness", json={"diver_id": diver_id, "heart_rate": 72, "breathing_rate": 14})
    assert response.status_code == 201
    assert client.get("/readiness", params={"diver_id": diver_id}).json()["readiness_hr"] == 72


def test_workouts_catalog(client):
    workouts = client.get("/workouts").json()
    assert len(workouts) >= 20
    assert {"id", "name", "targets", "description"} <= set(workouts[0])
