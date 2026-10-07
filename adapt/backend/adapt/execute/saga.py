"""Execution saga (C5, spec §9.3, §22.5). Cross-platform atomicity is impossible, so: ordered legs, verification,
compensation, explicit uncertainty.

Per leg (risk-reducing legs first): PLANNED -> pre-read (observed != expected before -> CONFLICT, abort) ->
PREREAD_OK -> SENT (absolute setter; retries only as absolute sets, max 3) -> on timeout / 5xx / lost response a
read-after-write first: desired state present -> VERIFIED, never a second mutation -> otherwise verify by polling
read-back (a separate read call) -> VERIFIED, or UNKNOWN (never assumed success) / FAILED.
Every transition is persisted (write-ahead) before the next external call, so a crash is recoverable (recover()).

Saga: PENDING -> EXECUTING -> SUCCEEDED | PARTIAL -> (ACCEPTED_PARTIAL if every completed leg is risk-reducing |
COMPENSATING -> COMPENSATED | COMPENSATION_FAILED -> HUMAN_RESOLUTION_REQUIRED -> RESOLVED_MANUALLY) | BLOCKED.
While a leg is UNKNOWN / CONFLICT or the saga is PARTIAL-before-resolution / COMPENSATING / COMPENSATION_FAILED /
HUMAN_RESOLUTION_REQUIRED, every affected budget and campaign carries an EXECUTION_UNCERTAINTY freeze; reservations
are held until a terminal state, which releases them and clears only that saga's freezes. decision.status is derived
from the saga state (decide.decisions.status), never written here.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from datetime import datetime

from adapt.decide.decisions import DecisionError, check_fresh, expire, get_decision
from adapt.execute.adapters import AdapterUnavailable, ReadError, platforms_config, redact
from adapt.pipeline.events import emit
from adapt.policy import locks

TERMINAL = {"SUCCEEDED", "ACCEPTED_PARTIAL", "COMPENSATED", "BLOCKED", "RESOLVED_MANUALLY"}
FREEZING = {"PARTIAL", "COMPENSATING", "COMPENSATION_FAILED", "HUMAN_RESOLUTION_REQUIRED"}
SAGA_TRANSITIONS = {
    ("PENDING", "EXECUTING"), ("EXECUTING", "SUCCEEDED"), ("EXECUTING", "PARTIAL"), ("EXECUTING", "BLOCKED"),
    ("PARTIAL", "ACCEPTED_PARTIAL"), ("PARTIAL", "COMPENSATING"), ("PARTIAL", "SUCCEEDED"), ("PARTIAL", "BLOCKED"),
    ("COMPENSATING", "COMPENSATED"), ("COMPENSATING", "COMPENSATION_FAILED"),
    ("COMPENSATION_FAILED", "HUMAN_RESOLUTION_REQUIRED"), ("HUMAN_RESOLUTION_REQUIRED", "RESOLVED_MANUALLY"),
}
LEG_TRANSITIONS = {
    ("PLANNED", "PREREAD_OK"), ("PLANNED", "CONFLICT"), ("CONFLICT", "RECONCILED_VERIFIED"), ("CONFLICT", "ABANDONED"),
    ("PREREAD_OK", "SENT"), ("SENT", "SENT"), ("SENT", "VERIFIED"), ("SENT", "UNKNOWN"), ("SENT", "FAILED"),
    ("UNKNOWN", "VERIFIED"), ("UNKNOWN", "FAILED"),
}
LEG_DONE = {"VERIFIED", "FAILED", "RECONCILED_VERIFIED", "ABANDONED"}

DDL = """
CREATE SCHEMA IF NOT EXISTS exec;
CREATE TABLE IF NOT EXISTS exec.sagas (
    saga_id VARCHAR PRIMARY KEY, decision_id VARCHAR NOT NULL, kind VARCHAR NOT NULL, state VARCHAR NOT NULL,
    actor VARCHAR, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, final_resolution VARCHAR,
    resolved_by VARCHAR, reason VARCHAR, UNIQUE (decision_id, kind)
);
CREATE TABLE IF NOT EXISTS exec.saga_legs (
    leg_id VARCHAR PRIMARY KEY, saga_id VARCHAR NOT NULL, decision_id VARCHAR NOT NULL, kind VARCHAR NOT NULL,
    seq INTEGER NOT NULL, platform VARCHAR NOT NULL, mode VARCHAR, budget_id VARCHAR NOT NULL, campaign_ids JSON,
    expected_before DOUBLE, desired_after DOUBLE, observed_before DOUBLE, observed_after DOUBLE,
    state VARCHAR NOT NULL, attempts INTEGER NOT NULL, request JSON, response JSON, error VARCHAR,
    external_state VARCHAR, sim_sync_state VARCHAR, updated_at TIMESTAMP NOT NULL, UNIQUE (decision_id, kind, seq)
);
CREATE TABLE IF NOT EXISTS exec.saga_transitions (
    saga_id VARCHAR NOT NULL, leg_id VARCHAR, from_state VARCHAR, to_state VARCHAR NOT NULL, at_ts TIMESTAMP NOT NULL,
    detail JSON
);
"""


class AlreadyExecuting(DecisionError):
    def __init__(self, message: str):
        super().__init__("CONFLICT", message)


def ensure(db) -> None:
    def work(cur):
        cur.execute(DDL)
        locks.ensure(cur)
    db.write(work)


# ---- persisted transitions (write-ahead) -----------------------------------------------------------------------------
def _saga_to(cur, saga_id: str, to: str, at: datetime, detail: dict | None = None) -> None:
    frm = cur.execute("SELECT state FROM exec.sagas WHERE saga_id = ?", [saga_id]).fetchone()[0]
    if frm == to:
        return
    if (frm, to) not in SAGA_TRANSITIONS:
        raise RuntimeError(f"illegal saga transition {frm} -> {to}")
    cur.execute("UPDATE exec.sagas SET state = ?, updated_at = ? WHERE saga_id = ?", [to, at, saga_id])
    cur.execute("INSERT INTO exec.saga_transitions VALUES (?, NULL, ?, ?, ?, ?)",
                [saga_id, frm, to, at, json.dumps(detail or {}, default=float)])


def _leg_to(cur, leg_id: str, to: str, at: datetime, **fields) -> None:
    frm, saga_id = cur.execute("SELECT state, saga_id FROM exec.saga_legs WHERE leg_id = ?", [leg_id]).fetchone()
    if (frm, to) not in LEG_TRANSITIONS:
        raise RuntimeError(f"illegal leg transition {frm} -> {to}")
    sets = ["state = ?", "updated_at = ?"]
    vals: list = [to, at]
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(json.dumps(v, default=float) if k in ("request", "response") else v)
    cur.execute(f"UPDATE exec.saga_legs SET {', '.join(sets)} WHERE leg_id = ?", [*vals, leg_id])
    cur.execute("INSERT INTO exec.saga_transitions VALUES (?, ?, ?, ?, ?, ?)",
                [saga_id, leg_id, frm, to, at, json.dumps({k: v for k, v in fields.items()
                                                           if k not in ("request", "response")}, default=float)])


def _leg(db, leg_id: str) -> dict:
    cols = ["leg_id", "saga_id", "decision_id", "kind", "seq", "platform", "mode", "budget_id", "campaign_ids",
            "expected_before", "desired_after", "observed_before", "observed_after", "state", "attempts", "error"]
    row = db.query(f"SELECT {', '.join(cols)} FROM exec.saga_legs WHERE leg_id = ?", [leg_id])[0]
    d = dict(zip(cols, row, strict=True))
    d["campaign_ids"] = json.loads(d["campaign_ids"] or "[]")
    return d


def _legs(db, saga_id: str) -> list[dict]:
    return [_leg(db, lid) for (lid,) in db.query("SELECT leg_id FROM exec.saga_legs WHERE saga_id = ? ORDER BY seq",
                                                 [saga_id])]


def _entities(leg: dict) -> list[tuple[str, str, str]]:
    return [("budget", leg["budget_id"], leg["budget_id"])] + \
           [("campaign", c, leg["budget_id"]) for c in leg["campaign_ids"] if c != leg["budget_id"]]


def _freeze_leg(db, leg: dict, at: datetime) -> None:
    db.write(lambda cur: locks.freeze(cur, _entities(leg), "EXECUTION_UNCERTAINTY", leg["saga_id"], leg["leg_id"], at))


# ---- one leg ---------------------------------------------------------------------------------------------------------
class Runner:
    def __init__(self, db, adapters: dict, now: datetime, sleep: Callable[[float], None] = time.sleep):
        self.db, self.adapters, self.now, self.sleep = db, adapters, now, sleep
        cfg = platforms_config()
        self.max_attempts = int(cfg["retry"]["max_attempts"])
        self.backoff = float(cfg["retry"]["backoff_s"])
        self.polls, self.interval = int(cfg["verify"]["polls"]), float(cfg["verify"]["interval_s"])

    def _close(self, a: float | None, b: float | None, adapter) -> bool:
        return a is not None and b is not None and abs(a - b) <= adapter.tolerance()

    def _read(self, adapter, leg) -> float | None:
        try:
            return float(adapter.read(leg["budget_id"])["amount_inr"])
        except (ReadError, KeyError, ValueError):
            return None

    def run_leg(self, leg: dict) -> str:
        """Drive one leg from PLANNED (or a recovered SENT) to VERIFIED / FAILED / UNKNOWN / CONFLICT."""
        db, at = self.db, self.now
        adapter = self.adapters.get(leg["platform"])
        if leg["state"] == "PLANNED":
            if adapter is None:
                raise AdapterUnavailable(f"no adapter for {leg['platform']}")
            observed = self._read(adapter, leg)  # LiveNotBuilt raises AdapterUnavailable: the leg is BLOCKED
            if observed is None:
                raise AdapterUnavailable(f"pre-read failed for {leg['platform']} budget {leg['budget_id']}")
            if not self._close(observed, leg["expected_before"], adapter):
                db.write(lambda cur: _leg_to(cur, leg["leg_id"], "CONFLICT", at, observed_before=observed,
                                             error="external state differs from the decision's expected before"))
                _freeze_leg(db, leg, at)
                return "CONFLICT"
            db.write(lambda cur: _leg_to(cur, leg["leg_id"], "PREREAD_OK", at, observed_before=observed))
            leg["state"], leg["observed_before"] = "PREREAD_OK", observed
        attempts = int(leg["attempts"])
        while True:
            attempts += 1
            rid = f"{leg['leg_id']}:a{attempts}"
            db.write(lambda cur, n=attempts: _leg_to(cur, leg["leg_id"], "SENT", at, attempts=n,
                                                      external_state="SENT"))
            res = adapter.set_budget(leg["budget_id"], leg["desired_after"], request_id=rid)
            db.write(lambda cur, r=res: cur.execute(
                "UPDATE exec.saga_legs SET request = ?, response = ? WHERE leg_id = ?",
                [json.dumps(redact(r.request)), json.dumps(redact(r.response | {"http_status": r.http_status})),
                 leg["leg_id"]]))
            if res.ok:
                return self._verify(adapter, leg)
            if res.transient or res.ambiguous:
                observed = self._read(adapter, leg)  # read-after-write before any retry: never double-mutate
                if self._close(observed, leg["desired_after"], adapter):
                    db.write(lambda cur, o=observed: _leg_to(cur, leg["leg_id"], "VERIFIED", at, observed_after=o,
                                                             external_state="VERIFIED", error=None))
                    return "VERIFIED"
                if observed is None:
                    db.write(lambda cur, r=res: _leg_to(cur, leg["leg_id"], "UNKNOWN", at, external_state="UNKNOWN",
                                                        error=f"{r.code}; read-after-write failed"))
                    _freeze_leg(db, leg, at)
                    return "UNKNOWN"
                if attempts < self.max_attempts:
                    self.sleep(self.backoff * 2 ** (attempts - 1))
                    continue
            db.write(lambda cur, r=res: _leg_to(cur, leg["leg_id"], "FAILED", at, external_state="FAILED",
                                                error=f"HTTP {r.http_status} {r.code}"))
            return "FAILED"

    def _verify(self, adapter, leg) -> str:
        observed = None
        for k in range(self.polls):
            observed = self._read(adapter, leg)
            if self._close(observed, leg["desired_after"], adapter):
                self.db.write(lambda cur, o=observed: _leg_to(cur, leg["leg_id"], "VERIFIED", self.now,
                                                              observed_after=o, external_state="VERIFIED"))
                return "VERIFIED"
            if k < self.polls - 1:
                self.sleep(self.interval)
        self.db.write(lambda cur: _leg_to(cur, leg["leg_id"], "UNKNOWN", self.now, observed_after=observed,
                                          external_state="UNKNOWN",
                                          error="sent, but read-back does not show the desired state"))
        _freeze_leg(self.db, leg, self.now)
        return "UNKNOWN"


# ---- saga ------------------------------------------------------------------------------------------------------------
def _create(db, decision_id: str, kind: str, legs: list[dict], actor: str, at: datetime, adapters: dict,
            priority: str = "NORMAL") -> str:
    saga_id = f"SAGA-{uuid.uuid4().hex[:12]}"

    def work(cur):
        cur.execute(DDL)
        if cur.execute("SELECT 1 FROM exec.sagas WHERE decision_id = ? AND kind = ?", [decision_id, kind]).fetchone():
            raise AlreadyExecuting(f"{decision_id} already has a {kind} saga (409 already executing)")
        cur.execute("INSERT INTO exec.sagas VALUES (?, ?, ?, 'PENDING', ?, ?, ?, NULL, NULL, NULL)",
                    [saga_id, decision_id, kind, actor, at, at])
        ents = [e for leg in legs for e in _entities({"budget_id": leg["budget_id"],
                                                      "campaign_ids": leg.get("campaign_ids", [])})]
        locks.acquire(cur, saga_id, ents, at, priority)
        for seq, leg in enumerate(legs):
            ad = adapters.get(leg["platform"])
            cur.execute("INSERT INTO exec.saga_legs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'PLANNED', 0, "
                        "NULL, NULL, NULL, NULL, 'NOT_REQUIRED', ?)",
                        [f"{saga_id}:{seq}", saga_id, decision_id, kind, seq, leg["platform"],
                         getattr(ad, "mode", None), leg["budget_id"], json.dumps(leg.get("campaign_ids", [])),
                         leg["before"], leg["after"], at])
        _saga_to(cur, saga_id, "EXECUTING", at, {"actor": actor})

    db.write(work)
    return saga_id


def _drive(db, saga_id: str, runner: Runner) -> dict:
    """Run the remaining legs in order; stop at the first leg that does not verify; then finalize."""
    stop_reason = None
    for leg in _legs(db, saga_id):
        if leg["state"] in LEG_DONE or leg["state"] in ("UNKNOWN", "CONFLICT"):
            if leg["state"] in ("UNKNOWN", "CONFLICT", "FAILED"):
                stop_reason = leg["state"]
                break
            continue
        frozen = {e for (_, e, _, r, s) in locks.active_freezes(db) if s != saga_id}
        if leg["budget_id"] in frozen or frozen & set(leg["campaign_ids"]):
            stop_reason = "EXECUTION_FREEZE: entity frozen by another saga"
            break
        try:
            out = runner.run_leg(leg)
        except AdapterUnavailable as exc:
            stop_reason = f"BLOCKED: {exc}"
            db.write(lambda cur, m=str(exc), lid=leg["leg_id"]: cur.execute(
                "UPDATE exec.saga_legs SET error = ? WHERE leg_id = ?", [m, lid]))
            break
        if out != "VERIFIED":
            stop_reason = out
            break
    return finalize(db, saga_id, runner, stop_reason)


def finalize(db, saga_id: str, runner: Runner, stop_reason: str | None = None) -> dict:
    at = runner.now
    legs = _legs(db, saga_id)
    kind = db.query("SELECT kind FROM exec.sagas WHERE saga_id = ?", [saga_id])[0][0]
    states = [leg["state"] for leg in legs]
    verified = [leg for leg in legs if leg["state"] == "VERIFIED"]
    state = db.query("SELECT state FROM exec.sagas WHERE saga_id = ?", [saga_id])[0][0]
    if state in TERMINAL:
        pass  # already resolved (e.g. a late conflict reconcile): only the freeze bookkeeping below runs
    elif all(s == "VERIFIED" for s in states):
        db.write(lambda cur: _saga_to(cur, saga_id, "SUCCEEDED", at))
    elif "CONFLICT" in states and not verified:
        pass  # stays EXECUTING with a frozen CONFLICT leg until /reconcile (RECONCILED_VERIFIED / ABANDONED)
    elif "UNKNOWN" in states:
        db.write(lambda cur: _saga_to(cur, saga_id, "PARTIAL", at, {"reason": stop_reason}))
    elif not verified:
        db.write(lambda cur: _saga_to(cur, saga_id, "BLOCKED", at, {"reason": stop_reason}))
    else:
        if state == "EXECUTING":
            db.write(lambda cur: _saga_to(cur, saga_id, "PARTIAL", at, {"reason": stop_reason}))
        if all(leg["desired_after"] < leg["expected_before"] for leg in verified):
            db.write(lambda cur: _saga_to(cur, saga_id, "ACCEPTED_PARTIAL", at,
                                          {"reason": "every completed leg is risk-reducing"}))
        else:
            _compensate(db, saga_id, verified, runner)
    state = db.query("SELECT state FROM exec.sagas WHERE saga_id = ?", [saga_id])[0][0]

    def wrap(cur):
        if state in TERMINAL:
            locks.release(cur, saga_id, at)
            locks.clear_saga_freezes(cur, saga_id, at, "saga", f"saga {state}")
            for leg in legs:  # an unreconciled CONFLICT stays frozen: only /reconcile lifts it
                if leg["state"] == "CONFLICT":
                    locks.freeze(cur, _entities(leg), "EXECUTION_UNCERTAINTY", saga_id, leg["leg_id"], at)
            for leg in verified:
                emit(cur, "action_executed", "budget", leg["budget_id"], at,
                     {"saga_id": saga_id, "kind": kind, "after": leg["desired_after"]},
                     dedupe_key=f"action_executed:{leg['leg_id']}")
        elif state in FREEZING:
            for leg in legs:
                locks.freeze(cur, _entities(leg), "EXECUTION_UNCERTAINTY", saga_id, leg["leg_id"], at)

    db.write(wrap)
    return saga_summary(db, saga_id)


def _compensate(db, saga_id: str, verified: list[dict], runner: Runner) -> None:
    at = runner.now
    db.write(lambda cur: _saga_to(cur, saga_id, "COMPENSATING", at))
    ok = True
    for leg in reversed(verified):
        adapter = runner.adapters[leg["platform"]]
        observed = runner._read(adapter, leg)
        if not runner._close(observed, leg["desired_after"], adapter):
            ok = False  # someone changed it after us: restoring blindly would overwrite their change
            continue
        res = adapter.set_budget(leg["budget_id"], leg["expected_before"], request_id=f"{leg['leg_id']}:comp")
        back = runner._read(adapter, leg)
        if not (res.ok or res.ambiguous) or not runner._close(back, leg["expected_before"], adapter):
            ok = False
        db.write(lambda cur, lg=leg, b=back, r=res: cur.execute(
            "INSERT INTO exec.saga_transitions VALUES (?, ?, 'VERIFIED', 'COMPENSATION', ?, ?)",
            [saga_id, lg["leg_id"], at, json.dumps({"restored_to": lg["expected_before"], "observed": b,
                                                    "http_status": r.http_status})]))
    if ok:
        db.write(lambda cur: _saga_to(cur, saga_id, "COMPENSATED", at))
    else:
        db.write(lambda cur: _saga_to(cur, saga_id, "COMPENSATION_FAILED", at))
        db.write(lambda cur: _saga_to(cur, saga_id, "HUMAN_RESOLUTION_REQUIRED", at,
                                      {"alert": "compensation failed; manual reconcile required"}))


def saga_summary(db, saga_id: str) -> dict:
    s = db.query("SELECT decision_id, kind, state, final_resolution FROM exec.sagas WHERE saga_id = ?", [saga_id])[0]
    return {"saga_id": saga_id, "decision_id": s[0], "kind": s[1], "state": s[2], "final_resolution": s[3],
            "legs": [{k: leg[k] for k in ("leg_id", "platform", "mode", "budget_id", "expected_before",
                                         "desired_after", "observed_before", "observed_after", "state", "attempts",
                                         "error")} for leg in _legs(db, saga_id)]}


# ---- entry points ----------------------------------------------------------------------------------------------------
def execute_decision(db, decision_id: str, adapters: dict, actor: str, now: datetime, state_now,
                     sleep: Callable[[float], None] = time.sleep) -> dict:
    """Execute an APPROVED decision. Fresh-checks the fingerprint first (stale -> EXPIRED, nothing sent); compare-and-
    set APPROVED -> EXECUTING via the unique saga row (a concurrent second request gets 409)."""
    ensure(db)
    d = get_decision(db, decision_id)
    if d["status"] == "EXECUTING" or d.get("saga_state"):
        raise AlreadyExecuting(f"{decision_id} is already {d['status']} (409)")
    if d["status"] != "APPROVED":
        raise DecisionError("CONFLICT", f"decision is {d['status']}, not APPROVED")
    diff = check_fresh(db, decision_id, now, state_now)
    if diff:
        expire(db, decision_id, now, diff, actor="execution-guard")
        raise DecisionError("EXPIRED", "inputs changed after approval; a fresh decision is required", {"diff": diff})
    legs = sorted(d["legs"], key=lambda leg: (leg["after"] > leg["before"], leg["unit_id"]))  # risk-reducing first
    saga_id = _create(db, decision_id, "EXECUTION", legs, actor, now, adapters)
    return _drive(db, saga_id, Runner(db, adapters, now, sleep))


def reverify(db, adapters: dict, now: datetime, sleep: Callable[[float], None] = time.sleep) -> list[dict]:
    """Background verifier: UNKNOWN legs re-read -> VERIFIED (desired present) / FAILED (still the old value)."""
    ensure(db)
    runner = Runner(db, adapters, now, sleep)
    touched = set()
    for (lid,) in db.query("SELECT leg_id FROM exec.saga_legs WHERE state = 'UNKNOWN'"):
        leg = _leg(db, lid)
        adapter = adapters[leg["platform"]]
        observed = runner._read(adapter, leg)
        if runner._close(observed, leg["desired_after"], adapter):
            db.write(lambda cur, o=observed, lid=lid: _leg_to(cur, lid, "VERIFIED", now, observed_after=o,
                                                              external_state="VERIFIED", error=None))
        elif runner._close(observed, leg["observed_before"], adapter):
            db.write(lambda cur, o=observed, lid=lid: _leg_to(cur, lid, "FAILED", now, observed_after=o,
                                                     external_state="FAILED", error="re-verify: not applied"))
        else:
            continue
        touched.add(leg["saga_id"])
    out = []
    for sid in touched:
        if not any(leg["state"] == "UNKNOWN" for leg in _legs(db, sid)):
            out.append(_drive(db, sid, runner) if db.query("SELECT state FROM exec.sagas WHERE saga_id = ?",
                                                           [sid])[0][0] == "EXECUTING" else finalize(db, sid, runner))
    return out


def recover(db, adapters: dict, now: datetime, sleep: Callable[[float], None] = time.sleep) -> list[dict]:
    """Crash recovery on restart: a leg left in PREREAD_OK / SENT is re-read. Desired state present -> VERIFIED;
    absent and the pre-read state unchanged -> re-send allowed (absolute setter); otherwise UNKNOWN. Then the saga
    continues with its remaining legs."""
    ensure(db)
    runner = Runner(db, adapters, now, sleep)
    out = []
    for (sid,) in db.query("SELECT saga_id FROM exec.sagas WHERE state = 'EXECUTING'"):
        for leg in _legs(db, sid):
            if leg["state"] not in ("PREREAD_OK", "SENT"):
                continue
            adapter = adapters[leg["platform"]]
            observed = runner._read(adapter, leg)
            if leg["state"] == "SENT" and runner._close(observed, leg["desired_after"], adapter):
                db.write(lambda cur, o=observed, lid=leg["leg_id"]: _leg_to(
                    cur, lid, "VERIFIED", now, observed_after=o, external_state="VERIFIED"))
            elif runner._close(observed, leg["observed_before"], adapter):
                if leg["state"] == "PREREAD_OK":
                    continue  # nothing was sent: run_leg proceeds from PREREAD_OK
                runner.run_leg(leg)  # SENT but not applied: re-send the same absolute state
            else:
                db.write(lambda cur, o=observed, lid=leg["leg_id"]: _leg_to(
                    cur, lid, "UNKNOWN", now, observed_after=o, external_state="UNKNOWN",
                    error="recovery: state is neither the pre-read nor the desired value"))
                _freeze_leg(db, leg, now)
        out.append(_drive(db, sid, runner))
    return out


def reconcile_conflict(db, leg_id: str, action: str, actor: str, role: str, now: datetime) -> dict:
    """/reconcile for a CONFLICT leg: accept the observed external state as truth (RECONCILED_VERIFIED, nothing sent)
    or ABANDONED. The decision then ends BLOCKED (execution had started, so it is never SUPERSEDED); a fresh decision
    is generated from the observed state by the next run."""
    if role not in ("manager", "admin"):
        raise DecisionError("FORBIDDEN", f"role {role} cannot reconcile")
    to = {"accept_observed": "RECONCILED_VERIFIED", "abandon": "ABANDONED"}[action]
    leg = _leg(db, leg_id)
    db.write(lambda cur: _leg_to(cur, leg_id, to, now, error=f"reconciled by {actor}"))
    return finalize(db, leg["saga_id"], Runner(db, {}, now), stop_reason=f"conflict {to}")


def resolve_manually(db, saga_id: str, final_resolution: str, adapters: dict, actor: str, role: str,
                     now: datetime) -> dict:
    """HUMAN_RESOLUTION_REQUIRED -> RESOLVED_MANUALLY after a FRESH read-back confirms the claimed final state."""
    if role not in ("manager", "admin"):
        raise DecisionError("FORBIDDEN", f"role {role} cannot resolve")
    if final_resolution not in ("ACCEPTED_PARTIAL", "COMPENSATED", "BLOCKED"):
        raise DecisionError("INVALID", "final_resolution must be ACCEPTED_PARTIAL, COMPENSATED or BLOCKED")
    runner = Runner(db, adapters, now)
    for leg in _legs(db, saga_id):
        if leg["state"] != "VERIFIED":
            continue
        observed = runner._read(adapters[leg["platform"]], leg)
        want = leg["desired_after"] if final_resolution == "ACCEPTED_PARTIAL" else leg["expected_before"]
        if not runner._close(observed, want, adapters[leg["platform"]]):
            raise DecisionError("CONFLICT", f"read-back of {leg['budget_id']} ({observed}) does not confirm "
                                            f"{final_resolution}")

    def work(cur):
        _saga_to(cur, saga_id, "RESOLVED_MANUALLY", now, {"final_resolution": final_resolution, "by": actor})
        cur.execute("UPDATE exec.sagas SET final_resolution = ?, resolved_by = ? WHERE saga_id = ?",
                    [final_resolution, actor, saga_id])
        locks.release(cur, saga_id, now)
        locks.clear_saga_freezes(cur, saga_id, now, actor, "resolved manually")

    db.write(work)
    return saga_summary(db, saga_id)


def rollback(db, decision_id: str, adapters: dict, actor: str, now: datetime,
             sleep: Callable[[float], None] = time.sleep) -> dict:
    """Revert an executed decision through the same saga machinery. If the platform no longer shows the decision's
    post-action state (someone changed it since), it is ROLLBACK_CONFLICT and nothing is sent (T14, T26)."""
    ensure(db)
    sid = db.query("SELECT saga_id FROM exec.sagas WHERE decision_id = ? AND kind = 'EXECUTION'", [decision_id])
    if not sid:
        raise DecisionError("CONFLICT", "nothing was executed")
    runner = Runner(db, adapters, now, sleep)
    legs = [leg for leg in _legs(db, sid[0][0]) if leg["state"] == "VERIFIED"]
    for leg in legs:
        observed = runner._read(adapters[leg["platform"]], leg)
        if not runner._close(observed, leg["desired_after"], adapters[leg["platform"]]):
            raise DecisionError("ROLLBACK_CONFLICT", f"{leg['budget_id']} is {observed}, not the executed "
                                                     f"{leg['desired_after']}: review required")
    back = [{"unit_id": leg["budget_id"], "budget_id": leg["budget_id"], "platform": leg["platform"],
             "campaign_ids": leg["campaign_ids"], "before": leg["desired_after"], "after": leg["expected_before"]}
            for leg in legs]
    back.sort(key=lambda leg: (leg["after"] > leg["before"], leg["unit_id"]))
    rid = _create(db, decision_id, "ROLLBACK", back, actor, now, adapters)
    return _drive(db, rid, runner)
