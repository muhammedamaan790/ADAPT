"""Stage 2 SIMULATED channels end to end (spec §1, §9.4 mock APIs): TikTok + Amazon Sponsored Products + the Amazon
marketplace flow world -> native-format connectors (USD/INR, UTC label, SP reports, SP-API orders) -> canonical state
(campaigns, budgets, ad metrics, marketplace order lines with sales_channel = amazon, platform-attributed SP sales) ->
data health -> economics units -> one pipeline cycle -> the C5 saga executes TikTok and Amazon legs through their
mock APIs with read-back."""

from datetime import date

import pytest
from tests.test_optimizer import T0

from adapt.decide import decisions as dec
from adapt.economics.state import load_state
from adapt.execute.adapters import build_adapters
from adapt.execute.saga import execute_decision
from adapt.ingest.http import SourceHttp
from adapt.ingest.sync import run_sync
from adapt.pipeline.cycle import run_cycle
from adapt.reconcile.build import build_canonical, logical_now

START = date(2026, 10, 1)


def test_tiktok_and_amazon_through_the_product(world_channels, db):
    http = SourceHttp("http://testserver", client=world_channels, sleep=lambda s: None)
    rep = run_sync(db, http)
    assert all(c["status"] == "OK" for c in rep["connectors"].values()), rep
    assert db.query("SELECT count(*) FROM stg.tiktok_ad_daily")[0][0] > 0
    assert db.query("SELECT DISTINCT currency FROM stg.tiktok_ad_daily") == [("USD",)]
    assert db.query("SELECT count(*) FROM stg.amazon_order_lines")[0][0] > 0
    out = build_canonical(db, logical_now(START))
    health = out["health"]
    assert {"tiktok_ads", "amazon_ads", "amazon_marketplace"} <= set(health)
    assert all(h["status"] != "RED" for s, h in health.items() if s in ("tiktok_ads", "amazon_ads")), health
    channels = dict(db.query("SELECT channel_id, count(*) FROM core.campaigns GROUP BY 1"))
    assert channels["tiktok"] == channels["amazon_sp"] == channels["meta"] // 2
    assert db.query("SELECT count(*) FROM core.budgets WHERE platform IN ('tiktok', 'amazon')")[0][0] == \
        channels["tiktok"] + channels["amazon_sp"]
    sales = dict(db.query("SELECT sales_channel, count(*) FROM core.orders GROUP BY 1"))
    assert sales["amazon"] > 0 and sales["web"] > 0
    assert db.query("SELECT count(*) FROM core.order_items WHERE sales_channel = 'amazon' "
                    "AND campaign_id IS NOT NULL")[0][0] == 0                 # marketplace orders carry no UTM
    tk = db.query("""SELECT sum(attributed_orders) FROM marts.campaign_daily
                     WHERE channel_id = 'tiktok'""")[0][0]
    am = db.query("""SELECT sum(attributed_net_revenue), sum(spend) FROM marts.campaign_daily
                     WHERE channel_id = 'amazon_sp'""")[0]
    assert tk > 0 and am[0] > 0 and am[1] > 0                                  # last-click (TikTok), SP-attributed
    w = db.query("""SELECT campaign_id, sum(attribution_weight) FROM core.campaign_sku cs JOIN core.campaigns c
                    USING (campaign_id) WHERE c.channel_id = 'amazon_sp' GROUP BY 1""")
    assert w and all(abs(x - 1) < 1e-6 for _, x in w)                       # normalisation holds for Amazon
    state = load_state(db, logical_now(START), curves={})
    assert {u.platform for u in state.units} >= {"tiktok", "amazon"}


def test_pipeline_cycle_and_saga_on_the_new_channels(world_channels, db):
    http = SourceHttp("http://testserver", client=world_channels, sleep=lambda s: None)
    ad = build_adapters(world_channels)
    assert {ad["tiktok"].mode, ad["amazon"].mode} == {"MOCK"}
    summary = run_cycle(db, http, logical_now(START), ad, sleep=lambda s: None)
    assert summary["steps"]["optimize"]["status"] == "OK"
    state = load_state(db, logical_now(START))
    tk = next(u for u in state.units if u.platform == "tiktok" and u.budget > 1000)
    am = next(u for u in state.units if u.platform == "amazon" and u.budget > 1000)
    legs = [{"unit_id": u.unit_id, "platform": u.platform, "channel": u.channel, "budget_id": u.unit_id,
             "campaign_ids": u.campaign_ids, "before": u.budget, "after": float(int(u.budget * 0.85 / 100) * 100)}
            for u in (tk, am)]
    exp = {"E": 100.0, "P10": 50.0, "P50": 100.0, "P90": 150.0, "prob_loss": 0.1, "delta_net_revenue": 200.0,
           "raw_pred": 100.0}
    at = logical_now(START)
    run = {"run_id": "ch", "calibration_factor": 0.9, "safety": [], "result": {
        "status": "OK", "decision_id": "ch:R", "legs": legs, "expected": exp, "why_not": [], "objective": "PROFIT",
        "inventory_risk_after": {"kind": "STOCKOUT_PROBABILITY", "by_sku": {}}, "unallocated": 0.0,
        "reserve_floor": 0.0}}
    assert dec.create_decisions(db, run, state, {}, at) == ["ch:R"]
    d = dec.get_decision(db, "ch:R")
    assert d["status"] == "PENDING_APPROVAL", [c for c in d["checks"] if not c["passed"]]
    dec.approve(db, "ch:R", d["decision_hash"], "maria", "manager", at, load_state(db, at))
    out = execute_decision(db, "ch:R", ad, "maria", at, load_state(db, at), sleep=lambda s: None)
    assert out["state"] == "SUCCEEDED" and {leg["platform"] for leg in out["legs"]} == {"tiktok", "amazon"}
    for leg in legs:
        assert ad[leg["platform"]].read(leg["budget_id"])["amount_inr"] == pytest.approx(leg["after"], abs=1.0)
    assert T0  # (shared fixtures module import)
