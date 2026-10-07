"""Ground-truth labelling contract (spec §16): incident-level matching, TP / FP / FN, latency, early warning,
diagnosis top-1 / top-3, negative-set scoring. Pure functions (T57).

GT incident: {incident_id, scenario, driver, entity_ids, metric_set, direction{metric: UP|DOWN}, injection_start_day,
onset_day, injection_end_day}. ADAPT incident: {anomaly_id, entity_ids, metric, direction, detection_day,
classification, top_driver, drivers[] (supporting, ranked)}.

- Matchable: entity overlap (incl. platform grouping: a platform incident lists its campaigns), metric in the GT
  metric set, direction equal to the GT direction for that metric, detection day in
  [injection start, injection end + 3].
- One-to-one: among maximum-cardinality matchings, deterministic lexicographic preference: ADAPT incidents in order
  of (detection day, id), each taking the lowest GT id that still allows a maximum-cardinality matching.
- Every unmatched ADAPT efficiency incident is a false positive (duplicates included); unmatched GT = false negative.
- Early warning: a TP detected before onset (but after injection start): latency < 0, reported separately; the
  reported median latency uses only detections at or after onset.
- Diagnosis accuracy on TPs only: top-1 = the highest-ranked supporting driver equals the GT driver category.
- Negative set (S7, S10): correct iff no efficiency incident on the affected entities in [start, end + 3].
"""

from __future__ import annotations

import numpy as np

DRIVER_CATEGORY = {"auction": "auction", "creative_fatigue": "fatigue", "inventory": "inventory", "price": "price",
                   "tracking": "tracking", "demand": "demand", "audience_saturation": "saturation"}
NEGATIVE_SCENARIOS = {"S7", "S10"}
INCIDENT_CLASSES = ("efficiency_anomaly", "tracking_issue")


def matchable(a: dict, g: dict) -> bool:
    return (bool(set(a["entity_ids"]) & set(g["entity_ids"])) and a["metric"] in g["metric_set"]
            and g["direction"].get(a["metric"]) == a["direction"]
            and g["injection_start_day"] <= a["detection_day"] <= g["injection_end_day"] + 3)


def _max_matching(edges: dict[int, list[int]], n_left: int, fixed: dict[int, int]) -> int:
    """Maximum bipartite matching size (Kuhn) given forced pairs (left -> right)."""
    match_r: dict[int, int] = {r: left for left, r in fixed.items()}

    def augment(u, seen):
        for v in edges.get(u, []):
            if v in seen or (v in match_r and match_r[v] in fixed):
                continue
            seen.add(v)
            if v not in match_r or augment(match_r[v], seen):
                match_r[v] = u
                return True
        return False

    size = len(fixed)
    for u in range(n_left):
        if u in fixed:
            continue
        if augment(u, set()):
            size += 1
    return size


def match(adapt: list[dict], gt: list[dict]) -> list[tuple[int, int]]:
    """Index pairs (adapt index, gt index) of the deterministic maximum-cardinality matching."""
    order_a = sorted(range(len(adapt)), key=lambda i: (adapt[i]["detection_day"], adapt[i]["anomaly_id"]))
    order_g = sorted(range(len(gt)), key=lambda j: gt[j]["incident_id"])
    pos_a = {i: k for k, i in enumerate(order_a)}
    edges = {pos_a[i]: [j for j in order_g if matchable(adapt[i], gt[j])] for i in range(len(adapt))}
    best = _max_matching(edges, len(adapt), {})
    fixed: dict[int, int] = {}
    for u in range(len(adapt)):
        for v in edges.get(u, []):
            if v in fixed.values():
                continue
            if _max_matching(edges, len(adapt), {**fixed, u: v}) == best:
                fixed[u] = v
                break
    return [(order_a[u], v) for u, v in sorted(fixed.items())]


def detection_metrics(adapt: list[dict], gt: list[dict]) -> dict:
    eff = [a for a in adapt if a.get("classification", "efficiency_anomaly") in INCIDENT_CLASSES]
    pos = [g for g in gt if g["scenario"] not in NEGATIVE_SCENARIOS]
    pairs = match(eff, pos)
    tp = len(pairs)
    fp = len(eff) - tp
    fn = len(pos) - tp
    lat = [eff[i]["detection_day"] - pos[j]["onset_day"] for i, j in pairs]
    after = [x for x in lat if x >= 0]
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if prec is not None and rec is not None else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec, "f1": f1,
            "median_latency_days": float(np.median(after)) if after else None,
            "early_warning": sum(1 for x in lat if x < 0), "early_warning_rate": (sum(1 for x in lat if x < 0) / tp)
            if tp else None, "pairs": [(eff[i]["anomaly_id"], pos[j]["incident_id"], lat[k])
                                       for k, (i, j) in enumerate(pairs)]}


def diagnosis_metrics(adapt: list[dict], gt: list[dict]) -> dict:
    eff = [a for a in adapt if a.get("classification", "efficiency_anomaly") in INCIDENT_CLASSES]
    pos = [g for g in gt if g["scenario"] not in NEGATIVE_SCENARIOS]
    top1 = top3 = n = 0
    rows = []
    for i, j in match(eff, pos):
        want = DRIVER_CATEGORY.get(pos[j]["driver"], pos[j]["driver"])
        ranked = eff[i].get("drivers") or ([eff[i]["top_driver"]] if eff[i].get("top_driver") else [])
        n += 1
        top1 += int(bool(ranked) and ranked[0] == want)
        top3 += int(want in ranked[:3])
        rows.append({"anomaly_id": eff[i]["anomaly_id"], "gt": pos[j]["incident_id"], "want": want,
                     "ranked": ranked[:3]})
    return {"scored": n, "top1": top1 / n if n else None, "top3": top3 / n if n else None, "rows": rows}


def negative_set(adapt: list[dict], gt: list[dict]) -> dict:
    out = []
    for g in (g for g in gt if g["scenario"] in NEGATIVE_SCENARIOS):
        raised = [a["anomaly_id"] for a in adapt
                  if a.get("classification", "efficiency_anomaly") == "efficiency_anomaly"
                  and set(a["entity_ids"]) & set(g["entity_ids"])
                  and g["injection_start_day"] <= a["detection_day"] <= g["injection_end_day"] + 3]
        out.append({"incident_id": g["incident_id"], "scenario": g["scenario"], "correct": not raised,
                    "raised": raised})
    return {"items": out, "accuracy": (sum(o["correct"] for o in out) / len(out)) if out else None}
