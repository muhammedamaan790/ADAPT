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

from adapt.economics.state import objectives_config
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
        raise HTTPException(503, detail="canonical state not built yet: run the connector sync and the A3 build "
                                        "(an uploaded-data workspace fills from its CSV uploads instead)")


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
    return reconciliation_view(_db(request))


def reconciliation_view(db) -> ReconciliationOut:
    """Shared by the endpoint and the Copilot's get_reconciliation tool."""
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


# -- inventory (one row per SKU, as of the latest built day) ---------------------------------------------------------
VELOCITY_ALERT = 0.25  # "selling 25%+ faster than its 28-day average"
InventoryAction = Literal["RESTOCK", "HOLD_SPEND", "EXPEDITE", "CLEAR_EXCESS", "SCALE_DEMAND", "WATCH", "CONTINUE"]


class InventorySku(BaseModel):
    sku: str
    title: str
    category: str | None
    promoted: bool
    on_hand: int
    reserved: int
    available: int
    inbound_qty: int
    expected_arrival: str | None
    units_7d_avg: float
    units_28d_avg: float
    velocity_change: float | None  # 7-day average / 28-day average - 1; None without 28-day demand
    cover_days: float | None  # available / 28-day average; None = no recent demand
    reorder_point: float
    safety_stock: float
    stockout_days_28d: int
    net_revenue_28d: float
    cba_28d: float  # contribution before ads
    ad_spend_28d: float  # campaign spend x the campaign's attribution weight on this SKU
    action: InventoryAction
    reason: str


class InventoryOut(BaseModel):
    as_of: str
    window_days: int
    lead_time_days: int
    excess_cover_days: float
    velocity_alert: float
    skus: list[InventorySku]
    note: str


def inventory_action(*, available: int, inbound: int, days_to_arrival: int | None, position: float, rop: float,
                     v28: float, change: float | None, spend: float, lead: int, excess: float) -> tuple[str, str]:
    """Deterministic inventory recommendation for one SKU (rules, not a model): the first matching rule wins."""
    cover = available / v28 if v28 > 0 else None
    faster = f" Selling {change:.0%} faster than its 28-day average." if change and change >= VELOCITY_ALERT else ""
    arrives = (f"{inbound:,} inbound units arrive {_in_days(days_to_arrival)}" if days_to_arrival is not None
               else "no inbound order is open")
    if position <= rop:
        return "RESTOCK", (f"Available plus confirmed inbound ({position:,.0f} units) is at or below the reorder point "
                           f"({rop:,.0f}); reorder now to cover the {lead}-day lead time.{faster}")
    if available <= 0 or (cover is not None and days_to_arrival is not None and cover < days_to_arrival):
        runs_out = "Out of stock" if available <= 0 else f"Sells out {_in_days(cover)}"
        when = arrives
        if spend > 0:
            return "HOLD_SPEND", f"{runs_out}; {when}. Its ads keep spending: reduce them until stock lands.{faster}"
        return "EXPEDITE", f"{runs_out}; {when}. Bring the inbound forward if possible.{faster}"
    if cover is None:
        return "CLEAR_EXCESS", f"{available:,} units on hand and no sales in 28 days."
    if cover > excess:
        return "CLEAR_EXCESS", (f"{cover:.0f} days of cover, above the {excess:.0f}-day excess band; the Inventory "
                                "Clearance objective can move spend toward it.")
    if change is not None and change >= VELOCITY_ALERT:
        if cover >= 2 * lead:
            return "SCALE_DEMAND", (f"Selling {change:.0%} faster than its 28-day average with {cover:.0f} days of "
                                    "cover: stock supports more spend.")
        return "WATCH", (f"Selling {change:.0%} faster than its 28-day average; {cover:.0f} days of cover against a "
                         f"{lead}-day lead time. Check the next reorder.")
    if days_to_arrival is not None and cover < lead:
        return "CONTINUE", f"{cover:.0f} days of cover and {arrives}; demand within its normal range."
    return "CONTINUE", f"{cover:.0f} days of cover; demand within its normal range."


def _in_days(days: float) -> str:
    n = round(days)
    return "within a day" if n < 1 else "in 1 day" if n == 1 else f"in about {n} days"


@router.get("/inventory", response_model=InventoryOut)
def inventory(request: Request) -> InventoryOut:
    return inventory_view(_db(request))


def inventory_view(db) -> InventoryOut:
    _require(db, "marts.sku_daily")
    end = db.query("SELECT max(date) FROM marts.sku_daily")[0][0]
    if end is None:
        raise HTTPException(503, detail="no inventory days yet")
    start, week = end - timedelta(days=WINDOW_DAYS - 1), end - timedelta(days=6)
    excess = float(objectives_config()["INVENTORY_CLEARANCE"].get("excess_cover_days", 45))
    rows = db.query("""
        WITH w AS (SELECT * FROM marts.sku_daily WHERE date BETWEEN ? AND ?),
             agg AS (SELECT sku, avg(units) AS v28, avg(units) FILTER (WHERE date >= ?) AS v7,
                            sum(net_revenue) AS rev, sum(cba) AS cba,
                            count(*) FILTER (WHERE on_hand - reserved <= 0) AS oos_days
                     FROM w GROUP BY sku),
             cs AS (SELECT campaign_id, sum(spend) AS spend FROM marts.campaign_daily
                    WHERE date BETWEEN ? AND ? GROUP BY 1),
             spend AS (SELECT m.sku, sum(cs.spend * m.attribution_weight) AS spend
                       FROM core.campaign_sku m JOIN cs USING (campaign_id)
                       WHERE m.sku <> '__unmapped__' GROUP BY 1)
        SELECT l.sku, coalesce(k.title, l.sku), l.category, coalesce(k.promoted, false), l.on_hand, l.reserved,
               l.inbound_qty, l.expected_arrival, l.inbound_confidence, l.lead_time_days, l.reorder_point,
               l.safety_stock, a.v7, a.v28, a.rev, a.cba, a.oos_days, coalesce(s.spend, 0)
        FROM marts.sku_daily l JOIN agg a USING (sku) LEFT JOIN core.skus k USING (sku) LEFT JOIN spend s USING (sku)
        WHERE l.date = ? ORDER BY l.sku""", [start, end, week, start, end, end])
    skus, lead_time = [], 0
    for (sku, title, cat, promoted, on_hand, reserved, inbound, arrival, conf, lead, rop, ss, v7, v28, rev, cba,
         oos, spend) in rows:
        on_hand, reserved, inbound = int(on_hand or 0), int(reserved or 0), int(inbound or 0)
        available, v7, v28, lead_time = on_hand - reserved, float(v7 or 0), float(v28 or 0), int(lead)
        change = v7 / v28 - 1 if v28 > 0 else None
        days_to_arrival = max(0, (arrival - end).days) if inbound > 0 and arrival else None
        action, reason = inventory_action(
            available=available, inbound=inbound, days_to_arrival=days_to_arrival,
            position=available + inbound * float(conf or 0), rop=float(rop or 0), v28=v28, change=change,
            spend=float(spend), lead=lead_time, excess=excess)
        skus.append(InventorySku(
            sku=sku, title=title, category=cat, promoted=bool(promoted), on_hand=on_hand, reserved=reserved,
            available=available, inbound_qty=inbound, expected_arrival=arrival.isoformat() if arrival else None,
            units_7d_avg=round(v7, 2), units_28d_avg=round(v28, 2),
            velocity_change=round(change, 4) if change is not None else None,
            cover_days=round(available / v28, 1) if v28 > 0 else None, reorder_point=round(float(rop or 0), 1),
            safety_stock=round(float(ss or 0), 1), stockout_days_28d=int(oos), net_revenue_28d=round(rev or 0, 2),
            cba_28d=round(cba or 0, 2), ad_spend_28d=round(float(spend), 2), action=action, reason=reason))
    return InventoryOut(
        as_of=end.isoformat(), window_days=WINDOW_DAYS, lead_time_days=lead_time, excess_cover_days=excess,
        velocity_alert=VELOCITY_ALERT, skus=skus,
        note=("Stock from the ERP feed, demand from store orders, ad spend from each campaign's attribution weights "
              "(last 28 complete days). Recommendations are deterministic rules on cover, reorder point, inbound and "
              "velocity; budget changes still go through a decision and its policy checks."))
