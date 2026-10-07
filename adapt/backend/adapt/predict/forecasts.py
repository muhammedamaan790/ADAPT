"""Stored daily forecasts and rolling-origin out-of-sample residuals (C2 + B8, spec §10 counterfactual contract).

Every pipeline run stores, per budget unit, its forecast of attributed net revenue for TOMORROW at the planned
(current) budget, made with the then-champion model (learn.forecasts). For a day t, residual = actual_t - the
forecast for t stored before t. A live residual is valid only when the budget in effect on t equals the forecast's
planned budget (a budget changed after the forecast, e.g. by an executed decision, would leak the action into the
error).
The 56 days before the first run are backfilled once with weekly refits: at each weekly origin a point curve is
fitted on the 120 days before it. Entity metadata (campaigns, budgets) is only observable from ADAPT's first sync, so
the canonical state cannot be rebuilt as of an earlier origin (it would have no campaigns); the fits use the first
run's marts restricted to dates before each origin, whose only look-ahead is refund maturation on training days
(already unbiased through the conditional refund expectation). The sources also expose current budgets only (ADAPT
observes budget history from its first run on), so a backfilled forecast conditions on the day's realized spend
(planned_budget NULL): its error is the revenue-given-spend error, which is exactly the uncertainty the
counterfactual (at a known pre-action budget) needs.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np

from adapt.predict.curves import CurveArtifact, adstock, fit, predict
from adapt.predict.fit_curves import _baseline, _daily, _normalised, budget_units, curve_config

MIN_POOL = 28
POOL_DAYS = 56

DDL = """
CREATE SCHEMA IF NOT EXISTS learn;
CREATE TABLE IF NOT EXISTS learn.forecasts (
    unit_id VARCHAR NOT NULL, target_date DATE NOT NULL, made_at TIMESTAMP NOT NULL, run_id VARCHAR,
    model VARCHAR NOT NULL, planned_budget DOUBLE, pred_revenue DOUBLE NOT NULL,
    PRIMARY KEY (unit_id, target_date, made_at)
);
"""


def _budget_on(db, unit: str, day: date) -> float | None:
    row = db.query("""SELECT value FROM core.budget_history WHERE entity_id = ? AND effective_from <= ?
                      AND (effective_to IS NULL OR ? < effective_to) ORDER BY effective_from DESC LIMIT 1""",
                   [unit, day, day])
    return float(row[0][0]) if row else None


def _pacing(spend: np.ndarray, budgets: np.ndarray) -> float:
    ok = budgets > 0
    return float(np.clip(spend[ok].sum() / budgets[ok].sum(), 0.3, 1.2)) if ok.any() else 1.0


def store_forecasts(db, as_of: datetime, curves: dict[str, CurveArtifact], run_id: str) -> int:
    """Forecast tomorrow (as_of.date() + 1) per unit at its current budget; adstock continued from fit_ts over the
    spends known since (today's spend assumed at budget x pacing)."""
    last = as_of.date() - timedelta(days=1)
    target = as_of.date() + timedelta(days=1)
    current = dict(db.query("SELECT budget_id, current_amount_inr FROM core.budgets"))
    rows = []
    for unit, cids, _ch, _ps in budget_units(db):
        a = curves.get(unit)
        budget = current.get(unit)
        if not budget:
            continue
        budget = float(budget)
        hist = _daily(db, cids, last - timedelta(days=27), last)
        budgets = np.array([_budget_on(db, unit, d) or 0.0 for d in hist.index])
        pacing = _pacing(hist["spend"].to_numpy(), budgets)
        planned = budget * pacing
        if a is not None and a.status != "MODEL_UNAVAILABLE":
            fit_last = db.query("SELECT max(fit_ts) FROM models.response_curves")[0][0].date() - timedelta(days=1)
            since = _daily(db, cids, fit_last + timedelta(days=1), last)["spend"].to_numpy() if fit_last < last \
                else np.zeros(0)
            path = np.concatenate([since, [planned, planned]])  # today (assumed), tomorrow
            pred, model = float(a.revenue(path)[-1]), f"curve:{a.status}"
        else:
            spend, rev = hist["spend"].sum(), hist["revenue"].sum()
            pred, model = (float(rev / spend * planned) if spend > 0 else 0.0), "proportional_roas"
        rows.append((unit, target, as_of, run_id, model, budget, pred))

    def work(cur):
        cur.execute(DDL)
        if rows:
            cur.executemany("INSERT OR REPLACE INTO learn.forecasts VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    db.write(work)
    return len(rows)


def forecast_days(db) -> int:
    db.write(lambda cur: cur.execute(DDL))
    return int(db.query("SELECT count(DISTINCT target_date) FROM learn.forecasts")[0][0])


def backfill_forecasts(db, as_of: datetime, days: int = POOL_DAYS) -> dict:
    """Weekly-refit backfill of the `days` before as_of (see the module docstring for the data used)."""
    cfg = curve_config()
    first = as_of.date() - timedelta(days=days)
    origins = [first + timedelta(days=7 * k) for k in range((days + 6) // 7)]
    fits: dict[tuple[date, str], dict] = {}
    units = budget_units(db)
    for o in origins:
        t_hi = o - timedelta(days=1)
        t_lo = t_hi - timedelta(days=cfg["train_days"] - 1)
        for unit, cids, _ch, ps in units:
            df = _daily(db, cids, t_lo, t_hi)
            s, r = df["spend"].to_numpy(), df["revenue"].to_numpy()
            b = _baseline(db, ps, t_lo, t_hi)
            ms, mr, bm = _normalised(s, r, b)
            entry = {"roas": float(r.sum() / s.sum()) if s.sum() > 0 else 0.0, "train_end": t_hi}
            if ms >= cfg["min_median_spend"] and (s > 0).sum() >= cfg["min_spend_days"]:
                x = s / ms
                f = fit([(x, r / mr, b / bm, float(x[:7].mean()))], cfg)
                if f.success:
                    entry.update(params=f.params, ms=ms, mr=mr, b_level=float(np.mean(b[-14:]) / bm),
                                 a_T=float(adstock(x, f.params[3], float(x[:7].mean()))[0][-1]))
            fits[(o, unit)] = entry
    rows = []
    for o in origins:
        for unit, cids, _ch, _ps in units:
            e = fits.get((o, unit))
            if e is None:
                continue  # the unit did not exist at this origin: no forecast was possible then
            for k in range(7):
                t = o + timedelta(days=k)
                if t >= as_of.date():
                    break
                spend_plan = float(_daily(db, cids, t, t)["spend"].iloc[0])  # spend-conditional (see module doc)
                if "params" in e:
                    known = _daily(db, cids, e["train_end"] + timedelta(days=1), t - timedelta(days=1))
                    x = np.concatenate([known["spend"].to_numpy(), [spend_plan]]) / e["ms"]
                    pred = float(predict(e["params"], x, np.full(len(x), e["b_level"]), e["a_T"])[-1] * e["mr"])
                    model = "curve:weekly_refit"
                else:
                    pred, model = e["roas"] * spend_plan, "proportional_roas"
                rows.append((unit, t, datetime.combine(t - timedelta(days=1), as_of.time()), "backfill",
                             f"{model}|spend_conditional", None, pred))

    def work(cur):
        cur.execute(DDL)
        if rows:
            cur.executemany("INSERT OR REPLACE INTO learn.forecasts VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    db.write(work)
    return {"origins": len(origins), "forecasts": len(rows)}


def residual_pool(db, unit: str, campaign_ids: list[str], before: date, days: int = POOL_DAYS) -> list[float] | None:
    """Relative rolling-origin out-of-sample residuals (actual - forecast) / forecast over the `days` before
    `before`, using for each day the latest forecast made before that day; None if fewer than MIN_POOL valid days."""
    db.write(lambda cur: cur.execute(DDL))
    marks = ", ".join("?" * len(campaign_ids))
    rows = db.query(f"""
        WITH f AS (SELECT target_date, pred_revenue, planned_budget FROM learn.forecasts
                   WHERE unit_id = ? AND target_date BETWEEN ? AND ? AND made_at < CAST(target_date AS TIMESTAMP)
                   QUALIFY row_number() OVER (PARTITION BY target_date ORDER BY made_at DESC) = 1),
             a AS (SELECT date, sum(attributed_net_revenue) AS rev FROM marts.campaign_daily
                   WHERE campaign_id IN ({marks}) AND date BETWEEN ? AND ? GROUP BY 1)
        SELECT f.target_date, f.pred_revenue, f.planned_budget, a.rev FROM f JOIN a ON a.date = f.target_date
        ORDER BY 1""", [unit, before - timedelta(days=days), before - timedelta(days=1), *campaign_ids,
                        before - timedelta(days=days), before - timedelta(days=1)])
    pool = []
    for d, pred, planned, rev in rows:
        if not pred or pred <= 0:
            continue
        if planned is not None:  # live forecast: valid only if the planned budget was the budget in effect
            in_effect = _budget_on(db, unit, d)
            if in_effect is None or abs(in_effect - planned) > 0.01 * max(planned, 1.0):
                continue
        pool.append(round(float(rev) / float(pred) - 1, 4))
    return pool if len(pool) >= MIN_POOL else None
