"""Stage 2 ★ narrator on REAL pipeline output (fixture world): one pipeline cycle with S1 active detects, diagnoses
and decides; every incident package, the decision package and the daily brief are narrated offline (template) and
each passes the same guard an LLM answer must pass. A forged LLM answer that reverses a real atom's direction is
rejected on the same package."""

from datetime import date, timedelta

from adapt.agent.atoms import decision_package, incident_package
from adapt.agent.brief import daily_brief
from adapt.agent.guard import check_narrative
from adapt.agent.narrator import narrate_decision, narrate_incident, template_narrative
from adapt.pipeline.cycle import run_cycle
from adapt.reconcile.build import logical_now

START = date(2026, 10, 1)


def test_narratives_on_pipeline_output_pass_the_guard(world, http, db):
    world.post("/control/scenario", json={"key": "S1"}, headers={"X-Request-ID": "s1"})
    world.post("/control/advance", json={"days": 4}, headers={"X-Request-ID": "adv"})
    as_of = logical_now(START + timedelta(days=4))
    run_cycle(db, http, as_of)
    incidents = [a for (a,) in db.query("SELECT anomaly_id FROM intel.anomalies WHERE is_incident")]
    assert incidents
    for aid in incidents:
        pkg = incident_package(db, aid)
        nar = narrate_incident(db, aid, None, as_of)
        assert nar["source"] == "template" and check_narrative(template_narrative(pkg), pkg).ok
        assert any(s["atom_ids"] for s in nar["sentences"])
    cpm = [a for a in incidents if "CPM" in a]
    if cpm:  # forge an LLM sentence that reverses the real CPM direction
        pkg = incident_package(db, cpm[0])
        a1 = pkg["atoms"]["A1"]
        wrong = "fell" if a1.direction == "UP" else "rose"
        forged = {"headline": "x", "sentences": [{"text": f"CPM {wrong} {a1.value:.1f}%.", "atom_ids": ["A1"]}],
                  "next_step": {"kind": "NONE", "ref_id": None}}
        assert not check_narrative(forged, pkg).ok
    decisions = [d for (d,) in db.query("SELECT decision_id FROM intel.decisions")]
    for did in decisions:
        pkg = decision_package(db, did)
        assert check_narrative(template_narrative(pkg), pkg).ok
        assert narrate_decision(db, did, None, as_of)["sentences"]
    brief = daily_brief(db, as_of, None)
    assert brief["source"] == "template" and brief["sentences"]
    assert db.query("SELECT count(*) FROM intel.narratives")[0][0] == len(incidents) + len(decisions) + 1
