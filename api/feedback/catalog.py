import json
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "workouts.json"


@lru_cache
def load_catalog() -> tuple[dict, ...]:
    return tuple(json.loads(CATALOG_PATH.read_text()))


def catalog_by_id() -> dict[str, dict]:
    return {w["id"]: w for w in load_catalog()}


def workouts_for(fault_ids: list[str], per_fault: int = 2, limit: int = 5) -> list[tuple[dict, str]]:
    """Workouts for the given faults (most important first) as (workout, fault_id) pairs."""
    picked: dict[str, tuple[dict, str]] = {}
    for fault in fault_ids:
        matches = [w for w in load_catalog() if fault in w["targets"] and w["id"] not in picked]
        for w in matches[:per_fault]:
            picked[w["id"]] = (w, fault)
        if len(picked) >= limit:
            break
    return list(picked.values())[:limit]
