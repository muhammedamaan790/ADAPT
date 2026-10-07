"""Policy engine (C4, spec §9.1, §9.2, §8.3 ownership boundary). Stage 1: Approve mode only.

- Policy version = hash of the policy config bundle (guardrails, objectives, data-health weights); every distinct
  bundle is recorded once in ops.policy_versions. Any change invalidates approved-but-not-executed decisions.
- validate(): re-checks every allocation rule against the SAME constraint object the optimizer is generated from
  (optimizer.build_constraints over guardrails.yaml), plus class rules, freezes, cooldown and the kill switch.
  An optimizer output that fails here is logged as OPTIMIZER_POLICY_MISMATCH (a bug signal).
- outcome of a passing decision in Approve mode: OPTIMIZATION -> PENDING_APPROVAL; SAFETY -> PENDING_APPROVAL with
  requires_review (a human always reviews in Stage 1). Anything failing -> BLOCKED with the failed rule ids.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from adapt.decide.hashing import content_hash
from adapt.decide.optimizer import FIXED_REASONS, FastEval, build_constraints, gate_label, search_draws
from adapt.economics.portfolio import Portfolio, PortfolioState
from adapt.economics.state import guardrails_config, objectives_config

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
SAFETY_MAY_REDUCE_UNDER = {"TRACKING_FREEZE", "DATA_DEPENDENCY"}  # precedence 1 > 2: safety may cut, never raise
ROLES_THAT_APPROVE = {"manager", "admin", "autonomy"}  # "autonomy": the qualified auto-execute step only

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.policy_versions (
    policy_version VARCHAR PRIMARY KEY, config_hash VARCHAR NOT NULL, config JSON NOT NULL, created_at TIMESTAMP
);
"""


def policy_bundle() -> dict:
    import yaml

    return {"guardrails": guardrails_config(), "objectives": objectives_config(),
            "health": yaml.safe_load((CONFIG_DIR / "health.yaml").read_text(encoding="utf-8"))}


def current_policy(db, at: datetime | None = None) -> dict:
    bundle = policy_bundle()
    h = content_hash(bundle)
    version = f"pv-{h[:12]}"

    def work(cur):
        cur.execute(DDL)
        if not cur.execute("SELECT 1 FROM ops.policy_versions WHERE policy_version = ?", [version]).fetchone():
            cur.execute("INSERT INTO ops.policy_versions VALUES (?, ?, ?, ?)", [version, h, json.dumps(bundle), at])

    db.write(work)
    return {"policy_version": version, "policy_config_hash": h, "config": bundle}


def _check(rule: str, passed: bool, detail: str = "") -> dict:
    return {"rule": rule, "passed": bool(passed), "detail": detail}


def validate(state: PortfolioState, allocation: dict[str, float], flags: dict[str, list[str]], decision_class: str,
             guardrails: dict | None = None, cooldown_units: set[str] | None = None,
             kill_switch: bool = False) -> list[dict]:
    g = guardrails or guardrails_config()
    c = build_constraints(state, flags, g)
    units = state.units
    s0 = np.array([u.budget for u in units])
    s = np.array([allocation.get(u.unit_id, u.budget) for u in units], dtype=float)
    changed = [i for i in range(len(units)) if abs(s[i] - s0[i]) > 1e-6]
    tol = 1e-6
    checks = [_check("KILL_SWITCH", not kill_switch, "global kill switch active" if kill_switch else "")]
    if decision_class == "SAFETY":
        checks.append(_check("SAFETY_ONLY_REDUCES", all(s[i] <= s0[i] + tol for i in changed),
                             "a safety decision may only reduce spend"))
        checks.append(_check("TOTAL_BUDGET", s.sum() <= s0.sum() + tol, "safety never increases total spend"))
    else:
        checks.append(_check("TOTAL_BUDGET", s.sum() <= c.cap_total + tol,
                             f"sum {s.sum():,.0f} vs cap {c.cap_total:,.0f} (B - R)"))
    box_lo = s0 * (1 - g["change"]["max_daily_change_pct"])
    box_hi = s0 * (1 + g["change"]["max_daily_change_pct"])
    bad_box = [units[i].unit_id for i in changed if s[i] < box_lo[i] - tol or s[i] > box_hi[i] + tol]
    checks.append(_check("MAX_DAILY_CHANGE", not bad_box, ",".join(bad_box)))
    unit_min = g["change"]["unit_min_budget_inr"]
    bad_min = [units[i].unit_id for i in changed if s[i] < min(unit_min, s0[i]) - tol]
    checks.append(_check("UNIT_MIN_BUDGET", not bad_min, ",".join(bad_min)))
    moved = float(np.abs(s - s0).sum())
    checks.append(_check("DAILY_RUPEES_MOVED_CAP", moved <= c.moved_cap + tol, f"{moved:,.0f} of {c.moved_cap:,.0f}"))
    tot = s.sum()
    share_bad = []
    for ch, mx in c.channel_max.items():
        sh = s[[i for i, u in enumerate(units) if u.channel == ch]].sum() / tot if tot else 0
        if sh > mx + tol:
            share_bad.append(f"{ch} {sh:.0%} > {mx:.0%}")
    for ch, mn in c.channel_min.items():
        sh = s[[i for i, u in enumerate(units) if u.channel == ch]].sum() / tot if tot else 0
        if sh < mn - tol:
            share_bad.append(f"{ch} {sh:.0%} < {mn:.0%}")
    checks.append(_check("CHANNEL_SHARE", not share_bad, "; ".join(share_bad)))
    inc = [i for i in changed if s[i] > s0[i] + tol]
    for rule in ("MODEL_UNAVAILABLE", "MIX_UNCERTAIN"):
        bad = [units[i].unit_id for i in inc if rule in c.unit_reasons.get(i, [])]
        checks.append(_check(f"NO_INCREASE_{rule}", not bad, ",".join(bad)))
    frozen_bad = []
    for i in changed:
        reasons = set(c.unit_reasons.get(i, [])) & set(FIXED_REASONS)
        if decision_class == "SAFETY" and s[i] < s0[i]:
            reasons -= SAFETY_MAY_REDUCE_UNDER
        if reasons:
            frozen_bad.append(f"{units[i].unit_id}:{'/'.join(sorted(reasons))}")
    checks.append(_check("FROZEN_OR_FIXED_UNITS", not frozen_bad, ",".join(frozen_bad)))
    if decision_class == "OPTIMIZATION":
        cool = sorted(units[i].unit_id for i in changed if units[i].unit_id in (cooldown_units or set()))
        checks.append(_check("COOLDOWN", not cool, ",".join(cool)))
    # inventory gate on every increased unit, evaluated jointly on the final allocation
    if inc:
        # the gate is one deterministic predicate on expected units: same draw subset as the optimizer's search
        fe = FastEval(Portfolio(state, search_draws(state)), g["inventory_gate"])
        st = fe.state_of(s)
        x = fe.exposure(st)
        labels = [f"{units[i].unit_id}:{gate_label(float(x[i]), g['inventory_gate'])}" for i in inc]
        checks.append(_check("INVENTORY_GATE", fe.gate_ok(st), ",".join(labels)))
    else:
        checks.append(_check("INVENTORY_GATE", True, "no increases"))
    return checks


def policy_result(checks: list[dict], decision_class: str) -> dict:
    failed = [c["rule"] for c in checks if not c["passed"]]
    if failed:
        return {"status": "BLOCKED", "failed_rules": failed, "requires_review": False}
    return {"status": "PENDING_APPROVAL", "failed_rules": [], "requires_review": decision_class == "SAFETY",
            "mode": "APPROVE"}


def cooldown_units(db, at: datetime, days: int | None = None) -> set[str]:
    """Budgets ADAPT changed (verified legs) within the cooldown window."""
    days = days if days is not None else guardrails_config()["change"]["cooldown_days"]
    if not db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'exec' "
                    "AND table_name = 'saga_legs'"):
        return set()
    return {b for (b,) in db.query("""SELECT DISTINCT budget_id FROM exec.saga_legs
                                      WHERE state = 'VERIFIED' AND updated_at >= ?""", [at - timedelta(days=days)])}
