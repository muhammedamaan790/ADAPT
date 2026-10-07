"""Impression-level latent prospects and the strictly conditional funnel (spec §10, §22.9).

A prospect is one impression opportunity. Prospect i of (seed, day, campaign) has fixed uniforms per purpose;
spend only decides how many prospects are exposed (indices 0..I-1). More spend therefore exposes a strict
superset of the same prospects, and two strategies with the same spend and world state see identical outcomes.

Chain: clicked -> session -> purchased -> returned. The SKU is drawn only for purchased prospects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from world.rng import uniforms

PURPOSES = ("click", "session", "purchase", "sku", "return", "user")
CAPACITY_HEADROOM = 1.5


class ProspectCapacityError(RuntimeError):
    """Spend exposed more prospects than the pre-generated pool holds (a hard error, never silently clipped)."""


def n_max(s_max: float, min_cpm: float) -> int:
    """Pool size: N_max = ceil(1.5 x 1000 x S_max / min CPM), S_max = the largest spend policy allows the unit."""
    if min_cpm <= 0:
        raise ValueError("min_cpm must be > 0")
    if s_max < 0:
        raise ValueError("s_max must be >= 0")
    return math.ceil(CAPACITY_HEADROOM * 1000.0 * s_max / min_cpm)


def impressions_bought(spend: float, cpm: float) -> int:
    if cpm <= 0:
        raise ValueError("cpm must be > 0")
    if spend < 0:
        raise ValueError("spend must be >= 0")
    return math.floor(1000.0 * spend / cpm)


@dataclass(frozen=True)
class FunnelInputs:
    """One campaign-day of world state. Rates are the day's truth values (fatigue etc. already applied)."""

    spend: float
    cpm: float
    ctr: float
    click_session_rate: float
    cvr: float
    availability: float = 1.0
    price_factor: float = 1.0
    audience_size: int = 1_000_000
    sku_ids: tuple[str, ...] = ()
    sku_weights: tuple[float, ...] = ()
    return_rates: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        for name in ("ctr", "click_session_rate", "availability"):
            v = getattr(self, name)
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {v}")
        if self.cvr < 0 or self.price_factor < 0:
            raise ValueError("cvr and price_factor must be >= 0")
        if self.audience_size < 1:
            raise ValueError("audience_size must be >= 1")
        n = len(self.sku_ids)
        if n == 0 or len(self.sku_weights) != n or len(self.return_rates) != n:
            raise ValueError("sku_ids, sku_weights and return_rates must be non-empty and the same length")
        if len(set(self.sku_ids)) != n:
            raise ValueError("sku_ids must be unique")
        if any(w < 0 for w in self.sku_weights) or not math.isclose(sum(self.sku_weights), 1.0, abs_tol=1e-9):
            raise ValueError("sku_weights must be >= 0 and sum to 1")
        if any(not 0.0 <= r <= 1.0 for r in self.return_rates):
            raise ValueError("return_rates must be in [0, 1]")

    @property
    def p_buy(self) -> float:
        return min(1.0, self.cvr * self.availability * self.price_factor)


@dataclass(frozen=True)
class FunnelArrays:
    """Per-prospect outcomes for exposed prospects 0..I-1 (sku_index = -1 where not purchased)."""

    clicked: np.ndarray
    session: np.ndarray
    purchased: np.ndarray
    returned: np.ndarray
    sku_index: np.ndarray
    user_id: np.ndarray


@dataclass(frozen=True)
class CampaignDayOutcome:
    impressions: int
    reach: int
    frequency: float | None
    clicks: int
    sessions: int
    purchases: int
    returns: int
    units_by_sku: dict[str, int] = field(default_factory=dict)
    returns_by_sku: dict[str, int] = field(default_factory=dict)


def prospect_funnel(seed: int, day: int, campaign_id: str, inputs: FunnelInputs, pool_size: int) -> FunnelArrays:
    exposed = impressions_bought(inputs.spend, inputs.cpm)
    if exposed > pool_size:
        raise ProspectCapacityError(
            f"{campaign_id} day {day}: {exposed} impressions exceed the prospect pool of {pool_size}"
        )

    def u(purpose: str) -> np.ndarray:
        return uniforms(seed, day, campaign_id, purpose, exposed)

    clicked = u("click") < inputs.ctr
    session = clicked & (u("session") < inputs.click_session_rate)
    purchased = session & (u("purchase") < inputs.p_buy)

    # Order SKUs by ID so the inverse-CDF mapping never depends on input order.
    order = sorted(range(len(inputs.sku_ids)), key=lambda k: inputs.sku_ids[k])
    cdf = np.cumsum([inputs.sku_weights[k] for k in order])
    cdf[-1] = 1.0
    drawn = np.minimum(np.searchsorted(cdf, u("sku"), side="right"), len(order) - 1)
    sorted_to_input = np.asarray(order)
    sku_index = np.where(purchased, sorted_to_input[drawn], -1)

    return_rate = np.asarray(inputs.return_rates)[np.maximum(sku_index, 0)]
    returned = purchased & (u("return") < return_rate)

    user_id = np.floor(u("user") * inputs.audience_size).astype(np.int64)
    return FunnelArrays(clicked, session, purchased, returned, sku_index, user_id)


def simulate_campaign_day(
    seed: int, day: int, campaign_id: str, inputs: FunnelInputs, pool_size: int
) -> CampaignDayOutcome:
    arrays = prospect_funnel(seed, day, campaign_id, inputs, pool_size)
    impressions = int(arrays.clicked.size)
    reach = int(np.unique(arrays.user_id).size)
    units = np.bincount(arrays.sku_index[arrays.purchased], minlength=len(inputs.sku_ids))
    rets = np.bincount(arrays.sku_index[arrays.returned], minlength=len(inputs.sku_ids))
    return CampaignDayOutcome(
        impressions=impressions,
        reach=reach,
        frequency=impressions / reach if reach else None,
        clicks=int(arrays.clicked.sum()),
        sessions=int(arrays.session.sum()),
        purchases=int(arrays.purchased.sum()),
        returns=int(arrays.returned.sum()),
        units_by_sku={s: int(units[k]) for k, s in enumerate(inputs.sku_ids) if units[k]},
        returns_by_sku={s: int(rets[k]) for k, s in enumerate(inputs.sku_ids) if rets[k]},
    )
