"""Stage 3 autonomy evidence (spec §8.4): Wilson bounds, region qualification over warm-up worlds 901-903 with the
held-out reliability world 904 (T38: INCONCLUSIVE never qualifies), one-world dominance, the P(loss) reliability
monitor (T63), the hashed track-record artifact, and the confidence-index components."""

from datetime import datetime

import pytest

from adapt.core.db import Database
from adapt.learn import qualification as q
from adapt.learn.confidence import band_of, region_of, track_record


def rec(world_id, verdict, region="R3", channel="tiktok", prob_loss=0.1, realized=5000.0, did=None, world="SIMULATED"):
    return {"world": world, "world_id": world_id, "decision_id": did or f"d{world_id}-{id(object())}",
            "channel": channel, "region": region, "raw_index": 0.85, "prob_loss": prob_loss, "verdict": verdict,
            "realized": realized, "executed_by": "sim-manager", "guardrail_violation": False}


def pool(per_world: dict[int, tuple[int, int]], region="R3"):
    out = []
    for w, (s, f) in per_world.items():
        out += [rec(w, "SUCCESS", region, did=f"{w}-s{i}") for i in range(s)]
        out += [rec(w, "FAILED", region, realized=-3000.0, did=f"{w}-f{i}") for i in range(f)]
    return out


def test_wilson_matches_the_spec_example():
    lo, hi = q.wilson(7, 8)
    assert lo == pytest.approx(0.529, abs=0.002) and hi == pytest.approx(0.978, abs=0.002)
    assert q.wilson(0, 0) == (None, None)


def test_region_qualifies_with_three_worlds_and_a_reliability_pass():
    recs = pool({901: (15, 1), 902: (14, 2), 903: (15, 1), 904: (14, 2)})
    r = q.qualify(recs, "tiktok", "R3")
    assert r["status"] == "QUALIFIED" and r["wilson_lower"] >= 0.60 and r["worlds"] == 3
    assert r["reliability"]["status"] == "PASS" and r["reliability"]["n"] == 16
    assert q.qualify(recs, "tiktok", "R2")["status"] == "UNQUALIFIED"                # no outcomes in R2
    assert q.qualify(recs, "meta", "R3")["status"] == "UNQUALIFIED"                  # per channel


def test_inconclusive_reliability_never_qualifies_T38():
    recs = pool({901: (15, 1), 902: (14, 2), 903: (15, 1), 904: (10, 0)})            # 10 < N_reliability
    r = q.qualify(recs, "tiktok", "R3")
    assert r["reliability"]["status"] == "INCONCLUSIVE" and r["status"] != "QUALIFIED"


def test_reliability_fail_one_world_dominance_and_low_bound():
    fail = pool({901: (15, 1), 902: (14, 2), 903: (15, 1), 904: (5, 11)})             # held-out rate outside
    assert q.qualify(fail, "tiktok", "R3")["reliability"]["status"] == "FAIL"
    dom = pool({901: (40, 2), 902: (3, 0), 903: (3, 0), 904: (14, 2)})
    r = q.qualify(dom, "tiktok", "R3")
    assert r["status"] == "UNQUALIFIED" and any("more than half" in x for x in r["reasons"])
    low = pool({901: (6, 4), 902: (6, 4), 903: (6, 4), 904: (10, 6)})
    assert q.qualify(low, "tiktok", "R3")["wilson_lower"] < 0.60
    assert q.qualify(low, "tiktok", "R3")["status"] == "UNQUALIFIED"


def test_real_and_simulated_pools_never_mix():
    recs = [{**r, "world": "REAL"} for r in pool({901: (15, 1), 902: (14, 2), 903: (15, 1), 904: (14, 2)})]
    assert q.qualify(recs, "tiktok", "R3")["n"] == 0


def test_inconclusive_outcomes_are_excluded_and_counted():
    recs = pool({901: (15, 1), 902: (14, 2), 903: (15, 1), 904: (14, 2)}) + [rec(901, "INCONCLUSIVE")] * 5
    r = q.qualify(recs, "tiktok", "R3")
    assert r["n"] == 48 and r["inconclusive"] == 5


def test_ploss_monitor_trips_above_30_percent_with_15_outcomes_T63():
    ok = [rec(901, "SUCCESS", did=f"a{i}") for i in range(11)] + \
         [rec(901, "FAILED", realized=-1.0, did=f"b{i}") for i in range(4)]            # 4/15 = 27%
    assert not q.ploss_monitor(ok, "tiktok")["tripped"]
    bad = ok + [rec(901, "FAILED", realized=-1.0, did="c")]                           # 5/16 = 31%
    assert q.ploss_monitor(bad, "tiktok")["tripped"]
    few = [rec(901, "FAILED", realized=-1.0, did=f"x{i}") for i in range(14)]          # 14 < 15: no verdict yet
    assert not q.ploss_monitor(few, "tiktok")["tripped"]
    high = [rec(901, "FAILED", prob_loss=0.5, realized=-1.0, did=f"h{i}") for i in range(20)]
    assert q.ploss_monitor(high, "tiktok")["n"] == 0                                 # only the [0, 0.2] bin


def test_track_record_artifact_is_hashed_and_tamper_evident(tmp_path):
    art = q.merge_artifacts([{"world_ids": [901], "records": pool({901: (2, 1)})}], "label")
    assert q.verify_artifact(art)
    db = Database(tmp_path / "w.duckdb")
    try:
        out = q.import_track_record(db, art, datetime(2026, 10, 8))
        assert out["records"] == 3 and len(q.imported_records(db)) == 3
        bad = {**art, "records": art["records"][:-1]}
        with pytest.raises(ValueError):
            q.import_track_record(db, bad, datetime(2026, 10, 8))
    finally:
        db.close()


def test_confidence_bands_regions_and_track_record_shrinkage(tmp_path):
    assert (band_of(0.85), band_of(0.7), band_of(0.3)) == ("HIGH", "MEDIUM", "LOW")
    assert (region_of(0.0), region_of(0.6), region_of(0.8), region_of(1.0)) == ("R1", "R2", "R3", "R3")
    db = Database(tmp_path / "t.duckdb")
    try:
        v, d = track_record(db)
        assert v == pytest.approx(0.7) and d["n"] == 0                               # the prior without outcomes
        db.write(lambda cur: cur.execute("""CREATE SCHEMA learn; CREATE TABLE learn.outcomes (
            outcome_id VARCHAR, decision_id VARCHAR, class VARCHAR, verdict VARCHAR, realized DOUBLE,
            raw_pred_window DOUBLE, raw_pred DOUBLE, calibrated_pred DOUBLE)"""))
        db.write(lambda cur: cur.execute("""INSERT INTO learn.outcomes VALUES
            ('o1', 'd1', 'OPTIMIZATION', 'SUCCESS', 10000, 10000, 20000, 18000),
            ('o2', 'd2', 'OPTIMIZATION', 'INCONCLUSIVE', 0, 5000, 5000, 4500)"""))
        v, d = track_record(db)
        # pred = 10,000 x 0.9 = 9,000 vs actual 10,000: sMAPE = 1,000 / 9,500
        smape = 1000 / 9500
        assert d["n"] == 1 and d["inconclusive"] == 1
        assert v == pytest.approx((1 * (1 - smape / 2) + 5 * 0.7) / 6)
    finally:
        db.close()
