"""Level-1 diagnosis (B3, spec §7.1): exact accounting decomposition and rate/mix drill-down.

Funnel identity on window totals: ROAS = CTR * CVR * AOV * 1000 / CPM, so
    ln(ROAS_post/ROAS_pre) = c_CTR + c_CVR + c_AOV + c_CPM,  c_x = ln(x_post/x_pre),  c_CPM = -ln(CPM_post/CPM_pre)
exactly. Rate/mix (midpoint): for M = sum_s w_s m_s (w = the segment's share of the denominator),
    rate_s = (w_pre + w_post)/2 * (m_post - m_pre),  mix_s = (w_post - w_pre) * (m_pre + m_post)/2,
and sum_s (rate_s + mix_s) = M_post - M_pre exactly. A segment missing on one side has w = 0 and m = 0 there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

FUNNEL_ORDER = ("CTR", "CVR", "AOV", "CPM")


@dataclass(frozen=True)
class FunnelDecomposition:
    status: str                         # OK | COLLAPSE
    collapsed_factor: str | None
    pre: dict[str, float | None]
    post: dict[str, float | None]
    contributions: dict[str, float]     # signed log contribution per lever (empty on COLLAPSE)
    pct_effect: dict[str, float]        # e^c - 1
    share_of_movement: dict[str, float]  # |c| / sum |c|  (a magnitude share, not "% of the drop")
    total_log_change: float | None      # ln(ROAS_post / ROAS_pre)
    absolute_deltas: dict[str, float | None] = field(default_factory=dict)


def funnel_factors(t: dict[str, float]) -> dict[str, float | None]:
    def ratio(a: float, b: float, scale: float = 1.0) -> float | None:
        return scale * a / b if b else None

    return {"CTR": ratio(t["clicks"], t["impressions"]), "CVR": ratio(t["orders"], t["clicks"]),
            "AOV": ratio(t["revenue"], t["orders"]), "CPM": ratio(t["spend"], t["impressions"], 1000.0),
            "ROAS": ratio(t["revenue"], t["spend"])}


def roas_decomposition(pre: dict[str, float], post: dict[str, float]) -> FunnelDecomposition:
    """pre/post: window totals with keys impressions, clicks, orders, revenue, spend."""
    fp, fq = funnel_factors(pre), funnel_factors(post)
    deltas = {k: (fq[k] - fp[k]) if fp[k] is not None and fq[k] is not None else None for k in fp}
    for k in FUNNEL_ORDER:
        if not (fp[k] and fq[k] and fp[k] > 0 and fq[k] > 0):
            return FunnelDecomposition("COLLAPSE", k, fp, fq, {}, {}, {}, None, deltas)
    c = {k: math.log(fq[k] / fp[k]) for k in ("CTR", "CVR", "AOV")}
    c["CPM"] = -math.log(fq["CPM"] / fp["CPM"])
    total_abs = sum(abs(v) for v in c.values())
    return FunnelDecomposition(
        status="OK", collapsed_factor=None, pre=fp, post=fq, contributions=c,
        pct_effect={k: math.expm1(v) for k, v in c.items()},
        share_of_movement={k: (abs(v) / total_abs if total_abs else 0.0) for k, v in c.items()},
        total_log_change=math.log(fq["ROAS"] / fp["ROAS"]), absolute_deltas=deltas)


@dataclass(frozen=True)
class SegmentEffect:
    segment: str
    w_pre: float
    w_post: float
    m_pre: float
    m_post: float
    rate_effect: float
    mix_effect: float

    @property
    def combined(self) -> float:
        return self.rate_effect + self.mix_effect


def rate_mix(pre: dict[str, tuple[float, float]], post: dict[str, tuple[float, float]],
             scale: float = 1.0) -> tuple[list[SegmentEffect], float, float]:
    """pre/post: segment -> (denominator, numerator). Returns (effects, M_pre, M_post);
    sum(combined) = M_post - M_pre."""
    d_pre = sum(d for d, _ in pre.values())
    d_post = sum(d for d, _ in post.values())
    effects = []
    for s in sorted(set(pre) | set(post)):
        dp, np_ = pre.get(s, (0.0, 0.0))
        dq, nq = post.get(s, (0.0, 0.0))
        w_pre = dp / d_pre if d_pre else 0.0
        w_post = dq / d_post if d_post else 0.0
        m_pre = scale * np_ / dp if dp else 0.0
        m_post = scale * nq / dq if dq else 0.0
        effects.append(SegmentEffect(
            s, w_pre, w_post, m_pre, m_post,
            rate_effect=(w_pre + w_post) / 2 * (m_post - m_pre),
            mix_effect=(w_post - w_pre) * (m_pre + m_post) / 2))
    m_pre_total = scale * sum(n for _, n in pre.values()) / d_pre if d_pre else 0.0
    m_post_total = scale * sum(n for _, n in post.values()) / d_post if d_post else 0.0
    return effects, m_pre_total, m_post_total


@dataclass(frozen=True)
class DrilldownResult:
    dimension: str
    segments: list[SegmentEffect]   # the minimal explaining set, ranked
    explained_share: float          # sum |combined| of the set / sum |combined| of all segments
    parent_delta: float


def drilldown(dimensions: dict[str, tuple[dict, dict]], scale: float = 1.0, target: float = 0.8,
              max_segments: int = 5) -> DrilldownResult | None:
    """Adtributor-style: per dimension rank segments by |rate + mix| (ties by id), take the smallest prefix explaining
    >= target of the total absolute effect (or max_segments); report the dimension with the smallest such set
    (ties: higher explained share, then dimension name)."""
    best: tuple | None = None
    for dim in sorted(dimensions):
        pre, post = dimensions[dim]
        effects, m_pre, m_post = rate_mix(pre, post, scale)
        total = sum(abs(e.combined) for e in effects)
        if total == 0:
            continue
        ranked = sorted(effects, key=lambda e: (-abs(e.combined), e.segment))
        chosen, acc = [], 0.0
        for e in ranked:
            chosen.append(e)
            acc += abs(e.combined)
            if acc / total >= target or len(chosen) >= max_segments:
                break
        cand = (len(chosen), -acc / total, dim)
        if best is None or cand < best[0]:
            best = (cand, DrilldownResult(dim, chosen, acc / total, m_post - m_pre))
    return best[1] if best else None
