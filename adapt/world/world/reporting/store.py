"""Mock store (Shopify-like Admin API 2025-07) and finance master data.

Orders: GET /store/admin/api/2025-07/orders.json?created_at_min&created_at_max&since_id&limit
  - prices are tax-exclusive; GST is charged on top (`total_tax`), so ADAPT must use subtotal, not total_price
  - UTM parameters live in `landing_site`; one line item per order (TheLook lines became orders)
Refunds are separate records: GET /store/admin/api/2025-07/refunds.json?created_at_min&created_at_max&since_id
Products: GET /store/admin/api/2025-07/products.json (current prices)
Finance:  GET /finance/v1/sku_economics (COGS, ship cost, payment fee) and /finance/v1/price_history (SCD2)
Money is INR as decimal strings; timestamps are ISO 8601 in Asia/Kolkata.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from world.accounts import STORE_CURRENCY, STORE_GST_RATE, WAREHOUSE_SKU
from world.priors import benchmarks
from world.reporting.common import NotSeeded, date_of, iso_local, not_seeded_response, parse_iso_date, world_of
from world.reporting.common import reportable_days as _days

router = APIRouter(tags=["mock-store"])
API = "/store/admin/api/2025-07"
MAX_LIMIT = 250
REFUND_MINUTE = 600  # refunds are processed at 10:00


def _m(x: float) -> str:
    return f"{x:.2f}"


def _landing_site(source: str, medium: str, campaign_id, creative_id, sku: str) -> str:
    path = f"/products/{sku.lower()}"
    if campaign_id:
        return f"{path}?utm_source={source}&utm_medium={medium}&utm_campaign={campaign_id}&utm_content={creative_id}"
    if medium == "email":
        return f"{path}?utm_source=email&utm_medium=email"
    return path


@router.get(f"{API}/orders.json")
def orders(request: Request, created_at_min: str | None = None, created_at_max: str | None = None,
           since_id: int = 0, limit: int = Query(default=50, ge=1, le=MAX_LIMIT)) -> JSONResponse:
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    lo, hi = _days(store, truth, parse_iso_date(created_at_min), parse_iso_date(created_at_max))
    rows = store.read("""
        SELECT order_id, day, minute, customer_id, is_new_customer, source, medium, campaign_id, creative_id, sku,
               unit_price_inr, discount_inr
        FROM fact_orders WHERE day BETWEEN ? AND ? AND order_id > ? AND channel NOT IN ('amazon_sp', 'amazon_organic')
        ORDER BY order_id LIMIT ?
    """, [lo, hi, since_id, limit])
    out = []
    for oid, day, minute, cust, is_new, source, medium, cid, crid, sku, price, discount in rows:
        sku_code = sku or WAREHOUSE_SKU
        subtotal = price - discount
        tax = round(subtotal * STORE_GST_RATE, 2)
        out.append({
            "id": oid, "name": f"#{oid % 1_000_000}", "created_at": iso_local(truth, day, minute),
            "currency": STORE_CURRENCY, "taxes_included": False, "financial_status": "paid",
            "subtotal_price": _m(subtotal), "total_discounts": _m(discount), "total_tax": _m(tax),
            "total_price": _m(subtotal + tax), "source_name": "web",
            "landing_site": _landing_site(source, medium, cid, crid, sku_code),
            "customer": {"id": cust, "orders_count": 1 if is_new else 2},
            "line_items": [{"id": oid * 10 + 1, "sku": sku_code, "quantity": 1, "price": _m(price),
                            "total_discount": _m(discount)}],
        })
    return JSONResponse({"orders": out})


@router.get(f"{API}/refunds.json")
def refunds(request: Request, created_at_min: str | None = None, created_at_max: str | None = None,
            since_id: int = 0, limit: int = Query(default=50, ge=1, le=MAX_LIMIT)) -> JSONResponse:
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    lo, hi = _days(store, truth, parse_iso_date(created_at_min), parse_iso_date(created_at_max))
    rows = store.read("""
        SELECT order_id, return_day, unit_price_inr - discount_inr FROM fact_orders
        WHERE returned AND return_day BETWEEN ? AND ? AND order_id > ?
          AND channel NOT IN ('amazon_sp', 'amazon_organic')
        ORDER BY order_id LIMIT ?
    """, [lo, hi, since_id, limit])
    out = []
    for oid, rday, subtotal in rows:
        tax = round(subtotal * STORE_GST_RATE, 2)
        out.append({
            "id": oid, "order_id": oid, "created_at": iso_local(truth, rday, REFUND_MINUTE),
            "refund_line_items": [{"line_item_id": oid * 10 + 1, "quantity": 1, "subtotal": _m(subtotal),
                                   "total_tax": _m(tax)}],
            "transactions": [{"kind": "refund", "amount": _m(subtotal + tax), "currency": STORE_CURRENCY}],
        })
    return JSONResponse({"refunds": out})


@router.get(f"{API}/products.json")
def products(request: Request) -> JSONResponse:
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    prices = dict(store.read("SELECT sku, price_inr FROM price_state"))
    cats = {c.code: c for c in truth.catalog.categories}
    out = [{"id": i + 1, "title": f"{cats[s.category_code].label} · band {s.price_band}",
            "product_type": cats[s.category_code].label, "variants": [{"sku": s.sku, "price": _m(prices[s.sku])}]}
           for i, s in enumerate(truth.catalog.skus)]
    out.append({"id": len(out) + 1, "title": "Other assorted products", "product_type": "Other",
                "variants": [{"sku": WAREHOUSE_SKU, "price": _m(float(truth.warehouse["price_inr"]))}]})
    return JSONResponse({"products": out})


@router.get("/finance/v1/sku_economics")
def sku_economics(request: Request) -> JSONResponse:
    try:
        _, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    rows = [{"sku": s.sku, "currency": STORE_CURRENCY, "cogs": _m(s.unit_cogs_inr), "ship_cost": _m(s.ship_cost_inr),
             "payment_fee_pct": s.fee_pct} for s in truth.catalog.skus]
    wh = truth.warehouse
    costs = benchmarks()["unit_costs"]
    wh_ship = max(costs["ship_cost_min_inr"], costs["ship_cost_pct"] * float(wh["price_inr"]))
    rows.append({"sku": WAREHOUSE_SKU, "currency": STORE_CURRENCY, "cogs": _m(float(wh["cogs_inr"])),
                 "ship_cost": _m(wh_ship), "payment_fee_pct": costs["payment_fee_pct"]})
    return JSONResponse({"sku_economics": rows})


@router.get("/finance/v1/price_history")
def price_history(request: Request) -> JSONResponse:
    """SCD2: each row is valid from `valid_from` until the next row of the same SKU."""
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    clock_day = store.clock()[1]
    rows = store.read("""
        SELECT sku, price_inr, effective_from_day,
               lead(effective_from_day) OVER (PARTITION BY sku ORDER BY effective_from_day) AS next_day
        FROM price_history WHERE effective_from_day <= ? ORDER BY sku, effective_from_day
    """, [clock_day])
    out = [{"sku": sku, "currency": STORE_CURRENCY, "price": _m(price),
            "valid_from": date_of(truth, start).isoformat(),
            "valid_to": date_of(truth, nxt).isoformat() if nxt is not None else None}
           for sku, price, start, nxt in rows]
    return JSONResponse({"price_history": out})
