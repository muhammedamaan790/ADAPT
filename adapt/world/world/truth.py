"""Per-seed hidden truth (spec §10): drawn from priors, written once to sim_truth.duckdb, never visible to ADAPT.

Calibration chain (docs/contracts/world_truth.md):
  backbone daily units per category  -> demand index (28-day trend x weekday factor)
  traffic-source purchase shares     -> expected paid purchases per campaign-day and unpaid orders per day
  Global Ads pools (bootstrap)       -> per-campaign base CTR / CVR / CPM
  response model (below)             -> historical monthly budgets that hit the expected paid purchases

Response model for one campaign-day at spend s (also used by world.step and eval-side truth economics):
  CPM(s)  = base_cpm x (1 + s / s_ref)^eta                  auction pressure
  I(s)    = 1000 s / CPM(s)                                 impressions
  freq(I) = I / (A (1 - exp(-I / A)))                       daily frequency over audience A
  CTR     = base_ctr x freq^-gamma                          saturation
  E[purchases] = I x CTR x csr x min(1, p_buy x demand^rho)
It is strictly increasing and concave in s, so a spend that hits any target is found by bisection.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from world.catalog import PRIOR_CHANNEL_OF, Catalog, build_catalog
from world.priors import RatePool, benchmarks, load_priors
from world.rng import generator

TRUTH_SCHEMA_VERSION = 1
FB_CHANNELS = ("meta_prospecting", "meta_retargeting")


@dataclass(frozen=True)
class WorldConfig:
    seed: int
    backbone_dir: Path
    global_ads_csv: Path | None = None
    brand_scale: float = 10.0
    history_end_date: date = date(2026, 9, 30)

    def world_date(self, day: int) -> date:
        """World day -1 is history_end_date; day 0 is the first live day."""
        return self.history_end_date + timedelta(days=day + 1)


@dataclass(frozen=True)
class CampaignTruth:
    campaign_id: str
    channel: str
    category_code: str
    budget_id: str
    base_ctr: float
    base_cvr: float
    click_session_rate: float
    p_buy: float
    base_cpm_inr: float
    ctr_freq_gamma: float
    cpm_eta: float
    s_ref: float
    audience_size: float
    target_freq: float
    cross_sell_share: float
    budget_share: float  # share of its budget's spend this campaign receives (1.0 unless the budget is shared)
    prior_row: int
    prior_source: str


@dataclass(frozen=True)
class CreativeTruth:
    creative_id: str
    campaign_id: str
    ctr_mult: float
    fatigue_half_life_impressions: float


@dataclass
class Truth:
    config: WorldConfig
    catalog: Catalog
    campaigns: dict[str, CampaignTruth]
    creatives: dict[str, CreativeTruth]
    elasticity: dict[str, float]
    demand: pd.DataFrame  # category_code, day, trend, weekday_factor, index (history days)
    weekday_factor: dict[str, list[float]]  # category -> factor by world weekday (Mon=0)
    future_level: dict[str, float]  # category -> trend level carried forward after history
    category_rates: pd.DataFrame  # category_code, units_mean, paid/unpaid expected orders per day at index 1
    warehouse: dict[str, float]  # unmapped store demand (non-promoted categories)
    history_budgets: pd.DataFrame  # budget_id, from_day, to_day, amount_inr
    source_shares: dict[str, float]
    meta: dict = field(default_factory=dict)

    def demand_index(self, category_code: str, day: int) -> float:
        if day < 0:
            row = self.demand[(self.demand.category_code == category_code) & (self.demand.day == day)]
            return float(row["index"].iloc[0])
        wd = self.config.world_date(day).weekday()
        return self.future_level[category_code] * self.weekday_factor[category_code][wd]


# ---- response model --------------------------------------------------------------------------------------
def cpm_at(spend: float, t: CampaignTruth, cpm_mult: float = 1.0) -> float:
    return t.base_cpm_inr * cpm_mult * (1.0 + spend / t.s_ref) ** t.cpm_eta


def impressions_at(spend: float, t: CampaignTruth, cpm_mult: float = 1.0) -> float:
    return 1000.0 * spend / cpm_at(spend, t, cpm_mult) if spend > 0 else 0.0


def frequency(impressions: float, audience: float) -> float:
    if impressions <= 0:
        return 1.0
    x = impressions / audience
    return x / -math.expm1(-x)  # I / (A (1 - e^{-I/A})), numerically stable


def expected_purchases(spend: float, t: CampaignTruth, demand: float = 1.0, ctr_mult: float = 1.0,
                       cpm_mult: float = 1.0) -> float:
    imps = impressions_at(spend, t, cpm_mult)
    if imps <= 0:
        return 0.0
    ctr = min(1.0, t.base_ctr * ctr_mult * frequency(imps, t.audience_size) ** -t.ctr_freq_gamma)
    p_buy = min(1.0, t.p_buy * demand ** benchmarks()["demand_on_conversion"])
    return imps * ctr * t.click_session_rate * p_buy


def spend_for_purchases(target: float, t: CampaignTruth, demand: float = 1.0) -> float:
    if target <= 0:
        return 0.0
    hi = max(t.s_ref, 1.0)
    for _ in range(80):
        if expected_purchases(hi, t, demand) >= target:
            break
        hi *= 2.0
    else:
        raise ValueError(f"{t.campaign_id}: target {target} unreachable")
    lo = 0.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if expected_purchases(mid, t, demand) < target:
            lo = mid
        else:
            hi = mid
    return hi


def _solve_audience(impressions: float, target_freq: float) -> float:
    lo, hi = math.log(impressions / 1e4), math.log(impressions * 1e4)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if frequency(impressions, math.exp(mid)) > target_freq:  # too concentrated -> bigger audience
            lo = mid
        else:
            hi = mid
    return math.exp(hi)


# ---- backbone-derived demand ---------------------------------------------------------------------------
def _backbone_frames(backbone_dir: Path) -> tuple[pd.DataFrame, dict[str, float], dict[str, float], dict]:
    q = lambda name: (backbone_dir / f"{name}.parquet").as_posix()  # noqa: E731
    con = duckdb.connect()
    manifest = json.loads((backbone_dir / "manifest.json").read_text(encoding="utf-8"))
    start = date.fromisoformat(manifest["window"]["start"])
    end = date.fromisoformat(manifest["window"]["end"])
    daily = con.execute(f"""
        SELECT c.category_code, i.analysis_date AS d, count(*) AS units
        FROM '{q('order_items')}' i
        JOIN '{q('product_sku')}' ps USING (product_id)
        JOIN '{q('categories')}' c ON c.department = ps.department AND c.category = ps.category
        WHERE i.status <> 'Cancelled'
        GROUP BY 1, 2
    """).df()
    shares_rows = con.execute(f"""
        SELECT traffic_source, count(*) FILTER (WHERE purchased) AS purchases FROM '{q('sessions')}' GROUP BY 1
    """).fetchall()
    total = sum(p for _, p in shares_rows) or 1
    shares = {src: p / total for src, p in shares_rows}
    wh = con.execute(f"""
        SELECT count(*) AS units, avg(sale_price) AS price, avg(cost) AS cogs,
               avg(CASE WHEN returned_at IS NOT NULL THEN 1.0 ELSE 0.0 END) AS return_rate
        FROM '{q('order_items')}' WHERE status <> 'Cancelled' AND sku IS NULL
    """).fetchone()
    con.close()
    n_days = (end - start).days + 1
    warehouse = {"units_mean": wh[0] / n_days, "price_usd": wh[1] or 0.0, "cogs_usd": wh[2] or 0.0,
                 "return_rate": wh[3] or 0.0}
    days = pd.date_range(start, end, freq="D").date
    daily["d"] = pd.to_datetime(daily["d"]).dt.date
    return daily, shares, warehouse, {"start": start, "end": end, "days": list(days)}


def _demand_index(daily: pd.DataFrame, window: dict, cfg: WorldConfig, codes: list[str]):
    days = window["days"]
    n = len(days)
    rows, wf_out, level_out, units_mean = [], {}, {}, {}
    for code in codes:
        s = (daily[daily.category_code == code].set_index("d")["units"]
             .reindex(days, fill_value=0).astype(float))
        mean = s.mean() if s.mean() > 0 else 1.0
        units_mean[code] = float(mean)
        # floor: a category that sells nothing for weeks keeps a small positive demand (never an unsolvable zero)
        trend = (s.rolling(28, center=True, min_periods=7).mean() / mean).clip(lower=0.05)
        ratio = (s / mean) / trend.replace(0, np.nan)
        bb_wd = pd.Series([d.weekday() for d in days], index=s.index)
        wf = ratio.groupby(bb_wd).mean().reindex(range(7)).fillna(1.0)
        wf = wf / wf.mean()
        wf_out[code] = [float(x) for x in wf]
        for i in range(n):
            day = i - n  # history days -n .. -1
            wd = cfg.world_date(day).weekday()
            idx = float(trend.iloc[i]) * wf_out[code][wd]
            rows.append((code, day, float(trend.iloc[i]), wf_out[code][wd], idx))
        level_out[code] = float(trend.iloc[-28:].mean())
    demand = pd.DataFrame(rows, columns=["category_code", "day", "trend", "weekday_factor", "index"])
    return demand, wf_out, level_out, units_mean


# ---- truth draws ------------------------------------------------------------------------------------------
def _uniform(rng: np.random.Generator, bounds) -> float:
    lo, hi = bounds
    return float(rng.uniform(lo, hi))


def _click_session_rate(rng: np.random.Generator) -> float:
    cs = benchmarks()["click_session"]
    for _ in range(10_000):
        x = float(rng.beta(cs["a"], cs["b"]))
        if cs["lo"] <= x <= cs["hi"]:
            return x
    raise RuntimeError("click->session truncation failed")


def _draw_campaign(rng: np.random.Generator, c, pool: RatePool, bench: dict, target_ref: float,
                   demand_ref: float) -> CampaignTruth:
    rt = bench["retargeting"]
    j = int(rng.integers(len(pool)))
    ctr, cvr, cpm = float(pool.ctr[j]), float(pool.cvr[j]), float(pool.cpm_usd[j]) * bench["fx_usd_inr"]
    if c.channel == "meta_retargeting":
        ctr, cvr, cpm = ctr * rt["ctr_mult"], min(cvr * rt["cvr_mult"], 0.5), cpm * rt["cpm_mult"]
    csr = _click_session_rate(rng)
    sat = bench["saturation"]
    gamma, eta = _uniform(rng, sat["ctr_freq_gamma"]), _uniform(rng, sat["cpm_eta"])
    target_freq, cross = _uniform(rng, sat["target_freq"]), _uniform(rng, sat["cross_sell_share"])
    p_buy = min(1.0, cvr / csr)
    # Reference point: the unsaturated spend that would hit the reference target; audience set so the daily
    # frequency there equals target_freq.
    q = ctr * csr * min(1.0, p_buy * demand_ref ** bench["demand_on_conversion"])
    imps0 = target_ref / q
    return CampaignTruth(
        campaign_id=c.campaign_id, channel=c.channel, category_code=c.category_code, budget_id=c.budget_id,
        base_ctr=ctr, base_cvr=cvr, click_session_rate=csr, p_buy=p_buy, base_cpm_inr=cpm,
        ctr_freq_gamma=gamma, cpm_eta=eta, s_ref=imps0 * cpm / 1000.0,
        audience_size=_solve_audience(imps0, target_freq), target_freq=target_freq, cross_sell_share=cross,
        budget_share=1.0, prior_row=j, prior_source=pool.source,
    )


CHANNEL_SOURCE = {"google_search": "Adwords", "google_video": "YouTube", "meta_prospecting": "Facebook",
                  "meta_retargeting": "Facebook"}


def build_truth(cfg: WorldConfig, catalog: Catalog | None = None, pools: dict[str, RatePool] | None = None) -> Truth:
    bench = benchmarks()
    catalog = catalog or build_catalog(cfg.backbone_dir)
    pools = pools or load_priors(cfg.global_ads_csv)
    daily, shares, warehouse, window = _backbone_frames(Path(cfg.backbone_dir))
    codes = [c.code for c in catalog.categories]
    demand, wf, level, units_mean = _demand_index(daily, window, cfg, codes)
    n_hist = len(window["days"])
    rt = bench["retargeting"]

    def channel_share(channel: str) -> float:
        base = shares.get(CHANNEL_SOURCE[channel], 0.0)
        if channel == "meta_prospecting":
            return base * (1 - rt["purchase_share"])
        if channel == "meta_retargeting":
            return base * rt["purchase_share"]
        return base

    k = cfg.brand_scale
    rate_rows = []
    for code in codes:
        row = {"category_code": code, "units_mean": units_mean[code]}
        for ch in ("google_search", "google_video", "meta_prospecting", "meta_retargeting"):
            row[f"paid_{ch}"] = k * units_mean[code] * channel_share(ch)
        row["unpaid_email"] = k * units_mean[code] * shares.get("Email", 0.0)
        row["unpaid_organic"] = k * units_mean[code] * shares.get("Organic", 0.0)
        rate_rows.append(row)
    category_rates = pd.DataFrame(rate_rows)
    rates = category_rates.set_index("category_code")

    idx_by_cat = {code: demand[demand.category_code == code].set_index("day")["index"] for code in codes}
    ref_days = list(range(-28, 0))

    # Campaign truth: rates from the prior pool, reference spend/audience from the last 28 history days.
    # Survivorship: redraw until the reference ROAS is plausible for a campaign the brand keeps running.
    roas_lo, roas_hi = bench["reference_roas_band"]
    campaigns: dict[str, CampaignTruth] = {}
    for c in catalog.campaigns:
        rng = generator(cfg.seed, 0, c.campaign_id, "truth:campaign")
        pool = pools[PRIOR_CHANNEL_OF[c.channel]]
        demand_ref = float(idx_by_cat[c.category_code].loc[ref_days].mean())
        target_ref = rates.loc[c.category_code, f"paid_{c.channel}"] * demand_ref
        if target_ref <= 0:
            raise ValueError(f"no paid demand for {c.channel} ({CHANNEL_SOURCE[c.channel]}) in the backbone sessions")
        price = sum(s.unit_price_inr * s.mix_weight for s in catalog.skus_of(c.category_code))
        best: tuple[float, CampaignTruth] | None = None
        for _ in range(500):
            t = _draw_campaign(rng, c, pool, bench, target_ref, demand_ref)
            roas = target_ref * price / spend_for_purchases(target_ref, t, demand_ref)
            miss = max(roas_lo - roas, roas - roas_hi, 0.0)
            if best is None or miss < best[0]:
                best = (miss, t)
            if miss == 0.0:
                break
        campaigns[c.campaign_id] = best[1]

    # Historical budgets: set on the 1st of each world month so expected purchases match the month's target.
    months: dict[tuple[int, int], list[int]] = {}
    for day in range(-n_hist, 0):
        d = cfg.world_date(day)
        months.setdefault((d.year, d.month), []).append(day)
    spend_needed: dict[tuple[str, int], float] = {}
    for cid, t in campaigns.items():
        idx = idx_by_cat[t.category_code]
        base_target = rates.loc[t.category_code, f"paid_{t.channel}"]
        for days in months.values():
            dem = float(idx.loc[days].mean())
            spend_needed[(cid, days[0])] = spend_for_purchases(base_target * dem, t, dem)

    budget_rows = []
    budgets = catalog.budgets()
    for budget_id, members in budgets.items():
        if len(members) > 1:  # shared budget: split observed at the reference month, held fixed
            last_month_start = list(months.values())[-1][0]
            need = {m: spend_needed[(m, last_month_start)] for m in members}
            total_need = sum(need.values()) or 1.0
            for m in members:
                campaigns[m] = CampaignTruth(**{**asdict(campaigns[m]), "budget_share": need[m] / total_need})
        for days in months.values():
            amount = sum(spend_needed[(m, days[0])] for m in members)
            budget_rows.append((budget_id, days[0], days[-1], float(round(amount / 100.0) * 100.0)))
    history_budgets = pd.DataFrame(budget_rows, columns=["budget_id", "from_day", "to_day", "amount_inr"])

    creatives: dict[str, CreativeTruth] = {}
    sigma = bench["creative_ctr_sigma"]
    for c in catalog.campaigns:
        crs = [cr for cr in catalog.creatives if cr.campaign_id == c.campaign_id]
        raw = {cr.creative_id: float(generator(cfg.seed, 0, cr.creative_id, "truth:creative").lognormal(0, sigma))
               for cr in crs}
        norm = sum(raw.values()) / len(raw)
        imps_ref = impressions_at(campaigns[c.campaign_id].s_ref, campaigns[c.campaign_id])
        for cr in crs:
            rng = generator(cfg.seed, 0, cr.creative_id, "truth:fatigue")
            half_life_days = _uniform(rng, bench["fatigue_half_life_days"])
            creatives[cr.creative_id] = CreativeTruth(
                cr.creative_id, c.campaign_id, raw[cr.creative_id] / norm, half_life_days * imps_ref / len(crs))

    elasticity = {code: _uniform(generator(cfg.seed, 0, code, "truth:elasticity"), bench["price_elasticity"])
                  for code in codes}
    warehouse = {**warehouse, "units_mean": warehouse["units_mean"] * k,
                 "price_inr": warehouse["price_usd"] * bench["fx_usd_inr"],
                 "cogs_inr": warehouse["cogs_usd"] * bench["fx_usd_inr"]}

    return Truth(
        config=cfg, catalog=catalog, campaigns=campaigns, creatives=creatives, elasticity=elasticity,
        demand=demand, weekday_factor=wf, future_level=level, category_rates=category_rates, warehouse=warehouse,
        history_budgets=history_budgets, source_shares=shares,
        meta={"schema_version": TRUTH_SCHEMA_VERSION, "seed": cfg.seed, "brand_scale": k,
              "history_end_date": cfg.history_end_date.isoformat(), "history_days": n_hist,
              "backbone_window": {"start": window["start"].isoformat(), "end": window["end"].isoformat()},
              "benchmarks": bench},
    )


# ---- persistence (immutable after seeding) ---------------------------------------------------------------
GT_INCIDENTS_DDL = """
CREATE TABLE gt_incidents (
    incident_id VARCHAR PRIMARY KEY, scenario VARCHAR, driver VARCHAR, entity_ids JSON, metric_set JSON,
    direction JSON, injection_start_day INTEGER, onset_day INTEGER, injection_end_day INTEGER, magnitude DOUBLE
)"""


def write_truth(truth: Truth, path: str | Path, overwrite: bool = False) -> None:
    path = Path(path)
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"{path} exists; sim_truth is immutable after seeding (overwrite=True reseeds)")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        frames = {
            "campaign_truth": pd.DataFrame([asdict(t) for t in truth.campaigns.values()]),
            "creative_truth": pd.DataFrame([asdict(t) for t in truth.creatives.values()]),
            "category_truth": truth.category_rates.assign(
                elasticity=truth.category_rates.category_code.map(truth.elasticity),
                future_level=truth.category_rates.category_code.map(truth.future_level),
                weekday_factors=truth.category_rates.category_code.map(lambda c: json.dumps(truth.weekday_factor[c])),
            ),
            "demand_index": truth.demand,
            "history_budgets": truth.history_budgets,
            "sku_truth": pd.DataFrame([asdict(s) for s in truth.catalog.skus]),
        }
        for name, df in frames.items():
            con.register("frame", df)
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM frame")
            con.unregister("frame")
        con.execute("CREATE TABLE meta (key VARCHAR PRIMARY KEY, value JSON)")
        meta = {**truth.meta, "source_shares": truth.source_shares, "warehouse": truth.warehouse}
        con.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v, default=str)) for k, v in meta.items()])
        con.execute(GT_INCIDENTS_DDL)
    finally:
        con.close()


def open_truth(path: str | Path) -> duckdb.DuckDBPyConnection:
    """Truth is only ever opened read-only after seeding."""
    return duckdb.connect(str(path), read_only=True)
