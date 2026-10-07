"""Anomaly detector (B2, spec §6): flag = MATERIAL AND (STAT OR SHIFT), plus the COLLAPSE path, then classification,
platform grouping and persistence.

Run as of a logical time T: the evaluated windows end at the last complete day (T - 1); every number comes from
marts built as of T. Three signals are kept separate and never blended: robust z (STAT), change point (SHIFT) and
₹ impact (MATERIAL).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from adapt.detect.stats import (
    backtest_residuals,
    expected_numerator,
    impact_cba,
    pelt_shift,
    robust_z,
    stat_fires,
    stl_forecast,
    to_transformed,
)

CONFIG = Path(__file__).resolve().parents[1] / "config"
EFFICIENCY = ("CTR", "CPM", "CVR", "AOV", "ROAS", "POAS", "CPA")
COLLAPSE_RULES = {  # metric -> (critical column that hits zero, column that must still be positive)
    "ROAS": ("attributed_net_revenue", "spend"),
    "CVR": ("attributed_orders", "clicks"),
    "CPA": ("attributed_orders", "spend"),
    "CTR": ("clicks", "impressions"),
    "AOV": ("attributed_orders", None),
}
INCIDENT_CLASSES = ("efficiency_anomaly", "tracking_issue")


@lru_cache
def catalog() -> dict:
    return yaml.safe_load((CONFIG / "metric_catalog.yaml").read_text(encoding="utf-8"))


@lru_cache
def materiality() -> dict:
    return yaml.safe_load((CONFIG / "materiality.yaml").read_text(encoding="utf-8"))


@dataclass
class MetricFlag:
    entity_type: str
    entity_id: str
    platform: str | None
    metric: str
    window_days: int
    post_start: date
    post_end: date
    status: str = "OK"            # OK | INELIGIBLE | INSUFFICIENT_HISTORY | INSUFFICIENT_VARIANCE
    direction: str = "FLAT"
    actual: float | None = None
    expected: float | None = None
    relative_change: float | None = None
    signed_impact: float = 0.0
    threshold: float = 0.0
    material: bool = False
    stat_fired: bool = False
    max_abs_z: float | None = None
    shift_fired: bool = False
    shift_score: float = 0.0
    collapse: bool = False
    flagged: bool = False
    pre_actual: float | None = None
    daily_z: list = field(default_factory=list)


def _window_value(num: np.ndarray, den: np.ndarray | None, scale: float) -> float | None:
    if den is None:
        return float(num.sum())
    d = den.sum()
    return float(scale * num.sum() / d) if d > 0 else None


def evaluate(entity_type: str, entity_id: str, platform: str | None, metric: str, spec: dict,
             frame: pd.DataFrame, window: int, cba_col: str = "attributed_cba",
             scale_cache: dict | None = None) -> MetricFlag:
    """One (entity, metric, window) evaluation on a complete daily frame ending at the last complete day.

    The error scale (backtest errors) ends before the longest evaluated window, so it is shared by every window of
    the same (entity, metric) and never sees any evaluated day; pass `scale_cache` to reuse it across windows."""
    cfg = materiality()
    det = cfg["detector"]
    n = len(frame)
    end = frame["date"].iloc[-1]
    flag = MetricFlag(entity_type, entity_id, platform, metric, window, end - timedelta(days=window - 1), end)
    if n < window + det["fit_days"]:
        flag.status = "INSUFFICIENT_HISTORY"
        return flag
    post = slice(n - window, n)
    fit = slice(n - window - det["fit_days"], n - window)
    pre = slice(n - window - det["pre_days"], n - window)
    level = "level" in spec
    num_col = spec["level"] if level else spec["num"]
    den_col = None if level else spec["den"]
    scale = float(spec.get("scale", 1.0))
    num = frame[num_col].to_numpy(dtype=float)
    den = None if level else frame[den_col].to_numpy(dtype=float)

    # eligibility on PRE-window totals only (spec §6)
    for col, minimum in spec.get("eligible", {}).items():
        if frame[col].iloc[pre].sum() < minimum:
            flag.status = "INELIGIBLE"
            return flag

    pre_tot = {c: float(frame[c].iloc[pre].sum()) for c in frame.columns
               if c != "date" and pd.api.types.is_numeric_dtype(frame[c]) and not pd.api.types.is_bool_dtype(frame[c])}
    ref = {
        "ctr": pre_tot.get("clicks", 0) / pre_tot["impressions"] if pre_tot.get("impressions") else 0.0,
        "cvr": pre_tot.get("attributed_orders", 0) / pre_tot["clicks"] if pre_tot.get("clicks") else 0.0,
        "aov": pre_tot.get("attributed_net_revenue", 0) / pre_tot["attributed_orders"]
        if pre_tot.get("attributed_orders") else 0.0,
        "session_cvr": pre_tot.get("attributed_orders", 0) / pre_tot["ga_sessions"] if pre_tot.get("ga_sessions")
        else 0.0,
        "unit_contribution": pre_tot.get("cba", 0) / pre_tot["units"] if pre_tot.get("units") else 0.0,
    }
    pseudo = spec.get("pseudo", 0.0)
    if pseudo == "half_order_value":
        pseudo = 0.5 * ref["aov"]
    pseudo_den = float(spec.get("pseudo_den", 0.0))
    y = to_transformed(num, den, spec["transform"], float(pseudo), pseudo_den)

    try:
        fc = stl_forecast(y[fit], window, det["trend_points"])
    except ValueError:
        flag.status = "INSUFFICIENT_HISTORY"
        return flag
    exp_num = expected_numerator(fc.yhat, None if den is None else den[post], spec["transform"], float(pseudo),
                                 pseudo_den)
    act_num = num[post]
    flag.actual = _window_value(act_num, None if den is None else den[post], scale)
    flag.expected = _window_value(np.maximum(exp_num, 0) if not spec.get("signed") else exp_num,
                                  None if den is None else den[post], scale)
    flag.pre_actual = _window_value(num[pre], None if den is None else den[pre], scale)

    # daily metric values for ImpactCBA (single-factor substitution with the day's actual denominator)
    if level:
        act_m, exp_m = act_num, np.maximum(exp_num, 0)
    else:
        d_post = den[post]
        with np.errstate(divide="ignore", invalid="ignore"):
            act_m = np.where(d_post > 0, scale * act_num / d_post, np.nan)
            exp_m = np.where(d_post > 0, scale * exp_num / d_post, np.nan)
    arrays = {c: frame[c].to_numpy(dtype=float)[post] for c in
              ("impressions", "clicks", "attributed_orders", "spend", "attributed_net_revenue") if c in frame}
    cba_pre_daily = pre_tot.get(cba_col, 0.0) / det["pre_days"]
    cm = pre_tot.get("attributed_cba", 0) / pre_tot["attributed_net_revenue"] \
        if pre_tot.get("attributed_net_revenue") else 0.0
    if metric == "SKU_UNITS":
        cba_pre_daily = pre_tot.get("cba", 0.0) / det["pre_days"]
    impact = impact_cba(metric, arrays, np.nan_to_num(exp_m), np.nan_to_num(act_m), cm, ref) \
        if metric not in ("SKU_UNITS", "SPEND") else np.nan_to_num(exp_m) - np.nan_to_num(act_m)
    if metric == "SKU_UNITS":
        impact = impact * ref["unit_contribution"]
    flag.signed_impact = float(np.nansum(impact))
    flag.threshold = max(cfg["impact"]["min_inr"], cfg["impact"]["share_of_daily_cba"] * max(cba_pre_daily, 0.0))

    # direction on window aggregates (spec §6): FLAT can never be MATERIAL
    if flag.actual is not None and flag.expected is not None:
        delta = flag.actual - flag.expected
        tol = cfg["absolute_direction_floor"][metric] if spec.get("signed") or abs(flag.expected) < 1e-12 \
            and metric in cfg["absolute_direction_floor"] else cfg["direction_tolerance"] * abs(flag.expected)
        flag.direction = "FLAT" if abs(delta) <= tol else ("UP" if delta > 0 else "DOWN")
        if not spec.get("signed") and flag.expected > 0:
            flag.relative_change = flag.actual / flag.expected - 1

    # COLLAPSE: the critical numerator/denominator reached zero in the post window while the pre window was eligible
    if metric in COLLAPSE_RULES:
        zero_col, pos_col = COLLAPSE_RULES[metric]
        if frame[zero_col].iloc[post].sum() == 0 and (pos_col is None or frame[pos_col].iloc[post].sum() > 0):
            flag.collapse = True
            flag.direction = "DOWN"  # direction of the business effect
            flag.material = abs(flag.signed_impact) >= flag.threshold
            flag.flagged = flag.material
            return flag

    floor = cfg["relative_floor"].get(metric, 0.0)
    rel_ok = spec.get("signed") or (flag.relative_change is not None and abs(flag.relative_change) >= floor)
    flag.material = flag.direction != "FLAT" and abs(flag.signed_impact) >= flag.threshold and bool(rel_ok)
    if not flag.material and metric != "SPEND":
        return flag  # flag = MATERIAL AND (STAT OR SHIFT): the statistical tests cannot change the outcome

    # error scale: out-of-sample backtest errors of the same forecast rule (fallback: in-sample residuals)
    key = (entity_type, entity_id, metric)
    if scale_cache is not None and key in scale_cache:
        scale_resid = scale_cache[key]
    else:
        scale_resid = backtest_residuals(y, n - max(det["post_windows"]), det["fit_days"], det["backtest_origins"], 7,
                                         det["trend_points"])
        if scale_cache is not None:
            scale_cache[key] = scale_resid
    if scale_resid is None or scale_resid.size == 0:
        scale_resid = fc.resid_insample
    rz = robust_z(y[post] - fc.yhat, scale_resid)
    flag.status = rz.status
    flag.daily_z = [None if not math.isfinite(v) else round(float(v), 3) for v in rz.z]
    finite = rz.z[np.isfinite(rz.z)]
    flag.max_abs_z = float(np.max(np.abs(finite))) if finite.size else None
    flag.stat_fired = stat_fires(rz.z, det["z1"], det["z2"], det["consecutive"])
    med = float(np.median(scale_resid))
    mad = float(np.median(np.abs(scale_resid - med)))
    sh = pelt_shift(y[n - det["shift_days"]:], 1.4826 * mad, window, det["beta"], det["min_segment"])
    flag.shift_fired, flag.shift_score = sh.fired, sh.score
    flag.flagged = flag.material and (flag.stat_fired or flag.shift_fired)
    return flag


# ---- running the detector over the marts ------------------------------------------------------------------------
def _campaign_frames(db, last: date) -> dict[str, tuple[str, pd.DataFrame]]:
    df = pd.DataFrame(db.query(
        "SELECT * FROM marts.campaign_daily WHERE date <= ? ORDER BY campaign_id, date", [last]),
        columns=[r[0] for r in db.query("DESCRIBE marts.campaign_daily")])
    return {cid: (g["platform"].iloc[0], g.reset_index(drop=True)) for cid, g in df.groupby("campaign_id")}


def _creative_frames(db, last: date) -> dict[str, tuple[str, str, pd.DataFrame]]:
    """Per creative a complete calendar (zero rows where the creative had no delivery) from its first day."""
    cols = ["date", "ad_id", "campaign_id", "impressions", "clicks", "spend", "attributed_orders",
            "attributed_net_revenue", "attributed_cba"]
    df = pd.DataFrame(db.query(f"SELECT {', '.join(cols)} FROM marts.creative_daily WHERE date <= ? "
                               "ORDER BY ad_id, date", [last]), columns=cols)
    platform = dict(db.query("SELECT campaign_id, platform FROM core.campaigns"))
    out = {}
    for ad, g in df.groupby("ad_id"):
        idx = pd.date_range(g["date"].min(), last, freq="D").date
        full = g.set_index("date").reindex(idx)
        full[cols[3:]] = full[cols[3:]].fillna(0)
        full = full.rename_axis("date").reset_index()
        cid = g["campaign_id"].iloc[0]
        out[ad] = (cid, platform.get(cid), full.drop(columns=["ad_id", "campaign_id"]))
    return out


def _sku_frames(db, last: date) -> dict[str, pd.DataFrame]:
    df = pd.DataFrame(db.query("SELECT date, sku, units, cba FROM marts.sku_daily WHERE date <= ? ORDER BY sku, date",
                               [last]), columns=["date", "sku", "units", "cba"])
    return {sku: g.reset_index(drop=True) for sku, g in df.groupby("sku")}


def classify(flags: list[MetricFlag], budget_events: dict[str, list[date]], camp_budget: dict[str, str],
             spend_change: dict[str, float], clicks_change: dict[str, float]) -> dict[tuple, str]:
    """Label each flagged (entity, metric) (spec §6). Only efficiency_anomaly and tracking_issue open incidents."""
    cl = materiality()["classifier"]
    labels: dict[tuple, str] = {}
    by_entity: dict[str, list[MetricFlag]] = {}
    for f in flags:
        if f.flagged:
            by_entity.setdefault(f.entity_id, []).append(f)
    for entity, fs in by_entity.items():
        holiday = any(f.post_start <= date.fromisoformat(h) <= f.post_end for f in fs for h in cl["holidays"])
        spend_flag = next((f for f in fs if f.metric == "SPEND"), None)
        budget_change = False
        if spend_flag is not None and abs(spend_change.get(entity, 0.0)) >= math.log(1 + cl["budget_spend_change"]):
            events = budget_events.get(camp_budget.get(entity, ""), [])
            budget_change = any(spend_flag.post_start - timedelta(days=3) <= e <= spend_flag.post_end for e in events)
            labels[(entity, "SPEND")] = "budget_change" if budget_change else "delivery_change"
        for f in fs:
            key = (entity, f.metric)
            if f.metric == "SPEND":
                continue
            if holiday:
                labels[key] = "seasonal_expected"
            elif f.metric == "SESSION_CLICK":
                stable = abs(clicks_change.get(entity, 0.0)) <= math.log(1 + cl["tracking_clicks_stable"])
                labels[key] = "tracking_issue" if f.direction == "DOWN" and stable else "measurement_change"
            elif budget_change and (f.max_abs_z is None or f.max_abs_z < cl["efficiency_band_z"]):
                labels[key] = "budget_change"
            else:
                labels[key] = "efficiency_anomaly"
    return labels


def _log_change(frame: pd.DataFrame, col: str, window: int, pre_days: int) -> float:
    post = frame[col].iloc[-window:].sum() / window
    pre = frame[col].iloc[-window - pre_days:-window].sum() / pre_days
    if post <= 0 or pre <= 0:
        return math.inf if post != pre else 0.0
    return math.log(post / pre)


INTEL_DDL = """
CREATE SCHEMA IF NOT EXISTS intel;
CREATE TABLE IF NOT EXISTS intel.metric_flags (
    run_id VARCHAR NOT NULL, as_of TIMESTAMP NOT NULL, entity_type VARCHAR NOT NULL, entity_id VARCHAR NOT NULL,
    platform VARCHAR, metric VARCHAR NOT NULL, window_days INTEGER NOT NULL, post_start DATE, post_end DATE,
    status VARCHAR, direction VARCHAR, actual DOUBLE, expected DOUBLE, pre_actual DOUBLE, relative_change DOUBLE,
    signed_impact DOUBLE, threshold DOUBLE, material BOOLEAN, stat_fired BOOLEAN, max_abs_z DOUBLE,
    shift_fired BOOLEAN, shift_score DOUBLE, collapse BOOLEAN, flagged BOOLEAN, daily_z JSON,
    classification VARCHAR, PRIMARY KEY (run_id, entity_type, entity_id, metric, window_days)
);
CREATE TABLE IF NOT EXISTS intel.anomalies (
    anomaly_id VARCHAR PRIMARY KEY, scope VARCHAR NOT NULL, entity_key VARCHAR NOT NULL, entity_ids JSON NOT NULL,
    platform VARCHAR, metric VARCHAR NOT NULL, direction VARCHAR NOT NULL, classification VARCHAR NOT NULL,
    is_incident BOOLEAN NOT NULL, status VARCHAR NOT NULL, window_start DATE, window_end DATE, actual DOUBLE,
    expected DOUBLE, pre_actual DOUBLE, relative_change DOUBLE, signed_impact DOUBLE, threshold DOUBLE,
    stat_fired BOOLEAN, max_abs_z DOUBLE, shift_fired BOOLEAN, collapse BOOLEAN,
    first_detected_at TIMESTAMP NOT NULL, last_detected_at TIMESTAMP NOT NULL, last_run_id VARCHAR NOT NULL
);
"""


def run_detection(db, as_of: datetime, run_id: str) -> dict:
    cat, det = catalog(), materiality()["detector"]
    last = as_of.date() - timedelta(days=1)
    camps = _campaign_frames(db, last)
    flags: list[MetricFlag] = []
    spend_change, clicks_change = {}, {}
    cache: dict = {}
    for cid, (platform, frame) in camps.items():
        frame = frame[frame["date"] <= last].reset_index(drop=True)
        for metric, spec in cat["campaign_metrics"].items():
            for w in det["post_windows"]:
                flags.append(evaluate("campaign", cid, platform, metric, spec, frame, w, scale_cache=cache))
        w = min(det["post_windows"])
        spend_change[cid] = _log_change(frame, "spend", w, det["pre_days"]) if len(frame) > w + det["pre_days"] else 0
        clicks_change[cid] = _log_change(frame, "clicks", w, det["pre_days"]) if len(frame) > w + det["pre_days"] else 0
    parent: dict[str, str] = {}
    for ad, (cid, platform, frame) in _creative_frames(db, last).items():
        parent[ad] = cid
        for metric, spec in cat["creative_metrics"].items():
            for w in det["post_windows"]:
                flags.append(evaluate("creative", ad, platform, metric, spec, frame, w, scale_cache=cache))
    for sku, frame in _sku_frames(db, last).items():
        for metric, spec in cat["sku_metrics"].items():
            for w in det["post_windows"]:
                flags.append(evaluate("sku", sku, None, metric, spec, frame, w, cba_col="cba", scale_cache=cache))

    # one row per (entity, metric): the flagged window with the largest |impact|, else the shortest window
    best: dict[tuple, MetricFlag] = {}
    for f in flags:
        k = (f.entity_type, f.entity_id, f.metric)
        cur = best.get(k)
        if cur is None or (f.flagged and (not cur.flagged or abs(f.signed_impact) > abs(cur.signed_impact))):
            best[k] = f
    chosen = list(best.values())

    camp_budget = dict(db.query("SELECT campaign_id, budget_id FROM core.campaigns"))
    events: dict[str, list[date]] = {}
    for bid, d in db.query("SELECT entity_id, effective_from FROM core.budget_history "
                           "WHERE source = 'observed_change'"):
        events.setdefault(bid, []).append(d)
    labels = classify([f for f in chosen if f.entity_type == "campaign"], events, camp_budget, spend_change,
                      clicks_change)
    for f in chosen:
        if f.entity_type == "sku" and f.flagged:
            labels[(f.entity_id, f.metric)] = "sku_signal"  # evidence for the inventory module, not an incident
        elif f.entity_type == "creative" and f.flagged:
            labels[(f.entity_id, f.metric)] = "efficiency_anomaly"

    def persist(cur) -> dict:
        cur.execute(INTEL_DDL)
        rows = []
        for f in flags:
            d = asdict(f)
            d["daily_z"] = json.dumps(d["daily_z"])
            d["classification"] = labels.get((f.entity_id, f.metric)) \
                if f is best[(f.entity_type, f.entity_id, f.metric)] else None
            rows.append({"run_id": run_id, "as_of": as_of, **d})
        frame = pd.DataFrame(rows)
        cur.register("_flags", frame)
        try:
            cur.execute("INSERT OR REPLACE INTO intel.metric_flags BY NAME SELECT * FROM _flags")
        finally:
            cur.unregister("_flags")
        return _upsert_anomalies(cur, chosen, labels, parent, as_of, run_id)

    summary = db.write(persist)
    summary["evaluations"] = len(flags)
    summary["flagged"] = sum(1 for f in chosen if f.flagged)
    return summary


def _upsert_anomalies(cur, chosen: list[MetricFlag], labels: dict, parent: dict[str, str], as_of: datetime,
                      run_id: str) -> dict:
    det = materiality()["detector"]
    flagged = [f for f in chosen if f.flagged and (f.entity_id, f.metric) in labels]
    # platform grouping: >= group_share of a platform's eligible campaigns flag the same metric + direction
    eligible: dict[tuple, int] = {}
    for f in chosen:
        if f.entity_type == "campaign" and f.status not in ("INELIGIBLE", "INSUFFICIENT_HISTORY"):
            eligible[(f.platform, f.metric)] = eligible.get((f.platform, f.metric), 0) + 1
    groups: dict[tuple, list[MetricFlag]] = {}
    for f in flagged:
        if f.entity_type == "campaign" and labels[(f.entity_id, f.metric)] == "efficiency_anomaly":
            groups.setdefault((f.platform, f.metric, f.direction), []).append(f)
    records = []
    grouped_ids: set[tuple] = set()
    for (platform, metric, direction), fs in groups.items():
        if len(fs) >= 2 and len(fs) >= det["group_share"] * eligible.get((platform, metric), 0):
            grouped_ids |= {(f.entity_id, f.metric) for f in fs}
            records.append(_record("platform", platform, sorted(f.entity_id for f in fs), platform, metric, direction,
                                   "efficiency_anomaly", fs))
    for f in flagged:
        if (f.entity_id, f.metric) in grouped_ids:
            continue
        # a creative incident carries its campaign first, so diagnosis and decisions act on the campaign
        ids = [parent[f.entity_id], f.entity_id] if f.entity_type == "creative" else [f.entity_id]
        records.append(_record(f.entity_type, f.entity_id, ids, f.platform, f.metric, f.direction,
                               labels[(f.entity_id, f.metric)], [f]))
    opened = updated = 0
    for r in records:
        prior = cur.execute(
            "SELECT anomaly_id FROM intel.anomalies WHERE scope = ? AND entity_key = ? AND metric = ? "
            "AND direction = ? AND status <> 'resolved' AND last_detected_at >= ? "
            "ORDER BY last_detected_at DESC LIMIT 1",
            [r["scope"], r["entity_key"], r["metric"], r["direction"], as_of - timedelta(days=det["reopen_days"])],
        ).fetchone()
        if prior:
            cur.execute("""UPDATE intel.anomalies SET window_start = ?, window_end = ?, actual = ?, expected = ?,
                           pre_actual = ?, relative_change = ?, signed_impact = ?, threshold = ?, stat_fired = ?,
                           max_abs_z = ?, shift_fired = ?, collapse = ?, last_detected_at = ?, last_run_id = ?,
                           entity_ids = ?, classification = ?, is_incident = ? WHERE anomaly_id = ?""",
                        [r["window_start"], r["window_end"], r["actual"], r["expected"], r["pre_actual"],
                         r["relative_change"], r["signed_impact"], r["threshold"], r["stat_fired"], r["max_abs_z"],
                         r["shift_fired"], r["collapse"], as_of, run_id, json.dumps(r["entity_ids"]),
                         r["classification"], r["is_incident"], prior[0]])
            updated += 1
        else:
            aid = f"ANM-{as_of:%Y%m%d}-{r['scope'][:4]}-{r['entity_key']}-{r['metric']}-{r['direction']}"
            cur.execute(f"INSERT INTO intel.anomalies VALUES ({', '.join('?' * 25)})",
                        [aid, r["scope"], r["entity_key"], json.dumps(r["entity_ids"]), r["platform"], r["metric"],
                         r["direction"], r["classification"], r["is_incident"], "detected", r["window_start"],
                         r["window_end"], r["actual"], r["expected"], r["pre_actual"], r["relative_change"],
                         r["signed_impact"], r["threshold"], r["stat_fired"], r["max_abs_z"], r["shift_fired"],
                         r["collapse"], as_of, as_of, run_id])
            opened += 1
    return {"opened": opened, "updated": updated, "incidents": sum(1 for r in records if r["is_incident"]),
            "records": len(records)}


def _record(scope, key, ids, platform, metric, direction, classification, fs: list[MetricFlag]) -> dict:
    lead = max(fs, key=lambda f: abs(f.signed_impact))
    return {"scope": scope, "entity_key": key, "entity_ids": ids, "platform": platform, "metric": metric,
            "direction": direction, "classification": classification,
            "is_incident": classification in INCIDENT_CLASSES,
            "window_start": min(f.post_start for f in fs), "window_end": max(f.post_end for f in fs),
            "actual": lead.actual, "expected": lead.expected, "pre_actual": lead.pre_actual,
            "relative_change": lead.relative_change, "signed_impact": float(sum(f.signed_impact for f in fs)),
            "threshold": lead.threshold, "stat_fired": any(f.stat_fired for f in fs),
            "max_abs_z": max((f.max_abs_z or 0.0) for f in fs), "shift_fired": any(f.shift_fired for f in fs),
            "collapse": any(f.collapse for f in fs)}
