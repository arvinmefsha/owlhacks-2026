import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import main
import pytest
from config import Settings
from fastapi.testclient import TestClient

from tests.fakes import FakeCoach, FakeDatabase

TUCK_103C = {
    "position": "tuck",
    "direction": "forward",
    "somersaults": 1.5,
    "apparatus": "springboard",
    "board_height_m": 1.0,
}


class FakeAnalysisJobs:
    def __init__(self):
        self.jobs: dict[UUID, dict] = {}
        self.submissions: list[tuple[dict, object, str]] = []

    def submit(self, payload, video_path, mime):
        job_id = uuid4()
        self.submissions.append((payload, video_path, mime))
        self.jobs[job_id] = {
            "id": str(job_id),
            "status": "queued",
            "stage": "Waiting for test worker",
            "progress": 0.0,
            "frames_processed": 0,
            "total_frames": None,
            "dive_id": None,
            "error": None,
            "profile": payload["profile"],
        }
        return job_id

    def get(self, job_id):
        return self.jobs.get(job_id)

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if job is None or job["status"] != "queued":
            return False
        job.update(status="cancelled", stage="Cancelled")
        return True


@pytest.fixture
def client(tmp_path):
    main.app.state.settings = Settings(
        gemini_api_key="test-key", database_url="postgres://user:pw@localhost/test", upload_dir=tmp_path
    )
    main.app.state.db = FakeDatabase()
    main.app.state.coach = FakeCoach()
    main.app.state.analysis_jobs = FakeAnalysisJobs()
    return TestClient(main.app)


def post_job(client, *, video=b"fake-video", mime="video/mp4", calibration=None, profile="quality"):
    payload = {
        "setup": TUCK_103C,
        "calibration": calibration
        or {"board_tip": {"x": 0.3, "y": 0.2}, "water_y": 0.8, "roi": None},
        "source": "upload",
        "profile": profile,
    }
    return client.post(
        "/analysis/jobs",
        files={
            "payload": ("payload.json", json.dumps(payload), "application/json"),
            "video": ("dive.mp4", video, mime),
        },
    )


def test_analysis_job_upload_and_status(client, tmp_path):
    response = post_job(client)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]

    manager = main.app.state.analysis_jobs
    payload, video_path, mime = manager.submissions[0]
    assert payload["profile"] == "quality"
    assert payload["diver_id"] == str(main.app.state.db.get_or_create_default_diver()["id"])
    assert payload["calibration"]["water_y"] == 0.8
    assert mime == "video/mp4"
    assert video_path.parent == tmp_path
    assert video_path.read_bytes() == b"fake-video"

    status = client.get(f"/analysis/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "queued"
    assert client.delete(f"/analysis/jobs/{job_id}").status_code == 204
    assert client.get(f"/analysis/jobs/{job_id}").json()["status"] == "cancelled"


def test_analysis_job_requires_calibration(client):
    response = post_job(client, calibration={"board_tip": None, "water_y": None, "roi": None})
    assert response.status_code == 422
    assert "water surface" in response.json()["detail"]
    assert main.app.state.analysis_jobs.submissions == []


def test_unsupported_video_type_is_415(client):
    assert post_job(client, video=b"GIF89a", mime="image/gif").status_code == 415


def test_malformed_payload_is_422_without_echoing_input(client):
    response = client.post(
        "/analysis/jobs",
        files={
            "payload": ("payload.json", b"{}", "application/json"),
            "video": ("dive.mp4", b"video", "video/mp4"),
        },
    )
    assert response.status_code == 422
    assert all("input" not in error for error in response.json()["detail"])


def test_missing_and_finished_jobs(client):
    missing = uuid4()
    assert client.get(f"/analysis/jobs/{missing}").status_code == 404
    assert client.delete(f"/analysis/jobs/{missing}").status_code == 409


def test_saved_dive_review_and_video(client, tmp_path):
    diver_id = main.app.state.db.get_or_create_default_diver()["id"]
    dive_id = uuid4()
    filename = f"{dive_id}.mp4"
    (tmp_path / filename).write_bytes(b"stored-video")
    db = main.app.state.db
    db.insert_dive(
        {
            "id": dive_id,
            "diver_id": diver_id,
            "recorded_at": datetime.now(UTC),
            "setup": TUCK_103C,
            "calibration": {"board_tip": {"x": 0.3, "y": 0.2}, "water_y": 0.8},
            "source": "upload",
            "video_path": filename,
            "video_mime": "video/mp4",
            "video_width": 1280,
            "video_height": 720,
            "fps": 60.0,
            "overall_score": 8.0,
            "scores": {"overall": 8.0},
            "analysis": {"scores": {"overall": 8.0}},
            "feedback": {"summary": "Good dive", "faults": [], "cues": [], "workouts": []},
            "feedback_source": "pending",
        },
        [{"t": 0.0, "lm": [[0.5, 0.5, 0.0, 0.9]] * 17}],
        [],
    )

    dive = client.get(f"/dives/{dive_id}")
    assert dive.status_code == 200
    assert len(dive.json()["frames"]["lm"][0]) == 68
    pending = client.get(f"/dives/{dive_id}/feedback")
    assert pending.json()["feedback_source"] == "pending"
    assert "frames" not in pending.json()
    db.save_feedback(dive_id, {"summary": "Gemini finished", "faults": [], "cues": [], "workouts": []}, "gemini")
    finished = client.get(f"/dives/{dive_id}/feedback")
    assert finished.json()["feedback_source"] == "gemini"
    assert finished.json()["feedback"]["summary"] == "Gemini finished"
    video = client.get(f"/dives/{dive_id}/video")
    assert video.status_code == 200
    assert video.content == b"stored-video"

    jpeg = b"\xff\xd8\xff\xe0fake-jpeg"
    files = {name: (f"{name}.jpg", jpeg, "image/jpeg") for name in ("takeoff", "apex", "entry")}
    response = client.post(f"/dives/{dive_id}/vision-review", files=files)
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == "Clean line at entry."
    assert [label for label, _, _ in main.app.state.coach.keyframes] == ["takeoff", "top of the flight", "entry"]

    assert client.delete(f"/dives/{dive_id}").status_code == 204
    assert not (tmp_path / filename).exists()
    assert client.get(f"/dives/{dive_id}/feedback").status_code == 404


def test_readiness(client):
    assert client.post("/readiness", json={}).status_code == 422
    response = client.post("/readiness", json={"heart_rate": 72, "breathing_rate": 14})
    assert response.status_code == 201
    assert client.get("/readiness").json()["readiness_hr"] == 72


def test_delete_all_dives_removes_videos_and_readiness(client, tmp_path):
    diver_id = main.app.state.db.get_or_create_default_diver()["id"]
    filename = "stored.mp4"
    (tmp_path / filename).write_bytes(b"stored-video")
    dive_id = uuid4()
    main.app.state.db.insert_dive(
        {
            "id": dive_id,
            "diver_id": diver_id,
            "recorded_at": datetime.now(UTC),
            "setup": TUCK_103C,
            "calibration": {},
            "source": "upload",
            "video_path": filename,
            "video_mime": "video/mp4",
            "analysis": {"method": "macro-envelope-v1"},
            "overall_score": 9.0,
            "scores": {"overall": 9.0},
            "feedback": {},
            "feedback_source": "rules",
        },
        [],
        [],
    )
    client.post("/readiness", json={"heart_rate": 72})
    assert client.delete("/dives").status_code == 204
    assert not (tmp_path / filename).exists()
    assert client.get("/progress").json()["dives"] == []


def test_workouts_catalog(client):
    workouts = client.get("/workouts").json()
    assert len(workouts) >= 20
    assert {"id", "name", "targets", "description"} <= set(workouts[0])
