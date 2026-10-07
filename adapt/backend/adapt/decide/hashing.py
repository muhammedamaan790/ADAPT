"""Canonical JSON + hashing for decisions and snapshots (spec §22.4).

decision_hash = sha256(JCS(payload)) with RFC 8785 canonical JSON. Before canonicalisation every value is normalised:
rupee amounts -> integers, other floats -> 4 decimals, -0.0 -> 0, arrays of entities sorted by their id by the
caller. NaN / Infinity are forbidden: the serializer raises and no decision is created (T20).
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np
import rfc8785

# keys whose values are rupees (rounded to whole rupees inside hashed payloads)
MONEY_KEYS = frozenset({
    "before", "after", "E", "P10", "P50", "P90", "delta_net_revenue", "raw_pred", "calibrated_pred", "abs_caa",
    "abs_net_revenue", "delta_spend", "unallocated", "reserve_floor", "total_budget", "marginal_value_per_step",
    "objective_change", "amount", "freed_budget", "wasted_spend", "step", "budget", "cap_total", "moved_cap",
})


class NonFiniteValue(ValueError):
    pass


def normalize(obj: Any, key: str | None = None) -> Any:
    if isinstance(obj, dict):
        return {str(k): normalize(v, str(k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [normalize(v, key) for v in obj]
    if isinstance(obj, np.ndarray):
        return [normalize(v, key) for v in obj.tolist()]
    if isinstance(obj, (np.floating, float)):
        x = float(obj)
        if not math.isfinite(x):
            raise NonFiniteValue(f"non-finite value at {key!r}")
        x = float(round(x)) if key in MONEY_KEYS else round(x, 4)
        return 0 if x == 0 else (int(x) if key in MONEY_KEYS else x)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def canonical_bytes(obj: Any) -> bytes:
    return rfc8785.dumps(normalize(obj))


def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def content_hash(obj: Any) -> str:
    return sha256_hex(canonical_bytes(obj))
