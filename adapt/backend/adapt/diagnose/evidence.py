"""Level-2 evidence modules (B4 + Stage 2, spec §22.1): auction, creative fatigue, inventory, price, tracking, and
(Stage 2) audience saturation and demand.

Each module returns Evidence(score in [0, 1] = gates x one magnitude term, levers, values) or an explicit
INSUFFICIENT_DATA / NOT_APPLICABLE state, never a fabricated number. post = the incident window, pre = the 28 days
before it; ratios use window totals. Budget changes are handled by the classifier (B2).

Data proxies (documented in docs/contracts/evidence.md): our sources have no sessions by landing SKU, so the CVR of a
SKU group = attributed orders on those SKUs / the campaign's clicks; creative frequency uses the campaign's
frequency (Meta reports reach per campaign; Google reports none, so the frequency gate is NOT_APPLICABLE there).
Saturation is therefore campaign-scoped (window frequency = impressions / daily reach, never summed unique reach),
and demand uses the category's unpaid (email + organic) ORDERS: GA4 reports unpaid sessions without a category.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config" / "evidence.yaml"
OK, INSUFFICIENT, NOT_APPLICABLE = "OK", "INSUFFICIENT_DATA", "NOT_APPLICABLE"


@lru_cache
def evidence_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


@dataclass
class Incident:
    anomaly_id: str
    platform: str | None
    campaign_ids: list[str]
    creative_id: str | None
    metric: str
    post_start: date
    post_end: date
    direction: str | None = None   # UP | DOWN | FLAT of the incident metric (the demand module's direction contract)

    @property
    def pre_start(self) -> date:
        return self.post_start - timedelta(days=28)

    @property
    def pre_end(self) -> date:
        return self.post_start - timedelta(days=1)


@dataclass
class Evidence:
    module: str
    status: str
    score: float
    levers: list[str]
    values: dict = field(default_factory=dict)
    reason: str | None = None


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _in(ids: list[str]) -> str:
    return ", ".join("?" * len(ids))


# ---- 1. auction pressure (lever CPM, platform scope) ----------------------------------------------
def auction(db, inc: Incident) -> Evidence:
    c = evidence_config()["auction"]
    rows = db.query("""
        SELECT campaign_id,
               sum(spend) FILTER (WHERE date BETWEEN ? AND ?), sum(impressions) FILTER (WHERE date BETWEEN ? AND ?),
               sum(spend) FILTER (WHERE date BETWEEN ? AND ?), sum(impressions) FILTER (WHERE date BETWEEN ? AND ?)
        FROM marts.campaign_daily WHERE platform = ? GROUP BY 1""",
                    [inc.pre_start, inc.pre_end, inc.pre_start, inc.pre_end, inc.post_start, inc.post_end,
                     inc.post_start, inc.post_end, inc.platform])
    r = {}
    for cid, s_pre, i_pre, s_post, i_post in rows:
        if (i_pre or 0) >= c["min_impressions"] and (i_post or 0) >= c["min_impressions"] and s_pre and s_post:
            r[cid] = math.log((s_post / i_post) / (s_pre / i_pre))
    if len(r) < c["min_campaigns"]:
        return Evidence("auction", INSUFFICIENT, 0.0, ["CPM"], {"eligible_campaigns": len(r)},
                        f"fewer than {c['min_campaigns']} campaigns with enough impressions on {inc.platform}")
    vals = np.array(list(r.values()))
    share_up = float(np.mean(vals > math.log(1 + c["rise_threshold"])))
    med = float(np.median(vals))
    score = (share_up >= c["share_up_gate"]) * min(1.0, max(med, 0.0) / math.log(1 + c["full_score_rise"]))
    own = [r[cid] for cid in inc.campaign_ids if cid in r]
    return Evidence("auction", OK, float(score), ["CPM"], {
        "platform": inc.platform, "eligible_campaigns": len(r), "share_up": round(share_up, 4),
        "median_cpm_change_pct": round(math.expm1(med) * 100, 2),
        "own_cpm_change_pct": round(math.expm1(own[0]) * 100, 2) if own else None})


# ---- 2. creative fatigue (lever CTR) ----------------------------------------------------------------------
def _creative_stats(db, ad_id: str, inc: Incident) -> dict | Evidence:
    """CTR decline vs the creative's first 7 qualifying days and the 14-day log-CTR slope (frequency: caller)."""
    c = evidence_config()["fatigue"]
    rows = db.query("SELECT date, impressions, clicks, spend FROM marts.creative_daily WHERE ad_id = ? AND date <= ? "
                    "ORDER BY date", [ad_id, inc.post_end])
    if not rows:
        return Evidence("fatigue", INSUFFICIENT, 0.0, ["CTR"], reason="no delivery")
    # first7 = the creative's first 7 delivery days; the minimum sample (>= 5,000 impressions, >= 100 clicks) applies to
    # the 7-day totals (a per-day 5,000 floor would exclude every creative of a mid-size campaign)
    days = [r for r in rows if r[1] > 0]
    first = days[:c["first_window_days"]]
    last7_lo = inc.post_end - timedelta(days=c["first_window_days"] - 1)
    last = [r for r in rows if r[0] >= last7_lo]
    age = (inc.post_end - rows[0][0]).days + 1
    if age < c["min_age_days"]:
        return Evidence("fatigue", NOT_APPLICABLE, 0.0, ["CTR"], reason=f"creative {ad_id} is {age} days old")
    fi, fc = sum(r[1] for r in first), sum(r[2] for r in first)
    li, lc = sum(r[1] for r in last), sum(r[2] for r in last)
    if len(first) < c["first_window_days"] or fi < c["first_min_impressions"] or fc < c["min_clicks"] \
            or li < c["first_min_impressions"] or lc < c["min_clicks"]:
        return Evidence("fatigue", INSUFFICIENT, 0.0, ["CTR"], reason=f"creative {ad_id}: too little delivery")
    decline = _clip01(1 - (lc / li) / (fc / fi))
    # impression-weighted OLS of continuity-corrected ln CTR on day over the last 14 days (days >= 500 impressions)
    lo = inc.post_end - timedelta(days=c["slope_days"] - 1)
    pts = [(r[0], r[1], r[2]) for r in rows if r[0] >= lo and r[1] >= c["slope_min_impressions"]]
    t_stat = None
    if len(pts) >= c["slope_min_days"]:
        x = np.array([(d - lo).days for d, _, _ in pts], dtype=float)
        y = np.log((np.array([k for _, _, k in pts]) + 0.5) / (np.array([i for _, i, _ in pts]) + 1.0))
        w = np.array([i for _, i, _ in pts], dtype=float)
        w = w / w.mean()
        xm, ym = np.average(x, weights=w), np.average(y, weights=w)
        sxx = float(np.sum(w * (x - xm) ** 2))
        slope = float(np.sum(w * (x - xm) * (y - ym)) / sxx)
        resid = y - (ym + slope * (x - xm))
        s2 = float(np.sum(w * resid ** 2) / (len(x) - 2))
        t_stat = slope / math.sqrt(s2 / sxx) if s2 > 0 else -math.inf if slope < 0 else math.inf
    spend_post = sum(r[3] for r in rows if inc.post_start <= r[0] <= inc.post_end)
    return {"ad_id": ad_id, "decline": decline, "t": t_stat, "spend_post": spend_post,
            "ctr_first7": fc / fi, "ctr_last7": lc / li, "first_window": (first[0][0], first[-1][0])}


def fatigue(db, inc: Incident) -> Evidence:
    c = evidence_config()["fatigue"]
    if len(inc.campaign_ids) != 1:
        return Evidence("fatigue", NOT_APPLICABLE, 0.0, ["CTR"], reason="fatigue is campaign/creative scoped")
    cid = inc.campaign_ids[0]
    ads = [r[0] for r in db.query("SELECT DISTINCT ad_id FROM marts.creative_daily WHERE campaign_id = ? "
                                  "AND date BETWEEN ? AND ? AND impressions > 0", [cid, inc.post_start, inc.post_end])]
    reach = {d: (r, i) for d, r, i in db.query("SELECT date, reach, impressions FROM core.campaign_reach_daily "
                                                "WHERE campaign_id = ?", [cid])}

    def freq(lo: date, hi: date) -> float | None:
        vals = [(r, i) for d, (r, i) in reach.items() if lo <= d <= hi and r]
        return sum(i for _, i in vals) / sum(r for r, _ in vals) if vals else None

    stats = []
    last_freq = freq(inc.post_end - timedelta(days=c["first_window_days"] - 1), inc.post_end)
    for ad in sorted(ads):
        probe = _creative_stats(db, ad, inc)
        if isinstance(probe, Evidence):
            continue
        lo_f, hi_f = probe["first_window"]
        f_first = freq(lo_f, hi_f)
        probe["freq_rise"] = (last_freq / f_first - 1) if f_first and last_freq else None
        stats.append(probe)
    if not stats:
        return Evidence("fatigue", INSUFFICIENT, 0.0, ["CTR"], reason="no creative with enough delivery")
    total_spend = sum(s["spend_post"] for s in stats) or 1.0
    per_creative, campaign_score = [], 0.0
    for s in stats:
        siblings = [o["decline"] for o in stats if o["ad_id"] != s["ad_id"]]
        specificity = s["decline"] - float(np.median(siblings)) if len(siblings) >= c["min_siblings"] else None
        slope_ok = s["t"] is not None and s["t"] <= c["slope_t"]
        freq_ok = s["freq_rise"] is not None and s["freq_rise"] >= c["freq_rise_gate"]
        spec_ok = specificity is None or specificity >= c["specificity_gate"]
        score = (slope_ok and freq_ok and spec_ok) * min(1.0, s["decline"] / c["full_score_decline"])
        if specificity is None:
            score = min(score, c["no_specificity_cap"])
        per_creative.append({**{k: s[k] for k in ("ad_id", "decline", "ctr_first7", "ctr_last7")},
                             "t_slope": None if s["t"] is None else round(s["t"], 2),
                             "freq_rise": None if s["freq_rise"] is None else round(s["freq_rise"], 3),
                             "specificity": None if specificity is None else round(specificity, 3),
                             "score": round(float(score), 4)})
        campaign_score += s["spend_post"] / total_spend * score
    top = max(per_creative, key=lambda p: p["score"])
    if inc.creative_id:  # a creative incident is about that creative
        own = next((p for p in per_creative if p["ad_id"] == inc.creative_id), None)
        if own is not None:
            top, campaign_score = own, own["score"]
    return Evidence("fatigue", OK, float(campaign_score), ["CTR"], {
        "top_creative": top["ad_id"], "ctr_decline_pct": round(top["decline"] * 100, 2), "creatives": per_creative,
        "frequency_source": "campaign reach (Meta)" if reach else "unavailable (Google reports no reach)"})


# ---- 3. inventory (lever CVR) ----------------------------------------------------------------------
def _group_cvr(db, cid_list: list[str], skus: set[str], lo: date, hi: date) -> tuple[float, float]:
    """(orders on the SKU group, campaign clicks x the group's attribution weight) over [lo, hi]."""
    marks = _in(cid_list)
    orders = db.query(f"SELECT sku, count(DISTINCT order_id) FROM core.order_items WHERE campaign_id IN ({marks}) "
                      f"AND analysis_date BETWEEN ? AND ? GROUP BY 1", [*cid_list, lo, hi])
    clicks = db.query(f"SELECT sum(clicks) FROM marts.campaign_daily WHERE campaign_id IN ({marks}) "
                      f"AND date BETWEEN ? AND ?", [*cid_list, lo, hi])[0][0] or 0.0
    return float(sum(n for s, n in orders if s in skus)), float(clicks)


def sku_risk(db, sku: str, as_of_day: date, horizon: int) -> dict:
    """The stage's inventory-risk predicate for one SKU (spec §22.1 #4): Stage 1 projected shortfall; Stage 2 NB2
    P(stockout) over the horizon from the seasonal-naive / champion P50 and the category's NB2 dispersion, at risk
    when P > the evidence threshold (0.5)."""
    from datetime import datetime as _dt

    from adapt.economics.inventory_risk import risk_config, stockout_probability

    base = projected_shortfall(db, sku, as_of_day, horizon)
    rc = risk_config()
    if rc["predicate"] != "STOCKOUT_PROBABILITY" or base.get("status") == INSUFFICIENT:
        return {**base, "kind": "PROJECTED_SHORTFALL", "at_risk": base.get("shortfall", 0) > 0}
    from adapt.predict.demand import dispersion_by_sku

    r = dispersion_by_sku(db, _dt.combine(as_of_day + timedelta(days=1), _dt.min.time())).get(sku, rc["r_cap"])
    p = float(stockout_probability(np.array([base["projected"]]), np.array([base["available"]]), np.array([r]))[0])
    return {**base, "kind": "STOCKOUT_PROBABILITY", "stockout_probability": round(p, 4), "dispersion": r,
            "at_risk": p > float(rc["evidence_threshold"])}


def projected_shortfall(db, sku: str, as_of_day: date, horizon: int) -> dict:
    """Stage 1 deterministic risk (spec §0.5): available = on_hand - reserved + inbound_confidence x inbound arriving
    within H; projected = seasonal-naive baseline (last 7 days of sales repeated over H); shortfall = max(projected -
    (available - safety_stock), 0)."""
    row = db.query("""SELECT on_hand, reserved, inbound_qty, expected_arrival, inbound_confidence, safety_stock
                      FROM marts.sku_daily WHERE sku = ? AND date = ?""", [sku, as_of_day])
    if not row:
        return {"status": INSUFFICIENT}
    on_hand, reserved, inbound, arrival, conf, ss = row[0]
    on_hand, reserved, conf, ss = float(on_hand), float(reserved or 0), float(conf or 0), float(ss or 0)
    inb = float(inbound or 0) if arrival is not None and arrival <= as_of_day + timedelta(days=horizon) else 0.0
    available = on_hand - reserved + conf * inb
    last7 = db.query("SELECT sum(units) FROM marts.sku_daily WHERE sku = ? AND date BETWEEN ? AND ?",
                     [sku, as_of_day - timedelta(days=6), as_of_day])[0][0] or 0
    projected = float(last7) / 7 * horizon
    shortfall = max(projected - (available - ss), 0.0)
    status = "OK" if projected <= available - ss else ("AT_RISK" if projected <= available else "SHORT")
    return {"status": status, "available": available, "projected": projected, "safety_stock": ss,
            "shortfall": shortfall, "on_hand": on_hand}


def inventory(db, inc: Incident) -> Evidence:
    c = evidence_config()["inventory"]
    marks = _in(inc.campaign_ids)
    weights = db.query(f"""SELECT sku, sum(attribution_weight) / {len(inc.campaign_ids)} FROM core.campaign_sku
                           WHERE campaign_id IN ({marks}) AND sku <> '__unmapped__' GROUP BY 1""", inc.campaign_ids)
    if not weights:
        return Evidence("inventory", NOT_APPLICABLE, 0.0, ["CVR"], reason="no mapped SKUs")
    w = dict(weights)
    stocked_out = {s for (s,) in db.query(f"""SELECT DISTINCT sku FROM marts.sku_daily WHERE on_hand = 0
                   AND date BETWEEN ? AND ? AND sku IN ({_in(list(w))})""", [inc.post_start, inc.post_end, *w])}
    risk = {s: sku_risk(db, s, inc.post_end, c["horizon_days"]) for s in w}
    exposed = {s for s in w if s in stocked_out or risk[s].get("at_risk")}
    exposed_share = sum(w[s] for s in exposed)
    if not exposed:
        return Evidence("inventory", OK, 0.0, ["CVR"], {"exposed_share": 0.0, "exposed_skus": []})
    e_pre_o, clicks_pre = _group_cvr(db, inc.campaign_ids, exposed, inc.pre_start, inc.pre_end)
    e_post_o, clicks_post = _group_cvr(db, inc.campaign_ids, exposed, inc.post_start, inc.post_end)
    others = set(w) - exposed
    n_pre_o, _ = _group_cvr(db, inc.campaign_ids, others, inc.pre_start, inc.pre_end)
    n_post_o, _ = _group_cvr(db, inc.campaign_ids, others, inc.post_start, inc.post_end)
    e_clicks_pre, n_clicks_pre = clicks_pre * exposed_share, clicks_pre * (1 - exposed_share)
    if e_clicks_pre < c["min_group_clicks"] or n_clicks_pre < c["min_group_clicks"] or not e_pre_o or not n_pre_o:
        return Evidence("inventory", INSUFFICIENT, 0.0, ["CVR"], {"exposed_share": round(exposed_share, 4)},
                        "fewer than 200 pre-window clicks or no orders in a SKU group")
    # same click split in both windows -> relative CVR drop of a group = 1 - (orders_post/clicks_post)/(orders_pre/...)
    drop_e = 1 - (e_post_o / clicks_post) / (e_pre_o / clicks_pre) if clicks_post else 0.0
    drop_n = 1 - (n_post_o / clicks_post) / (n_pre_o / clicks_pre) if clicks_post else 0.0
    gap = drop_e - drop_n
    score = (gap >= c["gap_gate"]) * min(1.0, exposed_share / c["full_score_exposure"])
    spend_by_day = dict(db.query(f"SELECT date, sum(spend) FROM marts.campaign_daily WHERE campaign_id IN ({marks}) "
                                 "AND date BETWEEN ? AND ? GROUP BY 1", [*inc.campaign_ids, inc.post_start,
                                                                         inc.post_end]))
    out_days = db.query(f"""SELECT date, sku FROM marts.sku_daily WHERE on_hand = 0 AND date BETWEEN ? AND ?
                            AND sku IN ({_in(list(w))})""", [inc.post_start, inc.post_end, *w])
    wasted = sum(spend_by_day.get(d, 0.0) * w[s] for d, s in out_days)
    return Evidence("inventory", OK, float(score), ["CVR"], {
        "exposed_skus": sorted(exposed), "stocked_out_skus": sorted(stocked_out),
        "exposed_share": round(exposed_share, 4), "cvr_drop_exposed": round(drop_e, 4),
        "cvr_drop_other": round(drop_n, 4), "gap": round(gap, 4), "wasted_spend_inr": round(wasted, 2),
        "risk_kind": next(iter({risk[s]["kind"] for s in risk if "kind" in risk[s]}), "PROJECTED_SHORTFALL"),
        "risk": {s: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in risk[s].items()}
                 for s in sorted(exposed)}})


# ---- 4. price change (levers CVR + AOV) ----------------------------------------------------------------------
def price(db, inc: Incident) -> Evidence:
    c = evidence_config()["price"]
    marks = _in(inc.campaign_ids)
    w = dict(db.query(f"""SELECT sku, sum(attribution_weight) / {len(inc.campaign_ids)} FROM core.campaign_sku
                          WHERE campaign_id IN ({marks}) AND sku <> '__unmapped__' GROUP BY 1""", inc.campaign_ids))
    if not w:
        return Evidence("price", NOT_APPLICABLE, 0.0, ["CVR", "AOV"], reason="no mapped SKUs")
    lo = inc.post_start - timedelta(days=c["lookback_days"])
    changes = {}
    for sku, valid_from, p_new in db.query(f"""SELECT sku, valid_from, price FROM core.pricing_snapshots
            WHERE sku IN ({_in(list(w))}) AND valid_from BETWEEN ? AND ? ORDER BY valid_from""",
                                           [*w, lo, inc.post_end]):
        prev = db.query("SELECT price FROM core.pricing_snapshots WHERE sku = ? AND valid_from < ? "
                        "ORDER BY valid_from DESC LIMIT 1", [sku, valid_from])
        if prev and prev[0][0] > 0 and abs(p_new / prev[0][0] - 1) >= c["min_change"]:
            changes[sku] = (prev[0][0], p_new)
    if not changes:
        return Evidence("price", OK, 0.0, ["CVR", "AOV"], {"priced_share": 0.0, "changed_skus": []})
    affected = set(changes)
    priced_share = sum(w[s] for s in affected)
    a_pre, clicks_pre = _group_cvr(db, inc.campaign_ids, affected, inc.pre_start, inc.pre_end)
    a_post, clicks_post = _group_cvr(db, inc.campaign_ids, affected, inc.post_start, inc.post_end)
    # "not affected" = every other attributed order of the campaign (unaffected mapped SKUs and cross-sell)
    all_pre = db.query(f"SELECT count(DISTINCT order_id) FROM core.order_items WHERE campaign_id IN ({marks}) "
                       "AND analysis_date BETWEEN ? AND ?", [*inc.campaign_ids, inc.pre_start, inc.pre_end])[0][0]
    all_post = db.query(f"SELECT count(DISTINCT order_id) FROM core.order_items WHERE campaign_id IN ({marks}) "
                        "AND analysis_date BETWEEN ? AND ?", [*inc.campaign_ids, inc.post_start, inc.post_end])[0][0]
    n_pre, n_post = all_pre - a_pre, all_post - a_post
    if a_pre < c["min_purchases"]:
        return Evidence("price", INSUFFICIENT, 0.0, ["CVR", "AOV"], {"priced_share": round(priced_share, 4)},
                        "fewer than 30 pre-window purchases on the repriced SKUs")
    if a_post <= 0 or not clicks_post or not clicks_pre:
        return Evidence("price", NOT_APPLICABLE, 0.0, ["CVR", "AOV"], reason="zero conversions on affected SKUs")
    dln_cvr_a = math.log((a_post / clicks_post) / (a_pre / clicks_pre))
    dln_cvr_n = math.log((n_post / clicks_post) / (n_pre / clicks_pre)) if n_pre > 0 and n_post > 0 else 0.0
    sw = sum(w[s] for s in affected)
    dln_price = sum(w[s] * math.log(changes[s][1] / changes[s][0]) for s in affected) / sw
    elasticity = dln_cvr_a / dln_price if dln_price else None
    diff = abs(dln_cvr_a - dln_cvr_n)
    gates = priced_share >= c["priced_share_gate"] and math.copysign(1, dln_cvr_a) == -math.copysign(1, dln_price)
    score = gates * min(1.0, diff / c["full_score_diff"])
    return Evidence("price", OK, float(score), ["CVR", "AOV"], {
        "changed_skus": sorted(affected), "price_change_pct": round(math.expm1(dln_price) * 100, 2),
        "priced_share": round(priced_share, 4), "elasticity": None if elasticity is None else round(elasticity, 3),
        "cvr_change_affected_pct": round(math.expm1(dln_cvr_a) * 100, 2),
        "cvr_change_other_pct": round(math.expm1(dln_cvr_n) * 100, 2)})


# ---- 5. tracking break (lever CVR, measurement) ----------------------------------------------------------------------
def tracking(db, inc: Incident) -> Evidence:
    c = evidence_config()["tracking"]
    marks = _in(inc.campaign_ids)

    def totals(lo, hi):
        return db.query(f"""SELECT sum(clicks), sum(ga_sessions), sum(attributed_orders), sum(ga_purchases)
                            FROM marts.campaign_daily WHERE campaign_id IN ({marks}) AND date BETWEEN ? AND ?""",
                        [*inc.campaign_ids, lo, hi])[0]

    cl_pre, s_pre, o_pre, g_pre = (float(v or 0) for v in totals(inc.pre_start, inc.pre_end))
    cl_post, s_post, o_post, g_post = (float(v or 0) for v in totals(inc.post_start, inc.post_end))
    days_pre, days_post = 28, (inc.post_end - inc.post_start).days + 1
    if cl_pre < c["min_clicks"] or cl_post < c["min_clicks"] or s_pre <= 0:
        return Evidence("tracking", INSUFFICIENT, 0.0, ["CVR"], reason="fewer than 300 clicks or no sessions")
    ratio_pre, ratio_post = s_pre / cl_pre, s_post / cl_post
    r_drop = 1 - ratio_post / ratio_pre
    dln_clicks = math.log((cl_post / days_post) / (cl_pre / days_pre))
    gates = abs(dln_clicks) <= math.log(1 + c["clicks_stable"]) and r_drop >= c["drop_gate"]
    score = gates * min(1.0, r_drop / c["full_score_drop"])
    orders_change = (o_post / days_post) / (o_pre / days_pre) - 1 if o_pre else None
    ga_change = (g_post / days_post) / (g_pre / days_pre) - 1 if g_pre else None
    return Evidence("tracking", OK, float(score), ["CVR"], {
        "session_click_pre": round(ratio_pre, 4), "session_click_post": round(ratio_post, 4),
        "session_click_drop_pct": round(r_drop * 100, 2), "clicks_change_pct": round(math.expm1(dln_clicks) * 100, 2),
        "orders_vs_ga_divergence": bool(orders_change is not None and ga_change is not None
                                        and abs(orders_change) < c["orders_stable"] and ga_change < -c["drop_gate"])})


# ---- 6. audience saturation (levers CTR + CPM; campaign scope) -------------------------------------------------------
def saturation(db, inc: Incident) -> Evidence:
    c = evidence_config()["saturation"]
    levers = ["CTR", "CPM"]
    if len(inc.campaign_ids) != 1:
        return Evidence("saturation", NOT_APPLICABLE, 0.0, levers, reason="saturation is campaign scoped")
    cid = inc.campaign_ids[0]

    def window(lo: date, hi: date) -> tuple[float, float, int]:
        r = db.query("SELECT sum(impressions), sum(reach), count(*) FROM core.campaign_reach_daily "
                     "WHERE campaign_id = ? AND date BETWEEN ? AND ? AND reach > 0", [cid, lo, hi])[0]
        return float(r[0] or 0), float(r[1] or 0), int(r[2] or 0)

    i_pre, r_pre, n_pre = window(inc.pre_start, inc.pre_end)
    i_post, r_post, n_post = window(inc.post_start, inc.post_end)
    if n_pre == 0 or n_post == 0:
        return Evidence("saturation", NOT_APPLICABLE, 0.0, levers,
                        reason="the platform reports no reach for this campaign (Google)")
    reach_pre, reach_post = r_pre / n_pre, r_post / n_post
    if reach_pre < c["min_daily_reach"]:
        return Evidence("saturation", INSUFFICIENT, 0.0, levers, {"daily_reach_pre": round(reach_pre, 1)},
                        f"daily reach below {c['min_daily_reach']:,}")
    freq_ratio = (i_post / r_post) / (i_pre / r_pre)
    reach_growth = reach_post / reach_pre - 1
    rows = db.query("""SELECT ad_id, sum(impressions) FILTER (WHERE date BETWEEN ? AND ?),
                              sum(clicks) FILTER (WHERE date BETWEEN ? AND ?),
                              sum(impressions) FILTER (WHERE date BETWEEN ? AND ?),
                              sum(clicks) FILTER (WHERE date BETWEEN ? AND ?)
                       FROM marts.creative_daily WHERE campaign_id = ? GROUP BY 1 ORDER BY 1""",
                    [inc.pre_start, inc.pre_end, inc.pre_start, inc.pre_end, inc.post_start, inc.post_end,
                     inc.post_start, inc.post_end, cid])
    declines = {}
    for ad, ip, cp, iq, cq in rows:
        if (ip or 0) >= c["min_creative_impressions"] and (iq or 0) >= c["min_creative_impressions"] and cp:
            declines[ad] = 1 - (float(cq or 0) / iq) / (float(cp) / ip)
    if len(declines) < c["min_creatives"]:
        return Evidence("saturation", INSUFFICIENT, 0.0, levers, {"eligible_creatives": len(declines)},
                        f"fewer than {c['min_creatives']} creatives with enough delivery in both windows")
    share_declined = sum(d >= c["creative_ctr_decline"] for d in declines.values()) / len(declines)
    gates = freq_ratio >= c["freq_ratio_gate"] and reach_growth <= c["reach_growth_max"] \
        and share_declined >= c["broad_share"]
    score = gates * min(1.0, (freq_ratio - 1) / c["full_score_freq_rise"])
    return Evidence("saturation", OK, float(score), levers, {
        "frequency_pre": round(i_pre / r_pre, 3), "frequency_post": round(i_post / r_post, 3),
        "frequency_change_pct": round((freq_ratio - 1) * 100, 2), "reach_change_pct": round(reach_growth * 100, 2),
        "share_creatives_ctr_down": round(share_declined, 4),
        "creative_ctr_decline": {k: round(v, 4) for k, v in declines.items()}})


# ---- 7. demand shift (levers CVR + volume; evidence only, never a causal control) ------------------------------------
def demand(db, inc: Incident) -> Evidence:
    from adapt.detect.stats import stl_forecast

    c = evidence_config()["demand"]
    levers = ["CVR"]
    cats = [r[0] for r in db.query(f"SELECT DISTINCT product_set FROM core.campaigns WHERE campaign_id IN "
                                   f"({_in(inc.campaign_ids)}) AND product_set IS NOT NULL", inc.campaign_ids)]
    if not cats:
        return Evidence("demand", NOT_APPLICABLE, 0.0, levers, reason="no product set (category) for the campaigns")
    fit_lo = inc.post_start - timedelta(days=c["fit_days"])
    rows = dict(db.query(f"""SELECT oi.analysis_date, count(DISTINCT oi.order_id) FROM core.order_items oi
                             JOIN core.skus k USING (sku)
                             WHERE oi.channel_id IN ('email', 'organic') AND k.category IN ({_in(cats)})
                               AND oi.analysis_date BETWEEN ? AND ? GROUP BY 1""", [*cats, fit_lo, inc.post_end]))
    days = [fit_lo + timedelta(days=k) for k in range((inc.post_end - fit_lo).days + 1)]
    series = np.array([float(rows.get(d, 0)) for d in days])
    n_post = (inc.post_end - inc.post_start).days + 1
    fit, post = series[:-n_post], series[-n_post:]
    pre_orders = float(fit[-28:].sum())
    if pre_orders < c["min_pre_orders"]:
        return Evidence("demand", INSUFFICIENT, 0.0, levers, {"unpaid_orders_pre": pre_orders},
                        f"fewer than {c['min_pre_orders']} unpaid orders in the pre window")
    expected = float(np.expm1(stl_forecast(np.log1p(fit), n_post).yhat).clip(min=0).sum())
    actual = float(post.sum())
    if expected <= 0 or actual <= 0:
        return Evidence("demand", INSUFFICIENT, 0.0, levers, reason="no unpaid orders expected or observed")
    d = math.log(actual / expected)
    score = (abs(d) >= math.log(1 + c["min_shift"])) * min(1.0, abs(d) / math.log(1 + c["full_score_shift"]))
    return Evidence("demand", OK, float(score), levers, {
        "categories": sorted(cats), "unpaid_orders_post": actual, "expected_unpaid_orders_post": round(expected, 2),
        "demand_change_pct": round(math.expm1(d) * 100, 2), "d": round(d, 6), "direction": "UP" if d > 0 else "DOWN",
        "basis": "unpaid (email + organic) orders of the category vs the out-of-sample STL expectation"})


MODULES = {"auction": auction, "fatigue": fatigue, "inventory": inventory, "price": price, "tracking": tracking,
           "saturation": saturation, "demand": demand}
DRIVER_LABEL = {"auction": "Auction pressure (platform-wide CPM)", "fatigue": "Creative fatigue",
                "inventory": "Inventory constraint", "price": "Price change", "tracking": "Tracking break",
                "saturation": "Audience saturation", "demand": "Demand shift (unpaid demand, same category)"}
