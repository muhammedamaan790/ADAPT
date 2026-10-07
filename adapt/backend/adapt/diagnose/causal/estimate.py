"""Gated synthetic control on the canonical state (Stage 2 ★, spec §7.1 level 3 "Gates", §22.9 "Causal").

Estimand: the % effect on the incident's ROAS-family metric, estimated on the treated units' daily reconciled ROAS
(attributed net revenue / spend; POAS and CPA incidents use the same ROAS series, POAS can be <= 0 so it is never
logged). effect% = observed window ROAS / counterfactual window ROAS - 1. The rupee figure is an ACCOUNTING
TRANSLATION of that effect (effect% x counterfactual revenue x the treated units' observed cm_before_ads), never
"causal CAA". Other metric families: NOT_ESTIMABLE (no Stage 2 estimator).

Control pool (all app-observable; hidden injected scenarios are never used, truth isolation):
  every campaign except the treated ones, minus
  - campaigns sharing SKUs or audiences with a treated unit (same product set)
  - campaigns with an event anywhere in the full estimation interval (placebo fit start .. post end): an observed
    budget or status change (core.budget_history / campaign_state_history), an open ADAPT incident whose window
    overlaps it, or a price change on one of its mapped SKUs (core.pricing_snapshots)
  - series with any non-positive day (no log of 0, no epsilon): a non-positive TREATED series is NOT_ESTIMABLE
Gates, all required: history, positivity, >= 3 controls, pre-fit sMAPE <= 15% on the 7-day holdout, in-time placebo
(14 days earlier: its % CI includes 0 AND |its rupee translation| < the incident's materiality threshold m), and
>= 50 post-period conversions. Any failure -> "No causal estimate: <reason>"; the gate values are always reported.
Passing the gates is a set of diagnostics, not proof of identification (the assumptions are listed with the result).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

from adapt.diagnose.causal.synthetic_control import synthetic_control

CONFIG = Path(__file__).resolve().parents[2] / "config" / "causal.yaml"
ROAS_FAMILY = ("ROAS", "POAS", "CPA")
ASSUMPTIONS = ["no unobserved time-varying confounding", "valid controls (no spillover from the treated units)",
               "no interference between units"]
ESTIMATED, NOT_ESTIMABLE = "ESTIMATED", "NOT_ESTIMABLE"

DDL = """
CREATE SCHEMA IF NOT EXISTS intel;
CREATE TABLE IF NOT EXISTS intel.causal_estimates (
    estimate_id VARCHAR NOT NULL, anomaly_id VARCHAR NOT NULL, as_of TIMESTAMP NOT NULL, method VARCHAR NOT NULL,
    status VARCHAR NOT NULL, reason VARCHAR, metric VARCHAR, estimand VARCHAR, effect_pct DOUBLE, ci_lo DOUBLE,
    ci_hi DOUBLE, inr_translation DOUBLE, gates JSON NOT NULL, controls JSON, details JSON,
    PRIMARY KEY (estimate_id, as_of)
);
"""


@lru_cache
def causal_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["synthetic_control"]


def _in(ids) -> str:
    return ", ".join("?" * len(ids))


def _daily(db, ids: list[str], lo: date, hi: date) -> dict[str, np.ndarray]:
    """Per-day totals of the given campaigns over [lo, hi] (complete calendar, zeros where absent)."""
    rows = {d: r for d, *r in db.query(f"""SELECT date, sum(attributed_net_revenue), sum(spend), sum(attributed_orders),
                                                  sum(attributed_cba) FROM marts.campaign_daily
                                           WHERE campaign_id IN ({_in(ids)}) AND date BETWEEN ? AND ? GROUP BY 1""",
                                        [*ids, lo, hi])}
    days = [lo + timedelta(days=k) for k in range((hi - lo).days + 1)]
    cols = np.array([[float(x or 0) for x in rows.get(d, (0, 0, 0, 0))] for d in days])
    return {"revenue": cols[:, 0], "spend": cols[:, 1], "orders": cols[:, 2], "cba": cols[:, 3]}


def _event_campaigns(db, lo: date, hi: date) -> dict[str, str]:
    """campaign_id -> why it is excluded: an app-observable event inside [lo, hi]."""
    out: dict[str, str] = {}
    budgets = {}
    for cid, bid in db.query("SELECT campaign_id, budget_id FROM core.campaigns"):
        budgets.setdefault(bid, []).append(cid)
    for (bid,) in db.query("""SELECT DISTINCT entity_id FROM core.budget_history WHERE source = 'observed_change'
                              AND effective_from BETWEEN ? AND ?""", [lo, hi]):
        for cid in budgets.get(bid, []):
            out.setdefault(cid, "budget change in the estimation interval")
    for (cid,) in db.query("""SELECT DISTINCT entity_id FROM core.campaign_state_history
                              WHERE source = 'observed_change' AND effective_from BETWEEN ? AND ?""", [lo, hi]):
        out.setdefault(cid, "status change in the estimation interval")
    if db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'intel' AND table_name = 'anomalies'"):
        for ids, in db.query("""SELECT entity_ids FROM intel.anomalies WHERE is_incident
                                AND window_start <= ? AND window_end >= ?""", [hi, lo]):
            for cid in json.loads(ids):
                out.setdefault(cid, "ADAPT incident in the estimation interval")
    changed = {s for (s,) in db.query("""SELECT DISTINCT p.sku FROM core.pricing_snapshots p
                                         WHERE p.valid_from BETWEEN ? AND ? AND EXISTS (
                                           SELECT 1 FROM core.pricing_snapshots q WHERE q.sku = p.sku
                                             AND q.valid_from < p.valid_from AND q.price <> p.price)""", [lo, hi])}
    if changed:
        for cid, sku in db.query(f"SELECT campaign_id, sku FROM core.campaign_sku WHERE attribution_weight > 0 "
                                 f"AND sku IN ({_in(list(changed))})", list(changed)):
            out.setdefault(cid, f"price change on {sku} in the estimation interval")
    return out


def _gate(passed: bool, **values) -> dict:
    return {"passed": bool(passed), **values}


def build_panel(db, inc) -> dict:
    """The estimation panel for one incident (reads only): treated daily totals and eligible control log-ROAS
    series over the full estimation interval, with the exclusion reason of every other campaign."""
    cfg = causal_config()
    f, h, off = int(cfg["fit_days"]), int(cfg["holdout_days"]), int(cfg["placebo_offset_days"])
    lo = inc.post_start - timedelta(days=off + f + h)         # placebo fit start: the full estimation interval
    product_sets = {p for (p,) in db.query(f"SELECT DISTINCT product_set FROM core.campaigns WHERE campaign_id IN "
                                           f"({_in(inc.campaign_ids)})", inc.campaign_ids)}
    events = _event_campaigns(db, lo, inc.post_end)
    excluded: dict[str, str] = {}
    ids, cols = [], []
    for cid, ps in db.query("SELECT campaign_id, product_set FROM core.campaigns ORDER BY campaign_id"):
        if cid in inc.campaign_ids:
            continue
        if ps in product_sets:
            excluded[cid] = "shares SKUs / audiences with a treated unit"
            continue
        if cid in events:
            excluded[cid] = events[cid]
            continue
        s = _daily(db, [cid], lo, inc.post_end)
        if (s["revenue"] <= 0).any() or (s["spend"] <= 0).any():
            excluded[cid] = "non-positive values (cannot be log-transformed)"
            continue
        ids.append(cid)
        cols.append(np.log(s["revenue"] / s["spend"]))
    return {"lo": lo, "start": off + f + h, "n_post": (inc.post_end - inc.post_start).days + 1,
            "treated": _daily(db, inc.campaign_ids, lo, inc.post_end), "ids": ids,
            "X": np.column_stack(cols) if cols else np.zeros((0, 0)), "excluded": excluded}


def estimate(db, inc, materiality_m: float | None = None) -> dict:
    """Gated synthetic control for one incident (reads only). inc: diagnose.evidence.Incident."""
    cfg = causal_config()
    f, h, off = int(cfg["fit_days"]), int(cfg["holdout_days"]), int(cfg["placebo_offset_days"])
    out = {"method": "synthetic_control", "metric": inc.metric, "claim_level": "QUASI_EXPERIMENTAL",
           "estimand": "% effect on the treated units' reconciled ROAS (window totals, original scale)",
           "assumptions": ASSUMPTIONS, "gates": {}, "status": NOT_ESTIMABLE, "reason": None}
    if inc.metric not in ROAS_FAMILY:
        out["reason"] = f"no causal estimator for the {inc.metric} family (Stage 2: ROAS/revenue family only)"
        return out
    first = db.query(f"SELECT min(date) FROM marts.campaign_daily WHERE campaign_id IN ({_in(inc.campaign_ids)})",
                     inc.campaign_ids)[0][0]
    available = (inc.post_start - first).days if first else 0
    needed = off + f + h
    gates = out["gates"]
    gates["history"] = _gate(available >= needed, needed_pre_days=needed, available_pre_days=available)
    if not gates["history"]["passed"]:
        out["reason"] = f"history: {available} pre-period days < {needed} (28 pre incl. 7 holdout + 14 placebo offset)"
        return out
    p = build_panel(db, inc)
    treated, ids, X, s_real, n_post = p["treated"], p["ids"], p["X"], p["start"], p["n_post"]
    pos_t = bool((treated["revenue"] > 0).all() and (treated["spend"] > 0).all())
    gates["positivity"] = _gate(pos_t, treated_nonpositive_days=int(((treated["revenue"] <= 0)
                                                                     | (treated["spend"] <= 0)).sum()))
    if not pos_t:
        out["reason"] = "non-positive values in the treated series (no log of 0, no epsilon)"
        return out
    gates["controls"] = _gate(len(ids) >= int(cfg["min_controls"]), eligible=len(ids),
                              required=int(cfg["min_controls"]), excluded=p["excluded"])
    if not gates["controls"]["passed"]:
        out["reason"] = f"fewer than {cfg['min_controls']} eligible controls ({len(ids)})"
        return out

    y = np.log(treated["revenue"] / treated["spend"])
    agg = treated["spend"][s_real:s_real + n_post]
    seed = int(hashlib.sha256(f"{inc.anomaly_id}|{inc.post_start}".encode()).hexdigest()[:8], 16)
    sc = synthetic_control(y, X, ids, s_real, n_post, agg, cfg, seed)
    pre = slice(s_real - 28, s_real)
    cm = float(treated["cba"][pre].sum() / treated["revenue"][pre].sum()) if treated["revenue"][pre].sum() else 0.0
    gates["pre_fit"] = _gate(sc.holdout_smape <= cfg["smape_max"], holdout_smape=round(sc.holdout_smape, 4),
                             max=cfg["smape_max"])
    placebo_start = s_real - off
    pl = synthetic_control(y, X, ids, placebo_start, n_post, treated["spend"][placebo_start:placebo_start + n_post],
                           cfg, seed + 1)
    pl_inr = pl.effect_pct * pl.counterfactual_level_sum * cm
    m = float(materiality_m) if materiality_m is not None else max(2000.0, 0.05 * float(treated["cba"][pre].mean()))
    pl_ok = pl.ci_lo <= 0 <= pl.ci_hi and abs(pl_inr) < m
    gates["placebo"] = _gate(pl_ok, effect_pct=round(pl.effect_pct, 4), ci=[round(pl.ci_lo, 4), round(pl.ci_hi, 4)],
                             inr_translation=round(pl_inr, 2), materiality_m=round(m, 2), offset_days=off)
    conv = float(treated["orders"][s_real:s_real + n_post].sum())
    gates["post_conversions"] = _gate(conv >= cfg["min_post_conversions"], conversions=conv,
                                      required=cfg["min_post_conversions"])
    out.update(controls=[{"campaign_id": c, "weight": round(w, 4)} for c, w in zip(sc.controls, sc.weights,
                                                                                   strict=True)],
               details={"lambda": sc.lam, "cm_before_ads": cm, "level": cfg.get("level", "demeaned"),
                        "windows": {"fit_days": f, "holdout_days": h, "post_days": n_post}})
    failed = [k for k in ("pre_fit", "placebo", "post_conversions") if not gates[k]["passed"]]
    if failed:
        reasons = {"pre_fit": f"pre-fit sMAPE {sc.holdout_smape:.1%} > {cfg['smape_max']:.0%} on the 7-day holdout",
                   "placebo": "in-time placebo found an effect (its CI excludes 0 or its rupee translation >= m)",
                   "post_conversions": f"{conv:.0f} post-period conversions < {cfg['min_post_conversions']}"}
        out["reason"] = reasons[failed[0]]
        return out  # no effect number is shown when a gate fails ("No causal estimate: <reason>")
    out.update(status=ESTIMATED, effect_pct=sc.effect_pct, ci=[sc.ci_lo, sc.ci_hi],
               observed_roas=sc.observed, counterfactual_roas=sc.counterfactual,
               inr_translation=sc.effect_pct * sc.counterfactual_level_sum * cm,
               inr_label="accounting translation of the estimated effect (not causal CAA)")
    return out


def persist(cur, anomaly_id: str, as_of, est: dict) -> str:
    cur.execute(DDL)
    eid = f"CSL-{anomaly_id}"
    ci = est.get("ci") or [None, None]
    cur.execute("INSERT OR REPLACE INTO intel.causal_estimates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [eid, anomaly_id, as_of, est["method"], est["status"], est["reason"], est["metric"], est["estimand"],
                 est.get("effect_pct"), ci[0], ci[1], est.get("inr_translation"), json.dumps(est["gates"], default=str),
                 json.dumps(est.get("controls")), json.dumps({"details": est.get("details"),
                                                              "assumptions": est["assumptions"]}, default=str)])
    return eid
