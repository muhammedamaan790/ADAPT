"""The Narrator (Stage 2 ★, spec §11): evidence package + claim atoms in, {headline, sentences[{text, atom_ids}],
next_step{kind, ref_id}} out.

Flow: build the package (code) -> if the LLM is online, ask for strict-schema JSON -> guard every sentence, the
headline and next_step -> on a failure retry ONCE with the violations fed back -> still failing (or offline) -> the
deterministic template. Financial figures in cards are always rendered by the UI from the decision DTO; the narrative
supplies connective prose and evidence chips. The badge is "evidence linked · values checked" (the guard checks
numbers, ids, direction and entities, not full relational meaning), never "verified".

The template is built from the same atoms and is tested to pass the same guard, so the offline path can never show
an unchecked claim. Narratives are stored in intel.narratives keyed by (kind, ref_id, package hash).
"""

from __future__ import annotations

import json
from datetime import datetime

from adapt.agent.atoms import NEXT_STEP_KINDS, Atom, decision_package, incident_package, package_hash
from adapt.agent.groq_client import GroqClient, LLMUnavailable
from adapt.agent.guard import check_narrative

BADGE = "evidence linked · values checked"
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["headline", "sentences", "next_step"],
    "properties": {
        "headline": {"type": "string"},
        "sentences": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "atom_ids"],
            "properties": {"text": {"type": "string"}, "atom_ids": {"type": "array", "items": {"type": "string"}}}}},
        "next_step": {"type": "object", "additionalProperties": False, "required": ["kind", "ref_id"],
                      "properties": {"kind": {"type": "string", "enum": list(NEXT_STEP_KINDS)},
                                     "ref_id": {"type": ["string", "null"]}}},
    },
}
SYSTEM = """You write short, plain-English explanations for a D2C growth manager. You never compute, invent, or round
numbers yourself: copy every number exactly as it appears in the `display` field of an atom, and list the atom ids each
sentence relies on in `atom_ids`. Rules:
- Only state facts present in the atoms. Name only entities, drivers, platforms and dates listed in the package.
- Direction words (rose, fell, increased, ...) must match the atom's direction.
- Intensity words are allowed only as given by an atom's `intensity` (slight / moderate / sharp / severe);
  'significant' only for atoms whose stat_status is STAT or SHIFT. No other evaluative adjectives.
- Never use causal verbs (caused, because, due to, led to, drove, proved). For STRONG_EVIDENCE / WEAK_EVIDENCE driver
  atoms write 'evidence points to ...' or 'associated with ...'. A QUASI_EXPERIMENTAL atom may only be written as
  'estimated effect of X% (90% interval A% to B%) ... under the stated assumptions'.
- No questions, no conditionals (if / would / could), at most one negation per sentence.
- next_step must be one of `next_step_options` exactly.
Return JSON only, matching the schema. 2-5 sentences."""


def _display(a: Atom) -> str | None:
    if a.value is None:
        return None
    if a.unit == "PCT":
        return f"{a.value:.1f}%"
    if a.unit == "INR":
        return "₹" + _indian(round(a.value))
    if a.unit == "X":
        return f"{a.value:.2f}"
    if a.unit == "FRACTION":
        return f"{a.value:.2f}"
    return f"{a.value:g}"


def _indian(n: int) -> str:
    s = str(abs(int(n)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return ("-" if n < 0 else "") + s


def prompt(pkg: dict, violations: list[str] | None = None) -> list[dict]:
    atoms = [{"atom_id": a.atom_id, "entity": a.entity, "metric": a.metric, "direction": a.direction,
              "display": _display(a), "period": a.period, "comparison_basis": a.comparison_basis,
              "claim_level": a.claim_level, "qualifier": a.qualifier, "stat_status": a.stat_status,
              "intensity": a.intensity, "driver": a.driver, "causal_assumptions": a.causal_assumptions}
             for a in pkg["atoms"].values()]
    user = {"kind": pkg["kind"], "atoms": atoms, "entities": sorted(pkg["entities"]),
            "drivers": sorted(pkg["drivers"]), "platforms": sorted(pkg["platforms"]),
            "next_step_options": pkg["next_step_options"]}
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(user, default=str)}]
    if violations:
        msgs.append({"role": "user", "content": "Your previous answer was rejected by the checker: "
                     + "; ".join(violations[:12]) + ". Rewrite it following the rules."})
    return msgs


# ---- deterministic template (offline / fallback) -------------------------------------------------------------------
_VERB = {"UP": "rose", "DOWN": "fell"}


def _sentence(text: str, ids: list[str]) -> dict:
    return {"text": text, "atom_ids": ids}


def template_narrative(pkg: dict) -> dict:
    a = pkg["atoms"]
    out: list[dict] = []
    headline = None
    if pkg["kind"] == "incident":
        lead = a.get("A1")
        if lead is not None:
            verb = _VERB.get(lead.direction, "moved")
            headline = f"{lead.entity}: {lead.metric} {verb} {_display(lead)} vs expected over the {lead.period}"
            out.append(_sentence(f"Over the {lead.period}, {lead.metric} {verb} {_display(lead)} against its "
                                 f"out-of-sample expectation.", ["A1"]))
        if "A2" in a and "A3" in a:
            out.append(_sentence(f"{a['A2'].metric} was {_display(a['A2'])} against an expected "
                                 f"{_display(a['A3'])}.", ["A2", "A3"]))
        funnel = [k for k in ("F1", "F2", "F3", "F4") if k in a]
        if funnel:
            parts = [f"{a[k].metric} {_VERB.get(a[k].direction, 'was flat at')} {_display(a[k])}" for k in funnel]
            out.append(_sentence("Exact funnel accounting vs the 28 days before: " + ", ".join(parts) + ".", funnel))
        elif "F0" in a:
            out.append(_sentence(f"Exact funnel accounting: the funnel collapsed at {a['F0'].metric}.", ["F0"]))
        for k in sorted(x for x in a if x.startswith("D")):
            d = a[k]
            val = f": {d.metric} {_VERB.get(d.direction, 'at')} {_display(d)}" if d.value is not None else ""
            out.append(_sentence(f"Evidence points to {d.driver.split(' (')[0].lower()} "
                                 f"({d.claim_level.replace('_', ' ').lower()}){val}.", [k]))
        if "C1" in a:
            c1, c2, c3, c4 = a["C1"], a["C2"], a["C3"], a["C4"]
            sign = "-" if c1.direction == "DOWN" else ""
            out.append(_sentence(
                f"Estimated effect of {sign}{_display(c1)} on ROAS (90% interval {c2.value:.1f}% to {c3.value:.1f}%; "
                f"accounting translation ≈ {_display(c4)}) under the stated assumptions.", ["C1", "C2", "C3", "C4"]))
        elif "C0" in a:
            out.append(_sentence("No causal estimate was possible (see the gate table).", ["C0"]))
    else:
        e1 = a["E1"]
        word = "a gain" if e1.direction == "UP" else ("a fall" if e1.direction == "DOWN" else "no change")
        headline = f"Expected contribution after ads: {word} of {_display(e1)} over the {e1.period}"
        out.append(_sentence(f"The expected change in contribution after ads is {word} of {_display(e1)} over the "
                             f"{e1.period}; after the optimism correction it is {_display(a['E5'])}.", ["E1", "E5"]))
        out.append(_sentence(f"Model P(loss) is {_display(a['E4'])}; the 10th to 90th percentile range of the "
                             f"expected CAA change spans {_display(a['E2'])} to {_display(a['E3'])} in magnitude.",
                             ["E2", "E3", "E4"]))
        for k in sorted((x for x in a if x.startswith("L") and x[1:].isdigit()), key=lambda x: int(x[1:])):
            leg = a[k]
            verb = "increased" if leg.direction == "UP" else "cut"
            out.append(_sentence(f"{leg.entity}: daily budget {verb} by {_display(leg)} to "
                                 f"{_display(a[k + 'a'])}.", [k, k + "a"]))
        if "U1" in a:
            out.append(_sentence(f"{_display(a['U1'])} per day is left unallocated.", ["U1"]))
    step = next((o for o in pkg["next_step_options"] if o["kind"] != "NONE"), {"kind": "NONE", "ref_id": None})
    if headline is None:
        headline = f"{pkg['kind'].title()} {pkg['ref_id']}"
    return {"headline": headline, "sentences": out, "next_step": step}


# ---- narrate -------------------------------------------------------------------------------------------------------
DDL = """
CREATE SCHEMA IF NOT EXISTS intel;
CREATE TABLE IF NOT EXISTS intel.narratives (
    kind VARCHAR NOT NULL, ref_id VARCHAR NOT NULL, package_hash VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL,
    source VARCHAR NOT NULL, narrative JSON NOT NULL, PRIMARY KEY (kind, ref_id, package_hash)
);
"""


def narrate(pkg: dict, client: GroqClient | None, template=None) -> dict:
    """Guarded LLM narrative, or the deterministic template (default: template_narrative). Never raises for LLM
    problems."""
    template = template or template_narrative
    phash = package_hash(pkg)
    rejections: list[list[str]] = []
    model, offline_reason = None, None
    if client is None or client.offline:
        offline_reason = "LLM offline"
    else:
        violations: list[str] | None = None
        for _attempt in range(2):
            try:
                model, nar, cached = client.structured(prompt(pkg, violations), SCHEMA, "narrative", phash)
            except LLMUnavailable as exc:
                offline_reason = f"LLM unavailable: {exc}"
                break
            g = check_narrative(nar, pkg)
            if g.ok:
                return _finish(pkg, nar, f"llm:{model}", phash, rejections, cached=cached)
            rejections.append(g.violations)
            violations = g.violations
    nar = template(pkg)
    g = check_narrative(nar, pkg)
    if not g.ok:  # a template that fails its own guard is a bug, never shown
        raise RuntimeError(f"template narrative failed the guard: {g.violations}")
    return _finish(pkg, nar, "template", phash, rejections, reason=offline_reason or "guard rejected the LLM twice")


def _finish(pkg, nar, source, phash, rejections, cached=False, reason=None) -> dict:
    sentences = [{**s, "evidence_ids": sorted({e for a in s["atom_ids"] for e in pkg["atoms"][a].evidence_ids}),
                  "claim_levels": sorted({pkg["atoms"][a].claim_level for a in s["atom_ids"]})}
                 for s in nar["sentences"]]
    out = {"kind": pkg["kind"], "ref_id": pkg["ref_id"], "headline": nar["headline"], "sentences": sentences,
           "next_step": nar["next_step"], "source": source, "badge": BADGE, "package_hash": phash,
           "guard_rejections": rejections, "cached": cached}
    if reason:
        out["fallback_reason"] = reason
    c0 = pkg["atoms"].get("C0")
    if c0 is not None:
        out["not_estimable_reason"] = c0.text  # rendered deterministically by the UI, never by the LLM
    return out


def persist(db, nar: dict, at: datetime) -> None:
    def work(cur):
        cur.execute(DDL)
        cur.execute("INSERT OR REPLACE INTO intel.narratives VALUES (?, ?, ?, ?, ?, ?)",
                    [nar["kind"], nar["ref_id"], nar["package_hash"], at, nar["source"], json.dumps(nar)])

    db.write(work)


def narrate_incident(db, anomaly_id: str, client: GroqClient | None, at: datetime | None = None) -> dict:
    nar = narrate(incident_package(db, anomaly_id), client)
    persist(db, nar, at or datetime.now())
    return nar


def narrate_decision(db, decision_id: str, client: GroqClient | None, at: datetime | None = None) -> dict:
    nar = narrate(decision_package(db, decision_id), client)
    persist(db, nar, at or datetime.now())
    return nar
