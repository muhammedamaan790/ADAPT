"""Stage 2 ★ evaluation harness (spec §16, §22.7, §22.8): T57 GT labelling (matching, TP/FP/FN, latency, early
warning, duplicates, negative set), T52 baseline rules (rank order, 10% step, tie-break, envelope clipping), report
maths (paired CI, oracle capture incl. NOT_INTERPRETABLE), and an end-to-end smoke run on the fixture world: all six
strategies on forks with common random numbers (T35), zero guardrail breaches by executed actions, ADAPT replay 100%,
and the observe-mode detection fork graded against the world's GT."""

import numpy as np
import pytest

from evalharness import report
from evalharness.matching import detection_metrics, diagnosis_metrics, match, negative_set
from evalharness.runner import run_decision_eval, run_detection_eval
from evalharness.strategies import Envelope, _pairs


def gt(iid, scenario="S2", driver="creative_fatigue", start=10, onset=14, end=23, ents=("C1",), metric="CTR",
       direction="DOWN"):
    return {"incident_id": iid, "scenario": scenario, "driver": driver, "entity_ids": list(ents),
            "metric_set": [metric, "ROAS"], "direction": {metric: direction, "ROAS": direction},
            "injection_start_day": start, "onset_day": onset, "injection_end_day": end}


def det(aid, day, ents=("C1",), metric="CTR", direction="DOWN", drivers=("fatigue",), cls="efficiency_anomaly"):
    return {"anomaly_id": aid, "entity_ids": list(ents), "metric": metric, "direction": direction,
            "detection_day": day, "classification": cls, "drivers": list(drivers),
            "top_driver": drivers[0] if drivers else None}


# ---- T57 -------------------------------------------------------------------------------------------------------------
def test_t57_before_inside_after_and_duplicates():
    g = [gt("G1")]
    early = detection_metrics([det("A1", 12)], g)                 # after injection start, before onset
    assert early["tp"] == 1 and early["early_warning"] == 1 and early["median_latency_days"] is None
    assert detection_metrics([det("A1", 9)], g)["fp"] == 1          # before injection start: not matchable
    late = detection_metrics([det("A1", 27)], g)                    # end + 3 = 26 < 27: outside the window
    assert late["tp"] == 0 and late["fp"] == 1 and late["fn"] == 1
    inside = detection_metrics([det("A1", 16), det("A2", 17)], g)   # a duplicate is a false positive
    assert (inside["tp"], inside["fp"], inside["fn"]) == (1, 1, 0) and inside["median_latency_days"] == 2.0
    assert detection_metrics([det("A1", 16, direction="UP")], g)["tp"] == 0   # wrong direction
    assert detection_metrics([det("A1", 16, metric="CPM")], g)["tp"] == 0     # metric outside the GT set
    assert detection_metrics([det("A1", 16, ents=("C9",))], g)["tp"] == 0     # no entity overlap


def test_t57_matching_is_maximum_cardinality_and_deterministic():
    g = [gt("G1", ents=("C1", "C2")), gt("G2", ents=("C2",))]
    a = [det("A1", 15, ents=("C2",)), det("A2", 16, ents=("C1",))]
    pairs = match(a, g)
    assert len(pairs) == 2 and sorted(pairs) == [(0, 1), (1, 0)]   # A1 takes G2 so that A2 can take G1
    assert match(a, g) == match(list(a), list(g))


def test_diagnosis_and_negative_set():
    g = [gt("G1"), gt("G7", scenario="S7", driver="budget_change", ents=("C5",), metric="SPEND")]
    a = [det("A1", 16, drivers=("auction", "fatigue")), det("A9", 11, ents=("C5",), metric="ROAS")]
    d = diagnosis_metrics(a, g)
    assert d["scored"] == 1 and d["top1"] == 0.0 and d["top3"] == 1.0
    neg = negative_set(a, g)
    assert neg["accuracy"] == 0.0 and neg["items"][0]["raised"] == ["A9"]  # an efficiency incident on S7's entity


# ---- T52 baseline rules ----------------------------------------------------------------------------------------------
def test_t52_pairs_rank_step_tiebreak_and_clipping():
    env = Envelope(B=1e9)
    cur = {"a": 10_000.0, "b": 10_000.0, "c": 10_000.0, "d": 10_000.0, "e": 10_000.0, "f": 10_000.0}
    stats = {u: {"spend7": 20_000.0} for u in cur}
    ranked = ["a", "b", "c", "d", "e", "f"]                         # best first
    new, un, _ = _pairs(cur, env, 0, ranked, stats, lambda u: True, "spend7")
    assert new["a"] == pytest.approx(11_000) and new["f"] == pytest.approx(9_000)   # 10% of the bottom unit
    assert sum(new.values()) == pytest.approx(sum(cur.values())) and un == 0      # spend preserved
    assert _pairs(cur, env, 0, ranked, stats, lambda u: True, "spend7")[0] == new  # deterministic
    blocked, un2, why = _pairs(cur, env, 0, ranked, stats, lambda u: u != "a", "spend7")
    assert blocked["a"] == 10_000 and un2 == pytest.approx(1_000) and why          # no receiver -> unallocated
    env.last_change["b"] = 0                                                       # b in cooldown on day 1
    cool, _, _ = _pairs(cur, env, 1, ranked, stats, lambda u: True, "spend7")
    assert cool["b"] == 10_000 and not env.feasible(cur, cool, 1)
    low = {u: {"spend7": 5_000.0} for u in cur}                                    # below the 10,000 eligibility
    assert _pairs(cur, env, 0, ranked, low, lambda u: True, "spend7")[0] == cur


# ---- report maths ----------------------------------------------------------------------------------------------------
def test_report_paired_ci_and_oracle_capture():
    def run(s, a, o):
        z = {"violations": [], "caa": 0.0}
        return {"common_random_numbers": True, "strategies": {
            "safe-static": {**z, "caa": s}, "adapt": {**z, "caa": a, "replay": {"decisions": 2, "matched": 2},
                                                       "verdicts": {"SUCCESS": 1}},
            "safe-contribution": {**z, "caa": s + 5}, "oracle": {**z, "caa": o}}}

    rep = report.build({101: run(100, 130, 160), 102: run(100, 120, 140), 103: run(100, 110, 90)})
    assert rep["primary"]["mean"] == pytest.approx(20.0) and rep["primary"]["excludes_zero"]
    assert rep["oracle_capture"]["interpretable"] == 2 and rep["oracle_capture"]["NOT_INTERPRETABLE"] == 1
    assert rep["oracle_capture"]["median_pct"] == pytest.approx(np.median([50.0, 50.0]))
    assert rep["replay"] == {"decisions": 6, "matched": 6} and rep["verdicts"] == {"SUCCESS": 3}


# ---- end-to-end smoke on the fixture world -------------------------------------------------------------------------
def test_all_six_strategies_on_forks_with_common_random_numbers(seeded_world, tmp_path):
    out = run_decision_eval(seeded_world, tmp_path, days=2)
    assert out["common_random_numbers"]                                            # T35
    st = out["strategies"]
    assert set(st) == {"safe-static", "roas-rank", "contribution-rank", "safe-contribution", "adapt", "oracle"}
    for name, r in st.items():
        assert r["violations"] == [], (name, r["violations"])                     # 0 guardrail breaches
        assert np.isfinite(r["caa"]) and r["spend"] > 0
        assert r["spend"] <= out["budget_ceiling"] * out["days"] + 1e-6
    rep = st["adapt"]["replay"]
    assert rep["decisions"] >= 1 and rep["matched"] == rep["decisions"]            # replay 100%
    assert any("truth_caa_7d" in e for e in st["oracle"]["log"])


def test_detection_fork_is_graded_against_world_gt(seeded_world, tmp_path):
    out = run_detection_eval(seeded_world, tmp_path, days=6, schedule=[("S1", 1), ("S7", 1)])
    assert out["gt_incidents"] >= 2
    d = out["detection"]
    assert d["tp"] >= 1, out                                                       # S1 (Meta CPM +45%) is found
    assert out["negative_set"]["items"] and all("correct" in i for i in out["negative_set"]["items"])


def test_oracle_perturbation_check_in_sub_forks(seeded_world, tmp_path):
    out = run_decision_eval(seeded_world, tmp_path, days=1, strategies=["safe-static", "oracle"], perturb_every=7)
    p = out["oracle_perturbation"]
    assert len(p["checks"]) == 1 and len(p["checks"][0]["perturbed_caa"]) == 10
    assert 0.0 <= p["suboptimality_rate"] <= 1.0
    assert not list(tmp_path.glob("perturb-*"))                                   # sub-forks discarded
