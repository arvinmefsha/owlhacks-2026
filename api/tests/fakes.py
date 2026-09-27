"""In-memory stand-ins for the database and the Gemini coach, for API tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from feedback.gemini import DiveFeedback, VisionNote, VisionReview, rule_based_feedback


class FakeDatabase:
    def __init__(self):
        self.divers: dict[UUID, dict] = {}
        self.dives: dict[UUID, dict] = {}
        self.frames: dict[UUID, list[dict]] = {}
        self.metrics: dict[UUID, list[tuple]] = {}
        self.readiness: list[dict] = []

    def ping(self) -> bool:
        return True

    def get_or_create_default_diver(self) -> dict:
        if self.divers:
            diver = next(iter(self.divers.values()))
        else:
            diver = {"id": uuid4(), "height_cm": None, "created_at": datetime.now(UTC)}
            self.divers[diver["id"]] = diver
        return {"id": diver["id"], "height_cm": diver["height_cm"]}

    def get_diver(self, diver_id: UUID) -> dict | None:
        return self.divers.get(diver_id)

    def insert_dive(self, dive: dict, frames: list[dict], metric_rows: list[tuple]) -> None:
        self.dives[dive["id"]] = {**dive, "vision_review": None}
        self.frames[dive["id"]] = frames
        self.metrics[dive["id"]] = metric_rows

    def list_dives(self, diver_id: UUID, limit: int) -> list[dict]:
        rows = [d for d in self.dives.values() if d["diver_id"] == diver_id]
        return sorted(rows, key=lambda d: d["recorded_at"], reverse=True)[:limit]

    def get_dive_meta(self, dive_id: UUID) -> dict | None:
        dive = self.dives.get(dive_id)
        return None if dive is None else {k: dive[k] for k in ("id", "setup", "analysis", "video_path", "video_mime")}

    def get_dive(self, dive_id: UUID) -> dict | None:
        dive = self.dives.get(dive_id)
        if dive is None:
            return None
        frames = [
            {"t": f["t"], "landmarks": None if f["lm"] is None else [v for point in f["lm"] for v in point]}
            for f in self.frames[dive_id]
        ]
        return {
            **dive,
            "readiness_hr": None,
            "readiness_br": None,
            "frames": frames,
        }

    def save_vision_review(self, dive_id: UUID, review: dict) -> None:
        self.dives[dive_id]["vision_review"] = review

    def delete_dive(self, dive_id: UUID) -> dict | None:
        dive = self.dives.pop(dive_id, None)
        return None if dive is None else {"id": dive_id, "video_path": dive["video_path"]}

    def save_readiness(self, diver_id: UUID, at: datetime, heart_rate, breathing_rate) -> dict:
        row = {"id": uuid4(), "diver_id": diver_id, "readiness_hr": heart_rate, "readiness_br": breathing_rate, "readiness_at": at}
        self.readiness.append(row)
        return row

    def latest_readiness(self, diver_id: UUID) -> dict | None:
        rows = [r for r in self.readiness if r["diver_id"] == diver_id]
        return rows[-1] if rows else None

    def progress(self, diver_id: UUID) -> dict:
        return {"dives": self.list_dives(diver_id, 500), "daily": []}


class FakeCoach:
    def __init__(self):
        self.feedback_calls = 0
        self.keyframes: list[tuple[str, bytes, str]] = []

    def feedback(self, analysis: dict, setup: dict) -> tuple[DiveFeedback, str]:
        self.feedback_calls += 1
        return rule_based_feedback(analysis), "rules"

    def review_keyframes(self, analysis: dict, setup: dict, frames: list[tuple[str, bytes, str]]) -> VisionReview:
        self.keyframes = frames
        return VisionReview(summary="Clean line at entry.", notes=[VisionNote(phase="entry", note="Hands look flat.")])

    def describe_error(self, exc: Exception) -> str:
        return type(exc).__name__
