"""Zero-configuration SQLite persistence for local analysis."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _loads(value: str | None, default=None):
    return default if value is None else json.loads(value)


class LocalDatabase:
    """Small single-machine database implementing the production database interface."""

    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def init_schema(self, _workouts) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS divers (
                    id TEXT PRIMARY KEY,
                    height_cm REAL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dives (
                    id TEXT PRIMARY KEY,
                    diver_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pose_frames (
                    dive_id TEXT NOT NULL,
                    frame_idx INTEGER NOT NULL,
                    t REAL NOT NULL,
                    landmarks TEXT,
                    PRIMARY KEY (dive_id, frame_idx)
                );
                CREATE TABLE IF NOT EXISTS dive_metrics (
                    dive_id TEXT NOT NULL,
                    diver_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    value REAL,
                    score REAL
                );
                CREATE TABLE IF NOT EXISTS readiness (
                    id TEXT PRIMARY KEY,
                    diver_id TEXT NOT NULL,
                    readiness_at TEXT NOT NULL,
                    readiness_hr REAL,
                    readiness_br REAL
                );
                CREATE INDEX IF NOT EXISTS local_dives_diver_idx
                    ON dives (diver_id, recorded_at DESC);
                CREATE INDEX IF NOT EXISTS local_metrics_diver_idx
                    ON dive_metrics (diver_id, metric, recorded_at DESC);
                """
            )

    def open(self) -> None:
        self.init_schema([])

    def close(self) -> None:
        return None

    def ping(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1").fetchone()[0] == 1

    def get_or_create_default_diver(self) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, height_cm FROM divers ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                diver_id = uuid4()
                connection.execute(
                    "INSERT INTO divers (id, height_cm, created_at) VALUES (?, NULL, ?)",
                    (str(diver_id), datetime.now(UTC).isoformat()),
                )
                return {"id": diver_id, "height_cm": None}
            return {"id": UUID(row["id"]), "height_cm": row["height_cm"]}

    def get_diver(self, diver_id: UUID) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, height_cm FROM divers WHERE id = ?", (str(diver_id),)
            ).fetchone()
        return None if row is None else {"id": UUID(row["id"]), "height_cm": row["height_cm"]}

    def insert_dive(self, dive: dict, frames: list[dict], metric_rows: list[tuple]) -> None:
        payload = {
            key: value
            for key, value in dive.items()
            if key not in {"id", "diver_id", "recorded_at"}
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO dives (id, diver_id, recorded_at, payload) VALUES (?, ?, ?, ?)",
                (str(dive["id"]), str(dive["diver_id"]), dive["recorded_at"].isoformat(), _json(payload)),
            )
            connection.executemany(
                "INSERT INTO pose_frames (dive_id, frame_idx, t, landmarks) VALUES (?, ?, ?, ?)",
                [
                    (str(dive["id"]), index, frame["t"], None if frame["lm"] is None else _json(frame["lm"]))
                    for index, frame in enumerate(frames)
                ],
            )
            connection.executemany(
                """
                INSERT INTO dive_metrics
                    (dive_id, diver_id, recorded_at, phase, metric, value, score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (str(row[0]), str(row[1]), row[2].isoformat(), row[3], row[4], row[5], row[6])
                    for row in metric_rows
                ],
            )

    @staticmethod
    def _dive(row: sqlite3.Row) -> dict:
        payload = _loads(row["payload"], {})
        return {
            "id": UUID(row["id"]),
            "diver_id": UUID(row["diver_id"]),
            "recorded_at": datetime.fromisoformat(row["recorded_at"]),
            **payload,
        }

    def list_dives(self, diver_id: UUID, limit: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM dives WHERE diver_id = ? ORDER BY recorded_at DESC LIMIT ?",
                (str(diver_id), limit),
            ).fetchall()
        result = []
        for row in rows:
            dive = self._dive(row)
            faults = dive.get("analysis", {}).get("faults", [])
            result.append(
                {
                    key: dive[key]
                    for key in ("id", "diver_id", "recorded_at", "setup", "source", "overall_score", "scores", "feedback_source")
                }
                | {"top_fault": faults[0]["title"] if faults else None}
            )
        return result

    def get_dive_meta(self, dive_id: UUID) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM dives WHERE id = ?", (str(dive_id),)).fetchone()
        if row is None:
            return None
        dive = self._dive(row)
        return {key: dive.get(key) for key in ("id", "setup", "analysis", "video_path", "video_mime")}

    def get_dive(self, dive_id: UUID) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM dives WHERE id = ?", (str(dive_id),)).fetchone()
            if row is None:
                return None
            frames = connection.execute(
                "SELECT t, landmarks FROM pose_frames WHERE dive_id = ? ORDER BY frame_idx",
                (str(dive_id),),
            ).fetchall()
            readiness = connection.execute(
                """
                SELECT readiness_hr, readiness_br FROM readiness
                WHERE diver_id = ? ORDER BY readiness_at DESC LIMIT 1
                """,
                (row["diver_id"],),
            ).fetchone()
        dive = self._dive(row)
        dive["frames"] = [
            {
                "t": frame["t"],
                "landmarks": (
                    None
                    if (landmarks := _loads(frame["landmarks"])) is None
                    else [value for point in landmarks for value in point]
                    if landmarks and isinstance(landmarks[0], list)
                    else landmarks
                ),
            }
            for frame in frames
        ]
        dive["readiness_hr"] = None if readiness is None else readiness["readiness_hr"]
        dive["readiness_br"] = None if readiness is None else readiness["readiness_br"]
        return dive

    def save_vision_review(self, dive_id: UUID, review: dict) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM dives WHERE id = ?", (str(dive_id),)).fetchone()
            if row is None:
                return
            payload = _loads(row["payload"], {})
            payload["vision_review"] = review
            connection.execute("UPDATE dives SET payload = ? WHERE id = ?", (_json(payload), str(dive_id)))

    def delete_dive(self, dive_id: UUID) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM dives WHERE id = ?", (str(dive_id),)).fetchone()
            if row is None:
                return None
            connection.execute("DELETE FROM pose_frames WHERE dive_id = ?", (str(dive_id),))
            connection.execute("DELETE FROM dive_metrics WHERE dive_id = ?", (str(dive_id),))
            connection.execute("DELETE FROM dives WHERE id = ?", (str(dive_id),))
        return {"id": dive_id, "video_path": _loads(row["payload"], {}).get("video_path")}

    def save_readiness(self, diver_id: UUID, at: datetime, heart_rate, breathing_rate) -> dict:
        row = {
            "id": uuid4(),
            "diver_id": diver_id,
            "readiness_hr": heart_rate,
            "readiness_br": breathing_rate,
            "readiness_at": at,
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO readiness VALUES (?, ?, ?, ?, ?)",
                (str(row["id"]), str(diver_id), at.isoformat(), heart_rate, breathing_rate),
            )
        return row

    def latest_readiness(self, diver_id: UUID) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM readiness WHERE diver_id = ? ORDER BY readiness_at DESC LIMIT 1",
                (str(diver_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": UUID(row["id"]),
            "diver_id": UUID(row["diver_id"]),
            "readiness_at": datetime.fromisoformat(row["readiness_at"]),
            "readiness_hr": row["readiness_hr"],
            "readiness_br": row["readiness_br"],
        }

    def progress(self, diver_id: UUID) -> dict:
        dives = self.list_dives(diver_id, 500)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT recorded_at, metric, value, score FROM dive_metrics
                WHERE diver_id = ? ORDER BY recorded_at
                """,
                (str(diver_id),),
            ).fetchall()
        buckets: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            buckets[(row["recorded_at"][:10], row["metric"])].append(row)
        daily = []
        for (day, metric), values in sorted(buckets.items()):
            measured = [row["value"] for row in values if row["value"] is not None]
            scores = [row["score"] for row in values if row["score"] is not None]
            daily.append(
                {
                    "bucket": f"{day}T00:00:00+00:00",
                    "metric": metric,
                    "avg_value": sum(measured) / len(measured) if measured else None,
                    "avg_score": sum(scores) / len(scores) if scores else None,
                    "dives": len({row["recorded_at"] for row in values}),
                }
            )
        return {"dives": list(reversed(dives)), "daily": daily}
