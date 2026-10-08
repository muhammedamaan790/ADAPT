"""Rule-based decision loop for a brand workspace (uploaded data only; the forecasting engine never runs here).

Every upload (and every approval) runs `cycle()` on the workspace's own database:

- Anomalies: per campaign, the last 3 days against the 14 before them. ROAS (needs the optional ads column
  conversion_value) moving 25%+ or CPC rising 30%+, on a campaign with 3%+ of recent spend, is flagged.
- Decisions:
  * reallocate (OPTIMIZATION): daily budget = the campaign's average spend over the last 7 days. Campaigns below 0.8x
    the blended ROAS lose up to 20% (weakest first, only as much as the receivers can absorb); campaigns above 1.2x
    gain up to 20%, in proportion to how far above they are. Total spend stays the same; a move whose expected value
    is not positive is not proposed. Expected 7-day revenue change = added spend x 0.7 x the
    receiver's ROAS (diminishing returns) - removed spend x the donor's ROAS, times the learned calibration factor.
  * restock_alert (OPERATIONAL): SKUs the Inventory page marks RESTOCK, with the 7-day revenue at risk.
  A new candidate supersedes the pending one of its type; a pending decision whose condition is gone expires.
- Approval records a change list (ADAPT is not connected to the brand's ad accounts): the execution says so and the
  ledger lists each budget to set by hand. Its effect is measured from later ads uploads.
- Outcomes: once ads data covers the 7 days after an approved reallocation took effect, measured = actual spend change x
  ROAS per leg (receivers at their post-change ROAS, donors at their pre-change ROAS). Verdict SUCCESS when it reaches
  half the prediction, FAILED below zero, INCONCLUSIVE when the budgets were not applied (under 30% of the planned
  change). Measured / raw prediction moves the calibration factor (30% weight, bounded 0.3..1.5), which scales the
  next prediction: the loop learns from what it recommended.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import date, datetime, timedelta

from adapt.api.views import has
from adapt.core.db import Database

MAX_CHANGE = 0.20
DONOR, RECEIVER = 0.8, 1.2
MARGINAL = 0.7
HORIZON = 7
POLICY = "uploads-rules-v1"
SCHEMA = """
CREATE SCHEMA IF NOT EXISTS eng;
CREATE TABLE IF NOT EXISTS eng.decisions (decision_id VARCHAR PRIMARY KEY, type VARCHAR, status VARCHAR,
    payload JSON, created_at TIMESTAMP, decided_at TIMESTAMP, actor VARCHAR, reason VARCHAR, effective_date DATE);
CREATE TABLE IF NOT EXISTS eng.anomalies (anomaly_id VARCHAR PRIMARY KEY, payload JSON, status VARCHAR,
    reason VARCHAR, detected_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS eng.executions (execution_id VARCHAR PRIMARY KEY, decision_id VARCHAR, payload JSON,
    started_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS eng.outcomes (outcome_id VARCHAR PRIMARY KEY, decision_id VARCHAR, payload JSON,
    matured_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS eng.events (id VARCHAR, logged_at TIMESTAMP, kind VARCHAR, message VARCHAR,
    decision_id VARCHAR);
CREATE TABLE IF NOT EXISTS eng.calibration (logged_at TIMESTAMP, outcome_id VARCHAR, decision_id VARCHAR,
    factor_before DOUBLE, factor_after DOUBLE);
"""


class BrandDecisionError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def ready(db: Database) -> bool:
    return has(db, "eng", "decisions")


def _rows(db: Database, sql: str, params: list | None = None) -> list[tuple]:
    return db.query(sql, params or [])


def _money(x: float) -> str:
    sign = "−" if x < 0 else ""
    x = abs(x)
    if x >= 1e7:
        return f"{sign}₹{x / 1e7:.2f} Cr"
    if x >= 1e5:
        return f"{sign}₹{x / 1e5:.2f} L"
    return f"{sign}₹{x:,.0f}"


def _round100(x: float) -> float:
    return float(round(x / 100.0) * 100)


def _has_conv(db: Database) -> bool:
    if not has(db, "stg", "upload_ads"):
        return False
    cols = {c for (c,) in _rows(db, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'stg' "
                                    "AND table_name = 'upload_ads'")}
    return "conversion_value" in cols and bool(
        _rows(db, "SELECT 1 FROM stg.upload_ads WHERE conversion_value IS NOT NULL LIMIT 1"))


def _daily(db: Database, conv: bool) -> dict[tuple[str, str], dict[date, dict]]:
    """(platform, budget_id) -> date -> {spend, clicks, impressions, conv}."""
    if not has(db, "stg", "upload_ads"):
        return {}
    cv = "sum(conversion_value)" if conv else "NULL"
    out: dict = {}
    for d, plat, bid, spend, clicks, impr, value in _rows(db, f"""
            SELECT CAST(date AS DATE), platform, budget_id, sum(spend), sum(clicks), sum(impressions), {cv}
            FROM stg.upload_ads GROUP BY 1, 2, 3"""):
        out.setdefault((plat, bid), {})[d] = {"spend": float(spend or 0), "clicks": float(clicks or 0),
                                              "impressions": float(impr or 0),
                                              "conv": None if value is None else float(value)}
    return out


def _sum(series: dict, days: list[date], key: str) -> float:
    return sum((series.get(d) or {}).get(key) or 0.0 for d in days)


def _span(last: date, start_back: int, length: int) -> list[date]:
    return [last - timedelta(days=start_back + i) for i in range(length)]


def entity(platform: str, budget_id: str) -> str:
    return f"{platform} campaign {budget_id}"


def factor(db: Database) -> float:
    if not has(db, "eng", "calibration"):
        return 1.0
    row = _rows(db, "SELECT factor_after FROM eng.calibration ORDER BY logged_at DESC LIMIT 1")
    return float(row[0][0]) if row else 1.0


def _event(cur, kind: str, message: str, decision_id: str | None, now: datetime) -> None:
    eid = hashlib.sha1(f"{kind}|{message}|{decision_id}|{now.isoformat()}".encode()).hexdigest()[:16]
    cur.execute("INSERT INTO eng.events VALUES (?, ?, ?, ?, ?)", [f"upl-ev-{eid}", now, kind, message, decision_id])


# ---- anomalies -------------------------------------------------------------------------------------------------------
def _detect(daily: dict, conv: bool) -> list[dict]:
    if not daily:
        return []
    last = max(d for s in daily.values() for d in s)
    recent, base = _span(last, 0, 3), _span(last, 3, 14)
    total_recent = sum(_sum(s, recent, "spend") for s in daily.values()) or 1.0
    found = []
    for (plat, bid), s in sorted(daily.items()):
        r_spend, b_spend = _sum(s, recent, "spend"), _sum(s, base, "spend")
        base_days = [d for d in base if (s.get(d) or {}).get("spend")]
        if r_spend <= 0 or b_spend <= 0 or len(base_days) < 7:
            continue
        share = r_spend / total_recent
        metrics = []
        if conv:
            r_roas, b_roas = _sum(s, recent, "conv") / r_spend, _sum(s, base, "conv") / b_spend
            daily_vals = [(s[d]["conv"] or 0) / s[d]["spend"] for d in base_days]
            recent_vals = [(s[d]["conv"] or 0) / s[d]["spend"] for d in recent if (s.get(d) or {}).get("spend")]
            metrics.append(("ROAS", r_roas, b_roas, daily_vals, recent_vals, (r_roas - b_roas) * r_spend,
                            "revenue impact over the 3-day window", 0.25, _sum(s, recent, "conv") == 0))
        r_clk, b_clk = _sum(s, recent, "clicks"), _sum(s, base, "clicks")
        if r_clk and b_clk:
            r_cpc, b_cpc = r_spend / r_clk, b_spend / b_clk
            daily_vals = [s[d]["spend"] / s[d]["clicks"] for d in base_days if s[d]["clicks"]]
            recent_vals = [s[d]["spend"] / s[d]["clicks"] for d in recent if (s.get(d) or {}).get("clicks")]
            metrics.append(("CPC", r_cpc, b_cpc, daily_vals, recent_vals, -(r_cpc - b_cpc) * r_clk,
                            "extra cost over the 3-day window", 0.30, False))
        for metric, cur_v, base_v, base_vals, rec_vals, impact, impact_label, threshold, collapse in metrics:
            if not base_v:
                continue
            rel = cur_v / base_v - 1
            bad_cpc = metric == "CPC" and rel < threshold
            if bad_cpc or abs(rel) < threshold or share < 0.03:
                continue
            med = statistics.median(base_vals)
            mad = statistics.median([abs(v - med) for v in base_vals]) * 1.4826
            z = (cur_v - med) / mad if mad else None
            consistent = bool(rec_vals) and all((v > med) == (rel > 0) for v in rec_vals)
            direction = "UP" if rel > 0 else "DOWN"
            if metric == "ROAS":
                r_cpc = r_spend / r_clk if r_clk else None
                b_cpc = b_spend / b_clk if b_clk else None
                cpc_rel = (r_cpc / b_cpc - 1) if r_cpc and b_cpc else 0.0
                driver = (f"Cost per click {'up' if cpc_rel > 0 else 'down'} {abs(cpc_rel):.0%}"
                          if abs(cpc_rel) >= abs(rel) / 2 else "Conversion value per click changed (offer, page "
                          "or audience), not the cost of clicks")
            else:
                driver = "Auction pressure or a bid/targeting change raised the cost of each click"
            found.append({
                "anomaly_id": f"upl-{metric.lower()}-{plat.lower()}-{bid}-{last:%Y%m%d}",
                "title": f"{metric} {direction.lower()} {abs(rel):.0%} on {entity(plat, bid)}",
                "entity": entity(plat, bid), "platform": plat, "metric": metric, "kind": "EFFICIENCY",
                "status": "OPEN", "direction": direction, "actual": round(cur_v, 4), "baseline": round(base_v, 4),
                "change": round(rel, 4), "impact": round(impact, 2), "impact_label": impact_label,
                "detected_at": datetime.combine(last, datetime.min.time()).replace(hour=12).isoformat(),
                "decision_id": None, "driver": driver, "provenance_inputs": ["UPLOADED"],
                "gates": [
                    {"id": "STAT", "label": "Robust z vs the prior 14 days", "passed": z is not None and abs(z) >= 2,
                     "detail": f"|z| {abs(z):.1f}" if z is not None else "flat baseline: not assessable"},
                    {"id": "SHIFT", "label": "Every recent day on the same side of the baseline",
                     "passed": consistent, "detail": f"{len(rec_vals)} recent days"},
                    {"id": "MATERIAL", "label": "Material share of recent spend", "passed": True,
                     "detail": f"{share:.0%} of spend in the last 3 days"},
                    {"id": "COLLAPSE", "label": "Collapse state", "passed": not collapse,
                     "detail": "no conversion value recorded in the window" if collapse else ""}],
                "causal": {"status": "NOT_ESTIMABLE", "reason": "uploads support a before/after comparison only; "
                           "no control group to separate this campaign from market-wide change",
                           "effect_pct": None, "lower_pct": None, "upper_pct": None, "assumptions": []},
                "resolution_reason": None,
                "_campaign": [plat, bid]})
    return found


# ---- decision candidates ---------------------------------------------------------------------------------------------
def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _prob_loss(p50: float, p10: float, p90: float) -> float:
    sigma = (p90 - p10) / 2.563 if p90 > p10 else 0.0
    if sigma <= 0:
        return 0.0 if p50 >= 0 else 1.0
    return round(0.5 * (1 + math.erf((-p50 / sigma) / math.sqrt(2))), 4)


def _base_decision(kind: str, cls: str, title: str, summary: str, legs: list, raw: float, fac: float,
                   checks: list, evidence: dict, extra: dict) -> dict:
    cal = raw * fac
    spread = 0.5 * abs(cal)
    p10, p90 = cal - spread, cal + spread
    core = {"type": kind, "legs": legs, "raw": round(raw, 2), "checks": checks, "extra": extra}
    h = _hash(core)
    return {
        "decision_id": f"dec-upl-{kind}-{h[:10]}", "title": title, "summary": summary, "class": cls, "type": kind,
        "objective": "GROWTH", "status": "PENDING_APPROVAL",
        "trigger": {"anomaly_id": extra.get("anomaly_id"), "opportunity_id": None}, "legs": legs,
        "expected": {"p10": round(p10, 2), "p50": round(cal, 2), "p90": round(p90, 2),
                     "prob_loss": _prob_loss(cal, p10, p90), "delta_net_revenue": round(cal, 2),
                     "raw_pred": round(raw, 2), "calibrated_pred": round(cal, 2)},
        "inventory_risk_after": {"kind": "PROJECTED_SHORTFALL", "by_sku": extra.get("shortfall", {})},
        "unallocated": round(extra.get("unallocated", 0.0), 2), "reserve_floor": 0.0,
        "budget_ceiling": round(extra.get("ceiling", 0.0), 2), "cost_of_inaction_7d": round(max(cal, 0.0), 2),
        "checks": checks, "evidence_ids": [], "why_not": extra.get("why_not", []), "snapshot_id": f"upl-{h[:12]}",
        "decision_hash": h, "policy_version": POLICY, "valuation_status": "AVAILABLE",
        "provenance_inputs": ["UPLOADED"], "created_at": "", "horizon_days": HORIZON, "_evidence": evidence,
        "_plan": extra.get("plan", {}), "_data_last": extra.get("data_last")}


def _reallocate(daily: dict, fac: float, inv_note: str, anomalies: list[dict]) -> dict | None:
    last = max(d for s in daily.values() for d in s)
    week = _span(last, 0, 7)
    days_total = len({d for s in daily.values() for d in s})
    camps = []
    for (plat, bid), s in sorted(daily.items()):
        spend, value = _sum(s, week, "spend"), _sum(s, week, "conv")
        if spend > 0:
            camps.append({"platform": plat, "budget_id": bid, "spend": spend, "conv": value, "roas": value / spend,
                          "daily": spend / 7})
    total_spend = sum(c["spend"] for c in camps)
    if not camps or not total_spend:
        return None
    blended = sum(c["conv"] for c in camps) / total_spend
    # a confirmed recent ROAS drop (robust z and every recent day below baseline) judges the campaign on its recent
    # ROAS, not a 7-day average the drop has only partly reached: the signal leads to the action
    for a in anomalies:
        gates = {g["id"]: g["passed"] for g in a["gates"]}
        if a["metric"] == "ROAS" and a["direction"] == "DOWN" and gates.get("STAT") and gates.get("SHIFT"):
            for c in camps:
                if [c["platform"], c["budget_id"]] == a["_campaign"]:
                    c["roas"], c["recent"] = a["actual"], True
    donors = [c for c in camps if c["roas"] < DONOR * blended and c["spend"] / total_spend >= 0.01]
    receivers = [c for c in camps if c["roas"] > RECEIVER * blended]
    if not donors or not receivers:
        return None
    # cut only what the receivers can absorb, weakest donor first: total spend stays put instead of shrinking
    pool = min(sum(MAX_CHANGE * c["daily"] for c in donors), sum(MAX_CHANGE * c["daily"] for c in receivers))
    cuts, need = {}, pool
    for c in sorted(donors, key=lambda c: c["roas"]):
        cuts[id(c)] = min(MAX_CHANGE * c["daily"], need)
        need -= cuts[id(c)]
    weights = {id(c): (c["roas"] - blended) * c["daily"] for c in receivers}
    add = {id(c): 0.0 for c in receivers}
    left, open_ = pool, list(receivers)
    while left > 1 and open_:
        wsum = sum(weights[id(c)] for c in open_)
        if wsum <= 0:
            break
        nxt, used = [], 0.0
        for c in open_:
            room = MAX_CHANGE * c["daily"] - add[id(c)]
            give = min(room, left * weights[id(c)] / wsum)
            add[id(c)] += give
            used += give
            if room - give > 1:
                nxt.append(c)
        left -= used
        open_ = nxt if used > 1 else []
    legs, plan = [], {}
    for c in donors:
        if cuts[id(c)] < 50:
            continue
        after = _round100(c["daily"] - cuts[id(c)])
        legs.append({"platform": c["platform"], "entity": entity(c["platform"], c["budget_id"]),
                     "budget_id": c["budget_id"], "before": _round100(c["daily"]), "after": after})
        plan[c["budget_id"]] = {"role": "donor", "roas_before": c["roas"], "platform": c["platform"]}
    for c in receivers:
        if add[id(c)] < 50:
            continue
        legs.append({"platform": c["platform"], "entity": entity(c["platform"], c["budget_id"]),
                     "budget_id": c["budget_id"], "before": _round100(c["daily"]),
                     "after": _round100(c["daily"] + add[id(c)])})
        plan[c["budget_id"]] = {"role": "receiver", "roas_before": c["roas"], "platform": c["platform"]}
    before = sum(leg["before"] for leg in legs)
    after = sum(leg["after"] for leg in legs)
    unallocated = max(before - after, 0.0)
    cut = sum(leg["before"] - leg["after"] for leg in legs if plan[leg["budget_id"]]["role"] == "donor")
    moved = sum(leg["after"] - leg["before"] for leg in legs if plan[leg["budget_id"]]["role"] == "receiver")
    roas = {c["budget_id"]: c["roas"] for c in camps}
    raw = sum((leg["after"] - leg["before"]) * HORIZON * roas[leg["budget_id"]] *
              (MARGINAL if plan[leg["budget_id"]]["role"] == "receiver" else 1.0) for leg in legs)
    if raw <= 0:  # diminishing returns on the receivers eat the gain: no proposal rather than a losing one
        return None
    why_not = [{"entity": entity(c["platform"], c["budget_id"]), "rule_id": "DAILY_CHANGE",
                "reason": "gains capped at +20% of its daily spend", "metric": f"ROAS {c['roas']:.2f}×"}
               for c in receivers if add[id(c)] >= MAX_CHANGE * c["daily"] - 1]
    why_not += [{"entity": entity(c["platform"], c["budget_id"]), "rule_id": "MIN_SHARE",
                 "reason": "below 0.8× blended ROAS but under 1% of spend: not worth a change",
                 "metric": f"ROAS {c['roas']:.2f}×"}
                for c in camps if c["roas"] < DONOR * blended and c not in donors]
    checks = [
        {"id": "BUDGET_CEILING", "label": "Total spend does not rise", "passed": after <= before,
         "detail": f"{_money(after)}/day after ≤ {_money(before)}/day before; {_money(unallocated)}/day left "
                   "unallocated"},
        {"id": "DAILY_CHANGE", "label": "Daily change limit", "passed": all(
            abs(leg["after"] - leg["before"]) <= MAX_CHANGE * leg["before"] + 100 for leg in legs),
         "detail": "every campaign stays within ±20% of its average daily spend over the last 7 days"},
        {"id": "DEPENDENCY_HEALTH", "label": "Required data", "passed": days_total >= 10,
         "detail": f"{days_total} days of ads data with conversion value (10 needed)"},
        {"id": "INVENTORY_GATE", "label": "Inventory exposure", "passed": True, "detail": inv_note},
        {"id": "EXECUTION_STATE", "label": "How it is applied", "passed": True,
         "detail": "ADAPT is not connected to this brand's ad accounts: approval records a change list to apply in "
                   "Meta Ads Manager / Google Ads; the next ads upload measures the result"}]
    donor_ids = {leg["budget_id"] for leg in legs if plan[leg["budget_id"]]["role"] == "donor"}
    trig = [a for a in anomalies if a["metric"] == "ROAS" and a["direction"] == "DOWN"
            and a["_campaign"][1] in donor_ids]
    trig.sort(key=lambda a: a["impact"])
    n_d, n_r = len(donor_ids), len(legs) - len(donor_ids)
    title = f"Move {_money(moved)}/day from {n_d} weak campaign{'s' if n_d != 1 else ''} to " \
            f"{n_r} stronger one{'s' if n_r != 1 else ''}"
    summary = (f"Blended ROAS over the last 7 days is {blended:.2f}×. Cut {n_d} campaign{'s' if n_d != 1 else ''} "
               f"below {DONOR * blended:.2f}× by 20% ({_money(cut)}/day) and add {_money(moved)}/day to "
               f"{n_r} above {RECEIVER * blended:.2f}×"
               + (f"; keep {_money(unallocated)}/day unspent rather than force it into capped campaigns"
                  if unallocated >= 100 else "") + ".")
    chart = []
    for d in sorted({d for s in daily.values() for d in s})[-21:]:
        dsp = sum((daily[(c["platform"], c["budget_id"])].get(d) or {}).get("spend") or 0 for c in donors)
        dcv = sum((daily[(c["platform"], c["budget_id"])].get(d) or {}).get("conv") or 0 for c in donors)
        asp = sum((s.get(d) or {}).get("spend") or 0 for s in daily.values())
        acv = sum((s.get(d) or {}).get("conv") or 0 for s in daily.values())
        chart.append({"date": d.isoformat(), "actual": round(dcv / dsp, 3) if dsp else 0.0,
                      "baseline": round(acv / asp, 3) if asp else 0.0})
    evidence = {"chart": chart, "chart_metric": "Daily ROAS: campaigns losing budget (line) vs the whole account "
                "(baseline)", "decomposition_kind": "ROAS",
                "decomposition": [{"label": leg["entity"], "value": round(roas[leg["budget_id"]], 3)} for leg in legs],
                "decomposition_total": round(blended, 3),
                "drivers": [{"id": f"roas-{c['budget_id']}", "title": f"{entity(c['platform'], c['budget_id'])}: "
                             f"ROAS {c['roas']:.2f}× vs {blended:.2f}× blended", "score": round(min(1.0, abs(
                                 c["roas"] / blended - 1)), 3), "level": "ACCOUNTING IDENTITY",
                             "detail": (f"recent 3-day ROAS after a confirmed drop; {_money(c['conv'])} conversion "
                                        f"value on {_money(c['spend'])} spend over 7 days" if c.get("recent") else
                                        f"{_money(c['conv'])} conversion value on {_money(c['spend'])} spend, last 7 "
                                        "days"), "observations": [f"{_money(c['daily'])}/day average spend"],
                             "source": "stg.upload_ads", "available_at": last.isoformat()}
                            for c in donors + receivers][:8]}
    return _base_decision("reallocate", "OPTIMIZATION", title, summary, legs, raw, fac, checks, evidence,
                          {"unallocated": unallocated, "ceiling": before, "why_not": why_not, "plan": plan,
                           "anomaly_id": trig[0]["anomaly_id"] if trig else None, "data_last": last.isoformat()})


def _restock(db: Database) -> dict | None:
    if not (has(db, "stg", "upload_inventory") and has(db, "stg", "upload_orders")):
        return None
    from adapt.api.routers.data import upload_inventory_view

    inv = upload_inventory_view(db)
    skus = [s for s in inv.skus if s.action == "RESTOCK" and s.units_28d_avg > 0]
    if not skus:
        return None
    price = dict(_rows(db, "SELECT sku, sum(net_revenue) / nullif(sum(quantity), 0) FROM stg.upload_orders "
                           "GROUP BY sku"))
    short = {s.sku: max(0.0, round(HORIZON * s.units_28d_avg - s.available, 1)) for s in skus}
    at_risk = sum(short[s.sku] * float(price.get(s.sku) or 0) for s in skus)
    top = sorted(skus, key=lambda s: -short[s.sku])[:5]
    title = f"Restock {len(skus)} SKU{'s' if len(skus) != 1 else ''} before they sell out"
    summary = (f"{len(skus)} SKUs are at or below their reorder point (safety stock + 7 days of demand). "
               f"About {_money(at_risk)} of sales over the next 7 days is at risk without a reorder.")
    checks = [{"id": "INVENTORY_GATE", "label": "Stock below reorder point", "passed": True,
               "detail": "; ".join(f"{s.sku}: {s.available} available, {s.units_28d_avg:.1f}/day" for s in top)},
              {"id": "EXECUTION_STATE", "label": "How it is applied", "passed": True,
               "detail": "approval records the reorder request; no budget changes"}]
    evidence = {"chart": [], "chart_metric": "", "decomposition_kind": "NOT_APPLICABLE", "decomposition": [],
                "decomposition_total": 0.0,
                "drivers": [{"id": f"sku-{s.sku}", "title": f"{s.sku}: {s.cover_days or 0:.1f} days of cover",
                             "score": round(min(1.0, short[s.sku] / max(HORIZON * s.units_28d_avg, 1)), 3),
                             "level": "ACCOUNTING IDENTITY", "detail": s.reason,
                             "observations": [f"reorder point {s.reorder_point:.0f}", f"safety stock "
                                              f"{s.safety_stock:.0f}"], "source": "stg.upload_inventory",
                             "available_at": inv.as_of} for s in top]}
    return _base_decision("restock_alert", "OPERATIONAL", title, summary, [], at_risk, 1.0, checks, evidence,
                          {"shortfall": short})


# ---- the cycle -------------------------------------------------------------------------------------------------------
def cycle(db: Database, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    db.write(lambda cur: cur.execute(SCHEMA))
    conv = _has_conv(db)
    daily = _daily(db, conv)
    found = _detect(daily, conv)
    fac = factor(db)
    inv_note = "no inventory upload"
    restock = _restock(db)
    if has(db, "stg", "upload_inventory"):
        inv_note = (f"{len(restock['inventory_risk_after']['by_sku'])} SKUs need a reorder (see the restock "
                    "proposal); uploads carry no campaign→SKU map, so stock cannot block a campaign"
                    if restock else "no SKU is at its reorder point")
    candidates = [c for c in (_reallocate(daily, fac, inv_note, found) if conv and daily else None, restock) if c]
    measured = _measure(db, daily, conv, now)

    def write(cur):
        for a in found:
            old = cur.execute("SELECT status, reason FROM eng.anomalies WHERE anomaly_id = ?",
                              [a["anomaly_id"]]).fetchall()
            if old:
                cur.execute("UPDATE eng.anomalies SET payload = ? WHERE anomaly_id = ?",
                            [json.dumps(a), a["anomaly_id"]])
            else:
                cur.execute("INSERT INTO eng.anomalies VALUES (?, ?, 'OPEN', NULL, ?)",
                            [a["anomaly_id"], json.dumps(a), now])
                _event(cur, "anomaly_detected", a["title"], None, now)
        kinds = {c["type"] for c in candidates}
        for c in candidates:
            if cur.execute("SELECT 1 FROM eng.decisions WHERE decision_id = ?", [c["decision_id"]]).fetchall():
                continue
            for (old_id,) in cur.execute("SELECT decision_id FROM eng.decisions WHERE type = ? AND status = "
                                         "'PENDING_APPROVAL'", [c["type"]]).fetchall():
                cur.execute("UPDATE eng.decisions SET status = 'SUPERSEDED', decided_at = ? WHERE decision_id = ?",
                            [now, old_id])
                _event(cur, "decision_superseded", "Superseded by a newer proposal from the latest upload", old_id,
                       now)
                c["follows"] = old_id
            c["created_at"] = now.isoformat(timespec="seconds")
            cur.execute("INSERT INTO eng.decisions VALUES (?, ?, 'PENDING_APPROVAL', ?, ?, NULL, NULL, NULL, NULL)",
                        [c["decision_id"], c["type"], json.dumps(c), now])
            _event(cur, "decision_created", f"Proposal: {c['title']}", c["decision_id"], now)
        for did, kind in cur.execute("SELECT decision_id, type FROM eng.decisions WHERE status = "
                                     "'PENDING_APPROVAL'").fetchall():
            if kind not in kinds:
                cur.execute("UPDATE eng.decisions SET status = 'EXPIRED', decided_at = ? WHERE decision_id = ?",
                            [now, did])
                _event(cur, "decision_expired", "Expired: the latest upload no longer shows the condition", did, now)
        for o in measured:
            cur.execute("INSERT INTO eng.outcomes VALUES (?, ?, ?, ?)",
                        [o["outcome_id"], o["decision_id"], json.dumps(o), now])
            if o["calibration_applied"]:
                cur.execute("INSERT INTO eng.calibration VALUES (?, ?, ?, ?, ?)",
                            [now, o["outcome_id"], o["decision_id"], o["factor_before"], o["factor_after"]])
            cur.execute("UPDATE eng.decisions SET status = 'EXECUTED' WHERE decision_id = ?", [o["decision_id"]])
            _event(cur, "outcome_measured", f"Outcome {o['verdict'].lower()}: measured {_money(o['measured'])} vs "
                   f"predicted {_money(o['predicted'])}", o["decision_id"], now)

    db.write(write)
    return {"anomalies": len(found), "decisions": [c["decision_id"] for c in candidates], "outcomes": len(measured)}


def _measure(db: Database, daily: dict, conv: bool, now: datetime) -> list[dict]:
    if not (conv and ready(db)):
        return []
    done = {d for (d,) in _rows(db, "SELECT decision_id FROM eng.outcomes")}
    last = max((d for s in daily.values() for d in s), default=None)
    out = []
    fac = factor(db)
    for did, payload, eff in _rows(db, "SELECT decision_id, payload, effective_date FROM eng.decisions WHERE "
                                       "status = 'APPROVED' AND type = 'reallocate' ORDER BY decided_at"):
        if did in done or eff is None or last is None or last < eff + timedelta(days=HORIZON - 1):
            continue
        d = json.loads(payload)
        window = [eff + timedelta(days=i) for i in range(HORIZON)]
        measured = planned = applied = actual_rev = 0.0
        for leg in d["legs"]:
            s = daily.get((leg["platform"], leg["budget_id"]), {})
            info = d["_plan"][leg["budget_id"]]
            spend_after = _sum(s, window, "spend")
            delta = spend_after - leg["before"] * HORIZON
            planned += abs(leg["after"] - leg["before"]) * HORIZON
            applied += abs(delta) if (delta > 0) == (leg["after"] > leg["before"]) else 0.0
            roas_after = _sum(s, window, "conv") / spend_after if spend_after else info["roas_before"]
            measured += delta * (roas_after if info["role"] == "receiver" else info["roas_before"])
            actual_rev += _sum(s, window, "conv")
        raw = d["expected"]["raw_pred"]
        compliance = applied / planned if planned else 0.0
        if compliance < 0.3:
            verdict, note = "INCONCLUSIVE", f"only {compliance:.0%} of the planned budget change shows in the ads data"
        elif measured < 0:
            verdict, note = "FAILED", "the moved spend earned less than it did before"
        elif measured >= 0.5 * d["expected"]["calibrated_pred"]:
            verdict, note = "SUCCESS", "reached at least half the prediction"
        else:
            verdict, note = "NEUTRAL", "positive but under half the prediction"
        applied_cal = verdict != "INCONCLUSIVE" and raw > 0
        ratio = max(0.0, min(2.0, measured / raw)) if raw > 0 else 1.0
        after = round(max(0.3, min(1.5, 0.7 * fac + 0.3 * ratio)), 4) if applied_cal else fac
        out.append({"outcome_id": f"out-{did}", "decision_id": did, "world": "REAL", "class": d["class"],
                    "verdict": verdict, "predicted": d["expected"]["calibrated_pred"], "measured": round(measured, 2),
                    "counterfactual": round(actual_rev - measured, 2), "factor_before": fac, "factor_after": after,
                    "matured_at": now.isoformat(timespec="seconds"),
                    "method": f"7 days from {eff.isoformat()} in the uploaded ads data: actual spend change × ROAS "
                              "per campaign (receivers at their post-change ROAS, donors at their pre-change ROAS)",
                    "calibration_applied": applied_cal,
                    "calibration_note": f"{note}; calibration {fac:.2f} → {after:.2f}" if applied_cal else note})
        fac = after
    return out


# ---- reads and actions -----------------------------------------------------------------------------------------------
def _public(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def decisions(db: Database) -> list[dict]:
    if not ready(db):
        return []
    out = []
    for status, payload in _rows(db, "SELECT status, payload FROM eng.decisions ORDER BY created_at DESC, "
                                     "decision_id"):
        d = json.loads(payload)
        d["status"] = status
        out.append(_public(d))
    return out


def decision(db: Database, decision_id: str) -> dict | None:
    return next((d for d in decisions(db) if d["decision_id"] == decision_id), None)


def evidence(db: Database, decision_id: str) -> dict | None:
    row = _rows(db, "SELECT payload FROM eng.decisions WHERE decision_id = ?", [decision_id]) if ready(db) else []
    return {"decision_id": decision_id, **json.loads(row[0][0])["_evidence"]} if row else None


def approve(db: Database, decision_id: str, decision_hash: str, actor: str, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    row = _rows(db, "SELECT status, payload FROM eng.decisions WHERE decision_id = ?", [decision_id]) \
        if ready(db) else []
    if not row:
        raise BrandDecisionError(404, f"decision {decision_id} not found")
    status, payload = row[0]
    d = json.loads(payload)
    if d["decision_hash"] != decision_hash:
        raise BrandDecisionError(409, "the decision changed; review it again")
    if status != "PENDING_APPROVAL":
        raise BrandDecisionError(409, f"decision is {status.lower().replace('_', ' ')}, not pending")
    eff = (date.fromisoformat(d["_data_last"]) + timedelta(days=1)) if d.get("_data_last") else None
    legs = [{**leg, "mode": "MOCK", "external_state": "PLANNED", "sim_sync_state": "NOT_REQUIRED",
             "read_back_budget": None} for leg in d["legs"]]
    detail = ("Change list recorded. ADAPT is not connected to this brand's ad accounts: set these daily budgets in "
              "Meta Ads Manager / Google Ads from " + (eff.isoformat() if eff else "now") + "; the ads upload that "
              "covers the 7 days after that measures the outcome."
              if legs else "Reorder request recorded; no budget changes.")
    execution = {"execution_id": f"exe-{decision_id}", "decision_id": decision_id, "state": "RESOLVED_MANUALLY",
                 "legs": legs, "started_at": now.isoformat(timespec="seconds"), "detail": detail}

    def write(cur):
        cur.execute("UPDATE eng.decisions SET status = 'APPROVED', decided_at = ?, actor = ?, effective_date = ? "
                    "WHERE decision_id = ?", [now, actor, eff, decision_id])
        cur.execute("INSERT INTO eng.executions VALUES (?, ?, ?, ?)",
                    [execution["execution_id"], decision_id, json.dumps(execution), now])
        _event(cur, "decision_approved", f"Approved by {actor}: {d['title']}", decision_id, now)

    db.write(write)
    return decision(db, decision_id)


def reject(db: Database, decision_id: str, decision_hash: str, actor: str, reason: str,
           now: datetime | None = None) -> dict:
    now = now or datetime.now()
    d = decision(db, decision_id)
    if d is None:
        raise BrandDecisionError(404, f"decision {decision_id} not found")
    if d["decision_hash"] != decision_hash:
        raise BrandDecisionError(409, "the decision changed; review it again")
    if d["status"] != "PENDING_APPROVAL":
        raise BrandDecisionError(409, f"decision is {d['status'].lower().replace('_', ' ')}, not pending")

    def write(cur):
        cur.execute("UPDATE eng.decisions SET status = 'REJECTED', decided_at = ?, actor = ?, reason = ? "
                    "WHERE decision_id = ?", [now, actor, reason, decision_id])
        _event(cur, "decision_rejected", f"Rejected by {actor}: {reason}", decision_id, now)

    db.write(write)
    return decision(db, decision_id)


def anomalies(db: Database) -> list[dict]:
    if not ready(db):
        return []
    decs = [(d["decision_id"], {leg["budget_id"] for leg in d["legs"]}) for d in decisions(db)]
    out = []
    for payload, status, reason in _rows(db, "SELECT payload, status, reason FROM eng.anomalies"):
        a = json.loads(payload)
        bid = a["_campaign"][1]
        a.update(status=status, resolution_reason=reason,
                 decision_id=next((did for did, bids in decs if bid in bids), None))
        out.append(_public(a))
    return sorted(out, key=lambda a: -abs(a["impact"]))


def anomaly(db: Database, anomaly_id: str) -> dict | None:
    return next((a for a in anomalies(db) if a["anomaly_id"] == anomaly_id), None)


def set_anomaly_status(db: Database, anomaly_id: str, status: str, reason: str) -> dict | None:
    if anomaly(db, anomaly_id) is None:
        return None
    db.write(lambda cur: cur.execute("UPDATE eng.anomalies SET status = ?, reason = ? WHERE anomaly_id = ?",
                                     [status, reason or None, anomaly_id]))
    return anomaly(db, anomaly_id)


def executions(db: Database) -> list[dict]:
    if not ready(db):
        return []
    return [json.loads(p) for (p,) in _rows(db, "SELECT payload FROM eng.executions ORDER BY started_at DESC")]


def ledger(db: Database) -> list[dict]:
    out = []
    for e in executions(db):
        for i, leg in enumerate(e["legs"]):
            out.append({"ledger_id": f"{e['execution_id']}-{i}", "execution_id": e["execution_id"],
                        "decision_id": e["decision_id"], "at": e["started_at"], "action": "SET_BUDGET",
                        "budget_id": leg["budget_id"], "entity": leg["entity"], "platform": leg["platform"],
                        "before": leg["before"], "after": leg["after"], "mode": "MOCK",
                        "request_id": e["execution_id"], "note": "change list: set this daily budget in the ad "
                                                                 "platform (ADAPT is not connected to it)"})
    return out


def outcomes(db: Database) -> list[dict]:
    if not ready(db):
        return []
    return [json.loads(p) for (p,) in _rows(db, "SELECT payload FROM eng.outcomes ORDER BY matured_at DESC")]


def events(db: Database) -> list[dict]:
    if not has(db, "eng", "events"):
        return []
    return [{"id": i, "at": at.isoformat(timespec="seconds"), "kind": k, "message": msg, "decision_id": did}
            for i, at, k, msg, did in _rows(db, "SELECT id, logged_at, kind, message, decision_id FROM eng.events "
                                                "ORDER BY logged_at DESC LIMIT 100")]


def calibration(db: Database) -> dict:
    updates = ([{"outcome_id": o, "decision_id": d, "before": b, "after": a, "at": at.isoformat(timespec="seconds")}
                for at, o, d, b, a in _rows(db, "SELECT logged_at, outcome_id, decision_id, factor_before, "
                                                "factor_after FROM eng.calibration ORDER BY logged_at")]
               if has(db, "eng", "calibration") else [])
    return {"factor": factor(db), "updates": updates,
            "note": "Uploaded-data workspace: each measured reallocation moves the factor 30% toward measured / "
                    "predicted (bounded 0.3–1.5); the next proposal's expected value is scaled by it."}


def accuracy(db: Database) -> dict:
    rows = [o for o in outcomes(db) if o["verdict"] != "INCONCLUSIVE"]
    mae = sum(abs(o["measured"] - o["predicted"]) for o in rows) / len(rows) if rows else None
    return {"sample_count": len(rows), "mae": round(mae, 2) if mae is not None else None,
            "note": "Mean absolute error of measured vs predicted 7-day revenue change, uploaded-data outcomes."}


def feedback(db: Database) -> list[dict]:
    return [{"outcome_id": o["outcome_id"], "decision_id": o["decision_id"], "eligible": o["calibration_applied"],
             "reason": o["calibration_note"]} for o in outcomes(db)]
