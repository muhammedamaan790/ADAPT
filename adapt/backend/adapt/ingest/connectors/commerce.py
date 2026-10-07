"""Store (Shopify-like), finance master data, GA4 and ERP connectors.

Store: tax-exclusive prices with GST on top -> revenue fields use the ex-tax subtotal and keep tax separately
(spec §5: tax excluded everywhere); UTMs parsed out of `landing_site`; refunds are separate records dated on
their own day. GA4: daily rows by source / medium / campaign. ERP: one end-of-day stock snapshot per date.
"""

from __future__ import annotations

from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit

import pandas as pd

from adapt.ingest.connectors.base import ConnectorResult, SyncContext, daterange, sources_config
from adapt.ingest.http import ConnectorError


# ---- pure normalisers --------------------------------------------------------------------------------------------
def parse_local_ts(value: str) -> datetime:
    """ISO 8601 with the brand offset -> naive brand-local timestamp (all workspace timestamps are IST-naive)."""
    dt = datetime.fromisoformat(value)
    if dt.utcoffset() is not None and dt.utcoffset().total_seconds() != 19800:
        raise ConnectorError("UNEXPECTED_TIMEZONE", f"{value} is not in Asia/Kolkata (+05:30)")
    return dt.replace(tzinfo=None)


def parse_utm(landing_site: str | None) -> dict:
    if not landing_site:
        return {"utm_source": None, "utm_medium": None, "utm_campaign": None, "utm_content": None,
                "landing_path": None}
    parts = urlsplit(landing_site)
    q = parse_qs(parts.query)
    first = {k: v[0] for k, v in q.items()}
    return {"utm_source": first.get("utm_source"), "utm_medium": first.get("utm_medium"),
            "utm_campaign": first.get("utm_campaign"), "utm_content": first.get("utm_content"),
            "landing_path": parts.path}


def normalize_orders(orders: list[dict]) -> pd.DataFrame:
    rows = []
    for o in orders:
        if o.get("taxes_included"):
            raise ConnectorError("TAX_INCLUDED_PRICES", f"order {o['id']}: tax-inclusive prices are not supported")
        created = parse_local_ts(o["created_at"])
        utm = parse_utm(o.get("landing_site"))
        for li in o["line_items"]:
            price, qty, disc = float(li["price"]), int(li["quantity"]), float(li.get("total_discount", 0))
            rows.append({
                "order_id": int(o["id"]), "line_item_id": int(li["id"]), "created_at": created, "date": created.date(),
                "customer_id": int(o["customer"]["id"]), "is_new_customer": int(o["customer"]["orders_count"]) == 1,
                "sku": li["sku"], "qty": qty, "unit_price": price, "line_discount": disc,
                "line_subtotal_ex_tax": price * qty - disc, "order_tax": float(o["total_tax"]),
                "order_total": float(o["total_price"]), "currency": o["currency"], **utm,
            })
    return pd.DataFrame(rows)


def normalize_refunds(refunds: list[dict]) -> pd.DataFrame:
    rows = []
    for r in refunds:
        created = parse_local_ts(r["created_at"])
        for li in r["refund_line_items"]:
            rows.append({"refund_id": int(r["id"]), "line_item_id": int(li["line_item_id"]),
                         "order_id": int(r["order_id"]), "created_at": created, "date": created.date(),
                         "quantity": int(li["quantity"]), "amount_ex_tax": float(li["subtotal"]),
                         "tax": float(li.get("total_tax", 0))})
    return pd.DataFrame(rows)


# ---- store -------------------------------------------------------------------------------------------------------
def _since_id_pages(ctx: SyncContext, source: str, path: str, key: str, params: dict):
    since_id, limit = 0, sources_config()["sync"]["page_limit"]
    while True:
        q = params | {"since_id": since_id, "limit": limit}
        page = ctx.http.get(path, q)
        ctx.record_page(source, path, q, page)
        items = page[key]
        if not items:
            return
        yield items
        if len(items) < limit:
            return
        since_id = items[-1]["id"]


def sync_store(ctx: SyncContext, since: date, until: date) -> ConnectorResult:
    api = f"/store/admin/api/{sources_config()['sources']['store']['api_version']}"
    res = ConnectorResult("store")
    rng = {"created_at_min": since.isoformat(), "created_at_max": until.isoformat()}
    orders = [o for page in _since_id_pages(ctx, "store", f"{api}/orders.json", "orders", rng) for o in page]
    refunds = [r for page in _since_id_pages(ctx, "store", f"{api}/refunds.json", "refunds", rng) for r in page]
    res.rows["stg.store_order_lines"] = ctx.upsert("stg.store_order_lines",
                                                   ctx.stamp(normalize_orders(orders), "store"))
    res.rows["stg.store_refund_lines"] = ctx.upsert("stg.store_refund_lines",
                                                    ctx.stamp(normalize_refunds(refunds), "store"))
    products = ctx.http.get(f"{api}/products.json")
    ctx.record_page("store", f"{api}/products.json", {}, products)
    prows = [{"sku": v["sku"], "title": p["title"], "product_type": p["product_type"], "price": float(v["price"]),
              "snapshot_date": ctx.world_date} for p in products["products"] for v in p["variants"]]
    res.rows["stg.products"] = ctx.upsert("stg.products", ctx.stamp(pd.DataFrame(prows), "store", date_col=None))
    res.pages = ctx.pages.get("store", 0)
    return res


def sync_finance(ctx: SyncContext, since: date, until: date) -> ConnectorResult:
    res = ConnectorResult("finance")
    econ = ctx.http.get("/finance/v1/sku_economics")
    ctx.record_page("finance", "/finance/v1/sku_economics", {}, econ)
    erows = [{"sku": e["sku"], "snapshot_date": ctx.world_date, "cogs": float(e["cogs"]),
              "ship_cost": float(e["ship_cost"]) if e["ship_cost"] is not None else None,
              "payment_fee_pct": float(e["payment_fee_pct"])} for e in econ["sku_economics"]]
    res.rows["stg.sku_economics"] = ctx.upsert("stg.sku_economics",
                                               ctx.stamp(pd.DataFrame(erows), "finance", date_col=None))
    hist = ctx.http.get("/finance/v1/price_history")
    ctx.record_page("finance", "/finance/v1/price_history", {}, hist)
    hrows = [{"sku": h["sku"], "valid_from": date.fromisoformat(h["valid_from"]),
              "valid_to": date.fromisoformat(h["valid_to"]) if h["valid_to"] else None, "price": float(h["price"])}
             for h in hist["price_history"]]
    hdf = pd.DataFrame(hrows)
    if not hdf.empty:
        hdf = ctx.stamp(hdf, "finance", date_col="valid_from")
    res.rows["stg.price_history"] = ctx.upsert("stg.price_history", hdf)
    res.pages = ctx.pages.get("finance", 0)
    return res


# ---- GA4 ---------------------------------------------------------------------------------------------------------
def normalize_ga4(report: dict) -> pd.DataFrame:
    dims = [h["name"] for h in report["dimensionHeaders"]]
    mets = [h["name"] for h in report["metricHeaders"]]
    rows = []
    for r in report.get("rows", []):
        d = dict(zip(dims, (v["value"] for v in r["dimensionValues"]), strict=True))
        m = dict(zip(mets, (v["value"] for v in r["metricValues"]), strict=True))
        rows.append({"date": datetime.strptime(d["date"], "%Y%m%d").date(), "source": d["sessionSource"],
                     "medium": d["sessionMedium"], "campaign_id": d["sessionCampaignId"],
                     "sessions": int(m["sessions"]), "purchases": int(m["ecommercePurchases"]),
                     "revenue_inr": float(m["purchaseRevenue"])})
    return pd.DataFrame(rows)


def sync_ga4(ctx: SyncContext, since: date, until: date) -> ConnectorResult:
    prop = sources_config()["sources"]["ga4"]["property_id"]
    path = f"/ga4/v1beta/properties/{prop}:runReport"
    res = ConnectorResult("ga4")
    frames, offset, limit = [], 0, 10_000
    while True:
        body = {"dateRanges": [{"startDate": since.isoformat(), "endDate": until.isoformat()}],
                "dimensions": [{"name": n} for n in ("date", "sessionSource", "sessionMedium", "sessionCampaignId")],
                "metrics": [{"name": n} for n in ("sessions", "ecommercePurchases", "purchaseRevenue")],
                "limit": limit, "offset": offset}
        report = ctx.http.post(path, body)
        ctx.record_page("ga4", path, body, report)
        frames.append(normalize_ga4(report))
        offset += limit
        if offset >= report.get("rowCount", 0):
            break
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    res.rows["stg.ga4_daily"] = ctx.upsert("stg.ga4_daily", ctx.stamp(df, "ga4") if not df.empty else df)
    res.pages = ctx.pages.get("ga4", 0)
    return res


# ---- ERP ---------------------------------------------------------------------------------------------------------
def sync_erp(ctx: SyncContext, since: date, until: date) -> ConnectorResult:
    res = ConnectorResult("erp")
    rows = []
    for d in daterange(since, until):
        try:
            snap = ctx.http.get("/erp/v1/stock", {"date": d.isoformat()})
        except ConnectorError as exc:
            if exc.code == "HTTP_404":  # no snapshot for that date (before go-live or not yet complete)
                continue
            raise
        ctx.record_page("erp", "/erp/v1/stock", {"date": d.isoformat()}, snap)
        for item in snap["items"]:
            inbound = item.get("inbound") or []
            rows.append({"date": date.fromisoformat(snap["as_of"]), "sku": item["sku"],
                         "on_hand": int(item["on_hand"]), "reserved": int(item["reserved"]),
                         "inbound_qty": sum(int(i["quantity"]) for i in inbound),
                         "expected_arrival": min((date.fromisoformat(i["expected_arrival"]) for i in inbound),
                                                 default=None)})
    res.rows["stg.erp_stock_daily"] = ctx.upsert("stg.erp_stock_daily", ctx.stamp(pd.DataFrame(rows), "erp"))
    rec = ctx.http.get("/erp/v1/receipts", {"from": since.isoformat(), "to": until.isoformat()})
    ctx.record_page("erp", "/erp/v1/receipts", {"from": since.isoformat(), "to": until.isoformat()}, rec)
    rdf = pd.DataFrame([{"date": date.fromisoformat(r["date"]), "sku": r["sku"], "quantity": int(r["quantity"])}
                        for r in rec["receipts"]])
    res.rows["stg.erp_receipts"] = ctx.upsert("stg.erp_receipts", ctx.stamp(rdf, "erp") if not rdf.empty else rdf)
    res.pages = ctx.pages.get("erp", 0)
    return res
