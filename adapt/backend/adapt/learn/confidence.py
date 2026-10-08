"""Confidence index (spec §8.4): {overall, band, region, data_quality, prediction_quality, track_record,
constraint_coverage}, every component bounded to [0, 1] by construction. Stored with the decision content, outside
the §22.4 hash payload (it reads outcomes and health that the replay snapshot does not carry).

- data_quality = min over the decision's required dependencies (each leg's ad platform + store, ERP, finance) of
  health score / 100.
- prediction_quality = min over the model families this decision's objective requires (no weights across unrelated
  metrics). Response curve per treated unit: clamp(R²_D, 0, 1) x exp(-W), W = (P90 - P10) / max(|P50|, ₹1,000) of the
  decision's dCAA; MODEL_UNAVAILABLE -> 0. Demand (always: inventory risk enters every budget decision):
  clamp(1 - WAPE, 0, 1) x (1 if P10-P90 coverage in [70%, 90%] else 0.5); seasonal-naive has no interval -> x 0.5.
- track_record = (n (1 - sMAPE_n / 2) + 5 x 0.7) / (n + 5): empirical shrinkage toward 0.7 over matured SUCCESS /
  NEUTRAL / FAILED OPTIMIZATION outcomes (the BUDGET_REALLOCATION family), sMAPE with a ₹1,000 floor, pred = the
  decision-time calibrated prediction over the outcome window, actual = the realized effect.
- overall = geometric mean of the three; constraint_coverage is a gate (every policy rule was evaluated), not a factor.
  MIX_UNCERTAIN units (unmapped share > 20%) cap the index below HIGH.
- band HIGH >= 0.8 / MEDIUM >= 0.6 / LOW is informational only; `region` ([0, 0.6), [0.6, 0.8), [0.8, 1]) is what
  qualification (learn/qualification.py) authorizes, never the band.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

import numpy as np

FLOOR_INR = 1000.0
PRIOR, PRIOR_N = 0.7, 5
REGIONS = ((0.0, 0.6, "R1"), (0.6, 0.8, "R2"), (0.8, 1.000001, "R3"))
COMMON_SOURCES = ("store", "erp", "finance")
PLATFORM_SOURCE = {"meta": "meta_ads", "google": "google_ads", "tiktok": "tiktok_ads", "amazon": "amazon_ads"}


def _has(db, schema: str, table: str) -> bool:
    return bool(db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                         [schema, table]))


def clamp01(x: float) -> float:
    return float(min(max(x, 0.0), 1.0))


def region_of(x: float) -> str:
    return next(r for lo, hi, r in REGIONS if lo <= x < hi)


def band_of(x: float) -> str:
    return "HIGH" if x >= 0.8 else "MEDIUM" if x >= 0.6 else "LOW"


# ---- components -----------------------------------------------------------------------------------------------------
def data_quality(db, platforms: set[str], as_of: datetime) -> tuple[float, dict]:
    deps = sorted({PLATFORM_SOURCE[p] for p in platforms if p in PLATFORM_SOURCE} | set(COMMON_SOURCES))
    if not _has(db, "ops", "data_health"):
        return 0.0, {"dependencies": deps, "reason": "no data health computed"}
    scores = dict(db.query("""SELECT source, score FROM ops.data_health WHERE as_of <= ?
                              QUALIFY row_number() OVER (PARTITION BY source ORDER BY as_of DESC) = 1""", [as_of]))
    per = {d: clamp01(float(scores[d]) / 100.0) if scores.get(d) is not None else 0.0 for d in deps}
    return min(per.values()), {"dependencies": per}


def curve_quality(units, legs: list[dict], expected: dict) -> tuple[float, dict]:
    """Min over the treated units of clamp(R²_D) x exp(-W). W uses the decision's dCAA P10/P50/P90 for every
    objective: decisions store no net-revenue quantiles (a GROWTH decision's revenue interval is not persisted)."""
    w = (float(expected["p90"]) - float(expected["p10"])) / max(abs(float(expected["p50"])), FLOOR_INR)
    by_id = {u.unit_id: u for u in units}
    per = {}
    for leg in legs:
        u = by_id.get(leg["unit_id"])
        if u is None or not u.model_available or u.curve is None:
            per[leg["unit_id"]] = 0.0  # MODEL_UNAVAILABLE -> 0 (spec §8.4)
            continue
        hold = (u.curve.diagnostics or {}).get("holdout") or {}
        r2 = hold.get("r2_D")
        per[leg["unit_id"]] = clamp01(float(r2)) * math.exp(-w) if r2 is not None else 0.0
    return (min(per.values()) if per else 0.0), {"per_unit": per, "interval_width": w}


def demand_quality(db, as_of: datetime) -> tuple[float, dict]:
    """The champion demand model's holdout WAPE and P10-P90 coverage; seasonal-naive is scored on the last 28 days
    (forecast = mean of the 7 days before each day) and has no interval, so its coverage factor is 0.5."""
    champ = None
    if _has(db, "learn", "model_registry"):
        from adapt.learn import governance

        champ = governance.champion(db, "demand")
    if champ and champ.get("kind") == "LIGHTGBM" and champ.get("validation_metrics"):
        m = champ["validation_metrics"]
        cov = float(m.get("coverage_p10_p90", 0.0))
        q = clamp01(1.0 - float(m["wape_p50"])) * (1.0 if 0.70 <= cov <= 0.90 else 0.5)
        return q, {"model": champ["version"], "wape": float(m["wape_p50"]), "coverage": cov}
    if not _has(db, "marts", "sku_daily"):
        return 0.0, {"model": "seasonal-naive", "reason": "no SKU history"}
    rows = db.query("""SELECT sku, date, units FROM marts.sku_daily WHERE date > ? AND date <= ? ORDER BY sku, date""",
                    [(as_of - timedelta(days=36)).date(), as_of.date()])
    by_sku: dict[str, list[float]] = {}
    for sku, _d, units in rows:
        by_sku.setdefault(sku, []).append(float(units or 0.0))
    y, yhat = [], []
    for series in by_sku.values():
        for t in range(7, len(series)):
            y.append(series[t])
            yhat.append(float(np.mean(series[t - 7:t])))
    if not y:
        return 0.0, {"model": "seasonal-naive", "reason": "fewer than 8 days of SKU history"}
    from adapt.predict.demand import wape

    wp = wape(np.array(y), np.array(yhat))
    return clamp01(1.0 - wp) * 0.5, {"model": "seasonal-naive", "wape": wp, "coverage": None}


def track_record(db, family: str = "BUDGET_REALLOCATION") -> tuple[float, dict]:
    """Shrinkage toward 0.7 over matured, decisive OPTIMIZATION outcomes (INCONCLUSIVE excluded and counted)."""
    if not _has(db, "learn", "outcomes"):
        return PRIOR, {"n": 0, "smape": None, "inconclusive": 0, "family": family}
    rows = db.query("""SELECT verdict, realized, raw_pred_window, raw_pred, calibrated_pred FROM learn.outcomes
                       WHERE class = 'OPTIMIZATION'""")
    errs, inconclusive = [], 0
    for verdict, realized, pred_w, raw, cal in rows:
        if verdict not in ("SUCCESS", "NEUTRAL", "FAILED"):
            inconclusive += 1
            continue
        if realized is None or pred_w is None:
            continue
        ratio = float(cal) / float(raw) if raw and raw > 0 and cal is not None else 1.0
        pred = float(pred_w) * ratio  # the calibrated prediction the manager saw, over the measured window
        errs.append(abs(pred - float(realized)) / max((abs(pred) + abs(float(realized))) / 2.0, FLOOR_INR))
    n = len(errs)
    smape = float(np.mean(errs)) if errs else None
    value = (n * (1 - (smape or 0.0) / 2) + PRIOR_N * PRIOR) / (n + PRIOR_N)
    return clamp01(value), {"n": n, "smape": smape, "inconclusive": inconclusive, "family": family}


# ---- the index ------------------------------------------------------------------------------------------------------
def confidence(db, state, content: dict, as_of: datetime) -> dict:
    legs = content["legs"]
    dq, dq_detail = data_quality(db, {leg["platform"] for leg in legs}, as_of)
    cq, cq_detail = curve_quality(state.units, legs, content["expected"])
    mq, mq_detail = demand_quality(db, as_of)
    pq = min(cq, mq)
    tr, tr_detail = track_record(db)
    overall = float((max(dq, 0.0) * max(pq, 0.0) * max(tr, 0.0)) ** (1.0 / 3.0))
    by_id = {u.unit_id: u for u in state.units}
    mix = sorted(leg["unit_id"] for leg in legs if leg["unit_id"] in by_id
                 and by_id[leg["unit_id"]].unmapped_share > 0.20)
    if mix:
        overall = min(overall, 0.799)  # MIX_UNCERTAIN: capped below HIGH (spec §8.1)
    coverage = all(c.get("passed") is not None for c in content.get("checks", [])) and bool(content.get("checks"))
    return {"overall": round(overall, 4), "band": band_of(overall), "region": region_of(overall),
            "data_quality": round(dq, 4), "prediction_quality": round(pq, 4), "track_record": round(tr, 4),
            "constraint_coverage": coverage,
            "detail": json.loads(json.dumps({"data": dq_detail, "curve": cq_detail, "demand": mq_detail,
                                             "track_record": tr_detail, "mix_uncertain": mix}, default=float))}
