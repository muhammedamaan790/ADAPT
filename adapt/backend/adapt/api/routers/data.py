"""Data Hub read API (C6, early): source health, mapping coverage, reconciliation (spec §12 Data rows).

Shapes match the frontend's zod contracts (`adapt/web/src/api/contracts.ts` sourceSchema,
`insight-contracts.ts` dataHealthSchema / reconciliationSchema). Everything is read from the canonical state built
by A3 (ops.data_health, core.campaign_sku, marts.recon_daily); nothing is recomputed per request.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from adapt.ingest.connectors.base import sources_config

router = APIRouter(prefix="/api/v1/data", tags=["data"])
Provenance = Literal["PUBLIC-SAMPLE", "CALIBRATED", "SIMULATED", "LIVE"]
SOURCE_META = {
    "meta_ads": ("Meta Ads", "ads"), "google_ads": ("Google Ads", "ads"), "store": ("Store orders", "commerce"),
    "finance": ("SKU economics", "finance"), "ga4": ("GA4", "analytics"), "erp": ("ERP inventory", "inventory"),
    "tiktok_ads": ("TikTok Ads", "ads"), "amazon_ads": ("Amazon Ads", "ads"),        # Stage 2 simulated channels
    "amazon_marketplace": ("Amazon marketplace orders", "commerce"),
}


def source_meta(src: str) -> tuple[str, str]:
    """(display name, kind) of a connector; a source added later shows its id rather than failing the page."""
    return SOURCE_META.get(src, (src.replace("_", " ").title(), "other"))
MIX_UNCERTAIN_SHARE = 0.20  # spec §8.1 coverage rule
WINDOW_DAYS = 28


class SourceOut(BaseModel):
    id: str
    name: str
    kind: str
    score: float = Field(ge=0, le=100)
    status: Literal["GREEN", "YELLOW", "RED"]
    freshness: str
    provenance: Provenance


class CheckOut(BaseModel):
    id: str
    passed: bool
    detail: str


class SourceHealthOut(SourceOut):
    as_of: str
    newest_date: str | None
    age_hours: float | None
    freshness_score: float
    completeness: float
    consistency: float
    hard_failures: list[str]
    checks: list[CheckOut]


class MappingCoverageOut(BaseModel):
    coverage: float = Field(ge=0, le=1)
    unmapped: list[str]
    note: str


class PlatformRecon(BaseModel):
    platform: str
    platform_conversions: float
    store_attributed_orders: int
    over_attribution: float | None
    over_attribution_reason: str | None = None
    session_click_ratio: float | None


class ReconciliationOut(BaseModel):
    platform_revenue: float
    store_revenue: float
    attribution_excess: float
    note: str
    window_start: str
    window_end: str
    platforms: list[PlatformRecon]


def _db(request: Request):
    return request.app.state.runtime.db  # the runtime swaps the connection on /sim/reset


def _require(db, table: str) -> None:
    schema, name = table.split(".")
    found = db.query("SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                     [schema, name])[0][0]
    if not found:
        raise HTTPException(503, detail="canonical state not built yet: run the connector sync and the A3 build")


def _latest_health(db) -> list[tuple]:
    _require(db, "ops.data_health")
    return db.query("""SELECT as_of, source, newest_date, age_hours, freshness, completeness, consistency, score,
                              status, hard_failures, checks
                       FROM ops.data_health WHERE as_of = (SELECT max(as_of) FROM ops.data_health) ORDER BY source""")


def _freshness_text(newest, age) -> str:
    if newest is None:
        return "no data received"
    return f"latest complete day {newest.isoformat()}, {age:.0f} h old" if age is not None else newest.isoformat()


@router.get("/sources", response_model=list[SourceOut])
def sources(request: Request) -> list[SourceOut]:
    cfg = sources_config()["sources"]
    return [SourceOut(id=src, name=source_meta(src)[0], kind=source_meta(src)[1], score=score, status=status,
                      freshness=_freshness_text(newest, age), provenance=cfg[src]["provenance"])
            for _, src, newest, age, _, _, _, score, status, _, _ in _latest_health(_db(request))]


@router.get("/health", response_model=list[SourceHealthOut])
def health(request: Request) -> list[SourceHealthOut]:
    cfg = sources_config()["sources"]
    out = []
    for as_of, src, newest, age, fr, comp, cons, score, status, hard, checks in _latest_health(_db(request)):
        out.append(SourceHealthOut(
            id=src, name=source_meta(src)[0], kind=source_meta(src)[1], score=score, status=status,
            freshness=_freshness_text(newest, age), provenance=cfg[src]["provenance"], as_of=as_of.isoformat(),
            newest_date=newest.isoformat() if newest else None, age_hours=age, freshness_score=fr,
            completeness=comp, consistency=cons, hard_failures=json.loads(hard),
            checks=[CheckOut(**c) for c in json.loads(checks)]))
    return out


@router.get("/mapping-coverage", response_model=MappingCoverageOut)
def mapping_coverage(request: Request) -> MappingCoverageOut:
    db = _db(request)
    _require(db, "core.campaign_sku")
    total, unmapped = db.query("""SELECT sum(attributed_revenue), sum(attributed_revenue) FILTER
                                  (WHERE sku = '__unmapped__') FROM core.campaign_sku""")[0]
    coverage = 1.0 if not total else max(0.0, min(1.0, 1 - (unmapped or 0) / total))
    flagged = db.query("""SELECT c.name, w.attribution_weight FROM core.campaign_sku w JOIN core.campaigns c
                          USING (campaign_id) WHERE w.sku = '__unmapped__' AND w.attribution_weight > ?
                          ORDER BY w.attribution_weight DESC""", [MIX_UNCERTAIN_SHARE])
    no_set = db.query("SELECT name FROM core.campaigns WHERE product_set IS NULL ORDER BY name")
    items = [f"{name}: {w:.0%} of attributed revenue outside its product set (MIX_UNCERTAIN)" for name, w in flagged]
    items += [f"{name}: no product set in the campaign name" for (name,) in no_set]
    window = db.query("SELECT min(active_from), max(active_to) FROM core.campaign_sku")[0]
    return MappingCoverageOut(
        coverage=round(coverage, 4), unmapped=items,
        note=(f"Share of paid attributed net revenue ({window[0]} to {window[1]}) landing on SKUs in the campaign's "
              f"product set. Campaigns above {MIX_UNCERTAIN_SHARE:.0%} unmapped are MIX_UNCERTAIN: no autonomous "
              "scale-up and the unmapped share counts as unknown inventory exposure."))


@router.get("/reconciliation", response_model=ReconciliationOut)
def reconciliation(request: Request) -> ReconciliationOut:
    db = _db(request)
    _require(db, "marts.recon_daily")
    end = db.query("SELECT max(date) FROM marts.recon_daily")[0][0]
    if end is None:
        raise HTTPException(503, detail="no reconciled days yet")
    start = end - timedelta(days=WINDOW_DAYS - 1)
    rows = db.query("""SELECT platform, sum(platform_revenue), sum(store_attributed_net_revenue),
                              sum(platform_conversions), sum(store_attributed_orders), sum(ga_sessions), sum(clicks)
                       FROM marts.recon_daily WHERE date BETWEEN ? AND ? GROUP BY 1 ORDER BY 1""", [start, end])
    platforms, prev, srev = [], 0.0, 0.0
    for platform, p_rev, s_rev, p_conv, s_orders, sessions, clicks in rows:
        prev += p_rev or 0.0
        srev += s_rev or 0.0
        platforms.append(PlatformRecon(
            platform=platform, platform_conversions=p_conv or 0.0, store_attributed_orders=int(s_orders or 0),
            over_attribution=round(p_conv / s_orders, 4) if s_orders else None,
            over_attribution_reason=None if s_orders else "ZERO_DENOMINATOR",
            session_click_ratio=round(sessions / clicks, 4) if clicks else None))
    return ReconciliationOut(
        platform_revenue=round(prev, 2), store_revenue=round(srev, 2), attribution_excess=round(prev - srev, 2),
        window_start=start.isoformat(), window_end=end.isoformat(), platforms=platforms,
        note=("Platform-claimed conversion value (includes view-through) against store net revenue attributed by "
              "last paid click (UTM), last 28 complete days. The excess is double counting across sources, not extra "
              "sales; over-attribution = platform conversions / store-attributed orders."))
