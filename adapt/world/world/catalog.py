"""The brand's ad-account structure, built deterministically from the backbone (spec §1).

Per promoted category: Google Search, Google Video (YouTube), Meta prospecting, Meta retargeting -> 48 campaigns.
The two lowest-ranked categories' Google Video campaigns share one budget (exercises shared-budget semantics).
Structure does not depend on the seed, so every seed and every strategy fork uses the same entity IDs.
IDs are numeric strings like the real platforms'; human-readable names carry the meaning.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import duckdb

from world.priors import benchmarks

CHANNELS = ("google_search", "google_video", "meta_prospecting", "meta_retargeting")
EXTRA_CHANNELS = ("tiktok", "amazon_sp")  # Stage 2 SIMULATED channels, opt-in per world (WorldConfig.extra_channels)
PLATFORM_OF = {"google_search": "google", "google_video": "google", "meta_prospecting": "meta",
               "meta_retargeting": "meta", "tiktok": "tiktok", "amazon_sp": "amazon"}
PRIOR_CHANNEL_OF = {"google_search": "google_search", "google_video": "google_video",
                    "meta_prospecting": "meta", "meta_retargeting": "meta", "tiktok": "tiktok",
                    "amazon_sp": "amazon_sp"}
CHANNEL_LABEL = {"google_search": "GOOGLE Search", "google_video": "GOOGLE Video",
                 "meta_prospecting": "META Prospecting", "meta_retargeting": "META Retargeting",
                 "tiktok": "TIKTOK Prospecting", "amazon_sp": "AMAZON Sponsored"}
ADSETS = {
    "google_search": ("generic", "product"),
    "google_video": ("in-market", "affinity"),
    "meta_prospecting": ("broad", "interest", "lookalike"),
    "meta_retargeting": ("cart abandoners", "product viewers"),
    "tiktok": ("broad", "interest"),
    "amazon_sp": ("auto", "manual"),
}
FORMATS = {"google_search": ("text",), "google_video": ("video",),
           "meta_prospecting": ("image", "video", "carousel"), "meta_retargeting": ("carousel", "image"),
           "tiktok": ("video",), "amazon_sp": ("sponsored_product",)}
HOOKS = ("discount", "new arrival", "social proof", "benefit", "urgency")
CTAS = ("Shop Now", "Buy Now", "Learn More")


def stable_int(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)


@dataclass(frozen=True)
class Category:
    code: str
    rank: int
    department: str
    name: str

    @property
    def label(self) -> str:
        return f"{self.department}·{self.name}"


@dataclass(frozen=True)
class Sku:
    sku: str
    category_code: str
    price_band: int
    unit_price_inr: float
    unit_cogs_inr: float
    ship_cost_inr: float
    fee_pct: float
    return_rate: float
    mix_weight: float  # share of the category's units in the backbone window (sums to 1 per category)


@dataclass(frozen=True)
class Campaign:
    campaign_id: str
    platform: str
    channel: str
    category_code: str
    budget_id: str
    name: str


@dataclass(frozen=True)
class AdSet:
    adset_id: str
    campaign_id: str
    audience: str


@dataclass(frozen=True)
class Creative:
    creative_id: str
    adset_id: str
    campaign_id: str
    format: str
    hook: str
    cta: str
    headline: str
    launch_day: int  # world day (history days are negative)


@dataclass(frozen=True)
class Catalog:
    categories: tuple[Category, ...]
    skus: tuple[Sku, ...]
    campaigns: tuple[Campaign, ...]
    adsets: tuple[AdSet, ...]
    creatives: tuple[Creative, ...]

    def campaign(self, campaign_id: str) -> Campaign:
        return next(c for c in self.campaigns if c.campaign_id == campaign_id)

    def skus_of(self, category_code: str) -> tuple[Sku, ...]:
        return tuple(s for s in self.skus if s.category_code == category_code)

    def budgets(self) -> dict[str, tuple[str, ...]]:
        """budget_id -> campaign_ids (a shared budget lists several)."""
        out: dict[str, list[str]] = {}
        for c in self.campaigns:
            out.setdefault(c.budget_id, []).append(c.campaign_id)
        return {b: tuple(ids) for b, ids in out.items()}


def build_catalog(backbone_dir: str | Path, history_days: int = 365, extra_channels: tuple[str, ...] = ()) -> Catalog:
    bench = benchmarks()
    fx = bench["fx_usd_inr"]
    costs = bench["unit_costs"]
    bdir = Path(backbone_dir)
    con = duckdb.connect()
    cat_rows = con.execute(
        f"SELECT category_code, rank, department, category FROM '{(bdir / 'categories.parquet').as_posix()}' "
        "ORDER BY rank"
    ).fetchall()
    categories = tuple(Category(code, int(rank), dept, name) for code, rank, dept, name in cat_rows)
    sku_rows = con.execute(f"""
        SELECT s.sku, c.category_code, s.price_band, s.unit_price, s.unit_cogs, s.return_rate, s.units_window,
               avg(s.return_rate) OVER (PARTITION BY c.category_code) AS cat_return_rate,
               sum(s.units_window) OVER (PARTITION BY c.category_code) AS cat_units,
               count(*) OVER (PARTITION BY c.category_code) AS n_bands
        FROM '{(bdir / 'skus.parquet').as_posix()}' s
        JOIN '{(bdir / 'categories.parquet').as_posix()}' c USING (department, category)
        ORDER BY s.sku
    """).fetchall()
    con.close()
    skus = []
    for sku, code, band, price, cogs, ret, units, cat_ret, cat_units, n_bands in sku_rows:
        price_inr = round(float(price) * fx, 2)
        return_rate = float(ret) if ret is not None else float(cat_ret or 0.0)
        weight = float(units) / float(cat_units) if cat_units else 1.0 / n_bands
        skus.append(Sku(
            sku=sku, category_code=code, price_band=int(band), unit_price_inr=price_inr,
            unit_cogs_inr=round(float(cogs) * fx, 2),
            ship_cost_inr=round(max(costs["ship_cost_min_inr"], costs["ship_cost_pct"] * price_inr), 2),
            fee_pct=costs["payment_fee_pct"], return_rate=return_rate, mix_weight=weight,
        ))

    shared_video = {c.code for c in categories[-2:]} if len(categories) >= 2 else set()
    shared_budget_id = str(30_000_000_000 + 999)
    campaigns, adsets, creatives = [], [], []
    n = 0
    for cat in categories:
        for channel in CHANNELS:
            n += 1
            platform = PLATFORM_OF[channel]
            if platform == "google":
                cid = str(20_000_000_000 + n)
                budget_id = shared_budget_id if (channel == "google_video" and cat.code in shared_video) else str(
                    30_000_000_000 + n)
            else:
                cid = str(238_000_000_000 + n)
                budget_id = cid  # Meta campaign budget optimisation: the budget lives on the campaign
            campaigns.append(Campaign(cid, platform, channel, cat.code, budget_id,
                                      f"{CHANNEL_LABEL[channel]} | {cat.label}"))
            for a_i, audience in enumerate(ADSETS[channel], start=1):
                asid = f"{cid}{a_i:02d}"
                adsets.append(AdSet(asid, cid, audience))
                n_creatives = 2 + stable_int(asid) % 3  # 2..4
                for c_i in range(1, n_creatives + 1):
                    crid = f"{asid}{c_i:02d}"
                    h = stable_int(crid)
                    hook = HOOKS[h % len(HOOKS)]
                    creatives.append(Creative(
                        creative_id=crid, adset_id=asid, campaign_id=cid,
                        format=FORMATS[channel][h % len(FORMATS[channel])], hook=hook, cta=CTAS[(h // 7) % len(CTAS)],
                        headline=f"{cat.label}: {hook}",
                        # the first creative of every ad set runs from the start of history; later ones rotate in
                        launch_day=-history_days if c_i == 1 else -(30 + (h // 11) % (history_days - 30)),
                    ))
    # Stage 2 SIMULATED channels, numbered after the base catalog so every base id is unchanged. TikTok and Amazon
    # Sponsored Products both keep the budget on the campaign; an Amazon "ad" is an advertised product.
    m = 0
    for channel in (c for c in EXTRA_CHANNELS if c in extra_channels):
        for cat in categories:
            m += 1
            cid = str(1_700_000_000_000_000_000 + m) if channel == "tiktok" else str(400_000_000_000 + m)
            campaigns.append(Campaign(cid, PLATFORM_OF[channel], channel, cat.code, cid,
                                      f"{CHANNEL_LABEL[channel]} | {cat.label}"))
            for a_i, audience in enumerate(ADSETS[channel], start=1):
                asid = f"{cid}{a_i:02d}"
                adsets.append(AdSet(asid, cid, audience))
                n_creatives = 2 if channel == "tiktok" else 1
                for c_i in range(1, n_creatives + 1):
                    crid = f"{asid}{c_i:02d}"
                    h = stable_int(crid)
                    hook = HOOKS[h % len(HOOKS)]
                    creatives.append(Creative(creative_id=crid, adset_id=asid, campaign_id=cid,
                                              format=FORMATS[channel][0], hook=hook, cta=CTAS[(h // 7) % len(CTAS)],
                                              headline=f"{cat.label}: {hook}", launch_day=-history_days))
    return Catalog(categories, tuple(skus), tuple(campaigns), tuple(adsets), tuple(creatives))
