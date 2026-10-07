"""Stage 2 SIMULATED channels (spec §1): opt-in, base ids and truth unchanged when off; TikTok runs through the same
conditional funnel; Amazon has a separate marketplace order population (Sponsored Products + organic) that never
reaches the web store or GA4; native-format TikTok / Amazon reporting; absolute-setter budget mutations with faults."""

import json

from fastapi.testclient import TestClient

from world.catalog import build_catalog
from world.main import create_app
from world.truth import load_truth


def test_opt_in_keeps_the_base_catalog_identical(backbone_dir):
    base = build_catalog(backbone_dir)
    ext = build_catalog(backbone_dir, extra_channels=("tiktok", "amazon_sp"))
    assert ext.campaigns[:len(base.campaigns)] == base.campaigns and ext.creatives[:len(base.creatives)] == \
        base.creatives
    extra = ext.campaigns[len(base.campaigns):]
    assert {c.platform for c in extra} == {"tiktok", "amazon"} and all(c.budget_id == c.campaign_id for c in extra)
    assert len(extra) == 2 * len(base.categories)


def test_extra_channel_truth_and_history(seeded_world_channels):
    truth = load_truth(seeded_world_channels / "sim_truth.duckdb")
    assert truth.config.extra_channels == ("tiktok", "amazon_sp")
    assert {"paid_tiktok", "paid_amazon_sp", "unpaid_amazon_organic"} <= set(truth.category_rates.columns)
    with TestClient(create_app(world_dir=seeded_world_channels)) as c:
        store = c.app.state.store
        by_channel = dict(store.read("SELECT channel, count(*) FROM fact_orders WHERE day < 0 GROUP BY 1"))
        assert by_channel["tiktok"] > 0 and by_channel["amazon_sp"] > 0 and by_channel["amazon_organic"] > 0
        # funnel invariants hold for the new channels too
        bad = store.read("""SELECT count(*) FROM fact_ad_campaign_daily d JOIN (SELECT DISTINCT campaign_id FROM
                            fact_ad_creative_daily WHERE platform IN ('tiktok', 'amazon')) x USING (campaign_id)
                            WHERE sessions > clicks OR purchases > sessions""")[0][0]
        assert bad == 0
        # the marketplace never reaches the web store or GA4
        web = c.get("/store/admin/api/2025-07/orders.json", params={"limit": 250}).json()["orders"]
        assert all("amazon" not in (o["landing_site"] or "") for o in web)
        ga = store.read("SELECT count(*) FROM fact_ga_daily WHERE source = 'amazon'")[0][0]
        assert ga == 0


def test_tiktok_and_amazon_reporting_and_mutations(seeded_world_channels):
    with TestClient(create_app(world_dir=seeded_world_channels)) as c:
        rep = c.get("/tiktok/v1.3/report/integrated/get/", params={
            "advertiser_id": "7012345678901234567", "start_date": "2026-09-01", "end_date": "2026-09-30"}).json()
        assert rep["code"] == 0 and rep["data"]["list"]
        row = rep["data"]["list"][0]
        assert row["dimensions"]["stat_time_day"].endswith("00:00:00") and float(row["metrics"]["spend"]) > 0
        camps = c.get("/tiktok/v1.3/campaign/get/", params={"advertiser_id": "7012345678901234567"}).json()
        tk = camps["data"]["list"][0]
        r = c.post("/tiktok/v1.3/campaign/update/", json={"advertiser_id": "7012345678901234567",
                                                          "campaign_id": tk["campaign_id"], "budget": 12.5},
                   headers={"X-Request-ID": "tk-1"})
        assert r.status_code == 200 and r.json()["code"] == 0
        back = c.get("/tiktok/v1.3/campaign/get/", params={"advertiser_id": "7012345678901234567"}).json()
        assert next(x for x in back["data"]["list"] if x["campaign_id"] == tk["campaign_id"])["budget"] == 12.5

        sp = c.get("/amazon/v3/reports/spCampaigns", params={"startDate": "2026-09-01", "endDate": "2026-09-30"})
        assert sp.json()["rows"] and sp.json()["currencyCode"] == "INR"
        pp = c.get("/amazon/v3/reports/spPurchasedProduct", params={"startDate": "2026-09-01",
                                                                    "endDate": "2026-09-30"}).json()["rows"]
        assert pp and all(x["purchasedAsin"] for x in pp)
        orders = c.get("/amazon/orders/v0/orders", params={"CreatedAfter": "2026-09-25",
                                                           "CreatedBefore": "2026-09-30"}).json()["payload"]
        assert orders["Orders"] and orders["Orders"][0]["SalesChannel"] == "Amazon.in"
        cid = c.get("/amazon/v3/sp/campaigns").json()["campaigns"][0]["campaignId"]
        c.post("/control/fault", json={"platform": "amazon", "fault": "unavailable"}, headers={"X-Request-ID": "f"})
        r = c.put("/amazon/v3/sp/campaigns", json={"campaigns": [{"campaignId": cid, "budget": {"budget": 777.0}}]},
                  headers={"X-Request-ID": "am-1"})
        assert r.status_code == 503
        r = c.put("/amazon/v3/sp/campaigns", json={"campaigns": [{"campaignId": cid, "budget": {"budget": 777.0}}]},
                  headers={"X-Request-ID": "am-2"})
        assert r.status_code == 200 and r.json()["campaigns"]["success"]
        got = c.get("/amazon/v3/sp/campaigns", params={"campaignIdFilter": cid}).json()["campaigns"][0]
        assert got["budget"]["budget"] == 777.0
        assert json.dumps(c.get("/health").json())
