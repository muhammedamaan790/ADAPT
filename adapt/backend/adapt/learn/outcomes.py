"""Outcome measurement (B8, spec §10): realized effect = observed - forecast counterfactual, with a 90% interval.

Counterfactual contract (forecast counterfactual, labelled MODEL ESTIMATE): built from the decision's frozen
measurement basis (decide/run.py): for each joint bootstrap draw k, the no-action revenue path of every treated unit
at its pre-action budget, times (1 + one moving-block resampled path of out-of-sample relative residuals; the same
block indices for every unit), converted to contribution with the unit's contribution margin per rupee, minus the
pre-action spend. Observed CAA uses the realized attributed net revenue and realized spend of the same days.
  effect_k = observed - counterfactual_k;  90% CI = [P5, P95];  realized = mean effect
Verdicts (m = max(2,000, 5% of the treated units' expected CBA over the window)):
  SUCCESS lower > +m | FAILED upper < -m | NEUTRAL CI inside [-m, +m] | INCONCLUSIVE otherwise or no valid sample.
Maturity: OPTIMIZATION >= 100 unique orders on the treated campaigns and >= 3 days, or 14 days at most (then
INCONCLUSIVE if the order count was not reached); SAFETY at the end of its 3-day window (avoided loss = effect).
Stage 1 residual pool: the curve candidate's out-of-sample relative errors on its P + D windows (42 days); the
spec's 56 rolling-origin days need the pipeline's stored daily forecasts (C2).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

import numpy as np

from adapt.learn.calibration import apply_outcome
from adapt.predict.curves import block_indices

MIN_ORDERS, MIN_DAYS, MAX_DAYS, SAFETY_DAYS, BLOCK = 100, 3, 14, 3, 7

DDL = """
CREATE SCHEMA IF NOT EXISTS learn;
CREATE TABLE IF NOT EXISTS learn.outcomes (
    outcome_id VARCHAR PRIMARY KEY, decision_id VARCHAR NOT NULL, class VARCHAR NOT NULL, measured_at TIMESTAMP,
    window_start DATE, window_days INTEGER, orders INTEGER, verdict VARCHAR NOT NULL, method VARCHAR NOT NULL,
    realized DOUBLE, ci_lo DOUBLE, ci_hi DOUBLE, materiality DOUBLE, observed_caa DOUBLE, counterfactual_caa DOUBLE,
    raw_pred_window DOUBLE, raw_pred DOUBLE, calibrated_pred DOUBLE, calibration JSON
);
"""


def verdict(lo: float, hi: float, m: float) -> str:
    if lo > m:
        return "SUCCESS"
    if hi < -m:
        return "FAILED"
    if lo >= -m and hi <= m:
        return "NEUTRAL"
    return "INCONCLUSIVE"


def effect_distribution(basis: dict, observed: dict[str, dict[str, np.ndarray]], days: int, seed: int) -> dict:
    """Pure: the counterfactual / effect distribution over the first `days` days of the basis.
    observed[unit] = {"revenue": (days,), "spend": (days,)}."""
    units = basis["units"]
    n_draws = len(next(iter(units.values()))["cf_revenue"])
    longest = max(len(u["residual_pool"]) for u in units.values())
    idx = block_indices(max(longest, days), BLOCK, n_draws, seed)[:, :days]
    cf = np.zeros(n_draws)
    expected_cba = 0.0
    obs = 0.0
    for uid, u in units.items():
        rev = np.asarray(u["cf_revenue"], dtype=float)[:, :days]
        pool = np.asarray(u["residual_pool"], dtype=float)
        res = pool[idx % len(pool)]
        cf += (rev * (1 + res)).sum(1) * u["cm"] - u["pre_budget"] * u["pacing"] * days
        expected_cba += float(rev.mean(0).sum() * u["cm"])
        o = observed[uid]
        obs += float(np.sum(o["revenue"]) * u["cm"] - np.sum(o["spend"]))
    effect = obs - cf
    m = max(2000.0, 0.05 * expected_cba)
    lo, hi = float(np.percentile(effect, 5)), float(np.percentile(effect, 95))
    return {"observed_caa": obs, "counterfactual_caa": float(cf.mean()), "realized": float(effect.mean()),
            "ci_lo": lo, "ci_hi": hi, "materiality": m, "verdict": verdict(lo, hi, m),
            "raw_pred_window": float(np.sum(basis["pred_daily_delta_caa"][:days]))}


def measure_outcome(db, decision_id: str, as_of: datetime) -> dict:
    """Measure one decision's outcome as of a logical time; idempotent once matured (stored, applied once)."""
    db.write(lambda cur: cur.execute(DDL))
    done = db.query("SELECT verdict, realized, ci_lo, ci_hi, window_days, calibration FROM learn.outcomes "
                    "WHERE outcome_id = ?", [decision_id])
    if done:
        v, r, lo, hi, wd, cal = done[0]
        return {"status": "MATURED", "verdict": v, "realized": r, "ci_lo": lo, "ci_hi": hi, "window_days": wd,
                "calibration": json.loads(cal) if cal else None, "idempotent_replay": True}
    row = db.query("SELECT decision_ts, class, family, raw_pred, calibrated_pred, basis FROM learn.measurement_basis "
                   "WHERE decision_id = ?", [decision_id])
    if not row:
        raise KeyError(f"no measurement basis for {decision_id}")
    decision_ts, cls, family, raw_pred, cal_pred, basis = row[0]
    basis = json.loads(basis)
    start = decision_ts.date()
    last = as_of.date() - timedelta(days=1)
    days = min((last - start).days + 1, basis["days"], MAX_DAYS)
    cids = sorted({c for u in basis["units"].values() for c in u["campaign_ids"]})
    marks = ", ".join("?" * len(cids))
    orders = int(db.query(f"""SELECT count(DISTINCT order_id) FROM core.order_items WHERE campaign_id IN ({marks})
                              AND analysis_date BETWEEN ? AND ?""", [*cids, start, last])[0][0]) if days > 0 else 0
    if cls == "SAFETY":
        matured, sample_ok = days >= SAFETY_DAYS, True
        days = min(days, SAFETY_DAYS)
    else:
        sample_ok = orders >= MIN_ORDERS
        matured = (sample_ok and days >= MIN_DAYS) or days >= MAX_DAYS
    if not matured:
        return {"status": "PENDING", "days": max(days, 0), "orders": orders}

    observed = {}
    for uid, u in basis["units"].items():
        m2 = ", ".join("?" * len(u["campaign_ids"]))
        rows = dict((d, (r, s)) for d, r, s in db.query(
            f"""SELECT date, sum(attributed_net_revenue), sum(spend) FROM marts.campaign_daily
                WHERE campaign_id IN ({m2}) AND date BETWEEN ? AND ? GROUP BY 1""",
            [*u["campaign_ids"], start, start + timedelta(days=days - 1)]))
        dates = [start + timedelta(days=t) for t in range(days)]
        observed[uid] = {"revenue": np.array([float(rows.get(d, (0, 0))[0] or 0) for d in dates]),
                         "spend": np.array([float(rows.get(d, (0, 0))[1] or 0) for d in dates])}
    seed = int(hashlib.sha256(decision_id.encode()).hexdigest()[:8], 16)
    out = effect_distribution(basis, observed, days, seed)
    if cls != "SAFETY" and not sample_ok:
        out["verdict"] = "INCONCLUSIVE"
        out["inconclusive_reason"] = f"minimum sample not reached by day {MAX_DAYS} ({orders} < {MIN_ORDERS} orders)"
    calibration = None
    if cls == "OPTIMIZATION":
        # rho compares like with like: realized over the window vs the raw prediction for the same days
        calibration = apply_outcome(db, decision_id, out["verdict"], out["realized"], out["raw_pred_window"],
                                    basis["total_budget"], as_of, family or "BUDGET_REALLOCATION")
    method = "MODEL_ESTIMATE (forecast counterfactual)"

    def work(cur):
        cur.execute("INSERT OR REPLACE INTO learn.outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "?, ?)",
                    [decision_id, decision_id, cls, as_of, start, days, orders, out["verdict"], method, out["realized"],
                     out["ci_lo"], out["ci_hi"], out["materiality"], out["observed_caa"], out["counterfactual_caa"],
                     out["raw_pred_window"], raw_pred, cal_pred, json.dumps(calibration)])

    db.write(work)
    return {"status": "MATURED", "orders": orders, "window_days": days, "method": method, **out,
            "calibration": calibration}
