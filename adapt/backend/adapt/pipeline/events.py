"""Events mark, the pipeline computes (C2, spec §2 runtime invariants).

ops.events is append-only; every event marks its entity dirty in ops.dirty_entities. A duplicate event (same type,
entity and dedupe key) is stored once and marks once (T15). Only a pipeline run recomputes, and it clears the marks
it consumed (cleared_run_id), so two events never produce two decisions.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.events (
    event_id VARCHAR PRIMARY KEY, type VARCHAR NOT NULL, entity_type VARCHAR NOT NULL, entity_id VARCHAR NOT NULL,
    dedupe_key VARCHAR NOT NULL UNIQUE, event_at TIMESTAMP NOT NULL, payload JSON
);
CREATE TABLE IF NOT EXISTS ops.dirty_entities (
    entity_type VARCHAR NOT NULL, entity_id VARCHAR NOT NULL, reason VARCHAR NOT NULL, marked_at TIMESTAMP NOT NULL,
    cleared_run_id VARCHAR, PRIMARY KEY (entity_type, entity_id, reason, marked_at)
);
"""


def emit(cur, type_: str, entity_type: str, entity_id: str, at: datetime, payload: dict | None = None,
         dedupe_key: str | None = None) -> bool:
    """Persist an event and mark its entity dirty. Returns False for a duplicate (nothing new is marked)."""
    cur.execute(DDL)
    key = dedupe_key or f"{type_}:{entity_type}:{entity_id}:{at.isoformat()}"
    if cur.execute("SELECT 1 FROM ops.events WHERE dedupe_key = ?", [key]).fetchone():
        return False
    cur.execute("INSERT INTO ops.events VALUES (?, ?, ?, ?, ?, ?, ?)",
                [f"EVT-{uuid.uuid4().hex[:12]}", type_, entity_type, entity_id, key, at,
                 json.dumps(payload or {}, default=float)])
    pending = cur.execute("""SELECT 1 FROM ops.dirty_entities WHERE entity_type = ? AND entity_id = ? AND reason = ?
                             AND cleared_run_id IS NULL""", [entity_type, entity_id, type_]).fetchone()
    if not pending:
        cur.execute("INSERT INTO ops.dirty_entities VALUES (?, ?, ?, ?, NULL)", [entity_type, entity_id, type_, at])
    return True


def dirty(db) -> list[tuple]:
    db.write(lambda cur: cur.execute(DDL))
    return db.query("SELECT entity_type, entity_id, reason, marked_at FROM ops.dirty_entities "
                    "WHERE cleared_run_id IS NULL ORDER BY marked_at")


def clear(cur, run_id: str) -> int:
    cur.execute(DDL)
    n = cur.execute("SELECT count(*) FROM ops.dirty_entities WHERE cleared_run_id IS NULL").fetchone()[0]
    cur.execute("UPDATE ops.dirty_entities SET cleared_run_id = ? WHERE cleared_run_id IS NULL", [run_id])
    return int(n)
