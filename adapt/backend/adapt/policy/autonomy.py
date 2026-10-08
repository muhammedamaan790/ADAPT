"""Autonomy ladder (Stage 3; spec §8.4 readiness table, §9.1 autonomy rule, §9.2 modes).

Readiness is tracked per channel at two levels, from separate outcome pools (never mixed):
- SIMULATION AUTONOMOUS (mock-executed channels): a qualified confidence region (learn/qualification.py: Wilson 95%
  lower bound >= 0.60 over warm-up worlds 901-903 + a reliability PASS on world 904), >= 10 executed simulated
  decisions on the channel with measured outcomes, 0 guardrail violations, no open tracking incident, a healthy mock
  execution mode, and the P(loss) monitor not tripped.
- PRODUCTION AUTONOMOUS: the same on REAL ad-account outcomes only, a serving live account, champion contracts and an
  admin-reviewed policy. No real outcome exists in this build (a Google test account serves no ads), so it is never
  met; the checklist says why.

Modes: enabling AUTONOMOUS requires simulation readiness (T38; an uncalibrated "HIGH" never authorizes anything);
OBSERVE and APPROVE are always allowed. The mode map is an EXACT staleness input, so a change expires pending
decisions.

auto_execute (the pipeline step after policy): a decision whose legs are all on AUTONOMOUS channels is executed by
ADAPT only if every gate passes (§9.1): its raw-confidence region is QUALIFIED on each channel, model P(loss) <= 0.2
(all 200 draws), portfolio movement sum|s' - s| / sum s <= 10%, every required data dependency GREEN, no MIX_UNCERTAIN
unit, no MODEL_UNAVAILABLE curve (prediction quality > 0), no autonomy pin on its units, the P(loss) monitor not
tripped, and an unchanged dependency fingerprint (checked again by approve + execute). Anything else is DOWNGRADED to
review with the failed gates recorded (ops.autonomy_log). OBSERVE channels never execute: their decisions are recorded
as shadow decisions (learn.shadow_decisions) with the forecast-implied would-have outcome, which never counts toward
readiness.
"""

from __future__ import annotations

import json
from datetime import datetime

from adapt.learn import qualification as q
from adapt.learn.confidence import COMMON_SOURCES, PLATFORM_SOURCE
from adapt.policy.modes import CHANNELS, channel_modes, write_mode

AUTONOMOUS_ACTOR = "ADAPT (autonomous, simulation)"
MAX_PROB_LOSS = 0.20
MAX_MOVEMENT = 0.10
MIN_EXECUTED = 10

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS learn;
CREATE TABLE IF NOT EXISTS ops.autonomy_log (
    decision_id VARCHAR NOT NULL, logged_at TIMESTAMP NOT NULL, result VARCHAR NOT NULL, gates JSON NOT NULL,
    PRIMARY KEY (decision_id)
);
CREATE TABLE IF NOT EXISTS learn.shadow_decisions (
    decision_id VARCHAR PRIMARY KEY, recorded_at TIMESTAMP NOT NULL, channels JSON NOT NULL, region VARCHAR,
    expected_e DOUBLE, p10 DOUBLE, p90 DOUBLE, prob_loss DOUBLE, would_pass_policy BOOLEAN NOT NULL, detail JSON
);
"""


def _has(db, schema: str, table: str) -> bool:
    return bool(db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                         [schema, table]))


def world_id(http) -> int:
    try:
        return int(http.get("/health").get("seed"))
    except Exception:  # noqa: BLE001 - an unreachable world only means no local world id
        return -1


# ---- readiness -------------------------------------------------------------------------------------------------------
def _tracking_open(db) -> set[str]:
    if not _has(db, "intel", "anomalies"):
        return set()
    return {p for (p,) in db.query("SELECT DISTINCT platform FROM intel.anomalies WHERE classification = "
                                   "'tracking_issue' AND is_incident AND status <> 'resolved'") if p}


def _unresolved_legs(db) -> dict[str, int]:
    if not _has(db, "exec", "saga_legs"):
        return {}
    return dict(db.query("SELECT platform, count(*) FROM exec.saga_legs WHERE state IN ('UNKNOWN', 'CONFLICT') "
                         "GROUP BY 1"))


def readiness(db, adapters: dict, local_world: int) -> dict[str, dict]:
    recs = q.all_records(db, local_world)
    tracking, unresolved = _tracking_open(db), _unresolved_legs(db)
    health = {}
    if adapters:
        from adapt.execute.adapters import platform_health

        health = platform_health(adapters)
    out = {}
    for ch in CHANNELS:
        regions = {r: q.qualify(recs, ch, r) for r in q.REGIONS}
        mine = [r for r in recs if r["channel"] == ch and r["world"] == "SIMULATED"]
        executed = len({(r["world_id"], r["decision_id"]) for r in mine})
        violations = sum(bool(r["guardrail_violation"]) for r in mine)
        h = health.get(ch, {"ok": False, "mode": "UNAVAILABLE", "reason": "no adapter"})
        monitor = q.ploss_monitor(recs, ch)
        sim_checks = [
            {"id": "REGION_QUALIFIED", "label": "A confidence region is qualified (Wilson ≥ 0.60, 3 worlds, "
             "reliability PASS)", "passed": any(x["status"] == "QUALIFIED" for x in regions.values()),
             "detail": "; ".join(f"{r}: {x['status']}" for r, x in regions.items())},
            {"id": "EXECUTED_SIMULATED", "label": f"≥ {MIN_EXECUTED} executed simulated decisions with measured "
             "outcomes", "passed": executed >= MIN_EXECUTED, "detail": f"{executed} measured"},
            {"id": "GUARDRAIL_VIOLATIONS", "label": "0 guardrail violations", "passed": violations == 0,
             "detail": f"{violations}"},
            {"id": "TRACKING_HEALTH", "label": "No open tracking incident", "passed": ch not in tracking,
             "detail": "open tracking incident" if ch in tracking else "healthy"},
            {"id": "EXECUTION_HEALTH", "label": "Mock execution healthy, no unresolved leg",
             "passed": bool(h.get("ok")) and h.get("mode") == "MOCK" and not unresolved.get(ch),
             "detail": f"{h.get('mode')}; {unresolved.get(ch, 0)} unresolved legs"
                       + (f"; {h['reason']}" if h.get("reason") else "")},
            {"id": "PLOSS_MONITOR", "label": "Model P(loss) reliability monitor not tripped",
             "passed": not monitor["tripped"],
             "detail": f"{monitor['losses']}/{monitor['n']} losses in the P(loss) ≤ 0.2 bin"}]
        real = [r for r in recs if r["channel"] == ch and r["world"] == "REAL"]
        prod_checks = [
            {"id": "REAL_OUTCOMES", "label": "≥ 30 matured REAL ad-account outcomes (Wilson ≥ 0.60) + reliability",
             "passed": False, "detail": f"{len(real)} real outcomes: a Google test account executes for real but "
                                        "serves no ads, so no real outcome exists"},
            {"id": "LIVE_SERVING_ACCOUNT", "label": "Channel in live mode with a serving ad account",
             "passed": False, "detail": f"{h.get('mode')} execution"}]
        out[ch] = {"execution_mode": h.get("mode", "UNAVAILABLE"),
                   "simulation": {"eligible": all(c["passed"] for c in sim_checks), "checks": sim_checks,
                                  "executed": executed, "violations": violations, "regions": regions,
                                  "ploss_monitor": monitor},
                   "production": {"eligible": False, "checks": prod_checks}}
    return out


def set_mode(db, channel: str, mode: str, actor: str, role: str, at: datetime, adapters: dict,
             local_world: int, reason: str = "") -> dict:
    if role != "admin":
        raise PermissionError("changing a channel's autonomy mode requires the admin role (spec §9.5)")
    if mode == "AUTONOMOUS":
        r = readiness(db, adapters, local_world)[channel]["simulation"]
        if not r["eligible"]:
            failed = [c["id"] for c in r["checks"] if not c["passed"]]
            raise ValueError(f"{channel} is not SIMULATION AUTONOMOUS-eligible: failed {', '.join(failed)}")
    write_mode(db, channel, mode, actor, at, reason)
    return channel_modes(db)


# ---- the pipeline step ----------------------------------------------------------------------------------------------
def _health_status(db, as_of: datetime) -> dict[str, str]:
    if not _has(db, "ops", "data_health"):
        return {}
    return dict(db.query("""SELECT source, status FROM ops.data_health WHERE as_of <= ?
                            QUALIFY row_number() OVER (PARTITION BY source ORDER BY as_of DESC) = 1""", [as_of]))


def _pinned(db, as_of: datetime) -> set[str]:
    """Entities pinned to Approve after >= 2 automated reversals in 7 days (policy/safety_monitor.py); a pin without
    an end date holds until released."""
    if not _has(db, "ops", "autonomy_pins"):
        return set()
    return {e for (e,) in db.query("SELECT entity_id FROM ops.autonomy_pins WHERE until IS NULL OR until > ?",
                                   [as_of])}


def gates(db, d: dict, state, as_of: datetime, recs: list[dict]) -> list[dict]:
    """Every autonomy gate of §9.1 for one decision, each {id, passed, detail}."""
    channels = sorted({leg["platform"] for leg in d["legs"]})
    conf = d.get("confidence") or {}
    region = conf.get("region")
    total = sum(u.budget for u in state.units)
    movement = sum(abs(leg["after"] - leg["before"]) for leg in d["legs"]) / max(total, 1.0)
    health = _health_status(db, as_of)
    deps = sorted({PLATFORM_SOURCE[c] for c in channels if c in PLATFORM_SOURCE} | set(COMMON_SOURCES))
    not_green = [s for s in deps if health.get(s) != "GREEN"]
    pinned = _pinned(db, as_of) & {leg["unit_id"] for leg in d["legs"]}
    mix = (conf.get("detail") or {}).get("mix_uncertain") or []
    out = []
    for ch in channels:
        qu = q.qualify(recs, ch, region) if region else {"status": "UNQUALIFIED", "reasons": ["no confidence"]}
        out.append({"id": f"REGION_QUALIFIED:{ch}", "passed": qu["status"] == "QUALIFIED",
                    "detail": f"region {region}: {qu['status']}" + (f" ({'; '.join(qu['reasons'])})"
                                                                     if qu.get("reasons") else "")})
        mon = q.ploss_monitor(recs, ch)
        out.append({"id": f"PLOSS_MONITOR:{ch}", "passed": not mon["tripped"],
                    "detail": f"{mon['losses']}/{mon['n']} losses in the P(loss) ≤ 0.2 bin"})
    out += [
        {"id": "PROB_LOSS", "passed": d["expected"]["prob_loss"] <= MAX_PROB_LOSS,
         "detail": f"model P(loss) {d['expected']['prob_loss']:.2f} (max {MAX_PROB_LOSS})"},
        {"id": "PORTFOLIO_MOVEMENT", "passed": movement <= MAX_MOVEMENT,
         "detail": f"{movement:.1%} of the portfolio moved (max {MAX_MOVEMENT:.0%})"},
        {"id": "DATA_DEPENDENCIES_GREEN", "passed": not not_green,
         "detail": "all GREEN" if not not_green else "not GREEN: " + ", ".join(
             f"{s} {health.get(s, 'missing')}" for s in not_green)},
        {"id": "NO_MIX_UNCERTAIN", "passed": not mix, "detail": ", ".join(mix) or "mapped"},
        {"id": "MODELS_AVAILABLE", "passed": float(conf.get("prediction_quality") or 0.0) > 0.0,
         "detail": f"prediction quality {float(conf.get('prediction_quality') or 0.0):.2f}"},
        {"id": "NOT_PINNED", "passed": not pinned, "detail": ", ".join(sorted(pinned)) or "no pin"},
    ]
    return out


def auto_execute(db, adapters: dict, as_of: datetime, state, local_world: int) -> dict:
    """Execute fully passing decisions on AUTONOMOUS channels; downgrade the rest; shadow-record OBSERVE ones."""
    from adapt.decide import decisions as dec
    from adapt.execute.saga import execute_decision

    db.write(lambda cur: cur.execute(DDL))
    modes = channel_modes(db)
    if not any(m in ("AUTONOMOUS", "OBSERVE") for m in modes.values()):
        return {"status": "OK", "executed": [], "downgraded": [], "shadow": [],
                "reason": "every channel is in Approve mode"}
    recs = q.all_records(db, local_world)
    out = {"status": "OK", "executed": [], "downgraded": [], "shadow": []}
    for (did,) in db.query("SELECT decision_id FROM intel.decisions WHERE created_at = ? ORDER BY decision_id",
                           [as_of]):
        d = dec.get_decision(db, did)
        if d["status"] != "PENDING_APPROVAL" or d["class"] != "OPTIMIZATION":
            continue
        chans = {leg["platform"] for leg in d["legs"]}
        if any(modes.get(c) == "OBSERVE" for c in chans):
            e = d["expected"]
            ok = all(c["passed"] for c in d["checks"])
            db.write(lambda cur, d=d, e=e, ok=ok, chans=chans: cur.execute(
                "INSERT OR IGNORE INTO learn.shadow_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [d["decision_id"], as_of, json.dumps(sorted(chans)), (d.get("confidence") or {}).get("region"),
                 e["E"], e["p10"], e["p90"], e["prob_loss"], ok,
                 json.dumps({"note": "forecast-implied would-have outcome; never counts toward readiness"})]))
            out["shadow"].append(did)
            continue
        if not all(modes.get(c) == "AUTONOMOUS" for c in chans):
            continue  # Approve channels: a human decides, as before
        g = gates(db, d, state, as_of, recs)
        result = "EXECUTED" if all(x["passed"] for x in g) else "DOWNGRADED"
        if result == "EXECUTED":
            try:
                dec.approve(db, did, d["decision_hash"], AUTONOMOUS_ACTOR, "autonomy", as_of, state)
                execute_decision(db, did, adapters, AUTONOMOUS_ACTOR, as_of, state)
            except dec.DecisionError as exc:  # stale fingerprint or a conflict: never forced through
                result = "DOWNGRADED"
                g.append({"id": "FRESH_AT_EXECUTION", "passed": False, "detail": f"{exc.code}: {exc}"})
        db.write(lambda cur, did=did, result=result, g=g: cur.execute(
            "INSERT OR REPLACE INTO ops.autonomy_log VALUES (?, ?, ?, ?)", [did, as_of, result, json.dumps(g)]))
        out["executed" if result == "EXECUTED" else "downgraded"].append(did)
    return out


def autonomy_entry(db, decision_id: str) -> dict | None:
    if not _has(db, "ops", "autonomy_log"):
        return None
    row = db.query("SELECT logged_at, result, gates FROM ops.autonomy_log WHERE decision_id = ?",
                   [decision_id])
    return {"at": row[0][0], "result": row[0][1], "gates": json.loads(row[0][2])} if row else None
