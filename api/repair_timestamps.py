"""Audited SQLite timestamp repair. Dry run by default; no pose inference."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import numpy as np
from analysis.pipeline import analyze_dive  # Preserve v1 semantics when repairing legacy timestamps.
from analysis_jobs import _metric_rows
from diving_tracker.timing import read_timeline
from feedback.gemini import rule_based_feedback


def validate_mapping(indices, old_times, new_times, fps):
    if len(indices) != len(new_times) or indices != list(range(len(new_times))):
        raise ValueError("Frame count/order mismatch; refusing an ambiguous mapping.")
    if np.allclose(old_times, new_times, atol=0.0001, rtol=0):
        return False
    if not fps or not np.isfinite(fps) or fps <= 0:
        raise ValueError("Missing legacy FPS; cannot verify frame correspondence.")
    if not np.allclose(old_times, np.arange(len(indices))/fps, atol=0.0001, rtol=0):
        raise ValueError("Unrecognized legacy timestamp sequence; refusing an ambiguous mapping.")
    return True


def repair(db_path: Path, upload_dir: Path, apply=False, dive_id=None):
    if not db_path.is_file():
        raise ValueError("SQLite database does not exist.")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    reports, updates = [], []
    try:
        rows = connection.execute("SELECT * FROM dives" + (" WHERE id = ?" if dive_id else ""), (dive_id,) if dive_id else ()).fetchall()
        for row in rows:
            report = {"id": row["id"]}
            try:
                payload = json.loads(row["payload"])
                if payload.get("analysis", {}).get("version", 1) != 1:
                    raise ValueError("Timestamp repair supports only legacy v1 records; reanalyze this clip instead.")
                if payload.get("analysis", {}).get("model", {}).get("schema") != "coco17-v1":
                    raise ValueError("Not the supported one-row-per-source-frame YOLO schema.")
                path = (upload_dir / payload["video_path"]).resolve()
                if path.parent != upload_dir.resolve() or not path.is_file():
                    raise ValueError("Original video is unavailable.")
                timeline = read_timeline(path)
                stored = connection.execute("SELECT * FROM pose_frames WHERE dive_id=? ORDER BY frame_idx", (row["id"],)).fetchall()
                old = [f["t"] for f in stored]
                if not validate_mapping([f["frame_idx"] for f in stored], old, timeline.times, payload.get("fps")):
                    report["status"] = "already-aligned"
                    reports.append(report)
                    continue
                frames = []
                for f, t in zip(stored, timeline.times):
                    lm = json.loads(f["landmarks"]) if f["landmarks"] else None
                    if lm is not None:
                        lm = np.asarray(lm).reshape(17, 4).tolist()
                    frames.append({"t": float(t), "lm": lm})
                diver = connection.execute("SELECT height_cm FROM divers WHERE id=?", (row["diver_id"],)).fetchone()
                analysis = analyze_dive(frames, payload["video_width"], payload["video_height"], payload["setup"], payload["calibration"], diver[0] if diver else None)
                analysis["model"] = payload["analysis"]["model"]
                with path.open("rb") as video_file:
                    digest = hashlib.file_digest(video_file, "sha256").hexdigest()
                analysis["processing"] = {"timing": timeline.metadata(), "filter": "legacy-causal",
                    "repair": {"video_sha256": digest, "needs_pose_reanalysis": True}}
                # Recompute time-dependent metrics/feedback; discard stale
                # kinematics and visual review from the previous timeline.
                payload.update(analysis=analysis, scores=analysis["scores"], overall_score=analysis["scores"]["overall"],
                    feedback=rule_based_feedback(analysis).model_dump(), feedback_source="rules", vision_review=None)
                metric_rows = _metric_rows(UUID(row["id"]), UUID(row["diver_id"]), datetime.fromisoformat(row["recorded_at"]), analysis)
                updates.append((row, payload, frames, metric_rows))
                report.update(status="repairable", max_shift_seconds=float(np.max(np.abs(np.array(old)-timeline.times))))
            except (ValueError, OSError, KeyError) as error:
                report.update(status="refused", reason=str(error))
            reports.append(report)
        if apply and updates:
            backup_path = db_path.with_name(f"{db_path.stem}.before-timing-repair-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.sqlite3")
            with sqlite3.connect(backup_path) as backup:
                connection.backup(backup)
            # Compare the snapshots after acquiring the write lock, avoiding lost
            # updates if a review was edited while the videos were decoded.
            connection.execute("BEGIN IMMEDIATE")
            for row, payload, frames, metrics in updates:
                current = connection.execute("SELECT payload FROM dives WHERE id=?", (row["id"],)).fetchone()
                if current is None or current[0] != row["payload"]:
                    raise ValueError("A dive changed during repair; no changes applied. Retry the dry run.")
                connection.executemany("UPDATE pose_frames SET t=? WHERE dive_id=? AND frame_idx=?",
                    [(f["t"], row["id"], i) for i, f in enumerate(frames)])
                connection.execute("UPDATE dives SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
                connection.execute("DELETE FROM dive_metrics WHERE dive_id=?", (row["id"],))
                connection.executemany("INSERT INTO dive_metrics VALUES (?,?,?,?,?,?,?)",
                    [(str(m[0]), str(m[1]), m[2].isoformat(), *m[3:]) for m in metrics])
            connection.commit()
            reports.append({"backup": str(backup_path), "updated": len(updates)})
        return reports
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(__file__).parent / "local-data/dives.sqlite3")
    parser.add_argument("--uploads", type=Path, default=Path(__file__).parent / "uploads")
    parser.add_argument("--dive-id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(args.db, args.uploads, args.apply, args.dive_id), indent=2))
