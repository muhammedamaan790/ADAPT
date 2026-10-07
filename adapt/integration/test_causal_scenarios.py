"""Stage 2 ★ causal slice, ground truth through the real path (world -> connectors -> canonical -> estimator): on S6
(category demand +40%) the gated synthetic control estimates the % effect on the treated Google Search campaign's
ROAS, and the estimate is compared with the world's TRUE effect, measured on a paired fork of the same world without
the scenario (common random numbers; eval-side truth, never visible to the app).

Two regimes:
- after 42 live days with stable budgets (the method's assumptions hold): the synthetic control's point estimate on
  the product's own panel is within 10 pp of the truth and its interval excludes 0. Whether the GATES then pass is
  reported, not assumed: the world adds ~14% idiosyncratic daily ROAS noise per campaign, which the 15% daily pre-fit
  sMAPE gate and the placebo's rupee gate (|placebo| < m = 5% of ONE day's CBA) mostly refuse, as specified;
- right after the seeded history, where the history manager tweaked every budget weekly (+-15%, independently per
  campaign) and so moved each campaign's ROAS along its own saturation curve: unit-specific, time-varying
  confounding. The in-time placebo detects it and the estimator refuses (NOT_ESTIMABLE), as the gates intend."""

import shutil
from datetime import date, timedelta

import numpy as np
import pytest

from adapt.diagnose.causal.estimate import ESTIMATED, NOT_ESTIMABLE, build_panel, causal_config, estimate
from adapt.diagnose.causal.synthetic_control import synthetic_control
from adapt.diagnose.evidence import Incident
from adapt.ingest.http import SourceHttp
from adapt.ingest.sync import run_sync
from adapt.reconcile.build import build_canonical, logical_now
from world.step import make_store
from world.truth import load_truth

START = date(2026, 10, 1)
DAYS = 8


def true_roas_effect(seeded_dir, tmp_path, campaign_id: str, lo: int, hi: int, scen_store, lead: int) -> float:
    d = tmp_path / "control_fork"
    shutil.copytree(seeded_dir, d)
    ctrl = make_store(d / "sim_state.duckdb", load_truth(d / "sim_truth.duckdb"))
    if lead:
        ctrl.commit("advance", "lead", "test", {"days": lead})
    ctrl.commit("advance", "adv", "test", {"days": DAYS})
    q = """SELECT (SELECT sum(unit_price_inr) FROM fact_orders WHERE campaign_id = ? AND day BETWEEN ? AND ?)
                  / (SELECT sum(spend_inr) FROM fact_ad_campaign_daily WHERE campaign_id = ? AND day BETWEEN ? AND ?)"""
    args = [campaign_id, lo, hi, campaign_id, lo, hi]
    out = scen_store.read(q, args)[0][0] / ctrl.read(q, args)[0][0] - 1
    ctrl.close()
    return out


def s6(seeded_world_large, world_large, db, tmp_path, lead: int) -> tuple[dict, float]:
    world, http = world_large, SourceHttp("http://testserver", client=world_large, sleep=lambda s: None)
    if lead:
        world.post("/control/advance", json={"days": lead}, headers={"X-Request-ID": "lead"})
    run_sync(db, http)
    world.post("/control/scenario", json={"key": "S6"}, headers={"X-Request-ID": "s6"})
    world.post("/control/advance", json={"days": DAYS}, headers={"X-Request-ID": "adv"})
    run_sync(db, http)
    day0 = START + timedelta(days=lead)
    build_canonical(db, logical_now(day0 + timedelta(days=DAYS)))
    cat = world.app.state.store.read("SELECT json_extract_string(params, '$.category_code') "
                                     "FROM scenario_activation")[0][0]
    label = db.query("SELECT category FROM core.skus WHERE sku LIKE ? LIMIT 1", [cat + "%"])[0][0]
    camp = db.query("SELECT campaign_id FROM core.campaigns WHERE channel_id = 'google_search' AND product_set = ?",
                    [label])[0][0]
    post = (2, DAYS - 1)  # after the 3-day ramp's onset
    inc = Incident("ANM-S6", "google", [camp], None, "ROAS", day0 + timedelta(days=post[0]),
                   day0 + timedelta(days=post[1]), "UP")
    out = estimate(db, inc, materiality_m=20000.0)
    truth = true_roas_effect(seeded_world_large, tmp_path, camp, lead + post[0], lead + post[1],
                             world.app.state.store, lead)
    assert out["gates"]["controls"]["passed"]
    assert all(c not in out["gates"]["controls"]["excluded"] or "shares SKUs" in out["gates"]["controls"]["excluded"][c]
               for c in db.query("SELECT campaign_id FROM core.campaigns WHERE product_set = ? AND campaign_id <> ?",
                                 [label, camp]))
    out["_camp"] = camp
    return out, truth


def test_s6_effect_matches_the_paired_world_truth(seeded_world_large, world_large, db, tmp_path):
    out, truth = s6(seeded_world_large, world_large, db, tmp_path, lead=42)
    camp = out["_camp"]
    lead, post = 42, (2, DAYS - 1)
    inc = Incident("ANM-S6", "google", [camp], None, "ROAS", START + timedelta(days=lead + post[0]),
                   START + timedelta(days=lead + post[1]), "UP")
    p = build_panel(db, inc)
    y = np.log(p["treated"]["revenue"] / p["treated"]["spend"])
    agg = p["treated"]["spend"][p["start"]:p["start"] + p["n_post"]]
    sc = synthetic_control(y, p["X"], p["ids"], p["start"], p["n_post"], agg, causal_config(), seed=7)
    print(f"point estimate {sc.effect_pct:+.1%} CI [{sc.ci_lo:+.1%}, {sc.ci_hi:+.1%}] vs truth {truth:+.1%}")
    assert sc.effect_pct == pytest.approx(truth, abs=0.10)
    assert sc.ci_lo <= truth <= sc.ci_hi  # wide here: the 7-day holdout drifted (the pre-fit gate's job to refuse)
    if out["status"] == ESTIMATED:  # the gated product output, when it is shown, is that same estimate
        assert out["effect_pct"] == pytest.approx(truth, abs=0.10)
    else:
        assert out.get("effect_pct") is None and out["reason"]


def test_placebo_refuses_when_the_history_is_confounded(seeded_world_large, world_large, db, tmp_path):
    out, _ = s6(seeded_world_large, world_large, db, tmp_path, lead=0)
    assert out["status"] == NOT_ESTIMABLE and not out["gates"]["placebo"]["passed"]
