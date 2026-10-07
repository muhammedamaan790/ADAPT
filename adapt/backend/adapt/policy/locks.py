"""Freezes, reservations and the kill switch (C4/C5, spec §4 ops tables, §9.3, §22.9).

- ops.entity_freezes: an entity is frozen iff it has a row with cleared_at IS NULL; the optimizer, pipeline and
  policy all read this one table. EXECUTION_UNCERTAINTY freezes are cleared only by the saga that created them.
- ops.entity_reservations: unique ACTIVE key (entity_type, entity_id); acquired and released inside the single-writer
  transaction; held through UNKNOWN / CONFLICT / COMPENSATING / HUMAN_RESOLUTION_REQUIRED; a RECOVERY reservation
  (manual reconcile) takes over a failed saga's reservation instead of deadlocking on it.
- ops.kill_switch: the global spend kill switch; while active nothing executes.
"""

from __future__ import annotations

import uuid
from datetime import datetime

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.entity_freezes (
    freeze_id VARCHAR PRIMARY KEY, entity_type VARCHAR NOT NULL, entity_id VARCHAR NOT NULL, budget_id VARCHAR,
    reason VARCHAR NOT NULL, source_saga_id VARCHAR, source_leg_id VARCHAR, created_at TIMESTAMP NOT NULL,
    cleared_at TIMESTAMP, cleared_by VARCHAR, clear_reason VARCHAR
);
CREATE TABLE IF NOT EXISTS ops.entity_reservations (
    reservation_id VARCHAR PRIMARY KEY, entity_type VARCHAR NOT NULL, entity_id VARCHAR NOT NULL, budget_id VARCHAR,
    saga_id VARCHAR NOT NULL, priority VARCHAR NOT NULL, acquired_at TIMESTAMP NOT NULL, released_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ops.kill_switch (
    id INTEGER PRIMARY KEY, active BOOLEAN NOT NULL, set_at TIMESTAMP, actor VARCHAR, reason VARCHAR
);
"""


class ReservationConflict(RuntimeError):
    """409: another saga holds a reservation on one of the entities."""


def ensure(cur) -> None:
    cur.execute(DDL)


def kill_switch_active(db) -> bool:
    db.write(ensure)
    row = db.query("SELECT active FROM ops.kill_switch WHERE id = 1")
    return bool(row and row[0][0])


def set_kill_switch(db, active: bool, actor: str, at: datetime, reason: str = "") -> None:
    def work(cur):
        ensure(cur)
        cur.execute("INSERT OR REPLACE INTO ops.kill_switch VALUES (1, ?, ?, ?, ?)", [active, at, actor, reason])
    db.write(work)


def acquire(cur, saga_id: str, entities: list[tuple[str, str, str | None]], at: datetime,
            priority: str = "NORMAL") -> None:
    """entities: (entity_type, entity_id, budget_id). All or nothing (raises inside the caller's transaction)."""
    ensure(cur)
    for etype, eid, bid in entities:
        held = cur.execute("""SELECT saga_id, priority FROM ops.entity_reservations
                              WHERE entity_type = ? AND entity_id = ? AND released_at IS NULL""",
                           [etype, eid]).fetchall()
        others = [h for h in held if h[0] != saga_id]
        if others and priority != "RECOVERY":
            raise ReservationConflict(f"{etype} {eid} is reserved by saga {others[0][0]}")
        for other, _ in others:  # recovery takes over (the original is suspended, not deadlocked)
            cur.execute("""UPDATE ops.entity_reservations SET released_at = ? WHERE saga_id = ? AND entity_id = ?
                           AND released_at IS NULL""", [at, other, eid])
        if not any(h[0] == saga_id for h in held):
            cur.execute("INSERT INTO ops.entity_reservations VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                        [f"RSV-{uuid.uuid4().hex[:12]}", etype, eid, bid, saga_id, priority, at])


def release(cur, saga_id: str, at: datetime) -> None:
    ensure(cur)
    cur.execute("UPDATE ops.entity_reservations SET released_at = ? WHERE saga_id = ? AND released_at IS NULL",
                [at, saga_id])


def freeze(cur, entities: list[tuple[str, str, str | None]], reason: str, saga_id: str | None, leg_id: str | None,
           at: datetime) -> None:
    ensure(cur)
    for etype, eid, bid in entities:
        exists = cur.execute("""SELECT 1 FROM ops.entity_freezes WHERE entity_type = ? AND entity_id = ? AND reason = ?
                                AND source_saga_id IS NOT DISTINCT FROM ? AND cleared_at IS NULL""",
                             [etype, eid, reason, saga_id]).fetchone()
        if not exists:
            cur.execute("INSERT INTO ops.entity_freezes VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
                        [f"FRZ-{uuid.uuid4().hex[:12]}", etype, eid, bid, reason, saga_id, leg_id, at])


def clear_saga_freezes(cur, saga_id: str, at: datetime, by: str, why: str) -> None:
    """A terminal saga clears ONLY the EXECUTION_UNCERTAINTY freezes it created (spec §22.5)."""
    ensure(cur)
    cur.execute("""UPDATE ops.entity_freezes SET cleared_at = ?, cleared_by = ?, clear_reason = ?
                   WHERE source_saga_id = ? AND reason = 'EXECUTION_UNCERTAINTY' AND cleared_at IS NULL""",
                [at, by, why, saga_id])


def active_freezes(db) -> list[tuple]:
    db.write(ensure)
    return db.query("SELECT entity_type, entity_id, budget_id, reason, source_saga_id FROM ops.entity_freezes "
                    "WHERE cleared_at IS NULL")
