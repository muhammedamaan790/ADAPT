"""Daily brief (Stage 2, with the narrator; spec §11): what changed, why, what ADAPT did, what awaits approval. Every
number comes from deterministic atoms over the day's intel / exec / learn tables; the narrator (or its template)
writes the prose and the same guard checks it."""

from __future__ import annotations

import json
from datetime import datetime

from adapt.agent.atoms import Atom, _base
from adapt.agent.groq_client import GroqClient
from adapt.agent.narrator import narrate, persist


def _exists(db, schema: str, table: str) -> bool:
    return bool(db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                         [schema, table]))


def brief_package(db, as_of: datetime) -> dict:
    pkg = _base("brief", f"brief-{as_of:%Y%m%d}")
    period = "today"
    atoms: list[Atom] = []
    if _exists(db, "intel", "anomalies"):
        rows = db.query("""SELECT anomaly_id, metric, direction, relative_change, entity_ids, platform FROM
                           intel.anomalies WHERE is_incident AND status <> 'resolved' AND last_detected_at = ?
                           ORDER BY abs(signed_impact) DESC""", [as_of])
        atoms.append(Atom("B1", [f"RUN-{as_of:%Y%m%d}"], "the brand", "open incidents", None, len(rows), "COUNT",
                          period, "detected in today's run", "EXACT_ACCOUNTING", "measured"))
        if rows:
            aid, metric, direction, rel, ids, platform = rows[0]
            names = dict(db.query("SELECT campaign_id, name FROM core.campaigns"))
            ent = names.get(json.loads(ids)[0], json.loads(ids)[0])
            pkg["entities"].update({ent, *json.loads(ids)})
            pkg["platforms"].add((platform or "").lower())
            if rel is not None:
                atoms.append(Atom("B2", [aid], ent, metric, direction, round(abs(rel) * 100, 1), "PCT", period,
                                  "the largest incident by rupee impact", "EXACT_ACCOUNTING", "measured"))
            pkg["next_step_options"].append({"kind": "INVESTIGATE", "ref_id": aid})
    if _exists(db, "ops", "decision_events"):
        last = db.query("""SELECT decision_id, event FROM ops.decision_events
                           QUALIFY row_number() OVER (PARTITION BY decision_id ORDER BY seq DESC) = 1""")
        pending = sorted(d for d, e in last if e == "submitted")
        atoms.append(Atom("B3", [f"RUN-{as_of:%Y%m%d}"], "ADAPT", "decisions awaiting approval", None, len(pending),
                          "COUNT", period, "pending approval", "EXACT_ACCOUNTING", "measured"))
        pkg["next_step_options"] += [{"kind": "APPROVE_REVIEW", "ref_id": d} for d in pending]
    if _exists(db, "exec", "sagas"):
        n = db.query("SELECT count(*) FROM exec.sagas WHERE kind = 'EXECUTION' AND state = 'SUCCEEDED' "
                     "AND updated_at >= ?", [as_of.replace(hour=0, minute=0)])[0][0]
        atoms.append(Atom("B4", [f"RUN-{as_of:%Y%m%d}"], "ADAPT", "decisions executed and verified", None, n, "COUNT",
                          period, "verified by read-back", "EXACT_ACCOUNTING", "measured"))
    if _exists(db, "learn", "outcomes"):
        rows = db.query("SELECT verdict, count(*) FROM learn.outcomes GROUP BY 1 ORDER BY 1")
        for k, (verdict, n) in enumerate(rows, start=1):
            atoms.append(Atom(f"O{k}", [f"OUT-{verdict}"], "ADAPT", f"{verdict} outcomes", None, n, "COUNT",
                              "to date", "matured outcomes", "EXACT_ACCOUNTING", "measured"))
    for a in atoms:
        pkg["atoms"][a.atom_id] = a
        pkg["evidence_ids"].update(a.evidence_ids)
    return pkg


def brief_template(pkg: dict) -> dict:
    a = pkg["atoms"]
    out = []
    if "B1" in a:
        out.append({"text": f"Today's run opened {a['B1'].value:g} incident(s).", "atom_ids": ["B1"]})
    if "B2" in a:
        verb = {"UP": "rose", "DOWN": "fell"}.get(a["B2"].direction, "moved")
        out.append({"text": f"The largest: {a['B2'].entity}, where {a['B2'].metric} {verb} {a['B2'].value:.1f}%.",
                    "atom_ids": ["B2"]})
    if "B4" in a:
        out.append({"text": f"ADAPT executed and verified {a['B4'].value:g} decision(s).", "atom_ids": ["B4"]})
    if "B3" in a:
        out.append({"text": f"{a['B3'].value:g} decision(s) await approval.", "atom_ids": ["B3"]})
    verdicts = [k for k in a if k.startswith("O")]
    if verdicts:
        out.append({"text": "Matured outcomes to date: " + ", ".join(f"{a[k].metric.split()[0]} {a[k].value:g}"
                                                                     for k in verdicts) + ".",
                    "atom_ids": verdicts})
    step = next((o for o in pkg["next_step_options"] if o["kind"] == "APPROVE_REVIEW"),
                next((o for o in pkg["next_step_options"] if o["kind"] != "NONE"), {"kind": "NONE", "ref_id": None}))
    return {"headline": "Daily brief", "sentences": out, "next_step": step}


def daily_brief(db, as_of: datetime, client: GroqClient | None) -> dict:
    out = narrate(brief_package(db, as_of), client, template=brief_template)
    persist(db, out, as_of)
    return out
