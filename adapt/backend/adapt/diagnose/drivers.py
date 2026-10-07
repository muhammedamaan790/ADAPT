"""Driver ranking (B4, spec §7.1 level 2 + §22.1 'Driver ranking').

ROAS-family incidents (ROAS, POAS, CPA) with an OK funnel: the evidence-weighted signed contribution of driver d is
    sum over the levers L that d claims of  c_L x score_d / (sum of scores of the modules claiming L)
where c_L is the exact funnel contribution (B3). These plus "unexplained" sum to ln(ROAS_post/ROAS_pre). Drivers
whose sign opposes the move are OFFSETTING and never top-1. Other incidents rank drivers by module score alone.
Reason levels: STRONG EVIDENCE (>= 0.7), WEAK EVIDENCE (0.4-0.7), considered-not-supported (< 0.4),
not assessable (INSUFFICIENT_DATA / NOT_APPLICABLE).
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


def rank(evidence: list[Evidence], metric: str, funnel: dict | None) -> dict:
    rows = [{"module": e.module, "label": DRIVER_LABEL[e.module], "status": e.status, "score": round(e.score, 4),
             "level": level(e), "levers": e.levers, "reason": e.reason} for e in evidence]
    signed = metric in ROAS_FAMILY and funnel is not None and funnel.get("status") == "OK"
    unexplained = None
    if signed:
        c = funnel["contributions"]
        total = sum(c.values())
        claims: dict[str, float] = {}
        for e in evidence:
            if e.status == OK and e.score > 0:
                for lever in e.levers:
                    claims[lever] = claims.get(lever, 0.0) + e.score
        for row, e in zip(rows, evidence, strict=True):
            contrib = 0.0
            if e.status == OK and e.score > 0:
                contrib = sum(c.get(lever, 0.0) * e.score / claims[lever] for lever in e.levers if claims.get(lever))
            row["signed_contribution"] = round(contrib, 6)
            row["offsetting"] = contrib != 0 and (contrib > 0) != (total > 0)
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
        key = lambda r: (r["level"] in ("NOT_ASSESSABLE", "NOT_SUPPORTED", "NOT_APPLICABLE"),  # noqa: E731
                         -r["score"], r["module"])
    rows.sort(key=key)
    top = rows[0] if rows and rows[0]["level"] in ("STRONG_EVIDENCE", "WEAK_EVIDENCE") \
        and not rows[0].get("offsetting") else None
    return {"method": "evidence_weighted_signed_contribution" if signed else "module_score",
            "top_driver": top["module"] if top else None, "top_level": top["level"] if top else "UNKNOWN",
            "unexplained": None if unexplained is None else round(unexplained, 6), "drivers": rows}
