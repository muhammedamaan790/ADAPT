"""Decision objects, snapshots, hash, fingerprint and lifecycle (C3, spec §2, §8.4, §22.4, §22.5, §22.9).

Three separate things, never conflated:
  (1) decision content (intel.decisions.payload + decision_hash): immutable once created;
  (2) lifecycle events (ops.decision_events): append-only;
  (3) the current status: DERIVED from (2) plus the saga state (exec.sagas), never stored.
At most one actionable decision per (entity, action family): a new run supersedes overlapping DRAFT /
PENDING_APPROVAL decisions (the latter with an explicit invalidation_reason); APPROVED and later are never touched.
Approval is bound to a decision_hash; at approval AND at execution the fingerprint is recomputed and the decision
EXPIRES (with the diff) on any EXACT change, TOLERANCE breach, STATUS downgrade, policy change, kill switch or TTL.
Replay re-runs portfolio_economics + optimizer + policy from the snapshot artifacts only and must reproduce the hash.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from adapt.decide.fingerprint import fingerprint, staleness_diff
from adapt.decide.hashing import canonical_bytes, content_hash, normalize, sha256_hex
from adapt.decide.optimizer import Optimizer
from adapt.decide.safety import safety_candidates
from adapt.decide.snapshot import get_artifact, put_artifact, replay_environment, state_from_dict, state_to_dict
from adapt.economics.portfolio import PortfolioState
from adapt.learn.calibration import calibrate
from adapt.policy.engine import ROLES_THAT_APPROVE, cooldown_units, current_policy, policy_result, validate
from adapt.policy.locks import kill_switch_active

OPTIMIZER_VERSION = "b7-greedy-slsqp-1"
TTL_HOURS = 6
ACTIONABLE = ("DRAFT", "PENDING_APPROVAL")

DDL = """
CREATE SCHEMA IF NOT EXISTS intel;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS intel.decisions (
    decision_id VARCHAR PRIMARY KEY, run_id VARCHAR, class VARCHAR NOT NULL, type VARCHAR NOT NULL,
    objective VARCHAR, created_at TIMESTAMP NOT NULL, payload JSON NOT NULL, decision_hash VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL, supersedes VARCHAR, follows VARCHAR
);
CREATE TABLE IF NOT EXISTS ops.decision_events (
    decision_id VARCHAR NOT NULL, seq INTEGER NOT NULL, event VARCHAR NOT NULL, event_at TIMESTAMP NOT NULL,
    actor VARCHAR NOT NULL, details JSON, PRIMARY KEY (decision_id, seq)
);
CREATE TABLE IF NOT EXISTS ops.decision_snapshots (
    snapshot_id VARCHAR PRIMARY KEY, decision_id VARCHAR NOT NULL, run_id VARCHAR, manifest_sha256 VARCHAR NOT NULL,
    manifest JSON NOT NULL, economics_hash VARCHAR NOT NULL, model_artifact_hashes JSON, optimizer_version VARCHAR,
    solver_config JSON, policy_version VARCHAR, policy_config_hash VARCHAR, config_hash VARCHAR, objective VARCHAR,
    seed INTEGER, code_sha VARCHAR, lock_hash VARCHAR, env_id VARCHAR, replay_environment_fingerprint VARCHAR,
    tz VARCHAR, decision_hash VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS ops.decision_fingerprints (
    decision_id VARCHAR PRIMARY KEY, economics_hash VARCHAR NOT NULL, fingerprint JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS ops.replay_environments (
    env_id VARCHAR PRIMARY KEY, code_sha VARCHAR, uv_lock_hash VARCHAR, npm_lock_hash VARCHAR,
    python_version VARCHAR, config_artifact_hashes JSON, replay_environment_fingerprint VARCHAR, created_at TIMESTAMP
);
"""

# lifecycle event -> pre-saga display status (spec §22.9)
EVENT_STATUS = {"created": "DRAFT", "submitted": "PENDING_APPROVAL", "blocked": "BLOCKED", "approved": "APPROVED",
                "rejected": "REJECTED", "expired": "EXPIRED", "superseded": "SUPERSEDED"}
# saga state -> decision status (spec §22.5)
SAGA_STATUS = {"PENDING": "EXECUTING", "EXECUTING": "EXECUTING", "PARTIAL": "PARTIAL", "COMPENSATING": "EXECUTING",
               "SUCCEEDED": "EXECUTED", "ACCEPTED_PARTIAL": "PARTIAL", "COMPENSATED": "PARTIAL",
               "COMPENSATION_FAILED": "PARTIAL", "HUMAN_RESOLUTION_REQUIRED": "PARTIAL", "BLOCKED": "BLOCKED"}


class DecisionError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.details = code, details or {}


def ensure(db) -> None:
    db.write(lambda cur: cur.execute(DDL))


# ---- lifecycle ------------------------------------------------------------------------------------------------------
def add_event(cur, decision_id: str, event: str, at: datetime, actor: str, details: dict | None = None) -> None:
    seq = cur.execute("SELECT coalesce(max(seq), 0) + 1 FROM ops.decision_events WHERE decision_id = ?",
                      [decision_id]).fetchone()[0]
    cur.execute("INSERT INTO ops.decision_events VALUES (?, ?, ?, ?, ?, ?)",
                [decision_id, seq, event, at, actor, json.dumps(details or {}, default=float)])


def status(db, decision_id: str) -> dict:
    """Derived status: the saga state when execution started, else the latest lifecycle event."""
    saga = []
    if db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'exec' AND table_name = 'sagas'"):
        saga = db.query("SELECT state, final_resolution FROM exec.sagas WHERE decision_id = ?", [decision_id])
    if saga:
        state, final = saga[0]
        if state == "RESOLVED_MANUALLY":
            return {"status": {"BLOCKED": "BLOCKED"}.get(final, "PARTIAL"), "saga_state": state,
                    "needs_resolution": False}
        return {"status": SAGA_STATUS[state], "saga_state": state,
                "needs_resolution": state == "HUMAN_RESOLUTION_REQUIRED"}
    ev = db.query("SELECT event, details FROM ops.decision_events WHERE decision_id = ? ORDER BY seq DESC LIMIT 1",
                  [decision_id])
    if not ev:
        raise DecisionError("NOT_FOUND", f"decision {decision_id} not found")
    details = json.loads(ev[0][1] or "{}")
    return {"status": EVENT_STATUS[ev[0][0]], "saga_state": None, "requires_review": details.get("requires_review")}


def get_decision(db, decision_id: str) -> dict:
    row = db.query("SELECT payload, decision_hash, snapshot_id, created_at, class FROM intel.decisions "
                   "WHERE decision_id = ?", [decision_id])
    if not row:
        raise DecisionError("NOT_FOUND", f"decision {decision_id} not found")
    payload, h, snap, created, cls = row[0]
    return {**json.loads(payload), "decision_hash": h, "snapshot_id": snap, "created_at": created,
            **status(db, decision_id)}


# ---- creation -------------------------------------------------------------------------------------------------------
def _type(legs: list[dict]) -> str:
    up = sum(leg["after"] > leg["before"] for leg in legs)
    down = len(legs) - up
    if up and down:
        return "REALLOCATE"
    if up:
        return "INCREASE_BUDGET"
    return "DECREASE_BUDGET"


def hash_payload(content: dict, snap: dict) -> dict:
    """The complete §22.4 hash payload: decision content + snapshot identity (no ids, timestamps or status)."""
    return {
        "legs": sorted(content["legs"], key=lambda leg: leg["unit_id"]), "expected": content["expected"],
        "alternatives": content["alternatives"],
        "why_not": sorted(content["why_not"], key=lambda w: w["unit_id"]), "checks": content["checks"],
        "objective": content["objective"], "unallocated": content["unallocated"],
        "reserve_floor": content["reserve_floor"], "inventory_risk_after": content["inventory_risk_after"],
        **{k: snap[k] for k in ("manifest_sha256", "economics_hash", "model_artifact_hashes", "optimizer_version",
                                "solver_config", "policy_version", "policy_config_hash", "config_hash", "lock_hash",
                                "code_sha", "replay_environment_fingerprint", "seed", "tz")}}


def _content(cls: str, prop: dict, state: PortfolioState, checks: list[dict], factor: float) -> dict:
    legs = prop["legs"]
    exp = prop["expected"]
    raw = exp.get("raw_pred", exp["E"])
    involved = {leg["unit_id"] for leg in legs}
    skus = {k for u in state.units if u.unit_id in involved for k in u.sku_weights}
    risk = prop.get("inventory_risk_after") or {"kind": "PROJECTED_SHORTFALL", "by_sku": {}}
    return {
        "class": cls, "type": _type(legs), "objective": "PROFIT",
        "legs": [{k: leg[k] for k in ("unit_id", "platform", "channel", "budget_id", "campaign_ids", "before", "after")}
                 for leg in legs],
        "expected": {"p10": exp["P10"], "p50": exp["P50"], "p90": exp["P90"], "E": exp["E"],
                     "prob_loss": exp["prob_loss"], "delta_net_revenue": exp["delta_net_revenue"],
                     "raw_pred": raw, "calibrated_pred": calibrate(raw, factor), "optimism_correction_factor": factor},
        "inventory_risk_after": {"kind": risk["kind"],
                                 "by_sku": {k: v for k, v in sorted(risk["by_sku"].items()) if k in skus}},
        "unallocated": prop.get("unallocated", 0.0), "reserve_floor": prop.get("reserve_floor", 0.0),
        "cost_of_inaction_7d": None, "alternatives": [],
        "why_not": [w for w in prop.get("why_not", []) if w["unit_id"] in involved or cls == "OPTIMIZATION"],
        "checks": checks,
        "trigger": ({"sku": prop.get("sku"), "kind": prop.get("trigger")} if cls == "SAFETY"
                    else {"kind": "OPTIMIZATION_RUN"}),
        "remaining_shortfall": prop.get("remaining_shortfall") if cls == "SAFETY" else None,
    }


def _model_hashes(db, state: PortfolioState) -> dict:
    if not db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'models' "
                    "AND table_name = 'response_curves'"):
        return {u.unit_id: None for u in state.units}
    rows = dict(db.query("""SELECT unit_id, artifact_sha256 FROM models.response_curves
                            WHERE fit_ts = (SELECT max(fit_ts) FROM models.response_curves)"""))
    return {u.unit_id: rows.get(u.unit_id) for u in state.units}


def create_decisions(db, run: dict, state: PortfolioState, flags: dict, at: datetime,
                     actor: str = "ADAPT") -> list[str]:
    """Turn an optimizer run (decide.run.run_optimizer) into decision objects. Returns the new decision ids."""
    ensure(db)
    pol = current_policy(db, at)
    ks = kill_switch_active(db)
    cool = cooldown_units(db, at)
    factor = run["calibration_factor"]
    proposals = []
    r = run["result"]
    if r.get("status") == "OK" and r.get("legs"):
        proposals.append(("OPTIMIZATION", r["decision_id"], r))
    for cand in run["safety"]:
        proposals.append(("SAFETY", cand["decision_id"], cand))
    if not proposals:
        return []
    fp = fingerprint(db, state, pol["policy_version"], ks, at)
    obj_cfg, g_cfg = pol["config"]["objectives"], pol["config"]["guardrails"]
    manifest = {"state": put_artifact(db, state_to_dict(state)), "flags": put_artifact(db, flags),
                "policy": put_artifact(db, pol["config"]), "calibration": put_artifact(db, {"factor": factor}),
                "cooldown": put_artifact(db, sorted(cool)), "kill_switch": put_artifact(db, {"active": ks})}
    manifest_sha = content_hash(manifest)
    env = replay_environment({k: manifest[k] for k in ("policy",)})
    env_id = f"env-{env['replay_environment_fingerprint'][:16]}"
    snap_common = {"manifest_sha256": manifest_sha, "economics_hash": fp["economics_hash"],
                   "model_artifact_hashes": _model_hashes(db, state), "optimizer_version": OPTIMIZER_VERSION,
                   "solver_config": obj_cfg["economics"], "policy_version": pol["policy_version"],
                   "policy_config_hash": pol["policy_config_hash"], "config_hash": content_hash(g_cfg),
                   "lock_hash": env["uv_lock_hash"], "code_sha": env["code_sha"],
                   "replay_environment_fingerprint": env["replay_environment_fingerprint"], "seed": 0,
                   "tz": "Asia/Kolkata"}
    created = []

    def work(cur):
        cur.execute(DDL)
        cur.execute("INSERT OR IGNORE INTO ops.replay_environments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [env_id, env["code_sha"], env["uv_lock_hash"], env["npm_lock_hash"], env["python_version"],
                     json.dumps(env["config_artifact_hashes"]), env["replay_environment_fingerprint"], at])
        for cls, did, prop in proposals:
            if cur.execute("SELECT 1 FROM intel.decisions WHERE decision_id = ?", [did]).fetchone():
                continue  # idempotent per run
            alloc = {u.unit_id: u.budget for u in state.units} | {leg["unit_id"]: leg["after"] for leg in prop["legs"]}
            checks = validate(state, alloc, flags, cls, g_cfg, cool, ks)
            content = _content(cls, prop, state, checks, factor)
            payload = hash_payload(content, snap_common)
            h = sha256_hex(canonical_bytes(payload))       # raises on NaN / Inf: no decision is created (T20)
            snap_id = f"SNAP-{sha256_hex(f'{did}|{h}')[:16]}"  # same content in two runs: same hash, two snapshots
            cur.execute("INSERT INTO intel.decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                        [did, run["run_id"], cls, content["type"], "PROFIT", at,
                         json.dumps({"decision_id": did, **normalize(content)}), h, snap_id])
            cur.execute("INSERT INTO ops.decision_snapshots VALUES "
                        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [snap_id, did, run["run_id"], manifest_sha, json.dumps(manifest), fp["economics_hash"],
                         json.dumps(snap_common["model_artifact_hashes"]), OPTIMIZER_VERSION,
                         json.dumps(obj_cfg["economics"]), pol["policy_version"], pol["policy_config_hash"],
                         snap_common["config_hash"], "PROFIT", 0, env["code_sha"], env["uv_lock_hash"], env_id,
                         env["replay_environment_fingerprint"], "Asia/Kolkata", h])
            cur.execute("INSERT INTO ops.decision_fingerprints VALUES (?, ?, ?, ?)",
                        [did, fp["economics_hash"], json.dumps(fp, default=float), at])
            add_event(cur, did, "created", at, actor, {"run_id": run["run_id"]})
            result = policy_result(checks, cls)
            if result["status"] == "BLOCKED":
                # the optimizer and policy share one feasible set, so a blocked optimizer output is a bug signal
                signal = {"signal": "OPTIMIZER_POLICY_MISMATCH"} if cls == "OPTIMIZATION" else {}
                add_event(cur, did, "blocked", at, "policy", {**result, **signal})
            else:
                _supersede_overlapping(cur, did, cls, {leg["budget_id"] for leg in prop["legs"]}, at)
                add_event(cur, did, "submitted", at, "policy", result)
            created.append(did)

    db.write(work)
    return created


def _supersede_overlapping(cur, new_id: str, cls: str, budgets: set[str], at: datetime) -> None:
    rows = cur.execute("SELECT decision_id, payload FROM intel.decisions WHERE class = ? AND decision_id <> ?",
                       [cls, new_id]).fetchall()
    for did, payload in rows:
        last = cur.execute("SELECT event FROM ops.decision_events WHERE decision_id = ? ORDER BY seq DESC LIMIT 1",
                           [did]).fetchone()
        started = cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = 'exec' "
                              "AND table_name = 'sagas'").fetchone() and \
            cur.execute("SELECT 1 FROM exec.sagas WHERE decision_id = ?", [did]).fetchone()
        if started or not last or EVENT_STATUS.get(last[0]) not in ACTIONABLE:
            continue
        if budgets & {leg["budget_id"] for leg in json.loads(payload)["legs"]}:
            add_event(cur, did, "superseded", at, "ADAPT",
                      {"superseded_by": new_id,
                       "invalidation_reason": "a newer run produced a decision on the same budgets"})


# ---- approval, rejection, staleness ----------------------------------------------------------------------------
def check_fresh(db, decision_id: str, now: datetime, state_now: PortfolioState) -> list[dict]:
    """Recompute the fingerprint against the current state; returns the staleness diff (empty = fresh)."""
    row = db.query("SELECT fingerprint, created_at FROM ops.decision_fingerprints WHERE decision_id = ?",
                   [decision_id])
    if not row:
        raise DecisionError("NOT_FOUND", f"no fingerprint for {decision_id}")
    before, created = json.loads(row[0][0]), row[0][1]
    pol = current_policy(db, now)
    snap_pv = db.query("SELECT policy_version FROM ops.decision_snapshots WHERE decision_id = ?", [decision_id])[0][0]
    ks = kill_switch_active(db)
    diff = staleness_diff(before, fingerprint(db, state_now, pol["policy_version"], ks, now))
    if pol["policy_version"] != snap_pv:
        diff.append({"class": "POLICY", "field": "policy_version", "before": snap_pv, "after": pol["policy_version"]})
    if ks:
        diff.append({"class": "POLICY", "field": "kill_switch", "before": False, "after": True})
    if now - created > timedelta(hours=TTL_HOURS):
        diff.append({"class": "TTL", "field": "age_hours", "before": TTL_HOURS,
                     "after": round((now - created).total_seconds() / 3600, 2)})
    return diff


def expire(db, decision_id: str, now: datetime, diff: list[dict], actor: str = "policy") -> None:
    db.write(lambda cur: add_event(cur, decision_id, "expired", now, actor, {"diff": diff}))


def approve(db, decision_id: str, decision_hash: str, actor: str, role: str, now: datetime,
            state_now: PortfolioState) -> dict:
    if role not in ROLES_THAT_APPROVE:
        raise DecisionError("FORBIDDEN", f"role {role} cannot approve")
    d = get_decision(db, decision_id)
    if d["status"] != "PENDING_APPROVAL":
        raise DecisionError("CONFLICT", f"decision is {d['status']}, not PENDING_APPROVAL")
    if decision_hash != d["decision_hash"]:
        raise DecisionError("HASH_MISMATCH", "approval must name the exact decision_hash shown to the approver")
    diff = check_fresh(db, decision_id, now, state_now)
    if diff:
        expire(db, decision_id, now, diff)
        raise DecisionError("EXPIRED", "inputs changed since the decision was created", {"diff": diff})
    db.write(lambda cur: add_event(cur, decision_id, "approved", now, actor,
                                   {"role": role, "decision_hash": decision_hash}))
    return get_decision(db, decision_id)


def reject(db, decision_id: str, actor: str, reason: str, now: datetime) -> dict:
    d = get_decision(db, decision_id)
    if d["status"] not in ACTIONABLE:
        raise DecisionError("CONFLICT", f"decision is {d['status']}")
    db.write(lambda cur: add_event(cur, decision_id, "rejected", now, actor, {"reason": reason}))
    return get_decision(db, decision_id)


# ---- replay ---------------------------------------------------------------------------------------------------------
def replay(db, decision_id: str) -> dict:
    """Re-run economics + optimizer + policy from the snapshot artifacts only; compare the decision hash."""
    snap = db.query("SELECT manifest, manifest_sha256, economics_hash, model_artifact_hashes, optimizer_version, "
                    "solver_config, policy_version, policy_config_hash, config_hash, lock_hash, code_sha, "
                    "replay_environment_fingerprint, seed, tz, decision_hash FROM ops.decision_snapshots "
                    "WHERE decision_id = ?", [decision_id])
    if not snap:
        raise DecisionError("NOT_FOUND", f"no snapshot for {decision_id}")
    (manifest, msha, ehash, mh, ov, sc, pv, pch, ch, lh, cs, ref, seed, tz, original) = snap[0]
    manifest = json.loads(manifest)
    if content_hash(manifest) != msha:
        return {"decision_id": decision_id, "match": False, "reason": "MANIFEST_CORRUPTED"}
    state = state_from_dict(get_artifact(db, manifest["state"]))
    flags = get_artifact(db, manifest["flags"])
    policy = get_artifact(db, manifest["policy"])
    factor = get_artifact(db, manifest["calibration"])["factor"]
    cool = set(get_artifact(db, manifest["cooldown"]))
    ks = get_artifact(db, manifest["kill_switch"])["active"]
    cls = db.query("SELECT class FROM intel.decisions WHERE decision_id = ?", [decision_id])[0][0]
    opt = Optimizer(state, flags, guardrails=policy["guardrails"], objectives=policy["objectives"])
    result = opt.solve()
    if cls == "OPTIMIZATION":
        prop = result
    else:
        sku = json.loads(db.query("SELECT payload FROM intel.decisions WHERE decision_id = ?",
                                  [decision_id])[0][0])["trigger"]["sku"]
        prop = next((c for c in safety_candidates(opt) if c["sku"] == sku), None)
        if prop is None:
            return {"decision_id": decision_id, "match": False, "reason": "SAFETY_CANDIDATE_NOT_REPRODUCED"}
    alloc = {u.unit_id: u.budget for u in state.units} | {leg["unit_id"]: leg["after"] for leg in prop["legs"]}
    checks = validate(state, alloc, flags, cls, policy["guardrails"], cool, ks)
    content = _content(cls, prop, state, checks, factor)
    snap_common = {"manifest_sha256": msha, "economics_hash": ehash, "model_artifact_hashes": json.loads(mh),
                   "optimizer_version": ov, "solver_config": json.loads(sc), "policy_version": pv,
                   "policy_config_hash": pch, "config_hash": ch, "lock_hash": lh, "code_sha": cs,
                   "replay_environment_fingerprint": ref, "seed": seed, "tz": tz}
    h = sha256_hex(canonical_bytes(hash_payload(content, snap_common)))
    return {"decision_id": decision_id, "match": h == original, "original_hash": original, "replay_hash": h,
            "environment": "in-process with snapshotted inputs (archived worktree replay: scripts, Stage 2)"}


