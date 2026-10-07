"""A4 scenarios: each injects exactly its intended effect. Because all randomness is keyed (common random numbers),
a scenario fork is compared against a control fork of the same world: everything the scenario does not touch is
identical, and what it touches moves by the injected amount. GT incidents follow the §16 onset rule."""

import json
import shutil

import pytest

from world.state import WorldStateConflict
from world.step import make_store
from world.truth import load_truth


@pytest.fixture(scope="module")
def truth(seeded_world_dir):
    return load_truth(seeded_world_dir / "sim_truth.duckdb")


@pytest.fixture
def forks(seeded_world_dir, truth, tmp_path):
    stores = []
    for name in ("control", "scenario"):
        d = tmp_path / name
        shutil.copytree(seeded_world_dir, d)
        stores.append(make_store(d / "sim_state.duckdb", truth))
    yield stores
    for s in stores:
        s.close()


def run(store, days, tag):
    store.commit("advance", f"{tag}:adv", "test", {"days": days})


def activate(store, key, params=None, start_day=None):
    payload = {"key": key, "params": params or {}}
    if start_day is not None:
        payload["start_day"] = start_day
    return store.commit("activate_scenario", f"act:{key}", "test", payload).result


def one(store, sql, params=None):
    return store.read(sql, params)[0][0]


def gt_rows(store):
    rows = store.read("SELECT scenario, driver, entity_ids, injection_start_day, onset_day, injection_end_day, "
                      "magnitude FROM gt_incidents ORDER BY incident_id")
    return [(s, d, json.loads(e), a, o, b, m) for s, d, e, a, o, b, m in rows]


def test_s1_meta_cpm_up_45_percent_google_untouched(forks):
    control, scen = forks
    activate(scen, "S1")
    for s, tag in ((control, "c"), (scen, "s")):
        run(s, 7, tag)
    q = "SELECT campaign_id, day, cpm_inr FROM fact_ad_campaign_daily WHERE day >= 0 AND spend_inr > 0 AND platform = ?"
    for platform, expected in (("meta", 1.45), ("google", 1.0)):
        c = {(r[0], r[1]): r[2] for r in control.read(q, [platform])}
        s = {(r[0], r[1]): r[2] for r in scen.read(q, [platform])}
        ratios = [s[k] / c[k] for k in c if k[1] <= 5]
        assert ratios and all(r == pytest.approx(expected, rel=1e-9) for r in ratios)
        after = [s[k] / c[k] for k in c if k[1] == 6]  # 6-day window has ended
        assert all(r == pytest.approx(1.0, rel=1e-9) for r in after)
    (row,) = gt_rows(scen)
    assert row[:2] == ("S1", "auction") and (row[3], row[4], row[5]) == (0, 0, 5) and row[6] == 0.45


def test_s2_creative_ctr_decays_and_frequency_rises(forks):
    control, scen = forks
    res = activate(scen, "S2")
    for s, tag in ((control, "c"), (scen, "s")):
        run(s, 12, tag)
    p = json.loads(one(scen, "SELECT params FROM scenario_activation"))
    camp, creative = p["campaign_id"], p["creative_id"]
    q = "SELECT sum(clicks)::DOUBLE / sum(impressions) FROM fact_ad_creative_daily WHERE creative_id = ? AND day = ?"
    ctr_c, ctr_s = one(control, q, [creative, 9]), one(scen, q, [creative, 9])
    assert ctr_s / ctr_c == pytest.approx(0.6 * 1.0, abs=0.12)  # -40% at full ramp, frequency effect on top
    fq = "SELECT impressions::DOUBLE / reach FROM fact_ad_campaign_daily WHERE campaign_id = ? AND day = 9"
    assert one(scen, fq, [camp]) > one(control, fq, [camp]) * 1.15
    gt = gt_rows(scen)[0]
    assert gt[1] == "creative_fatigue" and gt[4] == res["start_day"] + 4  # 50% of a 10-day ramp reached on day 5
    other = "SELECT sum(impressions) FROM fact_ad_campaign_daily WHERE campaign_id <> ? AND day >= 0"
    assert one(scen, other, [camp]) == one(control, other, [camp])  # nothing else moved


def test_s3_stockout_loses_demand_and_blocks_reorders(forks, truth):
    control, scen = forks
    activate(scen, "S3")
    for s, tag in ((control, "c"), (scen, "s")):
        run(s, 9, tag)
    p = json.loads(one(scen, "SELECT params FROM scenario_activation"))
    sku = p["sku"]
    assert one(scen, "SELECT count(*) FROM fact_orders WHERE sku = ? AND day BETWEEN 0 AND 6", [sku]) == 0
    assert one(control, "SELECT count(*) FROM fact_orders WHERE sku = ? AND day BETWEEN 0 AND 6", [sku]) > 0
    assert one(scen, "SELECT sum(units) FROM fact_lost_demand WHERE sku = ? AND day BETWEEN 0 AND 6", [sku]) > 0
    assert one(scen, "SELECT max(on_hand) FROM fact_erp_daily WHERE sku = ? AND day BETWEEN 0 AND 6", [sku]) == 0
    # the block ends on day 6, so the planner reorders on day 7 and the stock arrives after the lead time
    inbound_day = one(scen, "SELECT inbound_day FROM fact_erp_daily WHERE sku = ? AND day = 7", [sku])
    assert inbound_day == 7 + truth.sku_lead_time[sku]
    clicks = "SELECT sum(clicks) FROM fact_ad_campaign_daily WHERE day BETWEEN 0 AND 6"
    assert one(scen, clicks) == one(control, clicks)  # campaigns keep spending and getting clicks


def test_s4_price_up_18_percent_then_restored(forks, truth):
    control, scen = forks
    activate(scen, "S4")
    for s, tag in ((control, "c"), (scen, "s")):
        run(s, 15, tag)
    cat = json.loads(one(scen, "SELECT params FROM scenario_activation"))["category_code"]
    skus = {s.sku: s.unit_price_inr for s in truth.catalog.skus_of(cat)}
    prices = scen.read("SELECT sku, day, unit_price_inr FROM fact_orders WHERE day >= 0 AND sku IN (SELECT unnest(?))",
                       [list(skus)])
    assert prices and all(p == pytest.approx(round(skus[s] * 1.18, 2) if d <= 13 else skus[s])
                          for s, d, p in prices)
    units = "SELECT count(*) FROM fact_orders WHERE category_code = ? AND day BETWEEN 0 AND 13"
    assert one(scen, units, [cat]) < one(control, units, [cat])  # negative elasticity
    hist = scen.read("SELECT effective_from_day FROM price_history WHERE sku = ? ORDER BY 1", [next(iter(skus))])
    assert [h[0] for h in hist][-2:] == [0, 14]


def test_s5_ga_loses_60_percent_of_google_sessions_orders_unchanged(forks):
    control, scen = forks
    activate(scen, "S5")
    for s, tag in ((control, "c"), (scen, "s")):
        run(s, 7, tag)
    ga = ("SELECT sum(sessions) FROM fact_ga_daily WHERE day >= 0 AND source IN ('google', 'youtube') "
          "AND campaign_id IS NOT NULL")
    assert one(scen, ga) / one(control, ga) == pytest.approx(0.40, abs=0.05)
    meta = "SELECT sum(sessions) FROM fact_ga_daily WHERE day >= 0 AND source = 'facebook'"
    assert one(scen, meta) == one(control, meta)
    orders = "SELECT count(*), sum(unit_price_inr) FROM fact_orders WHERE day >= 0"
    assert scen.read(orders) == control.read(orders)  # a measurement break, not a business change
    clicks = "SELECT sum(clicks) FROM fact_ad_campaign_daily WHERE day >= 0"
    assert one(scen, clicks) == one(control, clicks)


def test_s7_human_budget_cut_is_a_budget_change(forks):
    control, scen = forks
    activate(scen, "S7")
    p = json.loads(one(scen, "SELECT params FROM scenario_activation"))
    for s, tag in ((control, "c"), (scen, "s")):
        run(s, 3, tag)
    assert one(scen, "SELECT amount FROM budgets_state WHERE budget_id = ?", [p["budget_id"]]) == p["amount"]
    gt = gt_rows(scen)[0]
    assert gt[1] == "budget_change"
    q = "SELECT sum(spend_inr) FROM fact_ad_campaign_daily WHERE day >= 0 AND campaign_id = ?"
    ratio = one(scen, q, [gt[2][0]]) / one(control, q, [gt[2][0]])
    assert ratio == pytest.approx(0.6, abs=0.02)  # spend follows the human's 40% cut


def test_demo_01_story(forks, truth):
    control, scen = forks
    activate(scen, "DEMO_01")
    for s, tag in ((control, "c"), (scen, "s")):
        run(s, 10, tag)
    p = json.loads(one(scen, "SELECT params FROM scenario_activation"))
    hero = p["hero_sku"]
    cover = one(scen, "SELECT on_hand FROM fact_erp_daily WHERE sku = ? AND day = 9", [hero])
    daily = one(control, "SELECT avg(units_sold) FROM fact_erp_daily WHERE sku = ? AND day BETWEEN 0 AND 9", [hero])
    assert cover / daily < 7  # LOW / CRITICAL cover by D-1
    g = "SELECT count(*) FROM fact_orders WHERE category_code = ? AND campaign_id IS NOT NULL AND day BETWEEN 5 AND 9"
    assert one(scen, g, [p["google_category_code"]]) > one(control, g, [p["google_category_code"]])  # demand surge
    drivers = [r[1] for r in gt_rows(scen)]
    assert drivers == ["creative_fatigue", "demand"]


def test_activation_rules(forks):
    _, scen = forks
    with pytest.raises(WorldStateConflict):
        activate(scen, "S1", start_day=-5)  # no scenarios in the past
    with pytest.raises(ValueError):
        scen.commit("activate_scenario", "bad", "test", {"key": "S99"})
    future = activate(scen, "S1", start_day=3)
    assert future["start_day"] == 3 and future["end_day"] == 8


def test_scheduled_scenario_during_seeding_and_replay(seeded_world_dir, truth, tmp_path, backbone_dir,
                                                       global_ads_csv):
    from datetime import date

    from world.seed import seed_world
    from world.state import replay
    from world.truth import WorldConfig

    cfg = WorldConfig(seed=42, backbone_dir=backbone_dir, global_ads_csv=global_ads_csv,
                      history_end_date=date(2026, 9, 30))
    store, _ = seed_world(cfg, tmp_path / "demo", scenarios=[("DEMO_01", -10, {})])
    assert store.clock()[1] == 0
    assert one(store, "SELECT start_day FROM scenario_activation") == -10
    fresh = make_store(tmp_path / "replayed.duckdb", truth)
    replay(store.log(), fresh)
    assert fresh.semantic_state_hash() == store.semantic_state_hash()
    store.close()
    fresh.close()


# ---- Stage 2 scenarios -------------------------------------------------------------------------------------------
def test_s6_category_demand_surge(forks):
    control, scen = forks
    activate(scen, "S6")
    for s, tag in ((control, "c"), (scen, "s")):
        run(s, 10, tag)
    cat = json.loads(one(scen, "SELECT params FROM scenario_activation"))["category_code"]
    unpaid = "SELECT count(*) FROM fact_orders WHERE category_code = ? AND campaign_id IS NULL " \
             "AND day BETWEEN 3 AND 9"
    assert one(scen, unpaid, [cat]) / one(control, unpaid, [cat]) == pytest.approx(1.40, abs=0.15)
    paid = "SELECT count(*) FROM fact_orders WHERE category_code = ? AND campaign_id IS NOT NULL " \
           "AND day BETWEEN 3 AND 9"
    assert one(scen, paid, [cat]) > one(control, paid, [cat])  # p_buy ~ demand^0.5: paid CVR up too
    other = "SELECT count(*) FROM fact_orders WHERE category_code <> ? AND day >= 0"
    assert one(scen, other, [cat]) == one(control, other, [cat])
    (row,) = gt_rows(scen)
    assert row[:2] == ("S6", "demand") and row[4] == 1 and row[6] == 0.40  # 3-day ramp: 50% on day 2 (index 1)


def test_s8_retargeting_frequency_rises_reach_flat_ctr_down_broadly(forks):
    control, scen = forks
    activate(scen, "S8")
    for s, tag in ((control, "c"), (scen, "s")):
        run(s, 10, tag)
    p = json.loads(one(scen, "SELECT params FROM scenario_activation"))
    camp = p["campaign_id"]
    q = "SELECT sum(impressions)::DOUBLE / sum(reach), avg(reach) FROM fact_ad_campaign_daily WHERE campaign_id = ? " \
        "AND day BETWEEN 7 AND 9"
    (f_s, r_s), (f_c, r_c) = scen.read(q, [camp])[0], control.read(q, [camp])[0]
    assert f_s == pytest.approx(p["target_frequency"], rel=0.15) and f_s > 1.8 * f_c
    assert r_s <= r_c * 1.05  # reach flat (it falls: the same budget reaches a smaller audience)
    cq = "SELECT creative_id, sum(clicks)::DOUBLE / sum(impressions) FROM fact_ad_creative_daily " \
         "WHERE campaign_id = ? AND day BETWEEN 7 AND 9 GROUP BY 1"
    ctr_c, ctr_s = dict(control.read(cq, [camp])), dict(scen.read(cq, [camp]))
    assert all(ctr_s[k] < ctr_c[k] for k in ctr_c)  # broad, not creative-specific
    other = "SELECT sum(impressions) FROM fact_ad_campaign_daily WHERE campaign_id <> ? AND day >= 0"
    assert one(scen, other, [camp]) == one(control, other, [camp])
    assert gt_rows(scen)[0][1] == "audience_saturation"


def test_s10_holiday_spike_then_back(forks):
    control, scen = forks
    activate(scen, "S10")
    for s, tag in ((control, "c"), (scen, "s")):
        run(s, 4, tag)
    unpaid = "SELECT count(*) FROM fact_orders WHERE campaign_id IS NULL AND sku IS NOT NULL AND day BETWEEN ? AND ?"
    assert one(scen, unpaid, [0, 1]) / one(control, unpaid, [0, 1]) == pytest.approx(1.6, abs=0.15)
    assert one(scen, "SELECT count(*) FROM fact_orders WHERE campaign_id IS NULL AND sku IS NOT NULL AND day = 3") \
        == pytest.approx(one(control, "SELECT count(*) FROM fact_orders WHERE campaign_id IS NULL "
                                      "AND sku IS NOT NULL AND day = 3"), rel=0.15)


def test_s11_excess_inventory(forks, truth):
    _, scen = forks
    activate(scen, "S11")
    run(scen, 1, "s")
    p = json.loads(one(scen, "SELECT params FROM scenario_activation"))
    for sku, stock in p["on_hand"].items():
        assert one(scen, "SELECT on_hand FROM fact_erp_daily WHERE sku = ? AND day = 0", [sku]) >= stock * 0.95
    assert gt_rows(scen)[0][1] == "excess_inventory"


def test_s12_fatigue_and_demand_and_the_counterfactual_forks(forks):
    control, scen = forks
    activate(scen, "S12")
    for s, tag in ((control, "c"), (scen, "s")):
        run(s, 12, tag)
    p = json.loads(one(scen, "SELECT params FROM scenario_activation"))
    assert [r[1] for r in gt_rows(scen)] == ["creative_fatigue", "demand"]
    q = "SELECT sum(clicks)::DOUBLE / sum(impressions) FROM fact_ad_creative_daily WHERE creative_id = ? AND day >= 9"
    assert one(scen, q, [p["creative_id"]]) < 0.75 * one(control, q, [p["creative_id"]])
    unpaid = "SELECT count(*) FROM fact_orders WHERE category_code = ? AND campaign_id IS NULL AND day >= 9"
    assert one(scen, unpaid, [p["category_code"]]) > 1.1 * one(control, unpaid, [p["category_code"]])


def test_s12_without_a_driver_removes_exactly_that_driver(seeded_world_dir, truth, tmp_path):
    stores = {}
    for name in ("full", "no_fatigue"):
        d = tmp_path / name
        shutil.copytree(seeded_world_dir, d)
        stores[name] = make_store(d / "sim_state.duckdb", truth)
    stores["full"].commit("activate_scenario", "a", "t", {"key": "S12", "params": {}})
    stores["no_fatigue"].commit("activate_scenario", "a", "t", {"key": "S12", "params": {"without": "fatigue"}})
    for name, s in stores.items():
        run(s, 12, name)
    assert [r[1] for r in gt_rows(stores["no_fatigue"])] == ["demand"]
    p = json.loads(one(stores["full"], "SELECT params FROM scenario_activation"))
    unpaid = "SELECT count(*) FROM fact_orders WHERE category_code = ? AND campaign_id IS NULL"
    assert one(stores["full"], unpaid, [p["category_code"]]) == one(stores["no_fatigue"], unpaid,
                                                                    [p["category_code"]])
    for s in stores.values():
        s.close()
