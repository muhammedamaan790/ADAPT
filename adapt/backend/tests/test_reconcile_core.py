"""A3 'done when' (hand-calculated fixtures): canonical tables, the refund contract, attribution, campaign_sku
weights, history tables, marts and data health match values worked out by hand; nothing after as_of leaks in."""

from datetime import date, datetime, timedelta

import pytest

from adapt.core.db import Database
from adapt.ingest.schema import ensure_schema
from adapt.reconcile.build import build_canonical
from adapt.reconcile.health import freshness

AS_OF = datetime(2026, 10, 1, 12, 0)  # today = 2026-10-01, last complete day = 2026-09-30
D = date(2026, 9, 30)


def at(d: date, hours: int = 0) -> datetime:
    return datetime.combine(d + timedelta(days=1), datetime.min.time()) + timedelta(hours=hours)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "ws.duckdb")
    d.write(ensure_schema)
    yield d
    d.close()


def ins(cur, table: str, rows: list[dict]) -> None:
    for r in rows:
        r = {"run_id": "t", "ingested_at": AS_OF, "provenance": "CALIBRATED", **r}
        cols = ", ".join(r)
        cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' for _ in r)})", list(r.values()))


def seed(cur, *, extra_snapshot=False) -> None:
    snap = lambda **k: {"snapshot_date": date(2026, 10, 1), "available_at": datetime(2026, 10, 1), **k}  # noqa: E731
    ins(cur, "stg.entity_snapshots", [
        snap(platform="meta", entity_type="campaign", entity_id="M1", campaign_id="M1", budget_id="M1",
             name="META Prospecting | Men·Jeans", status="ACTIVE", channel_type="OUTCOME_SALES",
             budget_amount_inr=1000.0, budget_is_shared=False),
        snap(platform="meta", entity_type="campaign", entity_id="M2", campaign_id="M2", budget_id="M2",
             name="META Prospecting | Women·Dresses", status="ACTIVE", channel_type="OUTCOME_SALES",
             budget_amount_inr=500.0, budget_is_shared=False),
        snap(platform="google", entity_type="campaign", entity_id="G1", campaign_id="G1", budget_id="B9",
             name="GOOGLE Search | Men·Jeans", status="ENABLED", channel_type="SEARCH", budget_amount_inr=2000.0),
        snap(platform="google", entity_type="budget", entity_id="B9", budget_id="B9", status="ENABLED",
             budget_amount_inr=2000.0, budget_is_shared=False),
        snap(platform="meta", entity_type="ad", entity_id="A1", parent_id="S1", campaign_id="M1", name="ad",
             status="ACTIVE"),
    ])
    if extra_snapshot:  # an earlier sync saw a different budget and a paused status
        ins(cur, "stg.entity_snapshots", [
            {"snapshot_date": date(2026, 9, 25), "available_at": datetime(2026, 9, 25), "platform": "meta",
             "entity_type": "campaign", "entity_id": "M1", "campaign_id": "M1", "budget_id": "M1",
             "name": "META Prospecting | Men·Jeans", "status": "PAUSED", "budget_amount_inr": 800.0}])
    ins(cur, "stg.products", [
        {"sku": s, "snapshot_date": date(2026, 10, 1), "title": s, "product_type": pt, "price": p,
         "available_at": datetime(2026, 10, 1)}
        for s, pt, p in [("J1", "Men·Jeans", 1000.0), ("J2", "Men·Jeans", 2000.0), ("D1", "Women·Dresses", 1500.0),
                         ("OTHER-ASSORTED", "Other", 500.0)]])
    ins(cur, "stg.sku_economics", [
        {"sku": s, "snapshot_date": date(2026, 10, 1), "cogs": c, "ship_cost": 50.0, "payment_fee_pct": 0.02,
         "available_at": datetime(2026, 10, 1)}
        for s, c in [("J1", 400.0), ("J2", 900.0), ("D1", 600.0), ("OTHER-ASSORTED", 200.0)]])

    def line(oid, d, sku, price, utm=(None, None, None, None), new=True):
        return {"order_id": oid, "line_item_id": oid * 10 + 1, "created_at": datetime.combine(d, datetime.min.time()),
                "date": d, "customer_id": oid, "is_new_customer": new, "sku": sku, "qty": 1, "unit_price": price,
                "line_discount": 0.0, "line_subtotal_ex_tax": price, "order_tax": round(price * 0.12, 2),
                "order_total": price * 1.12, "currency": "INR", "utm_source": utm[0], "utm_medium": utm[1],
                "utm_campaign": utm[2], "utm_content": utm[3], "available_at": at(d)}

    old = date(2026, 6, 1)  # matured (older than 30 days) and inside the return-rate window
    paid_m1 = ("facebook", "paid_social", "M1", "A1")
    ins(cur, "stg.store_order_lines", [
        # matured history for return rate + lag CDF: J1 sold 10, 2 returned (lags 5 and 15 days)
        *[line(100 + i, old, "J1", 1000.0) for i in range(10)],
        # recent paid M1 orders (attribution window), unpaid, warehouse
        line(1, D, "J1", 1000.0, paid_m1),                       # J1 via M1
        line(2, D - timedelta(days=2), "J2", 2000.0, paid_m1),   # J2 via M1
        line(3, D, "OTHER-ASSORTED", 500.0, paid_m1),            # cross-sell outside M1's product set
        line(4, D, "D1", 1500.0, ("email", "email", None, None)),
        line(5, D, "D1", 1500.0, ("google", "organic", None, None)),
        line(6, D, "D1", 1500.0),                                 # no UTM -> direct
        line(7, D - timedelta(days=5), "J1", 1000.0, paid_m1),   # returned already (realised 1000 > expected)
        line(8, date(2026, 10, 1), "J1", 1000.0, paid_m1) | {"available_at": at(date(2026, 10, 1))},  # future
    ])
    def refund(rid, line_id, oid, d):
        return {"refund_id": rid, "line_item_id": line_id, "order_id": oid,
                "created_at": datetime.combine(d, datetime.min.time()), "date": d, "quantity": 1,
                "amount_ex_tax": 1000.0, "tax": 120.0, "available_at": at(d)}

    ins(cur, "stg.store_refund_lines", [refund(100, 1001, 100, date(2026, 6, 6)),  # lag 5 days
                                        refund(101, 1011, 101, date(2026, 6, 16)),  # lag 15 days
                                        refund(7, 71, 7, date(2026, 9, 28))])
    ins(cur, "stg.meta_ad_daily", [
        {"date": D - timedelta(days=2), "campaign_id": "M1", "adset_id": "S1", "ad_id": "A1", "impressions": 1000,
         "clicks": 50, "spend_native": 12.0, "currency": "USD", "fx_rate": 83.0, "spend_inr": 996.0,
         "platform_conversions": 2, "platform_conversion_value_inr": 4000.0,
         "available_at": at(D - timedelta(days=2), 6)},
        {"date": D, "campaign_id": "M1", "adset_id": "S1", "ad_id": "A1", "impressions": 2000, "clicks": 80,
         "spend_native": 12.0, "currency": "USD", "fx_rate": 83.0, "spend_inr": 996.0, "platform_conversions": 3,
         "platform_conversion_value_inr": 3000.0, "available_at": at(D, 6)},
    ])
    ins(cur, "stg.price_history", [{"sku": s, "valid_from": date(2025, 10, 1), "valid_to": None, "price": p,
                                    "available_at": datetime(2025, 10, 2)}
                                   for s, p in [("J1", 1000.0), ("J2", 2000.0), ("D1", 1500.0)]])
    ins(cur, "stg.erp_stock_daily", [
        {"date": D - timedelta(days=i), "sku": "J1", "on_hand": 100 - i, "reserved": 0, "inbound_qty": 0,
         "expected_arrival": None, "available_at": at(D - timedelta(days=i), 2)} for i in range(3)])


def built(db, **kw):
    db.write(lambda cur: seed(cur, **kw))
    return build_canonical(db, AS_OF, run_id="r1")


def one(db, sql, params=None):
    return db.query(sql, params)[0]


def test_refund_contract_and_line_economics(db):
    built(db)
    # matured window: 10 J1 lines, 2 returned -> r = 0.2; lags 5 and 15 days -> F(a) = 0.5 for 5 <= a < 15
    assert one(db, "SELECT return_rate FROM core.sku_return_rates WHERE sku = 'J1'")[0] == pytest.approx(0.2)
    assert one(db, "SELECT cdf FROM core.return_lag_cdf WHERE age = 10")[0] == pytest.approx(0.5)
    # order 1: J1 sold yesterday (age 0, F = 0) -> expected = 0.2 x 1000 = 200, no realised -> liability 200
    r = one(db, "SELECT refund, refund_basis, net_revenue, cogs, ship_cost, payment_fee, cba FROM core.order_items "
                "WHERE order_id = 1")
    assert r == (pytest.approx(200.0), "liability_max", pytest.approx(800.0), 400.0, 50.0, pytest.approx(20.0),
                 pytest.approx(800 - 400 - 50 - 20))
    # order 7: age 5 -> F = 0.5 -> p = 0.2 x 0.5 / (1 - 0.1) = 0.1111; realised 1000 > expected -> refund 1000
    assert one(db, "SELECT refund FROM core.order_items WHERE order_id = 7")[0] == pytest.approx(1000.0)
    # a matured, returned line uses the realised refund
    assert one(db, "SELECT refund, refund_basis FROM core.order_items WHERE order_id = 100") == (1000.0, "realized")
    assert one(db, "SELECT refund FROM core.order_items WHERE order_id = 109")[0] == 0.0


def test_conditional_expected_refund_after_some_days(db):
    built(db)
    # order 2: J2 has no matured history -> brand rate 0.2; age 2 -> F(2) = 0 -> p = 0.2 -> 400
    assert one(db, "SELECT expected_refund FROM core.order_items WHERE order_id = 2")[0] == pytest.approx(400.0)


def test_attribution_channels_and_no_leakage(db):
    built(db)
    ch = dict(db.query("SELECT order_id, channel_id FROM core.order_items WHERE order_id BETWEEN 1 AND 6"))
    assert ch == {1: "meta", 2: "meta", 3: "meta", 4: "email", 5: "organic", 6: "direct"}
    att = db.query("SELECT order_id, campaign_id, ad_id, method FROM core.attribution ORDER BY order_id")
    assert [a[0] for a in att] == [1, 2, 3, 7] and all(a[1:] == ("M1", "A1", "last_paid_click") for a in att)
    assert one(db, "SELECT count(*) FROM core.order_items WHERE order_id = 8")[0] == 0  # available after as_of


def test_campaign_sku_weights_hand_check(db):
    built(db)
    # M1 product set = {J1, J2} (a = 0.5 each); attributed net revenue in the 28-day window:
    #   J1: order1 800 + order7 0 = 800, J2: order2 1600, unmapped: order3 500 x (1 - 0.2) = 400 -> R = 2800
    w = dict(db.query("SELECT sku, attribution_weight FROM core.campaign_sku WHERE campaign_id = 'M1'"))
    s, R = 0.01, 2800.0
    assert w["J1"] == pytest.approx((800 + s * R * 0.5) / (R * 1.01))
    assert w["J2"] == pytest.approx((1600 + s * R * 0.5) / (R * 1.01))
    assert w["__unmapped__"] == pytest.approx(400 / (R * 1.01))
    assert sum(w.values()) == pytest.approx(1.0)
    # M2 has no attributed revenue (R = 0) -> allocation weights, unmapped 0
    w2 = dict(db.query("SELECT sku, attribution_weight FROM core.campaign_sku WHERE campaign_id = 'M2'"))
    assert w2 == {"D1": 1.0, "__unmapped__": 0.0}
    for cid, total in db.query("SELECT campaign_id, sum(attribution_weight) FROM core.campaign_sku GROUP BY 1"):
        assert total == pytest.approx(1.0), cid


def test_budget_and_status_history_from_snapshots(db):
    built(db, extra_snapshot=True)
    hist = db.query("SELECT effective_from, effective_to, value, source FROM core.budget_history "
                    "WHERE entity_id = 'M1' ORDER BY effective_from")
    assert hist == [(date(2026, 9, 25), date(2026, 10, 1), 800.0, "first_observed"),
                    (date(2026, 10, 1), None, 1000.0, "observed_change")]
    st = db.query("SELECT value FROM core.campaign_state_history WHERE entity_id = 'M1' ORDER BY effective_from")
    assert [s[0] for s in st] == ["PAUSED", "ACTIVE"]
    assert one(db, "SELECT channel_id, product_set FROM core.campaigns WHERE campaign_id = 'G1'") == (
        "google_search", "Men·Jeans")


def test_campaign_mart_spine_and_totals(db):
    built(db)
    rows = db.query("SELECT date, impressions, spend, attributed_orders, attributed_net_revenue "
                    "FROM marts.campaign_daily WHERE campaign_id = 'M1' ORDER BY date")
    assert [r[0] for r in rows] == [D - timedelta(days=2), D - timedelta(days=1), D]  # zero day filled
    assert rows[1][1:] == (0, 0.0, 0, 0.0)
    assert rows[2][1] == 2000 and rows[2][3] == 2  # orders 1 and 3 on D
    assert rows[2][4] == pytest.approx(800 + 400)
    rec = one(db, "SELECT platform_conversions, store_attributed_orders FROM marts.recon_daily "
                  "WHERE platform = 'meta' AND date = ?", [D])
    assert rec == (3.0, 2)


def test_health_formula_and_hard_failures(db):
    assert freshness(None, 24) == 0.0 and freshness(10, 24) == 1.0
    assert freshness(48, 24) == pytest.approx(0.5) and freshness(72, 24) == 0.0
    db.write(lambda cur: seed(cur))
    db.write(lambda cur: cur.execute(
        "INSERT INTO ops.connector_status VALUES ('ga4', 'FAILED', NULL, ?, 'HTTP_503', NULL, 'x')", [AS_OF]))
    out = build_canonical(db, AS_OF)
    assert out["health"]["ga4"]["status"] == "RED"  # failed connector (and no data) forces RED
    assert out["health"]["meta_ads"]["status"] in ("GREEN", "YELLOW")
    checks = one(db, "SELECT checks FROM ops.data_health WHERE source = 'store'")[0]
    assert "store.utm_referential" in checks


def test_rebuild_is_idempotent(db):
    built(db)
    snap = lambda: {t: db.query(f"SELECT * FROM {t} ORDER BY ALL") for t in  # noqa: E731
                    ("core.order_items", "core.campaign_sku", "marts.campaign_daily", "marts.brand_daily")}
    first = snap()
    build_canonical(db, AS_OF, run_id="r2")
    assert snap() == first
