"""B2 'done when' (integration, fixture world): injected scenarios are detected and classified through the real path
world -> connectors -> canonical -> detector: S1 Meta auction -> CPM UP incident on Meta only; S5 tracking break ->
tracking_issue; S7 human budget cut -> budget_change, never an efficiency incident; quiet world -> no such signals."""

from datetime import date, timedelta

import pytest

from adapt.detect.detector import run_detection
from adapt.ingest.sync import run_sync
from adapt.reconcile.build import build_canonical, logical_now

DAYS = 4


def live(world, http, db, scenario: str | None) -> list[tuple]:
    """Backfill, (activate), then advance + sync one day at a time, rebuild and detect as of the final day."""
    run_sync(db, http)
    if scenario:
        r = world.post("/control/scenario", json={"key": scenario}, headers={"X-Request-ID": f"scn-{scenario}"})
        assert r.status_code == 200, r.text
    for i in range(DAYS):
        world.post("/control/advance", json={"days": 1}, headers={"X-Request-ID": f"adv-{i}"})
        run_sync(db, http)
    as_of = logical_now(date(2026, 10, 1) + timedelta(days=DAYS))
    build_canonical(db, as_of)
    run_detection(db, as_of, f"det-{scenario}")
    return db.query("""SELECT a.scope, a.entity_key, a.platform, a.metric, a.direction, a.classification,
                              a.is_incident, a.entity_ids FROM intel.anomalies a""")


def platforms_of(db) -> dict[str, str]:
    return dict(db.query("SELECT campaign_id, platform FROM core.campaigns"))


def test_quiet_world_raises_no_auction_tracking_or_budget_signals(world, http, db):
    rows = live(world, http, db, None)
    assert not [r for r in rows if r[3] in ("CPM", "SESSION_CLICK") and r[6]]
    assert not [r for r in rows if r[5] == "budget_change"]


def test_s1_meta_auction_pressure(world, http, db):
    rows = live(world, http, db, "S1")
    cpm = [r for r in rows if r[3] == "CPM" and r[4] == "UP" and r[6]]
    assert cpm, rows
    assert {r[2] for r in cpm} == {"meta"}  # Google untouched
    assert any(r[0] == "platform" for r in cpm)  # >= 70% of Meta campaigns -> one platform incident


def test_s5_tracking_break_is_a_tracking_issue(world, http, db):
    rows = live(world, http, db, "S5")
    tracking = [r for r in rows if r[5] == "tracking_issue"]
    assert tracking and all(r[3] == "SESSION_CLICK" and r[4] == "DOWN" and r[6] for r in tracking)
    platforms = platforms_of(db)
    assert {platforms[r[1]] for r in tracking} == {"google"}


def test_s7_budget_cut_is_classified_not_raised(world, http, db):
    gt = world.post("/control/scenario", json={"key": "S7"}, headers={"X-Request-ID": "peek"})  # target lookup
    assert gt.status_code == 200
    target_budget = world.app.state.store.read("SELECT json_extract_string(params, '$.budget_id') "
                                                "FROM scenario_activation")[0][0]
    rows = live(world, http, db, None)  # S7 already active from the call above (starts today)
    camp = db.query("SELECT campaign_id FROM core.campaigns WHERE budget_id = ?", [target_budget])[0][0]
    on_target = [r for r in rows if r[1] == camp]
    assert any(r[3] == "SPEND" and r[5] == "budget_change" and not r[6] for r in on_target), on_target
    assert not [r for r in on_target if r[6]]  # no efficiency incident on the cut campaign
    assert db.query("SELECT count(*) FROM core.budget_history WHERE entity_id = ? AND source = 'observed_change'",
                    [target_budget])[0][0] >= 1


@pytest.mark.parametrize("scenario", ["S1"])
def test_diagnosis_runs_on_detected_incidents(world, http, db, scenario):
    from adapt.diagnose.run import run_diagnosis

    live(world, http, db, scenario)
    as_of = logical_now(date(2026, 10, 1) + timedelta(days=DAYS))
    out = run_diagnosis(db, as_of)
    assert out["decomposed"] >= 1
    funnel = db.query("""SELECT json_extract_string(d.funnel, '$.status'),
                                json_extract(d.funnel, '$.contributions.CPM')::DOUBLE
                         FROM intel.decompositions d JOIN intel.anomalies a USING (anomaly_id)
                         WHERE a.metric = 'CPM' AND a.platform = 'meta'""")
    assert funnel and funnel[0][0] == "OK" and funnel[0][1] < 0  # higher CPM pushes ROAS down
