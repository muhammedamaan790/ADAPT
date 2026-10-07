"""Response-curve fitting and persistence (B5, spec §7.2 + §22.2).

Chronology for a fit at fit_ts (last complete day L), no row in two roles:
    D (diagnostic) = the 14 days ending at L; P (promotion holdout) = the 28 days before D;
    Train = the 120 days before P. The candidate is fitted on Train and scored on P (and D).
    Accepted (Stage 1, criteria a + c): holdout skill >= 0 per unit, and P10-P90 predictive coverage in [70%, 90%]
    over the family (all candidates' holdout days) -> refit on the 120 days ending at L with 200 joint moving-block
    bootstrap draws (the deployed artifact).
Units: one per budget (a shared Google budget is one unit). Target: last-click attributed net revenue; baseline:
the STL trend of the product set's unpaid (email + organic) orders (our sources have no unpaid sessions by category).
Fallbacks: spend floor / failed or rejected fit / unstable fit -> the pooled channel curve (w = 0); no usable pooled
curve -> MODEL_UNAVAILABLE (no scale-up may consume it). Thin data: w = n_eff / (n_eff + 30).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

from adapt.predict.curves import PARAMS, CurveArtifact, Fit, adstock, block_indices, bootstrap, fit, hill, predict

MODELS_PATH = Path(__file__).resolve().parents[1] / "config" / "models.yaml"


@lru_cache
def curve_config() -> dict:
    import yaml

    return yaml.safe_load(MODELS_PATH.read_text(encoding="utf-8"))["response_curve"]


def budget_units(db) -> list[tuple[str, list[str], str, str | None]]:
    rows = db.query("SELECT budget_id, campaign_id, channel_id, product_set FROM core.campaigns ORDER BY 1, 2")
    units: dict[str, tuple[list[str], str, str | None]] = {}
    for bid, cid, ch, ps in rows:
        units.setdefault(bid, ([], ch, ps))[0].append(cid)
    return [(bid, cids, ch, ps) for bid, (cids, ch, ps) in units.items()]


def _daily(db, campaign_ids: list[str], lo: date, hi: date) -> pd.DataFrame:
    marks = ", ".join("?" * len(campaign_ids))
    rows = db.query(f"""SELECT date, sum(spend), sum(attributed_net_revenue) FROM marts.campaign_daily
                        WHERE campaign_id IN ({marks}) AND date BETWEEN ? AND ? GROUP BY 1 ORDER BY 1""",
                    [*campaign_ids, lo, hi])
    df = pd.DataFrame(rows, columns=["date", "spend", "revenue"]).set_index("date")
    return df.reindex(pd.date_range(lo, hi).date, fill_value=0.0).astype(float)


def _baseline(db, product_set: str | None, lo: date, hi: date) -> np.ndarray:
    """STL trend (period 7) of the product set's unpaid orders, over [lo, hi]; flat 1.0 if there is no signal."""
    n = (hi - lo).days + 1
    if not product_set:
        return np.ones(n)
    rows = db.query("""SELECT oi.analysis_date, count(DISTINCT oi.order_id) FROM core.order_items oi
                       JOIN core.skus k USING (sku)
                       WHERE oi.channel_id IN ('email', 'organic') AND k.category = ?
                         AND oi.analysis_date BETWEEN ? AND ? GROUP BY 1""", [product_set, lo, hi])
    s = pd.Series(dict(rows), dtype=float).reindex(pd.date_range(lo, hi).date, fill_value=0.0)
    if s.sum() <= 0 or n < 14:
        return np.ones(n)
    trend = STL(s.to_numpy(), period=7, robust=True).fit().trend
    return np.maximum(np.asarray(trend), 1e-6)


def _normalised(spend: np.ndarray, revenue: np.ndarray, base: np.ndarray) -> tuple[float, float, float]:
    pos = spend > 0
    ms = float(np.median(spend[pos])) if pos.any() else 0.0
    mr = float(np.median(revenue[pos])) if pos.any() else 0.0
    return ms, max(mr, 1e-9), float(base.mean())


def _coverage(fit_: Fit, train_pred: np.ndarray, x: np.ndarray, y: np.ndarray, b: np.ndarray, a0: float,
              cfg: dict, seed: int) -> np.ndarray:
    """Per-day P10-P90 hits on a holdout: point prediction x (1 + moving-block resampled RELATIVE training
    residuals). Revenue noise is proportional to its level, and budgets grow, so absolute training residuals from a
    lower-spend period understate holdout noise."""
    pred = predict(fit_.params, x, b, a0)
    rel = -fit_.residuals / np.maximum(train_pred, 1e-9)  # (actual - predicted) / predicted, training window
    idx = block_indices(len(rel), cfg["block_days"], 500, seed)[:, : len(x)]
    paths = pred[None, :] * (1 + rel[idx])
    lo, hi = np.percentile(paths, 10, axis=0), np.percentile(paths, 90, axis=0)
    return (y >= lo) & (y <= hi)


def skill(y: np.ndarray, yhat: np.ndarray, naive: float) -> float:
    """Out-of-sample R^2 against the naive forecast available at fit time (the last 28 training days' mean), not
    against the holdout's own mean, which no forecast could know (that benchmark scores even a correct curve < 0
    whenever spend barely moves and daily noise dominates)."""
    sse = float(np.sum((y - yhat) ** 2))
    sse_naive = float(np.sum((y - naive) ** 2))
    return 1 - sse / sse_naive if sse_naive > 0 else float("nan")


def _oos_residuals(params, s_tr, r_tr, b_tr, s_h, r_h, b_h) -> tuple[list[float], str]:
    """Relative out-of-sample errors (actual - pred) / pred on P + D of the model fitted on Train only: the unit's
    candidate, the channel's pooled candidate, or (no curve) revenue proportional to spend at the Train ROAS. They are
    the residual pool of the outcome counterfactual (B8)."""
    model = "proportional_roas"
    pred = None
    if params is not None:
        ms, mr, bm = _normalised(s_tr, r_tr, b_tr)
        if ms > 0:
            x = s_tr / ms
            a_t = adstock(x, params[3], float(x[:7].mean()))[0][-1]
            pred = predict(params, s_h / ms, b_h / bm, a_t) * mr
            model = "curve_candidate"
    if pred is None:
        roas = float(r_tr.sum() / s_tr.sum()) if s_tr.sum() > 0 else 0.0
        pred = roas * s_h
    ok = pred > 1e-9
    return [round(float(v), 4) for v in (r_h[ok] - pred[ok]) / pred[ok]], model


def _stability(f: Fit, draws: np.ndarray, series: list[tuple[np.ndarray, np.ndarray, float]],
               cfg: dict) -> tuple[bool, dict]:
    """Identifiability diagnostics (spec §22.2) over every (x, b, a0) series the fit used (one, or a pooled stack)."""
    u = cfg["unstable"]
    k = draws[:, 1]
    cv_k = float(k.std() / k.mean()) if k.mean() > 0 else float("inf")
    h = np.concatenate([hill(adstock(x, f.params[3], a0)[0], f.params[1], f.params[2]) for x, _, a0 in series])
    b = np.concatenate([b_ for _, b_, _ in series])
    corr = float(np.corrcoef(h, b)[0, 1]) if h.std() > 0 and b.std() > 0 else 0.0
    diag = {"condition_number": f.condition_number, "bootstrap_cv_K": cv_k,
            "bootstrap_cv_beta": float(draws[:, 0].std() / draws[:, 0].mean()) if draws[:, 0].mean() > 0 else None,
            "baseline_corr": corr}
    reasons = [r for r, bad in (("condition_number", f.condition_number > u["condition_number"]),
                                ("bootstrap_cv_K", cv_k > u["cv_K"]),
                                ("baseline_corr", abs(corr) > u["baseline_corr"])) if bad]
    return not reasons, {**diag, "unstable_reason": ",".join(reasons) or None}


def fit_curves(db, as_of: datetime, cfg_overrides: dict | None = None) -> dict:
    cfg = {**curve_config(), **(cfg_overrides or {})}
    last = as_of.date() - timedelta(days=1)
    d_lo = last - timedelta(days=cfg["diagnostic_days"] - 1)
    p_hi = d_lo - timedelta(days=1)
    p_lo = p_hi - timedelta(days=cfg["holdout_days"] - 1)
    t_hi = p_lo - timedelta(days=1)
    t_lo = t_hi - timedelta(days=cfg["train_days"] - 1)
    dep_lo = last - timedelta(days=cfg["train_days"] - 1)
    idx_train = block_indices(cfg["train_days"], cfg["block_days"], cfg["bootstrap_draws"], cfg["seed"])

    units = budget_units(db)
    data = {}
    for unit, cids, channel, ps in units:
        full = _daily(db, cids, t_lo, last)
        base = _baseline(db, ps, t_lo, last)
        data[unit] = (cids, channel, full, base)

    def window(unit: str, lo: date, hi: date):
        _, _, full, base = data[unit]
        mask = (full.index >= lo) & (full.index <= hi)
        return full["spend"].to_numpy()[mask], full["revenue"].to_numpy()[mask], base[mask]

    # ---- per-unit candidate on Train, scored on P and D ----------------------------------------------------------
    results: dict[str, dict] = {}
    for unit, (cids, channel, _, _) in data.items():
        s, r, b = window(unit, t_lo, t_hi)
        ms, mr, bm = _normalised(s, r, b)
        res = {"channel": channel, "campaign_ids": cids, "status": "OK", "reason": None, "diagnostics": {}}
        if ms < cfg["min_median_spend"] or (s > 0).sum() < cfg["min_spend_days"]:
            res.update(status="POOLED", reason="INSUFFICIENT_DATA: spend floor")
            results[unit] = res
            continue
        x, y, bn = s / ms, r / mr, b / bm
        a0 = float(x[:7].mean())
        cand = fit([(x, y, bn, a0)], cfg)
        if cand.success:
            res["_cand"] = cand.params
        sp, rp, bp = window(unit, p_lo, p_hi)
        sd, rd, bd = window(unit, d_lo, last)
        a_t = adstock(x, cand.params[3], a0)[0][-1]
        naive = float(np.mean(y[-28:]))
        yp = predict(cand.params, sp / ms, bp / bm, a_t)
        r2_p = skill(rp / mr, yp, naive)
        a_p = adstock(sp / ms, cand.params[3], a_t)[0][-1]
        yd = predict(cand.params, sd / ms, bd / bm, a_p)
        r2_d = skill(rd / mr, yd, naive)
        wape_p = float(np.sum(np.abs(rp / mr - yp)) / max(np.sum(np.abs(rp / mr)), 1e-12))
        hits = _coverage(cand, predict(cand.params, x, bn, a0), sp / ms, rp / mr, bp / bm, a_t, cfg, cfg["seed"] + 1)
        res["holdout"] = {"r2_P": r2_p, "r2_D": r2_d, "wape_P": wape_p, "coverage_P": float(hits.mean()),
                          "train_r2": cand.r2}
        if not cand.success:
            res.update(status="POOLED", reason=f"FIT_FAILED: {cand.message}")
        elif r2_p < 0:
            res.update(status="POOLED", reason="REJECTED: holdout R^2 < 0")
        else:
            res["_hits"] = hits
        results[unit] = res

    # ---- family coverage (spec §7.3 / §10.2 (c)): P10-P90 coverage pooled over every candidate's holdout days.
    # Per unit, 28 days cannot test it: a correctly calibrated 80% interval lands outside [70%, 90%] ~1 time in 5.
    lo_c, hi_c = cfg["coverage_band"]
    all_hits = [r.pop("_hits") for r in results.values() if "_hits" in r]
    family_cov = float(np.concatenate(all_hits).mean()) if all_hits else float("nan")
    if all_hits and not lo_c <= family_cov <= hi_c:
        for r in results.values():
            if r["status"] == "OK":
                r.update(status="POOLED", reason=f"REJECTED: family holdout coverage {family_cov:.0%} outside "
                                                 f"[{lo_c:.0%}, {hi_c:.0%}]")

    # ---- pooled channel curves (normalised by each campaign's median), Train candidate + deployed refit ---------
    pooled: dict[str, dict] = {}
    for channel in sorted({v[1] for v in data.values()}):
        members = [u for u, v in data.items() if v[1] == channel]
        def stack(lo, hi, members=members):
            out = []
            for u in members:
                s, r, b = window(u, lo, hi)
                ms, mr, bm = _normalised(s, r, b)
                if ms > 0:
                    x = s / ms
                    out.append((u, (x, r / mr, b / bm, float(x[:7].mean())), ms, mr, bm))
            return out
        tr = stack(t_lo, t_hi)
        info = {"status": "MODEL_UNAVAILABLE", "members": len(tr)}
        if len(tr) >= cfg["pooled_min_campaigns"]:
            cand = fit([t[1] for t in tr], cfg)
            hold = stack(p_lo, p_hi)
            ys, ps_, nv = [], [], []
            for (_u, (x, y, _b, a0), *_), (_, (xp, yp_, bp_, _), *_) in zip(tr, hold, strict=False):
                a_t = adstock(x, cand.params[3], a0)[0][-1]
                ps_.append(predict(cand.params, xp, bp_, a_t))
                ys.append(yp_)
                nv.append(np.full(len(yp_), np.mean(y[-28:])))
            yy, pp, naive_all = np.concatenate(ys), np.concatenate(ps_), np.concatenate(nv)
            sse_naive = float(np.sum((yy - naive_all) ** 2))
            r2_p = 1 - float(np.sum((yy - pp) ** 2)) / sse_naive if sse_naive > 0 else float("nan")
            info.update({"r2_P": r2_p, "train_r2": cand.r2})
            if cand.success:
                info["cand_params"] = cand.params
            if cand.success and r2_p >= 0:
                dep = stack(dep_lo, last)
                f = fit([t[1] for t in dep], cfg, init=cand.params)
                draws = np.empty((cfg["bootstrap_draws"], len(PARAMS)))
                offsets = np.cumsum([0] + [len(t[1][0]) for t in dep])
                for d in range(cfg["bootstrap_draws"]):
                    series = []
                    for j, (_, (x, y, b, a0), *_rest) in enumerate(dep):
                        res_j = f.residuals[offsets[j]:offsets[j + 1]]
                        y_star = (y + res_j) - res_j[idx_train[d] % len(res_j)]
                        series.append((x, y_star, b, a0))
                    draws[d] = fit(series, cfg, init=f.params).params
                stable, diag = _stability(f, draws, [(x, b, a0) for _, (x, _, b, a0), *_ in dep], cfg)
                info.update({"status": "OK" if f.success and stable else "MODEL_UNAVAILABLE", "params": f.params,
                             "draws": draws, "diagnostics": diag,
                             "terminal": {u: adstock(x, f.params[3], a0)[0][-1] for u, (x, _, _, a0), *_ in dep},
                             "scales": {u: (ms, mr, bm) for u, _, ms, mr, bm in dep}})
        pooled[channel] = info

    # ---- deployed per-unit refits, stability, shrinkage, artifacts -------------------------------------------------
    artifacts: dict[str, CurveArtifact] = {}
    for unit, res in results.items():
        s, r, b = window(unit, dep_lo, last)
        ms, mr, bm = _normalised(s, r, b)
        pc = pooled.get(res["channel"], {})
        params = draws = None
        terminal = 0.0
        if res["status"] == "OK":
            x, y, bn = s / ms, r / mr, b / bm
            a0 = float(x[:7].mean())
            f = fit([(x, y, bn, a0)], cfg)
            draws = bootstrap((x, y, bn, a0), f, idx_train % len(x), cfg)
            stable, diag = _stability(f, draws, [(x, bn, a0)], cfg)
            res["diagnostics"] = diag
            if not (f.success and stable):
                res.update(status="POOLED", reason=f"UNSTABLE: {diag['unstable_reason'] or 'fit failed'}")
                draws = None
            else:
                params, terminal = f.params, adstock(x, f.params[3], a0)[0][-1]
        n_eff = int((s > 0).sum())
        w = n_eff / (n_eff + cfg["shrinkage_k"]) if res["status"] == "OK" else 0.0
        pooled_ok = pc.get("status") == "OK" and unit in pc.get("terminal", {})
        if w < 1 and not pooled_ok:
            if res["status"] == "OK":
                w = 1.0  # no pooled curve to shrink toward: the unit's own accepted, stable curve stands alone
            else:
                res.update(status="MODEL_UNAVAILABLE",
                           reason=(res["reason"] or "") + "; pooled channel curve unavailable")
        base_level = float(np.mean(b[-cfg["baseline_trend_days"]:]) / bm) if bm else 1.0
        cand_params = res.pop("_cand", None) if res["status"] == "OK" else \
            (pc.get("cand_params") if res["status"] == "POOLED" else None)
        res.pop("_cand", None)
        oos, oos_model = _oos_residuals(cand_params, *window(unit, t_lo, t_hi), *window(unit, p_lo, last))
        artifacts[unit] = CurveArtifact(
            unit_id=unit, status="OK" if res["status"] == "OK" else res["status"], w=w, median_spend=ms,
            median_revenue=mr, baseline_mean=bm, baseline_level=base_level, terminal_adstock=float(terminal),
            params=params, draws=draws,
            pooled_params=pc.get("params") if pooled_ok else None, pooled_draws=pc.get("draws") if pooled_ok else None,
            pooled_terminal_adstock=float(pc.get("terminal", {}).get(unit, 0.0)),
            diagnostics={**res.get("diagnostics", {}), "holdout": res.get("holdout"), "reason": res["reason"],
                         "n_eff": n_eff, "oos_rel_residuals": oos, "oos_model": oos_model,
                         "pooled": {k: v for k, v in pc.items()
                                    if k in ("status", "members", "r2_P", "train_r2", "diagnostics")},
                         "family_coverage_P": family_cov})
    _persist(db, as_of, artifacts, results)
    return {"units": len(artifacts),
            "status": {s: sum(1 for a in artifacts.values() if a.status == s)
                       for s in ("OK", "POOLED", "MODEL_UNAVAILABLE")},
            "pooled": {c: p.get("status") for c, p in pooled.items()}, "family_coverage_P": family_cov}


DDL = """
CREATE SCHEMA IF NOT EXISTS models;
CREATE TABLE IF NOT EXISTS models.response_curves (
    unit_id VARCHAR NOT NULL, fit_ts TIMESTAMP NOT NULL, channel_id VARCHAR, campaign_ids JSON, status VARCHAR,
    w DOUBLE, median_spend DOUBLE, median_revenue DOUBLE, baseline_mean DOUBLE, baseline_level DOUBLE,
    terminal_adstock DOUBLE, pooled_terminal_adstock DOUBLE, params JSON, pooled_params JSON, diagnostics JSON,
    artifact_sha256 VARCHAR, PRIMARY KEY (unit_id, fit_ts)
);
CREATE TABLE IF NOT EXISTS models.curve_draws (
    unit_id VARCHAR NOT NULL, fit_ts TIMESTAMP NOT NULL, draw INTEGER NOT NULL, kind VARCHAR NOT NULL,
    beta DOUBLE, K DOUBLE, S DOUBLE, theta DOUBLE, gamma DOUBLE, PRIMARY KEY (unit_id, fit_ts, kind, draw)
);
CREATE TABLE IF NOT EXISTS models.registry (
    model VARCHAR NOT NULL, version VARCHAR NOT NULL, role VARCHAR NOT NULL, fit_ts TIMESTAMP NOT NULL,
    validation_metrics JSON, promoted_at TIMESTAMP, promotion_reason VARCHAR, PRIMARY KEY (model, version)
);
"""


def _sha(a: CurveArtifact) -> str:
    h = hashlib.sha256()
    for arr in (a.params, a.draws, a.pooled_params, a.pooled_draws):
        h.update(b"-" if arr is None else np.ascontiguousarray(arr, dtype=float).tobytes())
    h.update(json.dumps([a.status, a.w, a.median_spend, a.median_revenue, a.baseline_level, a.terminal_adstock,
                         a.pooled_terminal_adstock]).encode())
    return h.hexdigest()


def _persist(db, as_of: datetime, artifacts: dict[str, CurveArtifact], results: dict) -> None:
    def work(cur):
        cur.execute(DDL)
        draws_rows = []
        for unit, a in artifacts.items():
            sha = _sha(a)
            cur.execute("INSERT OR REPLACE INTO models.response_curves VALUES "
                        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [unit, as_of, results[unit]["channel"], json.dumps(results[unit]["campaign_ids"]), a.status,
                         a.w, a.median_spend, a.median_revenue, a.baseline_mean, a.baseline_level, a.terminal_adstock,
                         a.pooled_terminal_adstock, json.dumps(None if a.params is None else a.params.tolist()),
                         json.dumps(None if a.pooled_params is None else a.pooled_params.tolist()),
                         json.dumps(a.diagnostics, default=float), sha])
            for kind, arr in (("unit", a.draws), ("pooled", a.pooled_draws)):
                if arr is not None:
                    draws_rows += [(unit, as_of, d, kind, *map(float, arr[d])) for d in range(len(arr))]
        if draws_rows:
            frame = pd.DataFrame(draws_rows, columns=["unit_id", "fit_ts", "draw", "kind", *PARAMS])
            cur.register("_draws", frame)
            try:
                cur.execute("INSERT OR REPLACE INTO models.curve_draws BY NAME SELECT * FROM _draws")
            finally:
                cur.unregister("_draws")
        version = hashlib.sha256("".join(sorted(_sha(a) for a in artifacts.values())).encode()).hexdigest()[:16]
        metrics = {"units": len(artifacts), "ok": sum(a.status == "OK" for a in artifacts.values())}
        cur.execute("UPDATE models.registry SET role = 'retired' WHERE model = 'response_curve' AND role = 'champion'")
        cur.execute("INSERT OR REPLACE INTO models.registry VALUES ('response_curve', ?, 'champion', ?, ?, ?, ?)",
                    [version, as_of, json.dumps(metrics), as_of, "Stage 1: first fit (criteria a + c)"])

    db.write(work)


def load_curves(db, fit_ts: datetime | None = None) -> dict[str, CurveArtifact]:
    ts = fit_ts or db.query("SELECT max(fit_ts) FROM models.response_curves")[0][0]
    out = {}
    draws = {}
    for unit, kind, _d, *p in db.query("SELECT unit_id, kind, draw, beta, K, S, theta, gamma FROM models.curve_draws "
                                      "WHERE fit_ts = ? ORDER BY unit_id, kind, draw", [ts]):
        draws.setdefault((unit, kind), []).append(p)
    for (unit, _ch, _cids, status, w, ms, mr, bm, bl, ta, pta, params, pparams,
         diag) in db.query("""SELECT unit_id, channel_id, campaign_ids, status, w, median_spend, median_revenue,
                                     baseline_mean, baseline_level, terminal_adstock, pooled_terminal_adstock, params,
                                     pooled_params, diagnostics FROM models.response_curves WHERE fit_ts = ?""", [ts]):
        p, pp = json.loads(params), json.loads(pparams)
        out[unit] = CurveArtifact(
            unit, status, w, ms, mr, bm, bl, ta, None if p is None else np.array(p),
            np.array(draws[(unit, "unit")]) if (unit, "unit") in draws else None,
            None if pp is None else np.array(pp),
            np.array(draws[(unit, "pooled")]) if (unit, "pooled") in draws else None, pta, json.loads(diag))
    return out
