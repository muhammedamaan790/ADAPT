"""Narrator + guard (Stage 2 ★, spec §11): T22 (valid numbers, unsupported claim), T36 (reversed direction, causal
verbs), T43 (>= 30 negation / qualifier phrasings), T56 (typed next_step, uncited Copilot numbers), number
normalisation, the template passing its own guard, and the Groq flow (offline, chain filtering, retry with feedback,
fallback, 429 backoff, cache, the non-streaming tool-free narrator request)."""

import json

import httpx
import pytest

from adapt.agent.atoms import Atom
from adapt.agent.groq_client import COPILOT_REQUEST, GroqClient
from adapt.agent.guard import check_answer, check_narrative, check_next_step, check_sentence
from adapt.agent.narrator import narrate, template_narrative
from adapt.agent.numbers import matches, parse

CAMP = "META Prospecting | Women·Intimates"


def incident_pkg() -> dict:
    atoms = [
        Atom("A1", ["ANM-1"], CAMP, "CPM", "UP", 25.0, "PCT", "last 6 days", "vs the out-of-sample expected value",
             "EXACT_ACCOUNTING", "measured", "STAT"),
        Atom("A2", ["ANM-1"], CAMP, "ROAS", "DOWN", 2.1, "X", "last 6 days", "actual", "EXACT_ACCOUNTING",
             "measured", "STAT"),
        Atom("A3", ["ANM-1"], CAMP, "ROAS", None, 2.8, "X", "last 6 days", "expected (out-of-sample forecast)",
             "MODEL_ESTIMATE", "estimated"),
        Atom("A4", ["ANM-1"], CAMP, "CBA impact", None, 125000, "INR", "last 6 days",
             "contribution before ads vs expected", "MODEL_ESTIMATE", "estimated"),
        Atom("F1", ["DCMP-1"], CAMP, "CTR", "DOWN", 3.0, "PCT", "last 6 days", "vs the 28 days before",
             "EXACT_ACCOUNTING", "measured"),
        Atom("F2", ["DCMP-1"], CAMP, "CVR", "FLAT", 0.0, "PCT", "last 6 days", "vs the 28 days before",
             "EXACT_ACCOUNTING", "measured"),
        Atom("F4", ["DCMP-1"], CAMP, "CPM", "UP", 25.0, "PCT", "last 6 days", "vs the 28 days before",
             "EXACT_ACCOUNTING", "measured"),
        Atom("D1", ["EVD-1-auction"], CAMP, "CPM", "UP", 45.0, "PCT", "last 6 days", "evidence module",
             "STRONG_EVIDENCE", "probable", driver="Auction pressure (platform-wide CPM)"),
        Atom("C1", ["CSL-1"], CAMP, "ROAS", "DOWN", 12.0, "PCT", "last 6 days", "vs the synthetic control",
             "QUASI_EXPERIMENTAL", "estimated", causal_assumptions=["no unobserved time-varying confounding"]),
        Atom("C2", ["CSL-1"], CAMP, "ROAS effect 90% interval", None, -20.0, "PCT", "last 6 days", "lower bound",
             "QUASI_EXPERIMENTAL", "estimated"),
        Atom("C3", ["CSL-1"], CAMP, "ROAS effect 90% interval", None, -4.0, "PCT", "last 6 days", "upper bound",
             "QUASI_EXPERIMENTAL", "estimated"),
        Atom("C4", ["CSL-1"], CAMP, "accounting translation", None, 120000, "INR", "last 6 days",
             "of the estimated effect", "QUASI_EXPERIMENTAL", "estimated"),
    ]
    return {"kind": "incident", "ref_id": "ANM-1", "atoms": {a.atom_id: a for a in atoms},
            "evidence_ids": {"ANM-1", "DCMP-1", "EVD-1-auction", "CSL-1"},
            "entities": {CAMP, "238000000003"}, "drivers": {"Auction pressure (platform-wide CPM)"},
            "platforms": {"meta"}, "dates": {"2026-10-01"},
            "next_step_options": [{"kind": "NONE", "ref_id": None}, {"kind": "INVESTIGATE", "ref_id": "ANM-1"},
                                  {"kind": "VIEW_DECISION", "ref_id": "D-9"}]}


PKG = incident_pkg()


def ok(text, ids):
    return check_sentence(text, ids, PKG).ok


# ---- T36: direction and causal verbs ---------------------------------------------------------------------------------
def test_t36_reversed_direction_and_causal_verbs():
    assert ok("CPM rose 25.0% over the last 6 days.", ["A1"])
    assert not ok("CPM decreased 25% over the last 6 days.", ["A1"])            # correct number, reversed direction
    assert not ok("Higher CPM caused ROAS to fall to 2.1.", ["A1", "A2"])       # causal verb at any level
    assert not ok("ROAS fell to 2.1 because of auction pressure.", ["A2", "D1"])
    assert ok("Evidence points to auction pressure (strong evidence): CPM rose 45.0%.", ["D1"])
    assert not ok("Auction pressure drove CPM up 45%.", ["D1"])


# ---- T43: >= 30 table-driven phrasings ------------------------------------------------------------------------------
T43 = [
    # (sentence, cited atoms, accepted?)
    ("CPM rose 25% over the last 6 days.", ["A1"], True),
    ("CPM increased 25.0%.", ["A1"], True),
    ("CPM went up 25%.", ["A1"], True),
    ("CPM was higher by 25%.", ["A1"], True),
    ("CPM fell 25%.", ["A1"], False),
    ("CPM dropped by 25%.", ["A1"], False),
    ("CPM did not increase.", ["A1"], False),                       # negated UP vs an UP atom
    ("CPM didn't rise.", ["A1"], False),
    ("CPM never fell.", ["A1"], True),                              # NOT_DOWN vs UP: consistent
    ("CPM did not decline.", ["A1"], True),
    ("CVR did not decline.", ["F2"], True),                         # NOT_DOWN vs FLAT
    ("CVR did not increase.", ["F2"], True),                        # NOT_UP vs FLAT
    ("CVR rose.", ["F2"], False),                                   # UP vs FLAT
    ("CPM did not fail to rise.", ["A1"], False),                   # double negation
    ("It is not true that CPM did not rise.", ["A1"], False),       # double negation
    ("If CPM had risen, ROAS would have fallen.", ["A1", "A2"], False),  # conditional
    ("CPM could rise further.", ["A1"], False),                     # conditional
    ("Did CPM rise 25%?", ["A1"], False),                           # question
    ("CPM rose sharply, by 25%.", ["A1"], True),                    # 25% -> sharp band
    ("CPM rose slightly, by 25%.", ["A1"], False),                  # slight band is < 5%
    ("CPM rose severely, by 25%.", ["A1"], False),
    ("CTR fell slightly, by 3.0%.", ["F1"], True),
    ("CTR fell moderately.", ["F1"], False),
    ("CPM rose a significant 25%.", ["A1"], True),                  # A1 carries a STAT test that fired
    ("CTR fell significantly.", ["F1"], False),                     # F1: no statistical test
    ("A disastrous 25% rise in CPM.", ["A1"], False),               # evaluative word
    ("CPM rose a massive 25%.", ["A1"], False),
    ("CPM rose a notable 25%.", ["A1"], True),                      # allowlisted
    ("ROAS was 2.1 against an expected 2.8.", ["A2", "A3"], True),
    ("ROAS was 2.4 against an expected 2.8.", ["A2", "A3"], False),  # 2.4 matches no atom
    ("The CBA shortfall was ₹1,25,000.", ["A4"], True),
    ("The CBA shortfall was 1.25 lakh.", ["A4"], True),
    ("The CBA shortfall was ₹1.3 lakh.", ["A4"], True),             # 1.25 lakh rounded half-up at 1 decimal
    ("The CBA shortfall was ₹1.4 lakh.", ["A4"], False),            # not a rounding of 1.25 lakh
    ("CPM rose 25% over the last 7 days.", ["A1"], False),          # period contradicts the atom
    ("CPM rose 25%.", [], False),                                   # claim without atoms
    ("CPM rose 25%.", ["Z9"], False),                               # unknown atom id
]


@pytest.mark.parametrize("text, ids, accepted", T43)
def test_t43_phrasing_table(text, ids, accepted):
    r = check_sentence(text, ids, PKG)
    assert r.ok == accepted, r.violations


def test_t43_has_at_least_30_phrasings():
    assert len(T43) >= 30


# ---- quasi-experimental wording and T22 (valid numbers, unsupported claim) -------------------------------------------
def test_quasi_experimental_form_only():
    good = ("Estimated effect of -12.0% on ROAS (90% interval -20.0% to -4.0%; accounting translation ≈ ₹1,20,000) "
            "under the stated assumptions.")
    assert ok(good, ["C1", "C2", "C3", "C4"])
    assert not ok("ROAS was cut by 12.0% by the incident (90% interval -20.0% to -4.0%).", ["C1", "C2", "C3"])


def test_t22_valid_numbers_but_unsupported_claim():
    assert not ok("Evidence points to creative fatigue: CPM rose 25%.", ["A1"])      # driver not in the package
    assert not ok("CPM rose 25% on Google.", ["A1"])                                # platform not in the package
    assert not ok("SKU W_DRESSES-P2 sold out while CPM rose 25%.", ["A1"])          # SKU not in the package
    assert not ok("Campaign 99999999999 saw CPM rise 25%.", ["A1"])                 # id not in the package
    assert ok(f"{CAMP}: CPM rose 25%.", ["A1"])
    assert not ok("GOOGLE Search | Men·Outerwear: CPM rose 25%.", ["A1"])
    assert not ok("On 2026-11-05 CPM rose 25%.", ["A1"])                            # date not in the package


def test_probable_driver_needs_hedged_wording():
    assert not ok("Auction pressure: CPM rose 45%.", ["D1"])
    assert ok("The CPM rise is associated with auction pressure (45.0%).", ["D1"])


# ---- number normalisation --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["₹1,25,000", "125000", "₹125k", "1.25 lakh", "1.25L", "12.5e4", "₹1,25,000.00",
                                  "Rs 125000", "INR 1.25 lakh"])
def test_number_normalisation_inr(text):
    assert matches(parse(text)[0], 125000, "INR", 0.005)


def test_number_normalisation_crore_percent_fraction():
    assert matches(parse("₹1.2 Cr")[0], 1.24e7, "INR", 0.005)                 # displayed rounding
    assert not matches(parse("₹1.3 Cr")[0], 1.24e7, "INR", 0.005)
    assert matches(parse("12.5%")[0], 0.125, "FRACTION", 0.005)
    assert matches(parse("0.125")[0], 12.5, "PCT", 0.005)
    assert matches(parse("39%")[0], 39.24, "PCT", 0.005) and not matches(parse("40%")[0], 39.24, "PCT", 0.005)
    assert parse("campaign 238000000003 on 2026-10-01") == []                    # ids and dates are entities


# ---- T56: typed next_step; Copilot answers ---------------------------------------------------------------------------
def test_t56_next_step_is_typed_and_copilot_numbers_must_be_cited():
    assert check_next_step({"kind": "INVESTIGATE", "ref_id": "ANM-1"}, PKG).ok
    assert not check_next_step({"kind": "INVESTIGATE", "ref_id": "ANM-1", "note": "raise budget 20%"}, PKG).ok
    assert not check_next_step({"kind": "RAISE_BUDGET", "ref_id": "ANM-1"}, PKG).ok
    assert not check_next_step({"kind": "VIEW_DECISION", "ref_id": "D-404"}, PKG).ok
    assert not check_next_step("Approve the ₹50,000 increase", PKG).ok
    assert check_answer("CPM rose 25%. ROAS was 2.1.", ["A1", "A2"], PKG).ok
    assert not check_answer("CPM rose 25%. Spend should go up by ₹50,000.", ["A1", "A2"], PKG).ok


def test_headline_is_guarded_too():
    nar = {"headline": "CPM fell 25%", "sentences": [{"text": "CPM rose 25%.", "atom_ids": ["A1"]}],
           "next_step": {"kind": "NONE", "ref_id": None}}
    r = check_narrative(nar, PKG)
    assert not r.ok and any(v.startswith("headline") for v in r.violations)


# ---- the template passes its own guard -------------------------------------------------------------------------------
def test_template_passes_the_guard():
    nar = template_narrative(PKG)
    assert check_narrative(nar, PKG).ok, check_narrative(nar, PKG).violations
    assert nar["next_step"] in PKG["next_step_options"]


# ---- Groq flow -------------------------------------------------------------------------------------------------------
GOOD = {"headline": "CPM rose 25.0% vs expected", "sentences": [
    {"text": "CPM rose 25.0% over the last 6 days.", "atom_ids": ["A1"]},
    {"text": "Evidence points to auction pressure (strong evidence): CPM rose 45.0%.", "atom_ids": ["D1"]}],
    "next_step": {"kind": "INVESTIGATE", "ref_id": "ANM-1"}}
BAD = {**GOOD, "sentences": [{"text": "CPM decreased 25% over the last 6 days.", "atom_ids": ["A1"]}]}


def groq(responses: list, models=("openai/gpt-oss-120b", "openai/gpt-oss-20b")):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in models]})
        body = json.loads(request.content)
        seen.append(body)
        status, content = responses.pop(0)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "x"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})

    client = GroqClient("test-key", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    return client, seen


def test_offline_uses_the_template():
    out = narrate(PKG, GroqClient(None))
    assert out["source"] == "template" and out["fallback_reason"] == "LLM offline"
    assert out["badge"] == "evidence linked · values checked"


def test_chain_is_filtered_to_available_models():
    client, _ = groq([], models=("openai/gpt-oss-20b", "llama-3.3-70b-versatile"))
    assert client.chain() == ["openai/gpt-oss-20b"]
    client, _ = groq([], models=())
    assert client.offline


def test_good_llm_answer_is_shown_with_evidence_chips():
    client, seen = groq([(200, GOOD)])
    out = narrate(PKG, client)
    assert out["source"] == "llm:openai/gpt-oss-120b"
    assert out["sentences"][1]["evidence_ids"] == ["EVD-1-auction"]
    req = seen[0]
    assert req["stream"] is False and "tools" not in req
    assert req["response_format"]["type"] == "json_schema" and req["response_format"]["json_schema"]["strict"]


def test_rejected_answer_is_retried_once_with_feedback_then_accepted():
    client, seen = groq([(200, BAD), (200, GOOD)])
    out = narrate(PKG, client)
    assert out["source"].startswith("llm:") and len(out["guard_rejections"]) == 1
    assert "rejected by the checker" in seen[1]["messages"][-1]["content"]


def test_twice_rejected_answer_falls_back_to_the_template():
    client, _ = groq([(200, BAD), (200, BAD)])
    out = narrate(PKG, client)
    assert out["source"] == "template" and len(out["guard_rejections"]) == 2


def test_429_backoff_then_next_model():
    sleeps = []
    client, seen = groq([(429, None), (429, None), (429, None), (200, GOOD)])
    client.sleep = sleeps.append
    out = narrate(PKG, client)
    assert out["source"] == "llm:openai/gpt-oss-20b" and sleeps == [1.0, 2.0]
    assert [b["model"] for b in seen] == ["openai/gpt-oss-120b"] * 3 + ["openai/gpt-oss-20b"]


def test_responses_are_cached(tmp_path):
    from adapt.core.db import Database

    db = Database(tmp_path / "ws.duckdb")
    client, seen = groq([(200, GOOD)])
    client.db = db
    first = narrate(PKG, client)
    client._chain = None
    second = narrate(PKG, client)
    assert len(seen) == 1 and not first["cached"] and second["cached"]
    db.close()


def test_copilot_profile_cannot_be_used_for_the_narrator():
    client, _ = groq([])
    with pytest.raises(ValueError):
        client.structured([], {}, "x", "h", request=COPILOT_REQUEST)
