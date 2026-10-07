"""Counter-based world randomness (spec §10).

Every stream is keyed by (seed, day, entity_id, purpose) and never by call order, so a change in one
strategy's budgets cannot shift any other stream: all strategy forks of a seed face identical exogenous
shocks and identical latent prospects (common random numbers, T35/T46).
"""

from __future__ import annotations

import hashlib
import json

import numpy as np


def stream_key(seed: int, day: int, entity_id: str, purpose: str) -> int:
    """128-bit Philox key. JSON encoding keeps the key unambiguous whatever characters the IDs contain."""
    material = json.dumps([int(seed), int(day), str(entity_id), str(purpose)], separators=(",", ":")).encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:16], "big")


def generator(seed: int, day: int, entity_id: str, purpose: str) -> np.random.Generator:
    return np.random.Generator(np.random.Philox(key=stream_key(seed, day, entity_id, purpose)))


def uniforms(seed: int, day: int, entity_id: str, purpose: str, n: int) -> np.ndarray:
    """The first n uniforms of a stream. Index-addressable: uniforms(..., n)[:m] == uniforms(..., m) for m <= n."""
    if n < 0:
        raise ValueError("n must be >= 0")
    return generator(seed, day, entity_id, purpose).random(n)
