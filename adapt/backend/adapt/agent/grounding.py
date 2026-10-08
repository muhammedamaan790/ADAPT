"""Number grounding for the Ask ADAPT agent (spec §11 check 2, applied to tool results instead of claim atoms).

Every quantity the agent states must match a value it actually received: a number anywhere in a tool result (or a
quantity written inside a tool result's text), the length of a returned list, or a number the user typed. Matching
reuses the guard's normalisation (₹1,25,000 / 1.25 L / 12.5% <-> 0.125 / 2.1x) and its ±0.5% / displayed-rounding
tolerance. Bare integers up to a small allowance are counts or list numbering and are not treated as claims.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from adapt.agent import numbers as N

UNITS = ("INR", "PCT", "FRACTION", "X", "DAYS", "PLAIN")
STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")


@dataclass
class Grounding:
    ok: bool
    unverified: list[str] = field(default_factory=list)


def values_in(obj, out: list[float] | None = None) -> list[float]:
    """Every number reachable in a JSON-like value: numeric leaves, quantities inside strings, list lengths."""
    out = [] if out is None else out
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        if math.isfinite(obj):
            out.append(float(obj))
    elif isinstance(obj, str):
        out.extend(q.value for q in N.parse(obj))
        for stamp in STAMP_RE.findall(obj):  # dates and times may be restated as "7 Oct 2026, 09:30"
            out.extend(float(x) for x in re.findall(r"\d+", stamp))
    elif isinstance(obj, dict):
        for v in obj.values():
            values_in(v, out)
    elif isinstance(obj, (list, tuple)):
        out.append(float(len(obj)))
        for v in obj:
            values_in(v, out)
    return out


def check(text: str, known: list[float], rel_tol: float = 0.005, small_int: int = 12) -> Grounding:
    """Which quantities in `text` match none of the `known` values."""
    bad: list[str] = []
    for q in N.parse(text):
        if q.unit == "PLAIN" and q.decimals == 0 and q.scale == 1 and abs(q.value) <= small_int:
            continue
        if not any(N.matches(q, k, unit, rel_tol) for k in known for unit in UNITS):
            if q.raw not in bad:
                bad.append(q.raw)
    return Grounding(not bad, bad)
