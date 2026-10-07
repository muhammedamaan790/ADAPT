"""Driver ranking (B4, spec §7.1 level 2 + §22.1 'Driver ranking').

ROAS-family incidents (ROAS, POAS, CPA) with an OK funnel: the evidence-weighted signed contribution of driver d is
    sum over the levers L that d claims of  c_L x score_d / (sum of scores of the modules claiming L)
where c_L is the exact funnel contribution (B3). These plus "unexplained" sum to ln(ROAS_post/ROAS_pre). Drivers
whose sign opposes the move are OFFSETTING and never top-1. Other incidents rank drivers by module score alone.
Reason levels: STRONG EVIDENCE (>= 0.7), WEAK EVIDENCE (0.4-0.7), considered-not-supported (< 0.4),
not assessable (INSUFFICIENT_DATA / NOT_APPLICABLE).

Demand direction contract (spec §22.1 #7, lever-specific): the demand module SUPPORTS the CVR lever when the sign of
its unpaid-demand shift d equals the sign of the CVR contribution (ROAS family) or of the incident's own move (CVR
incidents); otherwise it is OFFSETTING ("demand was rising, which partly masked the decline"). Only a supporting
demand module claims a share of a lever; an offsetting or undetermined one contributes nothing and is never top-1.
"""

from __future__ import annotations

from adapt.diagnose.evidence import DRIVER_LABEL, OK, Evidence, evidence_config

ROAS_FAMILY = ("ROAS", "POAS", "CPA")
# the lever an incident metric moves; outside the ROAS family only modules claiming that lever can explain it
METRIC_LEVER = {"CTR": "CTR", "CPM": "CPM", "CVR": "CVR", "AOV": "AOV", "SESSION_CLICK": "CVR"}


def level(ev: Evidence) -> str:
    r = evidence_config()["ranking"]
    if ev.status != OK:
        return "NOT_ASSESSABLE"
    if ev.score >= r["strong"]:
        return "STRONG_EVIDENCE"
    if ev.score >= r["weak"]:
        return "WEAK_EVIDENCE"
    return "NOT_SUPPORTED"


def _sign(x: float | None) -> int:
    return 0 if not x else (1 if x > 0 else -1)


def demand_relation(e: Evidence, metric: str, funnel: dict | None, direction: str | None) -> str:
    """SUPPORTS | OFFSETTING | UNDETERMINED for an OK demand module (lever-specific, see module docstring)."""
    d = _sign(e.values.get("d"))
    if metric in ROAS_FAMILY and funnel is not None and funnel.get("status") == "OK":
        lever = _sign(funnel["contributions"].get("CVR"))
    elif metric == "CVR":
        lever = {"UP": 1, "DOWN": -1}.get(direction or "", 0)
    else:
        lever = 0
    if d == 0 or lever == 0:
        return "UNDETERMINED"
    return "SUPPORTS" if d == lever else "OFFSETTING"


def rank(evidence: list[Evidence], metric: str, funnel: dict | None, direction: str | None = None) -> dict:
    rows = [{"module": e.module, "label": DRIVER_LABEL[e.module], "status": e.status, "score": round(e.score, 4),
             "level": level(e), "levers": e.levers, "reason": e.reason} for e in evidence]
    claiming = []  # modules allowed to claim a share of their levers
    for row, e in zip(rows, evidence, strict=True):
        ok = e.status == OK and e.score > 0
        if ok and e.module == "demand":
            rel = demand_relation(e, metric, funnel, direction)
            row["relation"] = rel
            if rel != "SUPPORTS":
                ok = False
                row["offsetting"] = rel == "OFFSETTING"
                if rel == "OFFSETTING":
                    row["reason"] = ("demand was rising, which partly masked the decline" if e.values["d"] > 0
                                     else "demand was falling, which partly masked the rise")
        claiming.append(ok)
    signed = metric in ROAS_FAMILY and funnel is not None and funnel.get("status") == "OK"
    unexplained = None
    if signed:
        c = funnel["contributions"]
        total = sum(c.values())
        claims: dict[str, float] = {}
        for e, ok in zip(evidence, claiming, strict=True):
            if ok:
                for lever in e.levers:
                    claims[lever] = claims.get(lever, 0.0) + e.score
        for row, e, ok in zip(rows, evidence, claiming, strict=True):
            contrib = 0.0
            if ok:
                contrib = sum(c.get(lever, 0.0) * e.score / claims[lever] for lever in e.levers if claims.get(lever))
            row["signed_contribution"] = round(contrib, 6)
            row["offsetting"] = row.get("offsetting", False) or (contrib != 0 and (contrib > 0) != (total > 0))
        explained = sum(r["signed_contribution"] for r in rows)
        unexplained = total - explained
        mags = [abs(r["signed_contribution"]) for r in rows] + [abs(unexplained)]
        denom = sum(mags) or 1.0
        for r in rows:
            r["magnitude_share"] = round(abs(r["signed_contribution"]) / denom, 4)
        key = lambda r: (r["offsetting"] or r["level"] in ("NOT_ASSESSABLE", "NOT_SUPPORTED"),  # noqa: E731
                         -abs(r["signed_contribution"]), -r["score"], r["module"])
    else:
        lever = METRIC_LEVER.get(metric)
        for r in rows:
            if lever is not None and lever not in r["levers"]:
                r["level"], r["reason"] = "NOT_APPLICABLE", f"does not move {lever}"
        key = lambda r: (r["level"] in ("NOT_ASSESSABLE", "NOT_SUPPORTED", "NOT_APPLICABLE")  # noqa: E731
                         or bool(r.get("offsetting")) or r.get("relation") == "UNDETERMINED", -r["score"], r["module"])
    rows.sort(key=key)
    top = rows[0] if rows and rows[0]["level"] in ("STRONG_EVIDENCE", "WEAK_EVIDENCE") \
        and not rows[0].get("offsetting") else None
    return {"method": "evidence_weighted_signed_contribution" if signed else "module_score",
            "top_driver": top["module"] if top else None, "top_level": top["level"] if top else "UNKNOWN",
            "unexplained": None if unexplained is None else round(unexplained, 6), "drivers": rows}
