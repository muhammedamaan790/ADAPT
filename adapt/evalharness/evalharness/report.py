"""Evaluation report (spec §16): per-seed results -> paired comparisons, oracle capture, detection / diagnosis
aggregates, safety, replay, verdict counts. The actual numbers are shown even when a target is missed.

- Primary: U(adapt vs safe-static) = CAA_adapt - CAA_safe-static per seed, with a paired bootstrap (over seeds) 95% CI.
  Secondary: U(adapt vs safe-contribution); all other comparators always reported (no cherry-picking).
- Oracle capture per seed: U_x = CAA_x - CAA_safe-static; U_oracle <= 0 -> NOT_INTERPRETABLE; else 100 U_adapt /
  U_oracle; aggregated as the median over interpretable seeds, with the non-interpretable count.
"""

from __future__ import annotations

import numpy as np

TARGETS = {"f1": 0.85, "median_latency_days": 2.0, "negative_set": 0.95, "top1": 0.70, "top3": 0.90,
           "safety_violations": 0, "replay": 1.0}


def paired_ci(values: list[float], seed: int = 20261005, n: int = 10_000) -> dict:
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return {"mean": None, "ci95": None, "seeds": 0}
    rng = np.random.default_rng(seed)
    boots = v[rng.integers(0, v.size, size=(n, v.size))].mean(1)
    lo, hi = np.quantile(boots, [0.025, 0.975])
    return {"mean": float(v.mean()), "ci95": [float(lo), float(hi)], "seeds": int(v.size),
            "excludes_zero": bool(lo > 0 or hi < 0)}


def build(decision_runs: dict[int, dict], detection_runs: dict[int, dict] | None = None) -> dict:
    seeds = sorted(decision_runs)
    strategies = sorted({s for r in decision_runs.values() for s in r["strategies"]})
    per_seed = {seed: {s: {k: v for k, v in r["strategies"][s].items() if k != "log"} for s in r["strategies"]}
                for seed, r in decision_runs.items()}
    out = {"N": len(seeds), "seeds": seeds, "per_seed": per_seed, "comparisons": {},
           "per_seed_meta": {seed: {k: r.get(k) for k in ("start_day", "days", "budget_ceiling", "reserve")}
                             for seed, r in decision_runs.items()}}
    for s in strategies:
        if s == "safe-static":
            continue
        u = [decision_runs[k]["strategies"][s]["caa"] - decision_runs[k]["strategies"]["safe-static"]["caa"]
             for k in seeds if s in decision_runs[k]["strategies"] and "safe-static" in decision_runs[k]["strategies"]]
        out["comparisons"][f"U({s} vs safe-static)"] = paired_ci(u)
    if all("safe-contribution" in r["strategies"] and "adapt" in r["strategies"] for r in decision_runs.values()):
        out["comparisons"]["U(adapt vs safe-contribution)"] = paired_ci(
            [r["strategies"]["adapt"]["caa"] - r["strategies"]["safe-contribution"]["caa"]
             for r in decision_runs.values()])
    out["primary"] = out["comparisons"].get("U(adapt vs safe-static)")
    caps, nonint = [], 0
    for r in decision_runs.values():
        st = r["strategies"]
        if not {"oracle", "adapt", "safe-static"} <= set(st):
            continue
        u_o = st["oracle"]["caa"] - st["safe-static"]["caa"]
        if u_o <= 0:
            nonint += 1
        else:
            caps.append(100 * (st["adapt"]["caa"] - st["safe-static"]["caa"]) / u_o)
    pert = [r["oracle_perturbation"]["suboptimality_rate"] for r in decision_runs.values()
            if r.get("oracle_perturbation")]
    out["oracle_suboptimality_rate"] = float(np.mean(pert)) if pert else None
    out["oracle_capture"] = {"median_pct": float(np.median(caps)) if caps else None, "interpretable": len(caps),
                             "NOT_INTERPRETABLE": nonint,
                             "label": "clairvoyant benchmark under true parameters, not an upper bound"}
    out["safety_violations"] = {s: sum(len(r["strategies"][s]["violations"]) for r in decision_runs.values()
                                       if s in r["strategies"]) for s in strategies}
    out["common_random_numbers"] = all(r.get("common_random_numbers") for r in decision_runs.values())
    rep = [r["strategies"]["adapt"].get("replay") for r in decision_runs.values() if "adapt" in r["strategies"]]
    rep = [x for x in rep if x]
    out["replay"] = {"decisions": sum(x["decisions"] for x in rep), "matched": sum(x["matched"] for x in rep)}
    verdicts: dict[str, int] = {}
    for r in decision_runs.values():
        for k, v in (r["strategies"].get("adapt", {}).get("verdicts") or {}).items():
            verdicts[k] = verdicts.get(k, 0) + v
    out["verdicts"] = verdicts
    if detection_runs:
        det = list(detection_runs.values())
        tp = sum(d["detection"]["tp"] for d in det)
        fp = sum(d["detection"]["fp"] for d in det)
        fn = sum(d["detection"]["fn"] for d in det)
        p = tp / (tp + fp) if tp + fp else None
        rc = tp / (tp + fn) if tp + fn else None
        lat = [x[2] for d in det for x in d["detection"]["pairs"] if x[2] >= 0]
        scored = sum(d["diagnosis"]["scored"] for d in det)
        out["detection"] = {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": rc,
                            "f1": 2 * p * rc / (p + rc) if p and rc else 0.0,
                            "median_latency_days": float(np.median(lat)) if lat else None,
                            "early_warning": sum(d["detection"]["early_warning"] for d in det)}
        out["diagnosis"] = {"scored": scored,
                            "top1": (sum(d["diagnosis"]["top1"] * d["diagnosis"]["scored"] for d in det
                                         if d["diagnosis"]["top1"] is not None) / scored) if scored else None,
                            "top3": (sum(d["diagnosis"]["top3"] * d["diagnosis"]["scored"] for d in det
                                         if d["diagnosis"]["top3"] is not None) / scored) if scored else None}
        neg = [i for d in det for i in d["negative_set"]["items"]]
        out["negative_set"] = {"items": len(neg), "accuracy": sum(i["correct"] for i in neg) / len(neg) if neg
                               else None}
    out["targets"] = TARGETS
    return out
