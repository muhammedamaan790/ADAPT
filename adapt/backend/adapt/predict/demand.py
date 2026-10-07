"""Demand model (Stage 2, spec §7.3, §22.2 "Demand"): LightGBM quantile regression (P10 / P50 / P90) over every SKU,
seasonal-naive baseline, layered promotion, and the NB2 dispersion per category.

Features for SKU k on day t (all known before t; T61: every row satisfies available_at <= fit_ts because the marts
are built as of fit_ts): day of week; units lag 1 / 7 / 14; mean of the last 7 and 28 days; the day's known price
(SCD2) and its ratio to the 28-day mean price; PLANNED spend = the mapped campaigns' spend on t - 1 (the latest known
plan; realized same-day spend is never a feature) weighted by attribution; holiday flag (classifier calendar); SKU and
category codes. Target: daily units.
Quantiles are sorted per row (P10 <= P50 <= P90, property test). Multi-day paths are recursive on P50.
Interval calibration (conformalized quantile regression, Romano et al. 2019): the quantile boosters are fitted on Train
minus its last `calibration_days`; on those days the conformity score E = max(P10 - y, y - P90) gives the adjustment
Q = the ceil((1 - a)(n + 1))/n quantile of E (a = 0.2), and the deployed interval is [P10 - Q, P90 + Q] (Q < 0 narrows
it). Raw quantile boosters are overconfident out of sample (measured: 64% P10-P90 coverage on a weekly-pattern panel),
which the coverage criterion (c) would rightly refuse.

Chronology (no row in two roles): D = the 14 days ending at the last complete day, P = the 28 days before D, Train =
the 120 days before P. Promotion (layered rule, spec §10.2): (a) beat the seasonal-naive WAPE on P by >= 5%,
(b) non-inferiority against an existing LightGBM champion (learn/governance.py), (c) P10-P90 coverage on P in
[70%, 90%]. Otherwise the seasonal-naive model (or the existing champion) stays champion. A promoted candidate is
refitted on the 120 days ending at the last complete day (same hyperparameters) and stored as a content-addressed
artifact (LightGBM model strings, deterministic, single thread).
Seasonal-naive (the Stage 1 demand model) = the mean of the last 7 days, flat over the horizon.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from adapt.economics.inventory_risk import risk_config

CONFIG = Path(__file__).resolve().parents[1] / "config" / "models.yaml"
FEATURES = ["dow", "lag1", "lag7", "lag14", "mean7", "mean28", "price", "price_ratio", "planned_spend", "holiday",
            "sku_code", "cat_code"]
QUANTILES = (0.1, 0.5, 0.9)
MODEL = "demand"

DDL = """
CREATE SCHEMA IF NOT EXISTS models;
CREATE TABLE IF NOT EXISTS models.demand_fits (
    fit_ts TIMESTAMP NOT NULL, version VARCHAR NOT NULL, kind VARCHAR NOT NULL, status VARCHAR NOT NULL,
    artifact_sha256 VARCHAR, feature_hash VARCHAR, metrics JSON, PRIMARY KEY (version)
);
"""


@lru_cache
def demand_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["demand"]


def _holidays() -> set[date]:
    cfg = yaml.safe_load((CONFIG.parent / "materiality.yaml").read_text(encoding="utf-8"))
    return {date.fromisoformat(str(h)) for h in cfg["classifier"].get("holidays") or []}


# ---- data ----------------------------------------------------------------------------------------------------------
def panel(db, lo: date, hi: date) -> pd.DataFrame:
    """Daily SKU panel over [lo, hi] (complete calendar per SKU) with units, price and planned spend."""
    skus = db.query("SELECT sku, category FROM core.skus WHERE promoted ORDER BY sku")
    if not skus:
        return pd.DataFrame()
    days = pd.date_range(lo, hi).date
    units = db.query("SELECT sku, date, units FROM marts.sku_daily WHERE date BETWEEN ? AND ?", [lo, hi])
    u = {(s, d): float(n or 0) for s, d, n in units}
    prices = db.query("SELECT sku, valid_from, coalesce(valid_to, DATE '2999-12-31'), price "
                      "FROM core.pricing_snapshots")
    by_sku: dict[str, list] = {}
    for sku, vf, vt, p in prices:
        by_sku.setdefault(sku, []).append((vf, vt, float(p or 0)))
    spend = db.query("""SELECT cs.sku, cd.date, sum(cd.spend * cs.attribution_weight) FROM marts.campaign_daily cd
                        JOIN core.campaign_sku cs USING (campaign_id) WHERE cd.date BETWEEN ? AND ?
                        GROUP BY 1, 2""", [lo - timedelta(days=1), hi])
    sp = {(s, d): float(v or 0) for s, d, v in spend}
    hol = _holidays()
    rows = []
    for code, (sku, cat) in enumerate(skus):
        for d in days:
            price = next((p for vf, vt, p in by_sku.get(sku, []) if vf <= d < vt), np.nan)
            rows.append((sku, cat, code, d, u.get((sku, d), 0.0), price, sp.get((sku, d - timedelta(days=1)), 0.0),
                         float(d in hol)))
    df = pd.DataFrame(rows, columns=["sku", "category", "sku_code", "date", "units", "price", "planned_spend",
                                     "holiday"])
    df["cat_code"] = df["category"].astype("category").cat.codes
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["sku", "date"]).copy()
    g = df.groupby("sku", sort=False)["units"]
    df["lag1"], df["lag7"], df["lag14"] = g.shift(1), g.shift(7), g.shift(14)
    df["mean7"] = g.transform(lambda s: s.shift(1).rolling(7).mean())
    df["mean28"] = g.transform(lambda s: s.shift(1).rolling(28).mean())
    df["price"] = df.groupby("sku")["price"].transform(lambda s: s.ffill().bfill())
    mp = df.groupby("sku")["price"].transform(lambda s: s.shift(1).rolling(28, min_periods=1).mean())
    df["price_ratio"] = (df["price"] / mp).fillna(1.0)
    df["dow"] = pd.to_datetime(df["date"]).dt.dayofweek
    return df


def wape(y: np.ndarray, yhat: np.ndarray) -> float:
    y, yhat = np.asarray(y, dtype=float), np.asarray(yhat, dtype=float)
    eps = max(0.01 * float(np.mean(np.abs(y))) if y.size else 0.0, 1e-9)
    return float(np.sum(np.abs(y - yhat)) / max(float(np.sum(np.abs(y))), eps))


# ---- LightGBM --------------------------------------------------------------------------------------------------------
def _lgb():
    try:
        import lightgbm as lgb
    except (ImportError, OSError) as exc:  # libomp missing etc.: the champion stays seasonal-naive
        raise RuntimeError(f"LightGBM unavailable: {exc}") from exc
    return lgb


def _fit_quantiles(data: pd.DataFrame, cfg: dict) -> dict:
    lgb = _lgb()
    out = {}
    for q in QUANTILES:
        params = {**cfg["lightgbm"], "objective": "quantile", "alpha": q, "verbose": -1, "num_threads": 1,
                  "deterministic": True, "force_row_wise": True, "seed": int(cfg["seed"])}
        booster = lgb.train(params, lgb.Dataset(data[FEATURES].to_numpy(float), data["units"].to_numpy(float)),
                            num_boost_round=int(cfg["rounds"]))
        out[q] = booster.model_to_string()
    return out


def train(df: pd.DataFrame, cfg: dict) -> dict:
    """Quantile boosters on the window minus its last calibration days + the CQR interval adjustment from them."""
    data = df.dropna(subset=FEATURES).sort_values("date")
    cut = data["date"].max() - timedelta(days=int(cfg["calibration_days"]))
    fit, cal = data[data["date"] <= cut], data[data["date"] > cut]
    out = _fit_quantiles(fit if len(fit) >= int(cfg["min_train_rows"]) // 2 else data, cfg)
    adj = 0.0
    if len(cal):
        raw = predict({**out, "cqr": 0.0}, cal[FEATURES].to_numpy(float))
        y = cal["units"].to_numpy(float)
        e = np.maximum(raw[:, 0] - y, y - raw[:, 2])
        n = len(e)
        level = min(1.0, np.ceil((1 - float(cfg["interval_alpha"])) * (n + 1)) / n)
        adj = float(np.quantile(e, level))
    out["cqr"] = adj
    return out


def boosters(models: dict) -> tuple[list, float]:
    """Parse the three model strings once (keys may be floats or their JSON string form) + the CQR adjustment."""
    lgb = _lgb()
    return [lgb.Booster(model_str=models.get(q, models.get(str(q)))) for q in QUANTILES], float(models.get("cqr", 0.0))


def predict(models, X: np.ndarray) -> np.ndarray:
    """(n, 3) sorted quantiles P10 <= P50 <= P90 with the CQR adjustment, floored at 0 (non-crossing by
    construction). `models` = model strings (dict) or parsed (boosters, adjustment)."""
    bs, adj = boosters(models) if isinstance(models, dict) else (models if isinstance(models, tuple) else (models, 0.0))
    cols = np.column_stack([b.predict(X) for b in bs])
    cols[:, 0] -= adj
    cols[:, 2] += adj
    return np.sort(np.maximum(cols, 0.0), axis=1)


def spec(cfg: dict) -> dict:
    """The model specification: what makes two fits comparable (features + hyperparameters)."""
    return {"features": FEATURES, "quantiles": list(QUANTILES), "lightgbm": cfg["lightgbm"], "rounds": cfg["rounds"],
            "seed": cfg["seed"], "calibration_days": cfg["calibration_days"], "interval_alpha": cfg["interval_alpha"]}


def seasonal_naive(df: pd.DataFrame) -> np.ndarray:
    return df["mean7"].to_numpy(float)


# ---- fit + promotion -------------------------------------------------------------------------------------------------
def fit_demand(db, as_of: datetime, cfg_overrides: dict | None = None) -> dict:
    """Weekly refit (spec §2 cadence): candidate on Train, scored on P against seasonal-naive and the champion."""
    from adapt.learn import governance

    cfg = {**demand_config(), **(cfg_overrides or {})}
    last = as_of.date() - timedelta(days=1)
    d_lo = last - timedelta(days=cfg["diagnostic_days"] - 1)
    p_hi = d_lo - timedelta(days=1)
    p_lo = p_hi - timedelta(days=cfg["holdout_days"] - 1)
    t_hi = p_lo - timedelta(days=1)
    t_lo = t_hi - timedelta(days=cfg["train_days"] - 1)
    raw = panel(db, t_lo - timedelta(days=35), last)
    if raw.empty:
        return {"status": "NO_DATA"}
    df = add_features(raw)
    tr = df[(df.date >= t_lo) & (df.date <= t_hi)]
    hold = df[(df.date >= p_lo) & (df.date <= p_hi)].dropna(subset=FEATURES)
    if len(tr.dropna(subset=FEATURES)) < cfg["min_train_rows"] or hold.empty:
        return {"status": "INSUFFICIENT_DATA", "train_rows": int(len(tr))}
    try:
        models = train(tr, cfg)
    except RuntimeError as exc:
        return {"status": "MODEL_UNAVAILABLE", "reason": str(exc)}
    q = predict(models, hold[FEATURES].to_numpy(float))
    y = hold["units"].to_numpy(float)
    naive = seasonal_naive(hold)
    metrics = {"wape_p50": wape(y, q[:, 1]), "wape_seasonal_naive": wape(y, naive),
               "coverage_p10_p90": float(np.mean((y >= q[:, 0]) & (y <= q[:, 2]))), "holdout_rows": int(len(y))}
    metrics["improvement_vs_naive"] = 1 - metrics["wape_p50"] / metrics["wape_seasonal_naive"] \
        if metrics["wape_seasonal_naive"] > 0 else 0.0
    lo_c, hi_c = cfg["coverage_band"]
    criteria = {"a_beats_baseline": metrics["improvement_vs_naive"] >= cfg["min_improvement_vs_naive"],
                "c_coverage": lo_c <= metrics["coverage_p10_p90"] <= hi_c}
    my_spec = spec(cfg)
    feature_hash = hashlib.sha256(json.dumps(my_spec, sort_keys=True).encode()).hexdigest()[:16]
    champ = governance.champion(db, MODEL)
    if champ is None:  # the Stage 1 model is the implicit first champion: make it explicit (rollback target)
        governance.register_champion(db, MODEL, "seasonal-naive", as_of, "SEASONAL_NAIVE",
                                     "Stage 1 demand model (mean of the last 7 days)")
        champ = governance.champion(db, MODEL)
    errors = {"candidate": np.abs(y - q[:, 1]), "days": np.asarray(hold["date"].to_numpy())}
    if champ is not None and champ["kind"] == "LIGHTGBM" and champ["feature_hash"] != feature_hash:
        # a DIFFERENT specification is the champion: refit ITS spec on the same Train window, so both are scored
        # out-of-sample on identical holdout rows (a paired comparison, spec §10.2 (b))
        champ_cfg = {**cfg, **(champ.get("spec") or {})}
        champ_models = train(tr, champ_cfg)
        errors["champion"] = np.abs(y - predict(champ_models, hold[FEATURES].to_numpy(float))[:, 1])
    # the deployed candidate is refit on the 120 days ending at the last complete day (same hyperparameters)
    dep = df[(df.date > last - timedelta(days=cfg["train_days"])) & (df.date <= last)]
    deployed = train(dep, cfg)
    sha = governance.put_artifact(db, {str(k): v for k, v in deployed.items()})
    version = f"demand-{as_of:%Y%m%dT%H%M}-{sha[:8]}"
    decision = governance.consider(db, MODEL, version, as_of, kind="LIGHTGBM", artifact_sha256=sha,
                                   feature_hash=feature_hash, spec=my_spec, validation=metrics,
                                   baseline={"wape": metrics["wape_seasonal_naive"]}, criteria=criteria,
                                   paired_errors=errors, y_mean=float(np.mean(np.abs(y))) if y.size else 0.0)

    def work(cur):
        cur.execute(DDL)
        cur.execute("INSERT OR REPLACE INTO models.demand_fits VALUES (?, ?, 'LIGHTGBM', ?, ?, ?, ?)",
                    [as_of, version, decision["role"], sha, feature_hash, json.dumps({**metrics, **decision})])

    db.write(work)
    return {"status": "OK", "version": version, **metrics, "promotion": decision}


# ---- forecasting for the decision -----------------------------------------------------------------------------------
def forecast(db, as_of: datetime, horizon: int) -> dict[str, dict]:
    """Per SKU: the champion's P50 path mean over the horizon (baseline_daily) and P10 / P90 sums, plus the kind used.
    Seasonal-naive (the mean of the last 7 days) when no LightGBM model is champion."""
    from adapt.learn import governance

    last = as_of.date() - timedelta(days=1)
    raw = panel(db, last - timedelta(days=40), last)
    if raw.empty:
        return {}
    df = add_features(raw)
    out: dict[str, dict] = {}
    recent = df[df.date > last - timedelta(days=7)].groupby("sku")["units"].mean()
    champ = governance.champion(db, MODEL)
    if champ is None or champ["kind"] != "LIGHTGBM":
        for sku, m in recent.items():
            out[sku] = {"baseline_daily": float(m), "p10_sum": None, "p90_sum": None, "model": "seasonal_naive"}
        return out
    bs = boosters(governance.load_artifact(db, champ["artifact_sha256"]))
    hol = _holidays()
    hist = {sku: g.sort_values("date") for sku, g in df.groupby("sku")}
    order = sorted(hist)
    units = {k: list(hist[k]["units"].to_numpy(float)) for k in order}
    prices = {k: list(hist[k]["price"].to_numpy(float)) for k in order}
    meta = {k: hist[k].iloc[-1] for k in order}
    planned = {k: float(hist[k]["planned_spend"].iloc[-1]) for k in order}
    acc = {k: [] for k in order}
    p10 = dict.fromkeys(order, 0.0)
    p90 = dict.fromkeys(order, 0.0)
    for h in range(1, horizon + 1):  # recursive on P50, all SKUs in one batch per step
        d = last + timedelta(days=h)
        X = np.array([[d.weekday(), units[k][-1], units[k][-7], units[k][-14], float(np.mean(units[k][-7:])),
                       float(np.mean(units[k][-28:])), prices[k][-1], prices[k][-1] / float(np.mean(prices[k][-28:])),
                       planned[k], float(d in hol), meta[k]["sku_code"], meta[k]["cat_code"]] for k in order])
        qs = predict(bs, X)
        for i, k in enumerate(order):
            acc[k].append(qs[i, 1])
            p10[k] += qs[i, 0]
            p90[k] += qs[i, 2]
            units[k].append(qs[i, 1])
            prices[k].append(prices[k][-1])
    for k in order:
        out[k] = {"baseline_daily": float(np.mean(acc[k])), "p10_sum": p10[k], "p90_sum": p90[k],
                  "model": f"lightgbm:{champ['version']}"}
    return out


def dispersion_by_sku(db, as_of: datetime) -> dict[str, float]:
    """NB2 r per category by method of moments on the trailing window of observed daily demand counts, by SKU. The
    moments are pooled WITHIN SKUs (r = sum_k mean_k^2 / sum_k (var_k - mean_k)): pooling raw counts across SKUs with
    different means would read their mean differences as overdispersion."""
    rc = risk_config()
    last = as_of.date() - timedelta(days=1)
    lo = last - timedelta(days=int(rc["dispersion_window_days"]) - 1)
    rows = db.query("""SELECT k.category, d.sku, d.units FROM marts.sku_daily d JOIN core.skus k USING (sku)
                       WHERE d.date BETWEEN ? AND ? ORDER BY d.sku, d.date""", [lo, last])
    series: dict[str, list[float]] = {}
    sku_cat: dict[str, str] = {}
    for cat, sku, n in rows:
        series.setdefault(sku, []).append(float(n or 0))
        sku_cat[sku] = cat
    num: dict[str, float] = {}
    den: dict[str, float] = {}
    for sku, v in series.items():
        a = np.array(v)
        if a.size < 2:
            continue
        c = sku_cat[sku]
        num[c] = num.get(c, 0.0) + float(a.mean()) ** 2
        den[c] = den.get(c, 0.0) + float(a.var(ddof=1) - a.mean())
    cap = float(rc["r_cap"])
    r_cat = {c: (min(num[c] / max(den[c], 1e-6), cap) if num[c] > 0 else cap) for c in num}
    return {sku: r_cat.get(c, cap) for sku, c in sku_cat.items()}
