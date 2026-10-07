"""B4 driver ranking: evidence-weighted signed contributions add up exactly, offsetting drivers never lead, lever
relevance outside the ROAS family, and not-assessable modules never win."""

import math

import pytest

from adapt.diagnose.drivers import rank
from adapt.diagnose.evidence import INSUFFICIENT, OK, Evidence

FUNNEL = {"status": "OK", "contributions": {"CTR": -0.05, "CVR": 0.02, "AOV": 0.0, "CPM": -0.30}}


def ev(module, score, levers, status=OK):
    return Evidence(module, status, score, levers)


def test_signed_contributions_plus_unexplained_equal_the_roas_change():
    evidence = [ev("auction", 0.9, ["CPM"]), ev("fatigue", 0.5, ["CTR"]), ev("inventory", 0.6, ["CVR"]),
                ev("price", 0.4, ["CVR", "AOV"]), ev("tracking", 0.0, ["CVR"])]
    r = rank(evidence, "ROAS", FUNNEL)
    total = sum(FUNNEL["contributions"].values())
    assert sum(d["signed_contribution"] for d in r["drivers"]) + r["unexplained"] == pytest.approx(total)
    by = {d["module"]: d for d in r["drivers"]}
    assert by["auction"]["signed_contribution"] == pytest.approx(-0.30)  # sole CPM claimant takes all of c_CPM
    # CVR (+0.02) is shared by inventory (0.6) and price (0.4) in proportion to their scores
    assert by["inventory"]["signed_contribution"] == pytest.approx(0.02 * 0.6 / 1.0)
    assert by["inventory"]["offsetting"] and by["price"]["offsetting"]  # they push ROAS up while it falls
    assert r["top_driver"] == "auction" and r["top_level"] == "STRONG_EVIDENCE"
    assert sum(d["magnitude_share"] for d in r["drivers"]) <= 1.0 + 1e-9


def test_offsetting_or_unsupported_drivers_never_lead():
    r = rank([ev("inventory", 0.9, ["CVR"]), ev("fatigue", 0.2, ["CTR"])], "ROAS", FUNNEL)
    assert r["top_driver"] is None and r["top_level"] == "UNKNOWN"  # inventory offsets, fatigue too weak


def test_non_roas_incidents_rank_by_score_among_relevant_levers():
    r = rank([ev("inventory", 1.0, ["CVR"]), ev("fatigue", 0.55, ["CTR"]), ev("auction", 0.0, ["CPM"])],
             "CTR", None)
    assert r["method"] == "module_score" and r["top_driver"] == "fatigue" and r["top_level"] == "WEAK_EVIDENCE"
    assert next(d for d in r["drivers"] if d["module"] == "inventory")["level"] == "NOT_APPLICABLE"


def test_not_assessable_modules_are_listed_but_never_win():
    r = rank([ev("tracking", 0.0, ["CVR"], INSUFFICIENT), ev("price", 0.45, ["CVR", "AOV"]),
              ev("fatigue", 0.9, ["CTR"])], "CVR", None)
    assert r["top_driver"] == "price"
    levels = {d["module"]: d["level"] for d in r["drivers"]}
    assert levels["tracking"] == "NOT_ASSESSABLE"
    assert levels["fatigue"] == "NOT_APPLICABLE"  # a CTR-only module cannot explain a CVR move, whatever its score


def test_collapse_funnel_falls_back_to_scores():
    r = rank([ev("inventory", 0.8, ["CVR"])], "ROAS", {"status": "COLLAPSE", "contributions": {}})
    assert r["method"] == "module_score" and r["top_driver"] == "inventory"
    assert not math.isnan(r["drivers"][0]["score"])
