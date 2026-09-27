"""Tiger Data (TimescaleDB) access with plain SQL through a psycopg connection pool."""

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

SQL_DIR = Path(__file__).resolve().parent
# Dives less than this far apart belong to the same training session.
SESSION_GAP = timedelta(hours=3)
DIVE_SUMMARY_COLUMNS = (
    "id, diver_id, recorded_at, setup, source, overall_score, scores, feedback_source, "
    "analysis ->> 'method' AS analysis_method, analysis, "
    "analysis -> 'faults' -> 0 ->> 'title' AS top_fault"
)


def _markers(analysis: dict) -> dict:
    metrics = analysis.get("metrics", [])
    return {
        "strengths": [m["label"] for m in metrics if m.get("status") == "within_target"],
        "focus": [f["title"] for f in analysis.get("faults", [])],
        "observations": sum(m.get("value") is not None for m in metrics),
    }


def split_statements(sql: str) -> list[str]:
    """Split a .sql file on semicolons that end a line (our files never nest them)."""
    without_comments = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in re.split(r";\s*(?:\n|$)", without_comments) if s.strip()]


class Database:
    def __init__(self, url: str):
        self._url = url
        self.pool = ConnectionPool(url, min_size=1, max_size=8, open=False, kwargs={"row_factory": dict_row})

    # Setup

    def open(self) -> None:
        self.pool.open(wait=True, timeout=20)

    def close(self) -> None:
        self.pool.close()

    def init_schema(self, workouts: Iterable[dict]) -> None:
        with psycopg.connect(self._url, autocommit=True) as conn:
            for name in ("schema.sql", "timescale.sql"):
                for statement in split_statements((SQL_DIR / name).read_text()):
                    conn.execute(statement)
            # Old pose frames are only replayed, never updated, so compress them after a week.
            # Compression settings can't be re-applied once chunks are compressed, hence the check.
            row = conn.execute(
                "SELECT compression_enabled FROM timescaledb_information.hypertables WHERE hypertable_name = 'pose_frames'"
            ).fetchone()
            if not row[0]:
                conn.execute(
                    "ALTER TABLE pose_frames SET (timescaledb.compress, "
                    "timescaledb.compress_segmentby = 'dive_id', timescaledb.compress_orderby = 'ts')"
                )
            conn.execute("SELECT add_compression_policy('pose_frames', INTERVAL '7 days', if_not_exists => TRUE)")
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO workouts (id, name, targets, description, sets, reps, equipment)
                    VALUES (%(id)s, %(name)s, %(targets)s, %(description)s, %(sets)s, %(reps)s, %(equipment)s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name, targets = EXCLUDED.targets, description = EXCLUDED.description,
                        sets = EXCLUDED.sets, reps = EXCLUDED.reps, equipment = EXCLUDED.equipment
                    """,
                    list(workouts),
                )

    def ping(self) -> bool:
        with self.pool.connection() as conn:
            return conn.execute("SELECT 1 AS ok").fetchone()["ok"] == 1

    # Divers and sessions

    def get_or_create_default_diver(self) -> dict:
        """Return the app's single internal profile, creating it on first use."""
        with self.pool.connection() as conn:
            conn.execute("LOCK TABLE divers IN SHARE ROW EXCLUSIVE MODE")
            row = conn.execute(
                "SELECT id, height_cm FROM divers ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is not None:
                return row
            return conn.execute(
                "INSERT INTO divers (name) VALUES ('Diver') RETURNING id, height_cm"
            ).fetchone()

    def get_diver(self, diver_id: UUID) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute("SELECT id, name, height_cm FROM divers WHERE id = %s", (diver_id,)).fetchone()

    def _session_for(self, conn: psycopg.Connection, diver_id: UUID, at: datetime) -> UUID:
        row = conn.execute(
            """
            SELECT s.id FROM sessions s
            LEFT JOIN LATERAL (SELECT max(recorded_at) AS last_dive FROM dives d WHERE d.session_id = s.id) d ON TRUE
            WHERE s.diver_id = %s
              AND greatest(s.started_at, s.readiness_at, d.last_dive) > %s
            ORDER BY s.started_at DESC
            LIMIT 1
            """,
            (diver_id, at - SESSION_GAP),
        ).fetchone()
        if row:
            return row["id"]
        return conn.execute(
            "INSERT INTO sessions (diver_id, started_at) VALUES (%s, %s) RETURNING id", (diver_id, at)
        ).fetchone()["id"]

    def save_readiness(self, diver_id: UUID, at: datetime, heart_rate: float | None, breathing_rate: float | None) -> dict:
        with self.pool.connection() as conn:
            session_id = self._session_for(conn, diver_id, at)
            return conn.execute(
                """
                UPDATE sessions SET readiness_hr = %s, readiness_br = %s, readiness_at = %s
                WHERE id = %s RETURNING id, diver_id, started_at, readiness_hr, readiness_br, readiness_at
                """,
                (heart_rate, breathing_rate, at, session_id),
            ).fetchone()

    def latest_readiness(self, diver_id: UUID) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute(
                """
                SELECT readiness_hr, readiness_br, readiness_at FROM sessions
                WHERE diver_id = %s AND readiness_at IS NOT NULL ORDER BY readiness_at DESC LIMIT 1
                """,
                (diver_id,),
            ).fetchone()

    # Dives

    def insert_dive(self, dive: dict, frames: list[dict], metric_rows: list[tuple]) -> None:
        """Insert a dive with its pose frames and metrics in one transaction."""
        with self.pool.connection() as conn:
            session_id = self._session_for(conn, dive["diver_id"], dive["recorded_at"])
            conn.execute(
                """
                INSERT INTO dives (id, session_id, diver_id, recorded_at, setup, calibration, source,
                                   video_path, video_mime, video_width, video_height, fps,
                                   overall_score, scores, analysis, feedback, feedback_source)
                VALUES (%(id)s, %(session_id)s, %(diver_id)s, %(recorded_at)s, %(setup)s, %(calibration)s, %(source)s,
                        %(video_path)s, %(video_mime)s, %(video_width)s, %(video_height)s, %(fps)s,
                        %(overall_score)s, %(scores)s, %(analysis)s, %(feedback)s, %(feedback_source)s)
                """,
                {
                    **dive,
                    "session_id": session_id,
                    **{k: Jsonb(dive[k]) for k in ("setup", "calibration", "scores", "analysis", "feedback")},
                },
            )
            with conn.cursor() as cur:
                with cur.copy("COPY pose_frames (dive_id, ts, frame_idx, t, landmarks) FROM STDIN") as copy:
                    copy.set_types(["uuid", "timestamptz", "int4", "float4", "float4[]"])
                    for i, frame in enumerate(frames):
                        flat = None if frame["lm"] is None else [round(v, 5) for point in frame["lm"] for v in point]
                        copy.write_row(
                            (dive["id"], dive["recorded_at"] + timedelta(seconds=frame["t"]), i, frame["t"], flat)
                        )
                cur.executemany(
                    """
                    INSERT INTO dive_metrics (dive_id, diver_id, recorded_at, phase, metric, value, score)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    metric_rows,
                )

    def list_dives(self, diver_id: UUID, limit: int) -> list[dict]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {DIVE_SUMMARY_COLUMNS} FROM dives WHERE diver_id = %s ORDER BY recorded_at DESC LIMIT %s",
                (diver_id, limit),
            ).fetchall()
        return [{key: value for key, value in row.items() if key != "analysis"} | {"markers": _markers(row["analysis"])} for row in rows]

    def get_dive_meta(self, dive_id: UUID) -> dict | None:
        """The dive row without pose frames or the diver join."""
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT id, setup, analysis, video_path, video_mime FROM dives WHERE id = %s", (dive_id,)
            ).fetchone()

    def get_dive(self, dive_id: UUID) -> dict | None:
        with self.pool.connection() as conn:
            dive = conn.execute(
                """
                SELECT d.id, d.diver_id, d.recorded_at, d.setup, d.calibration,
                       d.source, d.video_path, d.video_mime, d.video_width, d.video_height, d.fps,
                       d.overall_score, d.scores, d.analysis, d.feedback, d.feedback_source, d.vision_review,
                       s.readiness_hr, s.readiness_br
                FROM dives d
                LEFT JOIN sessions s ON s.id = d.session_id
                WHERE d.id = %s
                """,
                (dive_id,),
            ).fetchone()
            if dive is None:
                return None
            # The time bounds let TimescaleDB skip chunks that can't hold this dive's frames.
            dive["frames"] = conn.execute(
                """
                SELECT t, landmarks FROM pose_frames
                WHERE dive_id = %s AND ts >= %s AND ts < %s + INTERVAL '1 hour'
                ORDER BY frame_idx
                """,
                (dive_id, dive["recorded_at"], dive["recorded_at"]),
            ).fetchall()
            return dive

    def save_vision_review(self, dive_id: UUID, review: dict) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE dives SET vision_review = %s WHERE id = %s", (Jsonb(review), dive_id))

    def delete_dive(self, dive_id: UUID) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute("DELETE FROM dives WHERE id = %s RETURNING id, video_path", (dive_id,)).fetchone()

    def delete_all_dives(self, diver_id: UUID) -> list[dict]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT video_path FROM dives WHERE diver_id = %s", (diver_id,)
            ).fetchall()
            conn.execute("DELETE FROM dive_metrics WHERE diver_id = %s", (diver_id,))
            conn.execute("DELETE FROM readiness WHERE diver_id = %s", (diver_id,))
            conn.execute("DELETE FROM dives WHERE diver_id = %s", (diver_id,))
        return rows

    # Progress

    def progress(self, diver_id: UUID) -> dict:
        with self.pool.connection() as conn:
            dives = conn.execute(
                "SELECT id, recorded_at, setup, overall_score, scores, analysis ->> 'method' AS analysis_method, analysis FROM dives "
                "WHERE diver_id = %s ORDER BY recorded_at LIMIT 500",
                (diver_id,),
            ).fetchall()
            daily = conn.execute(
                """
                SELECT bucket, metric, avg_value, avg_score, dives FROM daily_metric_avg
                WHERE diver_id = %s AND bucket > now() - INTERVAL '90 days'
                ORDER BY bucket, metric
                """,
                (diver_id,),
            ).fetchall()
        return {"dives": [{key: value for key, value in row.items() if key != "analysis"} | {"markers": _markers(row["analysis"])} for row in dives], "daily": daily}
