"""portfolio_economics (B6, spec §8.1): one function values a whole allocation jointly, per joint bootstrap draw.

Over horizon H, per draw d:
  1. curve increments  dR_i = R_i(s') - R_i(s)          (daily paths, adstock continued from fit_ts)
  2. cannibalization   booked dR~_i from the steal / shortfall / recapture flows of economics/cannibalization.py
                       (Stage 2; the state's cannibalization config; disabled or T = 0 -> dR~ = dR, Stage 1)
  2b. SKU mix          du_ik = dR~_i x w_ik / nrpu_k     (mapped SKUs; the __unmapped__ share u_i makes no units;
                       transferred revenue lands in the RECEIVING unit's mix)
  3. rationing         H_k = max(ATP_k + released_k, 0); increases on k scaled by min(1, H_k / requested_k);
                       decreases are never scaled; rationed-away units earn nothing (wasted spend is reported)
  4. dCAA = sum du_eff x unit_contribution + sum dR~_i u_i ucr_i - sum (s' - s) x pacing x H
     dnet = sum du_eff x nrpu + sum dR~_i u_i
Absolute values are the status quo (curves at s plus the non-modelled baseline) + delta, so s' = s gives delta = 0
but abs_CAA = CAA0. Units whose curve is MODEL_UNAVAILABLE use a proportional model (observed ROAS x spend), which
the optimizer only ever lets decrease (a cut then assumes revenue falls in proportion: the conservative reading).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from adapt.economics.cannibalization import book, build_T
from adapt.economics.inventory_risk import (
    PROJECTED_SHORTFALL,
    STOCKOUT_PROBABILITY,
    status,
    stockout_probability,
)
from adapt.predict.curves import CurveArtifact


@dataclass
class UnitState:
    unit_id: str
    channel: str
    platform: str
    campaign_ids: list[str]
    budget: float                     # s0, rupees per day
    pacing: float                     # delivered spend / budget (trailing window)
    curve: CurveArtifact | None       # None or status MODEL_UNAVAILABLE -> proportional fallback
    observed_roas: float              # trailing attributed net revenue / spend
    sku_weights: dict[str, float]     # mapped SKUs; sums to 1 - unmapped_share
    unmapped_share: float             # u_i
    unmapped_cr: float                # ucr_i: CBA / net revenue of the unit's unmapped attributed lines
    is_shared: bool = False
    categories: list[str] = field(default_factory=list)   # product sets sold (cannibalization overlap)
    stage: str | None = None          # search | video | prospecting | retargeting (cannibalization coefficient)

    @property
    def model_available(self) -> bool:
        return self.curve is not None and self.curve.status != "MODEL_UNAVAILABLE"


@dataclass
class SkuState:
    sku: str
    nrpu: float                       # expected net revenue per physical unit
    unit_contribution: float          # nrpu - COGS - ship - fee% x nrpu
    available: float                  # on_hand - reserved + inbound_confidence x inbound within H
    safety_stock: float
    baseline_daily: float             # P50 baseline daily demand (champion demand model; seasonal-naive in Stage 1)
    on_hand: float = 0.0
    dispersion: float = 1e6           # NB2 r of the SKU's category (Stage 2 predicate; 1e6 ~ Poisson)

    def atp(self, horizon: int) -> float:
        return self.available - self.safety_stock - self.baseline_daily * horizon


@dataclass
class PortfolioState:
    as_of: datetime
    horizon: int
    units: list[UnitState]
    skus: dict[str, SkuState]
    other_cba_daily: float = 0.0      # contribution of revenue no curve models (organic, direct, email ...)
    other_net_revenue_daily: float = 0.0
    n_draws: int = 200
    meta: dict = field(default_factory=dict)
    cannibalization: dict = field(default_factory=dict)   # {} or {"enabled": false} -> T = 0 (Stage 1)
    inventory_risk: dict = field(default_factory=dict)    # {} -> PROJECTED_SHORTFALL (Stage 1); see inventory_risk.py

    @property
    def risk_kind(self) -> str:
        return self.inventory_risk.get("predicate", PROJECTED_SHORTFALL)

    @property
    def p_unsafe(self) -> float:
        return float(self.inventory_risk.get("p_unsafe", 0.3))


@dataclass
class Economics:
    """Per-draw arrays over the evaluated draws (length n)."""
    abs_caa: np.ndarray
    abs_net_revenue: np.ndarray
    delta_caa: np.ndarray
    delta_net_revenue: np.ndarray
    delta_spend: float
    daily_delta_caa: np.ndarray       # (n, H)
    units_by_sku: dict[str, float]    # expected effective delta units over H
    inventory_risk_by_sku: dict       # {kind, by_sku: {sku: {status, shortfall, projected, available}}}
    exposure_by_unit: dict[str, float]
    wasted_spend: float
    rationing_binds: bool
    baseline_deficit_skus: list[str]
    booked_net_revenue: np.ndarray | None = None   # (n, U) booked increments after cannibalization
    cannibalization: dict = field(default_factory=dict)  # expected flow totals (zero when T = 0)

    def summary(self) -> dict:
        d = self.delta_caa
        return {"E": float(d.mean()), "P10": float(np.percentile(d, 10)), "P50": float(np.percentile(d, 50)),
                "P90": float(np.percentile(d, 90)), "prob_loss": float(np.mean(d < 0)),
                "delta_net_revenue": float(self.delta_net_revenue.mean()), "abs_caa": float(self.abs_caa.mean()),
                "abs_net_revenue": float(self.abs_net_revenue.mean()), "delta_spend": self.delta_spend}


def _hill_paths(P: np.ndarray, x: float, b: float, a0: float, horizon: int) -> np.ndarray:
    """y_t for each parameter row of P (n, 5) under a constant normalised spend x: (n, H)."""
    beta, K, S, theta, gamma = P.T
    a = np.full(len(P), float(a0))
    out = np.empty((len(P), horizon))
    for t in range(horizon):
        a = x + theta * a
        aS = np.maximum(a, 0.0) ** S
        out[:, t] = beta * aS / (K ** S + aS) + gamma * b
    return out


def revenue_paths(unit: UnitState, budget: float, draws: np.ndarray, horizon: int) -> np.ndarray:
    """Expected daily attributed net revenue (rupees) at a constant budget: (n_draws, H)."""
    spend = unit.pacing * budget
    if not unit.model_available:
        return np.full((len(draws), horizon), unit.observed_roas * spend)
    a = unit.curve
    x = spend / a.median_spend
    out = np.zeros((len(draws), horizon))
    if a.w > 0 and a.draws is not None:
        out += a.w * _hill_paths(a.draws[draws % len(a.draws)], x, a.baseline_level, a.terminal_adstock, horizon)
    if a.w < 1:
        out += (1 - a.w) * _hill_paths(a.pooled_draws[draws % len(a.pooled_draws)], x, a.baseline_level,
                                       a.pooled_terminal_adstock, horizon)
    return out * a.median_revenue


class Portfolio:
    """Matrices + a per-(unit, budget) revenue cache, so a search can call evaluate() thousands of times."""

    def __init__(self, state: PortfolioState, draws: np.ndarray | None = None):
        self.state = state
        self.H = state.horizon
        self.draws = np.arange(state.n_draws) if draws is None else np.asarray(draws)
        self.units = state.units
        self.unit_index = {u.unit_id: i for i, u in enumerate(self.units)}
        self.sku_ids = sorted({k for u in self.units for k in u.sku_weights} & set(state.skus))
        sku_pos = {k: j for j, k in enumerate(self.sku_ids)}
        U, K = len(self.units), len(self.sku_ids)
        self.W = np.zeros((U, K))
        for i, u in enumerate(self.units):
            for k, w in u.sku_weights.items():
                if k in sku_pos:
                    self.W[i, sku_pos[k]] = w
        sk = [state.skus[k] for k in self.sku_ids]
        self.nrpu = np.array([s.nrpu for s in sk]) if sk else np.zeros(0)
        self.uc = np.array([s.unit_contribution for s in sk]) if sk else np.zeros(0)
        self.available = np.array([s.available for s in sk]) if sk else np.zeros(0)
        self.ss = np.array([s.safety_stock for s in sk]) if sk else np.zeros(0)
        self.baseline = np.array([s.baseline_daily for s in sk]) * self.H if sk else np.zeros(0)
        self.r = np.array([s.dispersion for s in sk]) if sk else np.zeros(0)
        self.kind, self.p_unsafe = state.risk_kind, state.p_unsafe
        self.atp = self.available - self.ss - self.baseline
        self.u = np.array([u.unmapped_share for u in self.units])
        self.ucr = np.array([u.unmapped_cr for u in self.units])
        self.pacing = np.array([u.pacing for u in self.units])
        self.s0 = np.array([u.budget for u in self.units])
        # contribution per rupee of attributed net revenue (status-quo mix), for absolute values
        self.cm = (self.W * (self.uc / self.nrpu)[None, :]).sum(1) + self.u * self.ucr if K else self.u * self.ucr
        self.T = build_T(self.units, state.cannibalization)   # None = Stage 1 (T = 0)
        self._cache: dict[tuple[int, float], np.ndarray] = {}
        self.R0 = np.stack([self.unit_paths(i, self.s0[i]) for i in range(U)], axis=1) if U else \
            np.zeros((len(self.draws), 0, self.H))
        self.R0h = self.R0.sum(-1)                            # (n, U) status-quo horizon revenue
        # absolute status quo: curves at s plus the non-modelled baseline (CAA0 = E[base_cba])
        self.base_cba = (self.R0h * self.cm[None, :]).sum(1) - float((self.s0 * self.pacing).sum()) * self.H \
            + state.other_cba_daily * self.H

    CACHE_LIMIT = 10_000  # a continuous solver would otherwise fill memory with one-off budgets

    def risk(self, projected: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(risk value, excess over the predicate) per SKU for projected horizon demand. Stage 1: projected shortfall
        in units (excess = shortfall). Stage 2: NB2 P(stockout) (excess = max(P - p_unsafe, 0))."""
        if self.kind == STOCKOUT_PROBABILITY:
            p = stockout_probability(projected, self.available, self.r)
            return p, np.maximum(p - self.p_unsafe, 0.0)
        sf = np.maximum(projected - (self.available - self.ss), 0.0)
        return sf, sf

    def unit_paths(self, i: int, budget: float) -> np.ndarray:
        key = (i, float(budget))
        hit = self._cache.get(key)
        if hit is None:
            hit = revenue_paths(self.units[i], float(budget), self.draws, self.H)
            if len(self._cache) < self.CACHE_LIMIT:
                self._cache[key] = hit
        return hit

    def evaluate(self, s_new: np.ndarray | dict) -> Economics:
        if isinstance(s_new, dict):
            s_new = np.array([s_new.get(u.unit_id, u.budget) for u in self.units], dtype=float)
        s_new = np.asarray(s_new, dtype=float)
        U, H = len(self.units), self.H
        changed = [i for i in range(U) if abs(s_new[i] - self.s0[i]) > 1e-9]
        dR = np.zeros_like(self.R0)  # (n, U, H)
        for i in changed:
            dR[:, i, :] = self.unit_paths(i, s_new[i]) - self.R0[:, i, :]
        dRh = dR.sum(-1)  # (n, U) curve increments
        flows = None
        if self.T is not None:
            flows = book(dRh, self.R0h + dRh, self.T)
            dRb = flows["booked"]
        else:
            dRb = dRh  # Stage 1: booked increment = curve increment

        # SKU mix -> units (booked increments, in the receiving unit's mix)
        du = dRb[:, :, None] * self.W[None, :, :] / self.nrpu[None, None, :] if len(self.sku_ids) else \
            np.zeros((len(self.draws), U, 0))
        released = np.clip(-du, 0, None).sum(1)            # (n, K)
        requested = np.clip(du, 0, None).sum(1)
        headroom = np.maximum(self.atp[None, :] + released, 0.0)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            scale =np.where(requested > headroom, headroom / np.where(requested > 0, requested, 1.0), 1.0)
        du_eff = np.where(du > 0, du * scale[:, None, :], du)
        rationed = du - du_eff                              # >= 0, only on increases

        unit_cba = (du_eff * self.uc[None, None, :]).sum(-1) + dRb * self.u * self.ucr   # (n, U)
        unit_net = (du_eff * self.nrpu[None, None, :]).sum(-1) + dRb * self.u
        dspend_unit = (s_new - self.s0) * self.pacing * H                              # (U,)
        delta_spend = float(dspend_unit.sum())
        dcaa = unit_cba.sum(1) - delta_spend
        dnet = unit_net.sum(1)

        # daily path: each unit's booked contribution spread over days in proportion to its daily increments
        with np.errstate(divide="ignore", invalid="ignore"):
            share = np.where(np.abs(dRh)[:, :, None] > 1e-12, dR / np.where(np.abs(dRh) > 1e-12, dRh, 1.0)[:, :, None],
                             1.0 / H)
        daily = (share * unit_cba[:, :, None]).sum(1) - (dspend_unit.sum() / H)

        base_cba = self.base_cba
        base_net = self.R0.sum(-1).sum(1) + self.state.other_net_revenue_daily * H

        # inventory after the change (expected over draws), through the stage's predicate
        mean_eff = du_eff.sum(1).mean(0)  # (K,)
        projected = self.baseline + mean_eff
        risk, excess = self.risk(projected)
        by_sku = {}
        for j, k in enumerate(self.sku_ids):
            entry = {"status": status(self.kind, float(risk[j]), float(projected[j]), float(self.available[j]),
                                      float(self.ss[j]), self.p_unsafe),
                     "projected": float(projected[j]), "available": float(self.available[j]),
                     "safety_stock": float(self.ss[j])}
            entry["shortfall" if self.kind == PROJECTED_SHORTFALL else "stockout_probability"] = float(risk[j])
            by_sku[k] = entry
        at_risk = excess > 1e-9
        exposure = {u.unit_id: float((self.W[i] * at_risk).sum() + self.u[i]) for i, u in enumerate(self.units)}

        pos_rev = (np.clip(du, 0, None) * self.nrpu[None, None, :]).sum(-1)              # (n, U)
        lost_rev = (rationed * self.nrpu[None, None, :]).sum(-1)
        with np.errstate(divide="ignore", invalid="ignore"):
            lost_share = np.where(pos_rev > 0, lost_rev / np.where(pos_rev > 0, pos_rev, 1.0), 0.0)
        wasted = float((lost_share * np.clip(dspend_unit, 0, None)[None, :]).sum(1).mean())

        return Economics(
            abs_caa=base_cba + dcaa, abs_net_revenue=base_net + dnet, delta_caa=dcaa, delta_net_revenue=dnet,
            delta_spend=delta_spend, daily_delta_caa=daily,
            units_by_sku={k: float(mean_eff[j]) for j, k in enumerate(self.sku_ids)},
            inventory_risk_by_sku={"kind": self.kind, "by_sku": by_sku},
            exposure_by_unit=exposure, wasted_spend=wasted, rationing_binds=bool((rationed > 1e-12).any()),
            baseline_deficit_skus=[k for j, k in enumerate(self.sku_ids) if self.atp[j] < 0],
            booked_net_revenue=dRb,
            cannibalization={"enabled": self.T is not None, **({k: float(v.sum(1).mean()) for k, v in flows.items()
                                                                 if k != "booked"} if flows else {})})


def portfolio_economics(state: PortfolioState, s_new: dict | np.ndarray, draws: np.ndarray | None = None) -> Economics:
    return Portfolio(state, draws).evaluate(s_new)
