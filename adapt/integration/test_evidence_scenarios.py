"""B4 'done when' (integration, fixture world): on each scenario's true incident (spec §16: diagnosis accuracy is
scored on true positives), the injected cause is the top-1 driver with STRONG/WEAK evidence:
S1 -> auction, S2 -> fatigue, S3 -> inventory, S4 -> price, S5 -> tracking."""

import json
from datetime import date, timedelta

import pytest

from adapt.diagnose.evidence import Incident
from adapt.diagnose.run import diagnose_incident
from adapt.ingest.sync import run_sync
from adapt.reconcile.build import build_canonical, logical_now

START = date(2026, 10, 1)  # fixture world day 0


def run_scenario(world, http, db, key: str, days: int) -> dict:
    run_sync(db, http)
    r = world.post("/control/scenario", json={"key": key}, headers={"X-Request-ID": f"scn-{key}"})
    assert r.status_code == 200, r.text
    world.post("/control/advance", json={"days": days}, headers={"X-Request-ID": "adv"})
    run_sync(db, http)
    build_canonical(db, logical_now(START + timedelta(days=days)))
    return json.loads(world.app.state.store.read("SELECT params FROM scenario_activation")[0][0])


def campaign_of(db, channel: str, category_label_contains: str) -> str:
    return db.query("SELECT campaign_id FROM core.campaigns WHERE channel_id = ? AND product_set LIKE ?",
                    [channel, f"%{category_label_contains}%"])[0][0]


def check(db, inc: Incident, expected: str) -> dict:
    d = diagnose_incident(db, inc)
    r = d["ranking"]
    assert r["top_driver"] == expected, json.dumps(r, indent=1, default=str)
    assert r["top_level"] in ("STRONG_EVIDENCE", "WEAK_EVIDENCE")
    return {e.module: e for e in d["evidence"]}


def test_s1_auction(world, http, db):
    run_scenario(world, http, db, "S1", 4)
    meta = [c for (c,) in db.query("SELECT campaign_id FROM core.campaigns WHERE platform = 'meta'")]
    ev = check(db, Incident("t", "meta", meta, None, "CPM", START, START + timedelta(days=3)), "auction")
    assert ev["auction"].values["median_cpm_change_pct"] == pytest.approx(45.0, abs=5.0)  # injected +45%


def test_s2_creative_fatigue(world_large, db):
    """Runs on the 6x world: in the base fixture each creative gets ~700 impressions a day, below the module's
    minimum sample (>= 5,000 impressions and 100 clicks per 7 days), where it correctly says INSUFFICIENT_DATA."""
    from adapt.ingest.http import SourceHttp

    world, http = world_large, SourceHttp("http://testserver", client=world_large, sleep=lambda s: None)
    p = run_scenario(world, http, db, "S2", 12)
    inc = Incident("t", "meta", [p["campaign_id"]], p["creative_id"], "CTR", START + timedelta(days=5),
                   START + timedelta(days=11))
    ev = check(db, inc, "fatigue")
    assert ev["fatigue"].values["top_creative"] == p["creative_id"]


def test_s3_inventory(world, http, db):
    p = run_scenario(world, http, db, "S3", 5)
    camps = [c for (c,) in db.query("SELECT campaign_id FROM core.campaign_sku WHERE sku = ? "
                                    "AND attribution_weight > 0.05", [p["sku"]])]
    google = [c for c in camps if c.startswith("2000")]
    ev = check(db, Incident("t", "google", google[:1], None, "CVR", START, START + timedelta(days=4)), "inventory")
    assert p["sku"] in ev["inventory"].values["stocked_out_skus"]
    assert ev["inventory"].values["wasted_spend_inr"] > 0


def test_s4_price(world, http, db):
    p = run_scenario(world, http, db, "S4", 7)
    label = db.query("SELECT category FROM core.skus WHERE sku LIKE ? LIMIT 1", [p["category_code"] + "%"])[0][0]
    camp = campaign_of(db, "google_search", label)
    ev = check(db, Incident("t", "google", [camp], None, "CVR", START, START + timedelta(days=6)), "price")
    assert ev["price"].values["price_change_pct"] == pytest.approx(18.0, abs=0.5)  # injected +18%
    assert ev["price"].values["elasticity"] < 0


def test_s5_tracking(world, http, db):
    p = run_scenario(world, http, db, "S5", 4)
    camp = db.query("SELECT campaign_id FROM core.campaigns WHERE channel_id = ? ORDER BY campaign_id LIMIT 1",
                    [p["channels"][0]])[0][0]
    ev = check(db, Incident("t", "google", [camp], None, "SESSION_CLICK", START, START + timedelta(days=3)),
               "tracking")
    assert ev["tracking"].values["session_click_drop_pct"] == pytest.approx(60.0, abs=8.0)  # injected -60%


# ---- Stage 2 modules: saturation (S8) and demand (S6, offsetting in S12) ---------------------------------------------
def test_s6_demand(world, http, db):
    p = run_scenario(world, http, db, "S6", 8)
    label = db.query("SELECT category FROM core.skus WHERE sku LIKE ? LIMIT 1", [p["category_code"] + "%"])[0][0]
    camp = campaign_of(db, "google_search", label)
    inc = Incident("t", "google", [camp], None, "CVR", START + timedelta(days=2), START + timedelta(days=7), "UP")
    ev = check(db, inc, "demand")
    assert ev["demand"].values["demand_change_pct"] == pytest.approx(40.0, abs=12.0)  # injected +40%


def test_s8_audience_saturation(world_large, db):
    from adapt.ingest.http import SourceHttp

    world, http = world_large, SourceHttp("http://testserver", client=world_large, sleep=lambda s: None)
    p = run_scenario(world, http, db, "S8", 12)
    inc = Incident("t", "meta", [p["campaign_id"]], None, "CTR", START + timedelta(days=5),
                   START + timedelta(days=11), "DOWN")
    ev = check(db, inc, "saturation")
    assert ev["saturation"].values["frequency_post"] > 1.3 * ev["saturation"].values["frequency_pre"]
    assert ev["fatigue"].score < 0.4  # the decline is broad, not creative-specific


def test_s2_is_not_saturation(world_large, db):
    from adapt.ingest.http import SourceHttp

    world, http = world_large, SourceHttp("http://testserver", client=world_large, sleep=lambda s: None)
    p = run_scenario(world, http, db, "S2", 12)
    inc = Incident("t", "meta", [p["campaign_id"]], p["creative_id"], "CTR", START + timedelta(days=5),
                   START + timedelta(days=11), "DOWN")
    ev = check(db, inc, "fatigue")
    assert ev["saturation"].score < 0.4


def test_demand_offsets_a_decline_it_did_not_cause():
    """Hand-made ranking, ROAS down through CTR with CVR up. (1) unpaid demand rose: it SUPPORTS the CVR lever, and
    because that lever pushed ROAS up while ROAS fell overall, its contribution is offsetting (spec: "ROAS fell but
    unpaid demand rose"). (2) unpaid demand fell while CVR rose: OFFSETTING on its own lever, it claims nothing.
    Neither case is ever top-1."""
    from adapt.diagnose.drivers import rank
    from adapt.diagnose.evidence import Evidence

    funnel = {"status": "OK", "contributions": {"CTR": -0.30, "CVR": 0.08, "AOV": 0.0, "CPM": 0.0}}
    ev = [Evidence("fatigue", "OK", 0.8, ["CTR"]), Evidence("demand", "OK", 0.9, ["CVR"], {"d": 0.2})]
    r = rank(ev, "ROAS", funnel)
    assert r["top_driver"] == "fatigue"
    demand_row = next(x for x in r["drivers"] if x["module"] == "demand")
    assert demand_row["relation"] == "SUPPORTS" and demand_row["offsetting"]  # explains CVR, which offset the drop
    ev[1] = Evidence("demand", "OK", 0.9, ["CVR"], {"d": -0.2})
    r = rank(ev, "ROAS", funnel)
    demand_row = next(x for x in r["drivers"] if x["module"] == "demand")
    assert demand_row["offsetting"] and demand_row["signed_contribution"] == 0.0 and r["top_driver"] == "fatigue"
