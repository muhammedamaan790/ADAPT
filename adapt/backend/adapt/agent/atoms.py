"""Claim atoms (spec §11, the single schema definition) and evidence packages, built deterministically by code BEFORE
any LLM call. Every fact a narrative may state is an atom:
  {atom_id, evidence_ids[], entity, metric, direction[UP|DOWN|FLAT], value, unit, period, comparison_basis,
   claim_level[EXACT_ACCOUNTING|QUASI_EXPERIMENTAL|STRONG_EVIDENCE|WEAK_EVIDENCE|MODEL_ESTIMATE|UNKNOWN],
   qualifier[probable|estimated|measured|unknown], stat_status[STAT|SHIFT|NONE], causal_assumptions[]}
plus two derived fields the guard uses: `intensity` (slight / moderate / sharp / severe from |value| of a % atom,
spec §11 qualifier atoms) and `driver` (the driver label an evidence atom is about).

A package = {kind, ref_id, atoms{id: atom}, evidence_ids, entities, drivers, platforms, dates, next_step_options}.
The whitelists are what a sentence may name; anything else is rejected by the guard (T22).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config" / "narrative.yaml"
CLAIM_LEVELS = ("EXACT_ACCOUNTING", "QUASI_EXPERIMENTAL", "STRONG_EVIDENCE", "WEAK_EVIDENCE", "MODEL_ESTIMATE",
                "UNKNOWN")
METRIC_UNIT = {"CTR": "PCT", "CVR": "PCT", "CPM": "INR", "CPC": "INR", "CPA": "INR", "AOV": "INR", "ROAS": "X",
               "POAS": "X", "SESSION_CLICK": "X", "SPEND": "INR", "SKU_UNITS": "COUNT"}
NEXT_STEP_KINDS = ("VIEW_DECISION", "APPROVE_REVIEW", "RECONCILE", "INVESTIGATE", "NONE")


@lru_cache
def narrative_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


@dataclass
class Atom:
    atom_id: str
    evidence_ids: list[str]
    entity: str
    metric: str
    direction: str | None
    value: float | None
    unit: str                      # PCT | INR | X | DAYS | COUNT | FRACTION | NONE
    period: str
    comparison_basis: str
    claim_level: str
    qualifier: str
    stat_status: str = "NONE"
    causal_assumptions: list[str] = field(default_factory=list)
    intensity: str | None = None
    driver: str | None = None
    text: str | None = None        # a deterministic phrase for the template (never shown as LLM output)

    def __post_init__(self):
        if self.claim_level not in CLAIM_LEVELS:
            raise ValueError(f"unknown claim level {self.claim_level}")
        if self.unit == "PCT" and self.value is not None and self.intensity is None:
            self.intensity = intensity_of(abs(self.value))


def intensity_of(pct: float) -> str:
    for word, (lo, hi) in narrative_config()["guard"]["qualifier_bands_pct"].items():
        if lo <= pct < hi:
            return word
    return "severe"


def _direction(x: float | None, tol: float = 1e-9) -> str:
    if x is None or abs(x) <= tol:
        return "FLAT"
    return "UP" if x > 0 else "DOWN"


def package_hash(pkg: dict) -> str:
    body = {k: v for k, v in pkg.items() if k != "atoms"} | {"atoms": {k: asdict(a) for k, a in pkg["atoms"].items()}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _names(db) -> dict[str, str]:
    return dict(db.query("SELECT campaign_id, name FROM core.campaigns"))


def _base(kind: str, ref_id: str) -> dict:
    return {"kind": kind, "ref_id": ref_id, "atoms": {}, "evidence_ids": set(), "entities": set(), "drivers": set(),
            "platforms": set(), "dates": set(), "next_step_options": [{"kind": "NONE", "ref_id": None}]}


def _add(pkg: dict, atom: Atom) -> None:
    pkg["atoms"][atom.atom_id] = atom
    pkg["evidence_ids"].update(atom.evidence_ids)


# ---- incident package ------------------------------------------------------------------------------------------------
def incident_package(db, anomaly_id: str) -> dict:
    row = db.query("""SELECT scope, entity_ids, platform, metric, direction, classification, window_start, window_end,
                             actual, expected, relative_change, signed_impact, stat_fired, shift_fired, collapse
                      FROM intel.anomalies WHERE anomaly_id = ?""", [anomaly_id])
    if not row:
        raise KeyError(f"anomaly {anomaly_id} not found")
    (scope, ids, platform, metric, direction, cls, lo, hi, actual, expected, rel, impact, stat, shift,
     collapse) = row[0]
    ids = json.loads(ids)
    names = _names(db)
    entity = platform.title() + " (platform)" if scope == "platform" else names.get(ids[0], ids[0])
    days = (hi - lo).days + 1
    period = f"last {days} days"
    pkg = _base("incident", anomaly_id)
    pkg["entities"].update({entity, *ids, *(names.get(i, i) for i in ids)})
    pkg["platforms"].add((platform or "").lower())
    pkg["dates"].update({lo.isoformat(), hi.isoformat()})
    stat_status = "STAT" if stat else ("SHIFT" if shift else "NONE")
    if rel is not None:
        _add(pkg, Atom("A1", [anomaly_id], entity, metric, direction, round(abs(rel) * 100, 1), "PCT", period,
                       "vs the out-of-sample expected value", "EXACT_ACCOUNTING", "measured", stat_status,
                       text=f"{metric} moved {rel * 100:+.1f}% vs expected over the {period}"))
    if actual is not None and expected is not None:
        unit = METRIC_UNIT.get(metric, "NONE")
        scale = 100.0 if unit == "PCT" else 1.0
        _add(pkg, Atom("A2", [anomaly_id], entity, metric, direction, round(actual * scale, 4), unit, period,
                       "actual", "EXACT_ACCOUNTING", "measured", stat_status))
        _add(pkg, Atom("A3", [anomaly_id], entity, metric, None, round(expected * scale, 4), unit, period,
                       "expected (out-of-sample forecast)", "MODEL_ESTIMATE", "estimated"))
    if impact is not None:
        _add(pkg, Atom("A4", [anomaly_id], entity, "CBA impact", None, round(abs(impact)), "INR", period,
                       "contribution before ads vs expected", "MODEL_ESTIMATE", "estimated"))
    dec = db.query("SELECT funnel FROM intel.decompositions WHERE anomaly_id = ? ORDER BY as_of DESC LIMIT 1",
                   [anomaly_id])
    if dec:
        funnel = json.loads(dec[0][0])
        if funnel.get("status") == "OK":
            for k, lever in enumerate(("CTR", "CVR", "AOV", "CPM"), start=1):
                change = (funnel["post"][lever] / funnel["pre"][lever] - 1) * 100  # the lever's own change
                _add(pkg, Atom(f"F{k}", [f"DCMP-{anomaly_id}"], entity, lever, _direction(change),
                               round(abs(change), 1), "PCT", period, "vs the 28 days before",
                               "EXACT_ACCOUNTING", "measured",
                               text=f"{lever} {change:+.1f}% vs the 28 days before (exact funnel accounting)"))
        else:
            _add(pkg, Atom("F0", [f"DCMP-{anomaly_id}"], entity, funnel.get("collapsed_factor") or "funnel", "DOWN",
                           None, "NONE", period, "vs the 28 days before", "EXACT_ACCOUNTING", "measured",
                           text=f"funnel collapse at {funnel.get('collapsed_factor')}"))
    diag = db.query("SELECT ranking FROM intel.diagnoses WHERE anomaly_id = ? ORDER BY as_of DESC LIMIT 1",
                    [anomaly_id])
    ev = {m: (json.loads(v) if v else {}) for m, v in db.query(
        """SELECT module, "values" FROM intel.evidence WHERE anomaly_id = ?
           QUALIFY row_number() OVER (PARTITION BY module ORDER BY as_of DESC) = 1""", [anomaly_id])}
    if diag:
        ranking = json.loads(diag[0][0])
        k = 0
        for d in ranking["drivers"]:
            if d["level"] not in ("STRONG_EVIDENCE", "WEAK_EVIDENCE") or d.get("offsetting"):
                continue
            k += 1
            key, unit, val, metric_name, mdir = _driver_value(d["module"], ev.get(d["module"], {}))
            pkg["drivers"].add(d["label"])
            _add(pkg, Atom(f"D{k}", [f"EVD-{anomaly_id}-{d['module']}"], entity, metric_name, mdir, val, unit,
                           period, "evidence module", d["level"], "probable", driver=d["label"],
                           text=f"{d['label']} ({d['level'].replace('_', ' ').lower()}, score {d['score']:.2f})"))
    causal = db.query("""SELECT status, reason, effect_pct, ci_lo, ci_hi, inr_translation, details FROM
                         intel.causal_estimates WHERE anomaly_id = ? ORDER BY as_of DESC LIMIT 1""", [anomaly_id])
    if causal:
        status, reason, eff, lo_ci, hi_ci, inr, details = causal[0]
        assumptions = (json.loads(details) or {}).get("assumptions", [])
        if status == "ESTIMATED":
            _add(pkg, Atom("C1", [f"CSL-{anomaly_id}"], entity, "ROAS", _direction(eff), round(abs(eff) * 100, 1),
                           "PCT", period, "vs the synthetic control", "QUASI_EXPERIMENTAL", "estimated",
                           causal_assumptions=assumptions))
            _add(pkg, Atom("C2", [f"CSL-{anomaly_id}"], entity, "ROAS effect 90% interval", None,
                           round(lo_ci * 100, 1), "PCT", period, "lower bound", "QUASI_EXPERIMENTAL", "estimated",
                           causal_assumptions=assumptions))
            _add(pkg, Atom("C3", [f"CSL-{anomaly_id}"], entity, "ROAS effect 90% interval", None,
                           round(hi_ci * 100, 1), "PCT", period, "upper bound", "QUASI_EXPERIMENTAL", "estimated",
                           causal_assumptions=assumptions))
            _add(pkg, Atom("C4", [f"CSL-{anomaly_id}"], entity, "accounting translation", None, round(abs(inr)),
                           "INR", period, "of the estimated effect", "QUASI_EXPERIMENTAL", "estimated",
                           causal_assumptions=assumptions))
        else:
            _add(pkg, Atom("C0", [f"CSL-{anomaly_id}"], entity, "causal estimate", None, None, "NONE", period,
                           "synthetic control", "UNKNOWN", "unknown", text=f"no causal estimate: {reason}"))
    decisions = [d for (d,) in db.query("""SELECT decision_id FROM intel.decisions
                                           WHERE created_at >= ? ORDER BY decision_id""", [hi])] \
        if db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'intel' "
                    "AND table_name = 'decisions'") else []
    pkg["next_step_options"] += [{"kind": "VIEW_DECISION", "ref_id": d} for d in decisions] + \
        [{"kind": "INVESTIGATE", "ref_id": anomaly_id}]
    return pkg


def _driver_value(module: str, v: dict) -> tuple[str, str, float | None, str, str | None]:
    """(value key, unit, value, metric name, direction) of the headline number of an evidence module."""
    table = {"auction": ("median_cpm_change_pct", "PCT", "CPM", 1), "fatigue": ("ctr_decline_pct", "PCT", "CTR", -1),
             "inventory": ("exposed_share", "FRACTION", "exposed SKU share", 0),
             "price": ("price_change_pct", "PCT", "price", 1),
             "tracking": ("session_click_drop_pct", "PCT", "sessions per click", -1),
             "saturation": ("frequency_change_pct", "PCT", "frequency", 1),
             "demand": ("demand_change_pct", "PCT", "unpaid demand", 1)}
    key, unit, metric, sign = table.get(module, (None, "NONE", module, 0))
    val = v.get(key) if key else None
    if val is None:
        return key, "NONE", None, metric, None
    if sign == -1:  # values reported as a decline / drop magnitude
        return key, unit, round(abs(float(val)), 2), metric, "DOWN" if float(val) > 0 else "UP"
    if sign == 1:
        return key, unit, round(abs(float(val)), 2), metric, _direction(float(val))
    return key, unit, round(float(val), 4), metric, None


# ---- decision package ------------------------------------------------------------------------------------------------
def decision_package(db, decision_id: str) -> dict:
    row = db.query("SELECT payload, class, type FROM intel.decisions WHERE decision_id = ?", [decision_id])
    if not row:
        raise KeyError(f"decision {decision_id} not found")
    d, cls, typ = json.loads(row[0][0]), row[0][1], row[0][2]
    names = _names(db)
    pkg = _base("decision", decision_id)
    exp = d["expected"]
    ent = "the portfolio"
    pkg["entities"].add(ent)
    period = "next 7 days"
    ev = [decision_id]
    _add(pkg, Atom("E1", ev, ent, "expected change in CAA", _direction(exp["E"]), round(abs(exp["E"])), "INR", period,
                   "vs doing nothing", "MODEL_ESTIMATE", "estimated",
                   text=f"expected change in contribution after ads {exp['E']:+,.0f} rupees over the {period}"))
    _add(pkg, Atom("E2", ev, ent, "CAA P10", _direction(exp["p10"]), round(abs(exp["p10"])), "INR", period,
                   "10th percentile of 200 joint bootstrap draws", "MODEL_ESTIMATE", "estimated"))
    _add(pkg, Atom("E3", ev, ent, "CAA P90", _direction(exp["p90"]), round(abs(exp["p90"])), "INR", period,
                   "90th percentile", "MODEL_ESTIMATE", "estimated"))
    _add(pkg, Atom("E4", ev, ent, "model P(loss)", None, round(exp["prob_loss"] * 100, 1), "PCT", period,
                   "share of joint bootstrap draws with a loss", "MODEL_ESTIMATE", "estimated"))
    _add(pkg, Atom("E5", ev, ent, "calibrated prediction", _direction(exp["calibrated_pred"]),
                   round(abs(exp["calibrated_pred"])), "INR", period,
                   f"x optimism correction factor {exp.get('optimism_correction_factor')}", "MODEL_ESTIMATE",
                   "estimated"))
    if d.get("unallocated"):
        _add(pkg, Atom("U1", ev, ent, "unallocated budget", None, round(d["unallocated"]), "INR", "per day",
                       "left unspent", "MODEL_ESTIMATE", "estimated"))
    for k, leg in enumerate(d["legs"], start=1):
        name = names.get(leg["campaign_ids"][0], leg["unit_id"]) if leg.get("campaign_ids") else leg["unit_id"]
        pkg["entities"].update({name, leg["unit_id"], *leg.get("campaign_ids", [])})
        pkg["platforms"].add(leg["platform"])
        _add(pkg, Atom(f"L{k}", ev, name, "daily budget", _direction(leg["after"] - leg["before"]),
                       round(abs(leg["after"] - leg["before"])), "INR", "per day", f"from {leg['before']:,.0f}",
                       "EXACT_ACCOUNTING", "measured", text=f"{name}: {leg['before']:,.0f} -> {leg['after']:,.0f}"))
        _add(pkg, Atom(f"L{k}a", ev, name, "daily budget after", None, round(leg["after"]), "INR", "per day",
                       "new budget", "EXACT_ACCOUNTING", "measured"))
        _add(pkg, Atom(f"L{k}b", ev, name, "daily budget before", None, round(leg["before"]), "INR", "per day",
                       "current budget", "EXACT_ACCOUNTING", "measured"))
    for k, w in enumerate(d.get("why_not", [])[:5], start=1):
        name = w["unit_id"]
        pkg["entities"].add(name)
        val = w.get("marginal_value_per_step")
        _add(pkg, Atom(f"W{k}", ev, name, w.get("binding_constraint") or w.get("reason") or "why not", None,
                       None if val is None else round(abs(val)), "INR" if val is not None else "NONE", "per step",
                       "at the final allocation", "MODEL_ESTIMATE", "estimated", text=w.get("text")))
    pkg["next_step_options"] += [{"kind": "APPROVE_REVIEW", "ref_id": decision_id},
                                 {"kind": "VIEW_DECISION", "ref_id": decision_id}]
    pkg["meta"] = {"class": cls, "type": typ}
    return pkg
