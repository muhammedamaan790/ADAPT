"""The narrative guard (Stage 2 ★, spec §11): every sentence, the headline and every Copilot answer is checked
against the claim atoms it cites BEFORE display. It checks numbers, IDs, direction, entities, qualifiers and causal
wording; it does not check full relational meaning, which is why the UI says "evidence linked · values checked" and
never "verified".

Checks (each failure is a named violation; any violation rejects the sentence):
  1. every cited atom exists; a sentence with a number, direction, qualifier or driver must cite atoms
  2. every number matches a cited atom (±0.5% or its displayed rounding; ₹ lakh/crore/k, % <-> fraction; days by unit)
  3. direction: rose/fell/... against the cited atom of the nearest metric mention; a direction word within 3 tokens
     after a negator is inverted (NOT_UP / NOT_DOWN); double negation, conditionals and questions are unresolvable
  4. intensity words (slight / moderate / sharp / severe) must match the cited atom's derived band; "significant" needs
     an atom whose statistical test fired; other evaluative words need the allowlist
  5. no causal verbs (caused, because of, due to, led to, drove, proved, ...) at any level; a QUASI-EXPERIMENTAL atom
     may only be stated as "estimated effect ... under the stated assumptions"; a named driver below that level needs
     hedged wording ("associated with", "evidence points to", ...)
  6. entities: drivers, SKUs, platforms, campaign ids / names, dates must be in the package whitelist (T22)
  7. period phrases ("last 6 days", "28-day") must agree with the cited atoms' periods
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from adapt.agent.atoms import NEXT_STEP_KINDS, narrative_config
from adapt.agent.numbers import dates, matches, parse

UP_WORDS = {"rose", "rise", "rises", "rising", "risen", "increase", "increased", "increases", "increasing", "up",
            "higher", "spiked", "spike", "spikes", "jumped", "jump", "climbed", "climb", "grew", "grow", "grown",
            "growing", "growth", "surged", "surge", "gained", "gain", "went up", "above"}
DOWN_WORDS = {"fell", "fall", "falls", "falling", "fallen", "drop", "dropped", "drops", "dropping", "declined",
              "decline", "declines", "declining", "decreased", "decrease", "decreases", "decreasing", "lower", "down",
              "slipped", "slid", "shrank", "shrunk", "reduced", "reduce", "reduction", "weakened", "cut", "below"}
NEGATORS = {"not", "never", "no", "neither", "nor", "without", "fail", "fails", "failed", "lack", "lacks", "lacked"}
NEUTRAL_PHRASES = ("lower bound", "upper bound", "up to", "follow up", "follow-up", "break down", "breakdown",
                   "set up", "down from", "up from", "drill-down", "drill down", "cut-off")
CONDITIONAL = {"if", "unless", "whether", "would", "could", "should", "might"}
INTENSITY = {"slight": "slight", "slightly": "slight", "moderate": "moderate", "moderately": "moderate",
             "sharp": "sharp", "sharply": "sharp", "steep": "sharp", "steeply": "sharp", "severe": "severe",
             "severely": "severe", "dramatic": "severe", "dramatically": "severe"}
SIGNIFICANT = {"significant", "significantly"}
EVALUATIVE = {"disastrous", "terrible", "awful", "great", "excellent", "alarming", "worrying", "worrisome",
              "catastrophic", "huge", "massive", "enormous", "tremendous", "horrible", "amazing", "fantastic",
              "impressive", "dire", "crisis", "skyrocketed", "skyrocketing", "plummeted", "plummeting", "plunged",
              "tanked", "crashed", "soared", "exploded", "unprecedented", "extreme", "extremely", "very", "hugely",
              "incredibly", "remarkable", "remarkably", "substantial", "substantially", "considerable",
              "considerably", "dangerous", "brutal", "strong", "strongly", "weak", "healthy", "poor", "bad", "good"}
CAUSAL = re.compile(r"\b(caus(e|ed|es|ing)|because|due to|led to|leads? to|resulted in|results? in|result of|"
                    r"drove|drives|driven by|proved?|proves|proven|proof|responsible for|attributable to|"
                    r"thanks to|owing to)\b")
DRIVER_KEYWORDS = {"creative fatigue": "Creative fatigue", "fatigue": "Creative fatigue",
                   "auction pressure": "Auction pressure (platform-wide CPM)", "auction": "Auction pressure "
                   "(platform-wide CPM)", "audience saturation": "Audience saturation", "saturation":
                   "Audience saturation", "tracking break": "Tracking break", "tracking": "Tracking break",
                   "inventory constraint": "Inventory constraint", "stockout": "Inventory constraint",
                   "stock-out": "Inventory constraint", "price change": "Price change",
                   "demand shift": "Demand shift (unpaid demand, same category)"}
PLATFORM_WORDS = {"meta": "meta", "facebook": "meta", "instagram": "meta", "google": "google", "youtube": "google",
                  "tiktok": "tiktok", "amazon": "amazon"}
METRIC_ALIASES = {
    "ctr": ("ctr", "click-through rate", "click through rate", "clickthrough rate"),
    "cvr": ("cvr", "conversion rate"),
    "cpm": ("cpm", "cost per thousand", "cost per 1,000 impressions"),
    "cpa": ("cpa", "cost per order", "cost per acquisition"),
    "roas": ("roas", "return on ad spend"),
    "poas": ("poas",),
    "aov": ("aov", "order value"),
    "spend": ("spend", "spending"),
    "caa": ("caa", "contribution after ads", "contribution"),
    "cba": ("cba", "contribution before ads"),
    "budget": ("budget", "budgets"),
    "demand": ("demand",),
    "frequency": ("frequency",),
    "price": ("price", "prices"),
    "sessions": ("sessions per click", "session/click", "sessions"),
    "p(loss)": ("p(loss)", "probability of loss", "chance of a loss"),
}
PERIOD_RE = re.compile(r"\b(?:last|past|previous|prior|next|over)\s+(\d+)\s+days?\b|\b(\d+)-day\b")
SKU_RE = re.compile(r"\b[A-Z][A-Z0-9_]+-P\d+\b")
ID_RE = re.compile(r"\b\d{8,}\b")
TOKEN_RE = re.compile(r"\S+")
EDGE = ".,;:!?()'\"[]"


@dataclass
class GuardResult:
    ok: bool
    violations: list[str] = field(default_factory=list)


def _metric_key(metric: str) -> str | None:
    m = metric.lower()
    for key in METRIC_ALIASES:
        if key in m or any(a in m for a in METRIC_ALIASES[key]):
            return key
    return None


def _normalised(text: str, levels: set[str] = frozenset()) -> str:
    t = text.lower().replace("n't", " not").replace("’", "'")
    for p in NEUTRAL_PHRASES:
        t = t.replace(p, " " * len(p))
    for level in ("strong evidence", "weak evidence"):  # a claim-level label, allowed when a cited atom has it
        if level.replace(" ", "_").upper() in levels:
            t = t.replace(level, " " * len(level))
    return re.sub(r"\bwent up\b", "up     ", t)


def _tokens(text: str, levels: set[str] = frozenset()) -> list[str]:
    """Whitespace tokens with edge punctuation stripped ("rose." -> "rose"); decimals stay intact."""
    return [w.strip(EDGE) for w in TOKEN_RE.findall(_normalised(text, levels))]


def _metric_mentions(text: str, cited: list) -> list[tuple[int, object]]:
    """(token position, atom) for every mention of a cited atom's metric (aliases matched on the text)."""
    t = _normalised(text)
    starts = [m.start() for m in TOKEN_RE.finditer(t)]
    out = []
    for atom in cited:
        key = _metric_key(atom.metric)
        if key is None:
            continue
        for alias in METRIC_ALIASES[key]:
            for m in re.finditer(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", t):
                out.append((sum(1 for s in starts if s <= m.start()) - 1, atom))
    return out


def check_sentence(text: str, atom_ids: list[str], pkg: dict) -> GuardResult:
    cfg = narrative_config()["guard"]
    v: list[str] = []
    atoms = pkg["atoms"]
    missing = [a for a in atom_ids if a not in atoms]
    if missing:
        v.append(f"unknown atom ids {missing}")
    cited = [atoms[a] for a in atom_ids if a in atoms]
    for a in cited:
        bad = [e for e in a.evidence_ids if e not in pkg["evidence_ids"]]
        if bad:
            v.append(f"atom {a.atom_id} cites unknown evidence {bad}")
    low = text.lower()
    toks = _tokens(text, {a.claim_level for a in cited})

    # unresolvable constructions
    if "?" in text:
        v.append("question (unresolvable)")
    if CONDITIONAL & set(toks):
        v.append("conditional construction (unresolvable)")
    neg_positions = [i for i, t in enumerate(toks) if t in NEGATORS]
    if len(neg_positions) >= 2:
        v.append("double negation (unresolvable)")

    # periods first (their numbers are not quantities to match); numbers inside an atom's own code-built labels
    # (metric, period, comparison basis, template text) count as cited
    label_nums = {abs(float(n)) for a in cited for n in re.findall(
        r"\d+(?:\.\d+)?", f"{a.metric} {a.period} {a.comparison_basis} {a.text or ''}".replace(",", ""))}
    period_nums = label_nums
    consumed = []
    for m in PERIOD_RE.finditer(low):
        n = float(m.group(1) or m.group(2))
        consumed.append((m.start(), m.end()))
        if n not in period_nums:
            v.append(f"period '{m.group(0)}' contradicts the cited atoms")

    # numbers
    rel = float(cfg["relative_tolerance"])
    for q in parse(text):
        if any(lo <= q.start < hi for lo, hi in consumed):
            continue
        if not cited:
            v.append(f"number '{q.raw}' without a cited atom")
            continue
        ok = any(a.value is not None and matches(q, a.value, a.unit if a.unit != "COUNT" else "PLAIN", rel)
                 for a in cited) or abs(q.value) in label_nums
        if not ok:
            v.append(f"number '{q.raw}' matches no cited atom")

    # direction (with negation scope)
    mentions = _metric_mentions(text, cited)
    scope = int(cfg["negation_scope_tokens"])
    for i, t in enumerate(toks):
        d = "UP" if t in UP_WORDS else ("DOWN" if t in DOWN_WORDS else None)
        if d is None:
            continue
        negated = any(0 < i - p <= scope for p in neg_positions)
        directional = [a for a in cited if a.direction in ("UP", "DOWN", "FLAT")]
        if mentions:
            target = [min(mentions, key=lambda m: (abs(m[0] - i), m[0]))[1]]
            target = [a for a in target if a.direction] or directional
        else:
            target = directional
        if not target:
            v.append(f"direction word '{t}' without a cited directional atom")
            continue
        compatible = [a for a in target if (a.direction != d if negated else a.direction == d)]
        if not compatible:
            v.append(f"'{'not ' if negated else ''}{t}' contradicts {target[0].metric} {target[0].direction}")

    # intensity / significance / evaluative wording
    allow = set(cfg["evaluative_allowlist"])
    for i, t in enumerate(toks):
        if t in INTENSITY:
            near = [m[1] for m in sorted(mentions, key=lambda m: abs(m[0] - i)) if m[1].intensity]
            target = near[:1] or [a for a in cited if a.intensity]
            if not target or all(a.intensity != INTENSITY[t] for a in target):
                v.append(f"intensity '{t}' does not match the cited change "
                         f"({target[0].intensity if target else 'no % atom'})")
        elif t in SIGNIFICANT:
            if not any(a.stat_status in ("STAT", "SHIFT") for a in cited):
                v.append(f"'{t}' without a statistical test that fired")
        elif t in EVALUATIVE and t not in allow:
            v.append(f"evaluative word '{t}' is not allowed")

    # causal wording
    if CAUSAL.search(low):
        v.append(f"causal verb '{CAUSAL.search(low).group(0)}' is not allowed at any level")
    if any(a.claim_level == "QUASI_EXPERIMENTAL" for a in cited):
        if "estimated effect" not in low or "under the stated assumptions" not in low:
            v.append("a quasi-experimental estimate must read 'estimated effect ... under the stated assumptions'")

    # entities: drivers, SKUs, ids, platforms, names, dates
    named_drivers = set()
    for kw, label in DRIVER_KEYWORDS.items():
        if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", low):
            named_drivers.add(label)
    for label in named_drivers - set(pkg["drivers"]):
        v.append(f"driver '{label}' is not supported by this package")
    if named_drivers and not any(a.claim_level in ("EXACT_ACCOUNTING", "QUASI_EXPERIMENTAL") for a in cited) \
            and not any(h in low for h in cfg["hedges"]):
        v.append("a probable driver needs hedged wording (e.g. 'evidence points to', 'associated with')")
    for sku in SKU_RE.findall(text):
        if sku not in pkg["entities"]:
            v.append(f"SKU {sku} is not in the package")
    for eid in ID_RE.findall(text):
        if eid not in pkg["entities"]:
            v.append(f"entity id {eid} is not in the package")
    for word, platform in PLATFORM_WORDS.items():
        if re.search(rf"\b{word}\b", low) and platform not in pkg["platforms"]:
            v.append(f"platform '{word}' is not in the package")
    for seg in re.findall(r"[A-Za-z]+(?:[ ][A-Za-z]+)? \| [^,.;]+", text):
        if not any(seg.strip() in e or e in seg for e in pkg["entities"]):
            v.append(f"entity '{seg.strip()}' is not in the package")
    for d in dates(text):
        if d not in pkg["dates"]:
            v.append(f"date {d} is not in the package")

    # a sentence that claims anything must cite atoms
    claims = bool(parse(text)) or any(t in UP_WORDS or t in DOWN_WORDS or t in INTENSITY for t in toks) \
        or bool(named_drivers)
    if claims and not cited:
        v.append("a claim without cited atoms")
    return GuardResult(not v, v)


def check_next_step(step: dict, pkg: dict) -> GuardResult:
    """next_step is a typed object, never free text (T56): kind from the enum, ref_id from the package's options."""
    if not isinstance(step, dict) or set(step) - {"kind", "ref_id"}:
        return GuardResult(False, ["next_step must be {kind, ref_id} only"])
    if step.get("kind") not in NEXT_STEP_KINDS:
        return GuardResult(False, [f"unknown next_step kind {step.get('kind')}"])
    if {"kind": step["kind"], "ref_id": step.get("ref_id")} not in pkg["next_step_options"]:
        return GuardResult(False, [f"next_step {step} is not one of the package's options"])
    return GuardResult(True)


def check_narrative(nar: dict, pkg: dict) -> GuardResult:
    """Headline (against every atom the sentences cite), each sentence, and next_step."""
    v: list[str] = []
    if not isinstance(nar, dict) or not isinstance(nar.get("sentences"), list) or not nar.get("headline"):
        return GuardResult(False, ["narrative must have a headline and sentences"])
    cited_all = sorted({a for s in nar["sentences"] for a in s.get("atom_ids", [])})
    if "estimated effect" not in str(nar["headline"]).lower():  # the QUASI wording rule binds only if it states one
        cited_all = [a for a in cited_all if a in pkg["atoms"] and pkg["atoms"][a].claim_level != "QUASI_EXPERIMENTAL"]
    h = check_sentence(nar["headline"], cited_all, pkg)
    v += [f"headline: {x}" for x in h.violations]
    for k, s in enumerate(nar["sentences"]):
        r = check_sentence(str(s.get("text", "")), list(s.get("atom_ids", [])), pkg)
        v += [f"sentence {k + 1}: {x}" for x in r.violations]
    n = check_next_step(nar.get("next_step"), pkg)
    v += n.violations
    return GuardResult(not v, v)


def check_answer(text: str, cited_ids: list[str], pkg: dict) -> GuardResult:
    """A Copilot answer (Stage 3 caller): every sentence must pass against the atoms / tool-result ids it cites."""
    v: list[str] = []
    for k, sent in enumerate(s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s):
        v += [f"sentence {k + 1}: {x}" for x in check_sentence(sent, cited_ids, pkg).violations]
    return GuardResult(not v, v)
