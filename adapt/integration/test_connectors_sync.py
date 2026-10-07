"""A2 'done when' (integration): every connector loads the world's native reports, and stg.* reconciles exactly
with the world's own facts after micros / cents / FX / GST / timezone normalisation."""

from datetime import date, datetime

import httpx
import pytest

from adapt.ingest.connectors.base import fx_to_inr
from adapt.ingest.http import SourceHttp
from adapt.ingest.sync import run_sync

FX = fx_to_inr("USD")
LAST = date(2026, 9, 30)  # fixture world: day -1


def facts(world, sql, params=None):
    return world.app.state.store.read(sql, params)


def q(db, sql, params=None):
    return db.query(sql, params)


def test_full_backfill_reconciles_with_the_world(world, http, db):
    report = run_sync(db, http)
    assert report["status"] == "OK" and report["world_date"] == "2026-10-01"
    assert all(c["status"] == "OK" for c in report["connectors"].values())

    # Meta: USD -> INR at the simulation rate (cents rounding only)
    (w_spend,) = facts(world, "SELECT sum(spend_inr) FROM fact_ad_creative_daily WHERE platform = 'meta'")[0]
    (s_spend, s_conv), = q(db, "SELECT sum(spend_inr), sum(platform_conversions) FROM stg.meta_ad_daily")
    assert s_spend == pytest.approx(w_spend, rel=1e-4)
    assert s_conv == facts(world, "SELECT sum(conversions) FROM fact_ad_creative_daily WHERE platform = 'meta'")[0][0]
    # Google: micros exact
    g_world = facts(world, "SELECT sum(spend_inr), sum(impressions) FROM fact_ad_creative_daily "
                           "WHERE platform = 'google'")[0]
    g_stg = q(db, "SELECT sum(cost_inr), sum(impressions) FROM stg.google_ad_daily")[0]
    assert g_stg[0] == pytest.approx(g_world[0], rel=1e-9) and g_stg[1] == g_world[1]
    # Store: one line per order, ex-tax subtotal = world revenue, UTMs on every paid order
    n, rev, paid = facts(world, "SELECT count(*), sum(unit_price_inr), count(campaign_id) FROM fact_orders")[0]
    sn, srev, spaid = q(db, "SELECT count(*), sum(line_subtotal_ex_tax), count(utm_campaign) "
                            "FROM stg.store_order_lines")[0]
    assert (sn, spaid) == (n, paid) and srev == pytest.approx(rev, rel=1e-6)
    assert q(db, "SELECT count(*) FROM stg.store_order_lines WHERE abs(order_tax - "
                 "round(line_subtotal_ex_tax * 0.12, 2)) > 0.011")[0][0] == 0
    refunds = facts(world, "SELECT count(*) FROM fact_orders WHERE returned AND return_day < 0")[0][0]
    assert q(db, "SELECT count(*) FROM stg.store_refund_lines")[0][0] == refunds
    # GA4, ERP
    assert q(db, "SELECT sum(sessions) FROM stg.ga4_daily")[0][0] == facts(
        world, "SELECT sum(sessions) FROM fact_ga_daily")[0][0]
    erp = q(db, "SELECT count(*), count(DISTINCT date), sum(on_hand) FROM stg.erp_stock_daily")[0]
    w_erp = facts(world, "SELECT count(*), count(DISTINCT day), sum(on_hand) FROM fact_erp_daily")[0]
    assert erp == w_erp
    # entity snapshots: every campaign of both platforms, budgets in INR
    snaps = dict(q(db, "SELECT platform, count(*) FROM stg.entity_snapshots WHERE entity_type = 'campaign' "
                       "GROUP BY 1"))
    assert snaps == {"meta": 6, "google": 6}
    shared = q(db, "SELECT count(*) FROM stg.entity_snapshots WHERE entity_type = 'budget' AND budget_is_shared")
    assert shared[0][0] == 1
    meta_budget = q(db, "SELECT budget_amount_inr FROM stg.entity_snapshots WHERE platform = 'meta' "
                        "AND entity_type = 'campaign' LIMIT 1")[0][0]
    assert meta_budget > 0
    # finance
    assert q(db, "SELECT count(*) FROM stg.sku_economics")[0][0] == 7
    assert q(db, "SELECT count(*) FROM stg.price_history")[0][0] == 6


def test_metadata_logical_time_and_raw_pages(world, http, db):
    run_sync(db, http)
    a = q(db, "SELECT DISTINCT available_at FROM stg.meta_ad_daily WHERE date = ?", [LAST])
    assert a == [(datetime(2026, 10, 1, 6, 0),)]  # next day 00:00 + 6h report lag, in logical time
    assert q(db, "SELECT DISTINCT provenance FROM stg.store_order_lines") == [("CALIBRATED",)]
    assert q(db, "SELECT DISTINCT provenance FROM stg.sku_economics") == [("PUBLIC-SAMPLE",)]
    pages = dict(q(db, "SELECT source, count(*) FROM raw.api_pages GROUP BY 1"))
    assert set(pages) == {"meta_ads", "google_ads", "store", "finance", "ga4", "erp"}
    cols = q(db, "SELECT DISTINCT source_currency FROM raw.api_pages WHERE source = 'meta_ads'")
    assert cols == [("USD",)]
    status = dict(q(db, "SELECT connector, status FROM ops.connector_status"))
    assert set(status.values()) == {"OK"} and len(status) == 6


def test_resync_is_idempotent_and_advance_pulls_only_new_days(world, http, db):
    run_sync(db, http)
    tables = ["stg.meta_ad_daily", "stg.google_ad_daily", "stg.store_order_lines", "stg.ga4_daily",
              "stg.erp_stock_daily"]
    before = {t: q(db, f"SELECT count(*) FROM {t}")[0][0] for t in tables}
    second = run_sync(db, http)
    assert {t: q(db, f"SELECT count(*) FROM {t}")[0][0] for t in tables} == before  # upserts, no duplicates
    assert second["connectors"]["store"]["since"] == "2026-09-28"  # lookback window (3 days)
    world.post("/control/advance", json={"days": 1}, headers={"X-Request-ID": "adv"})
    third = run_sync(db, http)
    assert third["world_date"] == "2026-10-02"
    assert q(db, "SELECT max(date) FROM stg.store_order_lines")[0][0] == date(2026, 10, 1)
    new_orders = facts(world, "SELECT count(*) FROM fact_orders WHERE day = 0")[0][0]
    assert q(db, "SELECT count(*) FROM stg.store_order_lines WHERE date = ?", [date(2026, 10, 1)])[0][0] == new_orders


def test_a_failing_source_is_isolated_and_recorded(world, db):
    def handler(request: httpx.Request) -> httpx.Response:
        if "runReport" in request.url.path:
            return httpx.Response(503, json={"error": "down"})
        r = world.request(request.method, str(request.url), content=request.content,
                          headers={"content-type": "application/json"} if request.content else None)
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})

    flaky = SourceHttp("http://testserver", httpx.Client(transport=httpx.MockTransport(handler)),
                       sleep=lambda s: None)
    report = run_sync(db, flaky)
    assert report["status"] == "PARTIAL"
    assert report["connectors"]["ga4"] == {**report["connectors"]["ga4"], "status": "FAILED", "error": "HTTP_503"}
    assert report["connectors"]["store"]["status"] == "OK"
    assert q(db, "SELECT count(*) FROM stg.ga4_daily")[0][0] == 0  # nothing partial was committed
    status, code, last_ok = q(db, "SELECT status, last_error_code, last_success_ts FROM ops.connector_status "
                                  "WHERE connector = 'ga4'")[0]
    assert (status, code, last_ok) == ("FAILED", "HTTP_503", None)
    assert q(db, "SELECT status FROM ops.sync_runs")[0][0] == "PARTIAL"


def test_unseeded_world_is_refused(db, tmp_path):
    from fastapi.testclient import TestClient

    from adapt.ingest.http import ConnectorError
    from world.main import create_app

    with TestClient(create_app(state_path=tmp_path / "bare.duckdb")) as bare:
        with pytest.raises(ConnectorError) as e:
            run_sync(db, SourceHttp("http://testserver", client=bare))
    assert e.value.code == "WORLD_NOT_SEEDED"
