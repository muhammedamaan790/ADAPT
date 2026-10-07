"""Hybrid simulation mirror (Stage 2 ★, spec §9.4, §22.5 mirror table): after a LIVE leg is VERIFIED on the real
platform, the same absolute budget is mirrored into the world service so the simulated data plane reacts.

States (exec.saga_legs.sim_sync_state, history in exec.saga_transitions as MIRROR:<state>):
  NOT_REQUIRED (terminal; mock or non-hybrid leg)
  MIRROR_PENDING -> MIRRORED (terminal)
  MIRROR_PENDING -> MIRROR_FAILED (after 1 h of idempotent absolute-set retries)
  MIRROR_FAILED  -> MIRROR_RESOLVED_MANUALLY (terminal; a manager applies the mirror after a FRESH live read-back
                    confirms the verified state)
A mirror problem is not an execution problem: the saga stays SUCCEEDED, decision.status stays EXECUTED, the verified
Google change is never compensated (T41). While any leg is MIRROR_PENDING or MIRROR_FAILED the world cannot advance
(advance_world raises SimOutOfSync -> HTTP 409 "sim divergence pending") and the UI shows SIM OUT OF SYNC.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from adapt.decide.decisions import DecisionError

MIRROR_WINDOW = timedelta(hours=1)
TRANSITIONS = {("NOT_REQUIRED", "MIRROR_PENDING"), ("MIRROR_PENDING", "MIRRORED"),
               ("MIRROR_PENDING", "MIRROR_FAILED"), ("MIRROR_FAILED", "MIRROR_RESOLVED_MANUALLY")}
DIVERGENT = ("MIRROR_PENDING", "MIRROR_FAILED")

DDL = """
CREATE SCHEMA IF NOT EXISTS exec;
CREATE TABLE IF NOT EXISTS exec.leg_mirror (
    leg_id VARCHAR PRIMARY KEY, budget_id VARCHAR NOT NULL, amount DOUBLE NOT NULL, state VARCHAR NOT NULL,
    pending_since TIMESTAMP NOT NULL, attempts INTEGER NOT NULL, last_error VARCHAR, updated_at TIMESTAMP NOT NULL,
    resolved_by VARCHAR
);
"""


class SimOutOfSync(RuntimeError):
    """409: the world cannot advance while a verified live change is not yet mirrored into the simulation."""


def _to(cur, leg_id: str, to: str, at: datetime, **fields) -> None:
    frm = cur.execute("SELECT sim_sync_state FROM exec.saga_legs WHERE leg_id = ?", [leg_id]).fetchone()[0]
    if frm == to:
        return
    if (frm, to) not in TRANSITIONS:
        raise RuntimeError(f"illegal mirror transition {frm} -> {to}")
    cur.execute("UPDATE exec.saga_legs SET sim_sync_state = ?, updated_at = ? WHERE leg_id = ?", [to, at, leg_id])
    sets = ", ".join(f"{k} = ?" for k in ("state", "updated_at", *fields))
    cur.execute(f"UPDATE exec.leg_mirror SET {sets} WHERE leg_id = ?", [to, at, *fields.values(), leg_id])
    saga_id = cur.execute("SELECT saga_id FROM exec.saga_legs WHERE leg_id = ?", [leg_id]).fetchone()[0]
    cur.execute("INSERT INTO exec.saga_transitions VALUES (?, ?, ?, ?, ?, ?)",
                [saga_id, leg_id, f"MIRROR:{frm}", f"MIRROR:{to}", at, json.dumps(fields, default=str)])


def _attempt(db, leg_id: str, mirror, at: datetime) -> bool:
    budget_id, amount, attempts = db.query("SELECT budget_id, amount, attempts FROM exec.leg_mirror "
                                           "WHERE leg_id = ?", [leg_id])[0]
    try:
        mirror(budget_id, amount, f"mirror:{leg_id}:{attempts + 1}")
    except Exception as exc:  # noqa: BLE001 - every mirror failure is recorded, none is fatal
        error = str(exc)[:300]
        db.write(lambda cur: cur.execute("UPDATE exec.leg_mirror SET attempts = attempts + 1, last_error = ?, "
                                         "updated_at = ? WHERE leg_id = ?", [error, at, leg_id]))
        return False

    def mirrored(cur):
        cur.execute("UPDATE exec.leg_mirror SET attempts = attempts + 1, last_error = NULL WHERE leg_id = ?", [leg_id])
        _to(cur, leg_id, "MIRRORED", at)

    db.write(mirrored)
    return True


def start(db, leg: dict, mirror, at: datetime) -> str:
    """Called by the saga right after a LIVE leg is VERIFIED: MIRROR_PENDING, then one immediate attempt."""

    def work(cur):
        cur.execute(DDL)
        cur.execute("INSERT OR IGNORE INTO exec.leg_mirror VALUES (?, ?, ?, 'NOT_REQUIRED', ?, 0, NULL, ?, NULL)",
                    [leg["leg_id"], leg["budget_id"], float(leg["desired_after"]), at, at])
        _to(cur, leg["leg_id"], "MIRROR_PENDING", at)

    db.write(work)
    return "MIRRORED" if _attempt(db, leg["leg_id"], mirror, at) else "MIRROR_PENDING"


def retry_pending(db, mirror, at: datetime) -> list[dict]:
    """The mirror retrier (idempotent absolute set): retry every MIRROR_PENDING leg; after 1 h -> MIRROR_FAILED."""
    db.write(lambda cur: cur.execute(DDL))
    out = []
    for leg_id, since in db.query("SELECT leg_id, pending_since FROM exec.leg_mirror WHERE state = 'MIRROR_PENDING' "
                                  "ORDER BY leg_id"):
        if mirror is not None and _attempt(db, leg_id, mirror, at):
            out.append({"leg_id": leg_id, "state": "MIRRORED"})
        elif at - since >= MIRROR_WINDOW:
            db.write(lambda cur, lid=leg_id: _to(cur, lid, "MIRROR_FAILED", at))
            out.append({"leg_id": leg_id, "state": "MIRROR_FAILED"})
        else:
            out.append({"leg_id": leg_id, "state": "MIRROR_PENDING"})
    return out


def resolve_manually(db, leg_id: str, live_adapter, mirror, actor: str, role: str, at: datetime) -> dict:
    """/reconcile?target=sim for a MIRROR_FAILED leg: a fresh live read-back must confirm the verified amount, then the
    mirror is applied (once) -> MIRROR_RESOLVED_MANUALLY. The saga and the Google change are never altered."""
    if role not in ("manager", "admin"):
        raise DecisionError("FORBIDDEN", f"role {role} cannot resolve a mirror")
    row = db.query("SELECT budget_id, amount, state FROM exec.leg_mirror WHERE leg_id = ?", [leg_id])
    if not row or row[0][2] != "MIRROR_FAILED":
        raise DecisionError("CONFLICT", f"leg {leg_id} is not MIRROR_FAILED")
    budget_id, amount, _ = row[0]
    observed = float(live_adapter.read(budget_id)["amount_inr"])
    if abs(observed - amount) > live_adapter.tolerance():
        raise DecisionError("CONFLICT", f"live read-back {observed} does not confirm the verified {amount}")
    mirror(budget_id, amount, f"mirror:{leg_id}:manual")
    db.write(lambda cur: _to(cur, leg_id, "MIRROR_RESOLVED_MANUALLY", at, resolved_by=actor))
    return {"leg_id": leg_id, "state": "MIRROR_RESOLVED_MANUALLY", "observed_live": observed}


def divergent(db) -> list[dict]:
    db.write(lambda cur: cur.execute(DDL))
    return [{"leg_id": lid, "budget_id": b, "amount": a, "state": s} for lid, b, a, s in db.query(
        "SELECT leg_id, budget_id, amount, state FROM exec.leg_mirror WHERE state IN (?, ?) ORDER BY leg_id",
        list(DIVERGENT))]


def advance_world(db, world_client, days: int, request_id: str, base_url: str = "") -> dict:
    """The only path ADAPT uses to advance the simulated world (Scenario Lab / scheduler): refused while the
    simulation diverges from a verified live change."""
    pending = divergent(db)
    if pending:
        raise SimOutOfSync(f"sim divergence pending on {len(pending)} leg(s): " +
                           ", ".join(f"{p['budget_id']} ({p['state']})" for p in pending))
    r = world_client.post(f"{base_url.rstrip('/')}/control/advance", json={"days": days},
                          headers={"X-Request-ID": request_id})
    if r.status_code != 200:
        raise RuntimeError(f"world advance HTTP {r.status_code}: {r.text[:200]}")
    return r.json()
