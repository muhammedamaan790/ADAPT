"""A5/A2 groundwork: every native-format report reconciles exactly with the world's own facts, only complete
days are visible, pagination returns everything once, and reset restores the post-history baseline."""

import json

import duckdb
import pytest
from fastapi.testclient import TestClient

from world.accounts import GA4_PROPERTY_ID, META_AD_ACCOUNT_ID, STORE_GST_RATE
from world.main import create_app
from world.priors import benchmarks
from world.truth import TruthMismatch, load_truth

CID = "6876531111"
FX = benchmarks()["fx_usd_inr"]
LAST = "2026-09-30"  # world day -1 (history_end_date)
FIRST_WEEK = ("2026-09-24", "2026-09-30")


@pytest.fixture
def client(world_copy):
    with TestClient(create_app(world_dir=world_copy)) as c:
        yield c


def facts(client, sql, params=None):
    return client.app.state.store.read(sql, params)


def gaql(client, query, page_token=None):
    body = {"query": query} | ({"pageToken": page_token} if page_token else {})
    r = client.post(f"/google/v25/customers/{CID}/googleAds:search", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_health_reports_the_seeded_clock(client):
    assert client.get("/health").json() == {"seeded": True, "seed": 42, "day": 0}


# ---- Meta ---------------------------------------------------------------------------------------------------
def test_meta_insights_reconcile_with_facts_and_page_completely(client):
    tr = json.dumps({"since": FIRST_WEEK[0], "until": FIRST_WEEK[1]})
    url = f"/meta/v25.0/act_{META_AD_ACCOUNT_ID}/insights"
    params = {"level": "ad", "time_range": tr, "time_increment": "1", "limit": 7,
              "fields": "ad_id,date_start,impressions,clicks,spend,actions,account_currency"}
    rows, after = [], None
    while True:
        r = client.get(url, params=params | ({"after": after} if after else {})).json()
        rows += r["data"]
        if "next" not in r["paging"]:
            break
        after = r["paging"]["cursors"]["after"]
    assert len({(x["ad_id"], x["date_start"]) for x in rows}) == len(rows)  # no duplicates across pages
    imps, clicks, spend, conv = facts(client, """
        SELECT sum(impressions), sum(clicks), sum(spend_inr), sum(conversions) FROM fact_ad_creative_daily
        WHERE platform = 'meta' AND day BETWEEN -7 AND -1""")[0]
    assert sum(int(x["impressions"]) for x in rows) == imps
    assert sum(int(x["clicks"]) for x in rows) == clicks
    assert sum(float(x["spend"]) for x in rows) * FX == pytest.approx(spend, rel=1e-3)  # USD, rounded to cents
    assert sum(int(a["value"]) for x in rows for a in x.get("actions", [])) == conv
    assert {x["account_currency"] for x in rows} == {"USD"}


def test_meta_campaign_level_has_reach_and_listing_matches_budgets(client):
    tr = json.dumps({"since": LAST, "until": LAST})
    r = client.get(f"/meta/v25.0/act_{META_AD_ACCOUNT_ID}/insights",
                   params={"level": "campaign", "time_range": tr, "fields": "campaign_id,reach,frequency,impressions"})
    data = r.json()["data"]
    assert data and all(int(x["reach"]) <= int(x["impressions"]) for x in data)
    bad = client.get(f"/meta/v25.0/act_{META_AD_ACCOUNT_ID}/insights",
                     params={"level": "ad", "time_range": tr, "fields": "reach"})
    assert bad.status_code == 400  # reach is not reported below campaign level (never summed)
    listing = client.get(f"/meta/v25.0/act_{META_AD_ACCOUNT_ID}/campaigns",
                         params={"fields": "id,name,status,daily_budget", "limit": 100}).json()["data"]
    budgets = dict(facts(client, "SELECT budget_id, amount FROM budgets_state WHERE platform = 'meta'"))
    assert len(listing) == len(budgets)
    for c in listing:
        assert c["status"] == "ACTIVE" and int(c["daily_budget"]) == round(budgets[c["id"]] / FX * 100)
    assert client.get("/meta/v25.0/act_999/insights").status_code == 400


# ---- Google -------------------------------------------------------------------------------------------------
def test_google_gaql_reconciles_across_levels(client):
    q = ("SELECT campaign.id, segments.date, metrics.impressions, metrics.cost_micros, metrics.conversions "
         f"FROM campaign WHERE segments.date BETWEEN '{FIRST_WEEK[0]}' AND '{FIRST_WEEK[1]}'")
    camp = gaql(client, q)["results"]
    ads = gaql(client, "SELECT ad_group_ad.ad.id, metrics.impressions, metrics.cost_micros FROM ad_group_ad "
                       f"WHERE segments.date BETWEEN '{FIRST_WEEK[0]}' AND '{FIRST_WEEK[1]}'")["results"]
    imps, spend, conv = facts(client, """SELECT sum(impressions), sum(spend_inr), sum(conversions)
        FROM fact_ad_creative_daily WHERE platform = 'google' AND day BETWEEN -7 AND -1""")[0]
    assert sum(int(r["metrics"]["impressions"]) for r in camp) == imps
    assert sum(int(r["metrics"]["impressions"]) for r in ads) == imps  # ad-level totals == campaign-level totals
    assert sum(int(r["metrics"]["costMicros"]) for r in camp) / 1e6 == pytest.approx(spend, rel=1e-6)
    assert sum(r["metrics"]["conversions"] for r in camp) == pytest.approx(conv)
    assert {r["segments"]["date"] for r in camp} <= {f"2026-09-{d}" for d in range(24, 31)}
    assert isinstance(camp[0]["metrics"]["impressions"], str)  # int64 as JSON string


def test_google_entities_customer_and_shared_budget(client):
    cust = gaql(client, "SELECT customer.currency_code, customer.time_zone FROM customer")["results"][0]
    assert cust["customer"] == {"currencyCode": "INR", "timeZone": "Asia/Kolkata"}
    camps = gaql(client, "SELECT campaign.id, campaign.advertising_channel_type, campaign.campaign_budget "
                         "FROM campaign")["results"]
    assert {c["campaign"]["advertisingChannelType"] for c in camps} == {"SEARCH", "VIDEO"}
    budgets = gaql(client, "SELECT campaign_budget.id, campaign_budget.explicitly_shared FROM campaign_budget")
    assert sum(1 for b in budgets["results"] if b["campaignBudget"]["explicitlyShared"]) == 1
    one = gaql(client, f"SELECT campaign.id, metrics.clicks FROM campaign WHERE campaign.id = "
                       f"{camps[0]['campaign']['id']} AND segments.date = '{LAST}'")["results"]
    assert len(one) == 1 and one[0]["campaign"]["id"] == camps[0]["campaign"]["id"]


# ---- store / finance ---------------------------------------------------------------------------------------------
def test_store_orders_page_by_since_id_and_reconcile(client):
    orders, since = [], 0
    while True:
        page = client.get("/store/admin/api/2025-07/orders.json",
                          params={"created_at_min": LAST, "created_at_max": LAST, "since_id": since,
                                  "limit": 250}).json()["orders"]
        if not page:
            break
        orders += page
        since = page[-1]["id"]
    n, revenue = facts(client, "SELECT count(*), sum(unit_price_inr) FROM fact_orders WHERE day = -1")[0]
    assert len(orders) == n
    assert sum(float(o["subtotal_price"]) for o in orders) == pytest.approx(revenue, abs=0.01 * n)
    o = orders[0]
    assert float(o["total_tax"]) == pytest.approx(round(float(o["subtotal_price"]) * STORE_GST_RATE, 2))
    assert float(o["total_price"]) == pytest.approx(float(o["subtotal_price"]) + float(o["total_tax"]))
    assert o["created_at"].startswith(LAST) and o["created_at"].endswith("+05:30")
    paid = [o for o in orders if "utm_campaign=" in o["landing_site"]]
    paid_facts = facts(client, "SELECT count(*) FROM fact_orders WHERE day = -1 AND campaign_id IS NOT NULL")[0][0]
    assert len(paid) == paid_facts


def test_refunds_are_separate_records_on_their_return_day(client):
    r = client.get("/store/admin/api/2025-07/refunds.json",
                   params={"created_at_min": FIRST_WEEK[0], "created_at_max": FIRST_WEEK[1], "limit": 250}).json()
    n = facts(client, "SELECT count(*) FROM fact_orders WHERE returned AND return_day BETWEEN -7 AND -1")[0][0]
    assert len(r["refunds"]) == min(n, 250) and n > 0
    assert all(x["created_at"][:10] >= FIRST_WEEK[0] for x in r["refunds"])


def test_finance_master_data(client):
    econ = client.get("/finance/v1/sku_economics").json()["sku_economics"]
    assert len(econ) == 7  # 6 fixture SKUs + the assorted warehouse line
    hist = client.get("/finance/v1/price_history").json()["price_history"]
    assert len(hist) == 6 and all(h["valid_to"] is None for h in hist)
    products = client.get("/store/admin/api/2025-07/products.json").json()["products"]
    assert {v["sku"] for p in products for v in p["variants"]} >= {e["sku"] for e in econ}


# ---- GA4 / ERP -----------------------------------------------------------------------------------------------
def test_ga4_run_report_reconciles(client):
    body = {"dateRanges": [{"startDate": FIRST_WEEK[0], "endDate": "yesterday"}],
            "dimensions": [{"name": "sessionCampaignId"}],
            "metrics": [{"name": "sessions"}, {"name": "ecommercePurchases"}]}
    r = client.post(f"/ga4/v1beta/properties/{GA4_PROPERTY_ID}:runReport", json=body).json()
    sessions, purchases = facts(client, "SELECT sum(sessions), sum(purchases) FROM fact_ga_daily "
                                        "WHERE day BETWEEN -7 AND -1")[0]
    assert sum(int(row["metricValues"][0]["value"]) for row in r["rows"]) == sessions
    assert sum(int(row["metricValues"][1]["value"]) for row in r["rows"]) == purchases
    assert {"(not set)"} <= {row["dimensionValues"][0]["value"] for row in r["rows"]}
    assert r["metadata"]["currencyCode"] == "INR"
    assert client.post("/ga4/v1beta/properties/1:runReport", json=body).status_code == 403


def test_erp_snapshot_and_receipts(client):
    snap = client.get("/erp/v1/stock").json()
    assert snap["as_of"] == LAST
    on_hand = dict(facts(client, "SELECT sku, on_hand FROM fact_erp_daily WHERE day = -1"))
    assert {i["sku"]: i["on_hand"] for i in snap["items"]} == on_hand
    assert client.get("/erp/v1/stock", params={"date": "2026-10-01"}).status_code == 404  # day 0 not complete
    rec = client.get("/erp/v1/receipts", params={"from": "2026-09-01", "to": LAST}).json()["receipts"]
    total = facts(client, "SELECT sum(receipts) FROM fact_erp_daily WHERE day BETWEEN -30 AND -1")[0][0]
    assert sum(x["quantity"] for x in rec) == total  # 2026-09-01..30 = world days -30..-1
    assert all("2026-09-01" <= x["date"] <= LAST for x in rec)


# ---- clock, reset, truth -----------------------------------------------------------------------------------------
def test_only_complete_days_are_reported_and_advance_reveals_the_next(client):
    q = "SELECT segments.date, metrics.impressions FROM campaign WHERE segments.date = '2026-10-01'"
    assert gaql(client, q)["results"] == []
    r = client.post("/control/advance", json={"days": 1}, headers={"X-Request-ID": "adv1"})
    assert r.status_code == 200
    assert gaql(client, q)["results"]


def test_reset_restores_the_post_history_baseline(client):
    before = client.app.state.store.semantic_state_hash()
    client.post("/control/advance", json={"days": 2}, headers={"X-Request-ID": "adv2"})
    assert client.get("/health").json()["day"] == 2
    r = client.post("/control/reset", json={"seed": 42}, headers={"X-Request-ID": "reset1"})
    assert r.status_code == 200 and r.json()["result"] == {"seed": 42, "day": 0, "restored": "baseline"}
    assert client.app.state.store.semantic_state_hash() == before
    assert client.post("/control/reset", json={"seed": 7}, headers={"X-Request-ID": "r2"}).status_code == 409


def test_truth_loads_only_if_the_fingerprint_matches(world_copy):
    truth = load_truth(world_copy / "sim_truth.duckdb")
    assert truth.config.seed == 42
    con = duckdb.connect(str(world_copy / "sim_truth.duckdb"))
    con.execute("UPDATE meta SET value = '\"tampered\"' WHERE key = 'fingerprint'")
    con.close()
    with pytest.raises(TruthMismatch):
        load_truth(world_copy / "sim_truth.duckdb")


def test_truth_is_never_served(client):
    for path in ("/truth", "/truth/gt_incidents", "/control/truth", "/meta/v25.0/act_1029384756/truth"):
        assert client.get(path).status_code in (400, 404)
