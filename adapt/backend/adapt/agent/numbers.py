"""Number normalisation for the guard (spec §11 check 2): every number in a sentence is parsed into (value, unit) so it
can be compared with the cited atoms. Handles Indian and western grouping (₹1,25,000 / 125,000), suffixes (k, lakh /
lac / L, crore / Cr), scientific notation (12.5e4), percent and percentage points, multiples (2.1x) and day counts.
ISO dates are extracted separately (they are entity references, not quantities)."""

from __future__ import annotations

import re
from dataclasses import dataclass

SCALE = {"k": 1e3, "thousand": 1e3, "l": 1e5, "lac": 1e5, "lacs": 1e5, "lakh": 1e5, "lakhs": 1e5,
         "cr": 1e7, "crore": 1e7, "crores": 1e7, "mn": 1e6, "million": 1e6}
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUM_RE = re.compile(
    r"(?P<cur>₹|rs\.?\s?|inr\s?)?"
    r"(?P<sign>[-+−])?"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?(?:e[-+]?\d+)?|\.\d+)"
    r"\s?(?P<suffix>%|pp|percentage points?|percent|x\b|k\b|lakhs?\b|lacs?\b|l\b|crores?\b|cr\b|mn\b|million\b"
    r"|thousand\b|days?\b|-day\b)?",
    re.IGNORECASE)


@dataclass(frozen=True)
class Quantity:
    value: float          # in the unit's base (INR, percent points, days, plain)
    unit: str             # INR | PCT | DAYS | X | PLAIN
    decimals: int         # displayed decimals of the mantissa
    scale: float          # suffix multiplier (1 if none): rounding tolerance = 0.5 x 10^-decimals x scale
    raw: str
    start: int
    end: int


def dates(text: str) -> list[str]:
    return DATE_RE.findall(text)


def parse(text: str) -> list[Quantity]:
    """All quantities in `text` (dates and long numeric IDs excluded)."""
    clean = DATE_RE.sub(lambda m: " " * len(m.group(0)), text)
    out = []
    for m in NUM_RE.finditer(clean):
        num = m.group("num")
        digits = num.replace(",", "")
        if len(digits.split(".")[0].split("e")[0]) >= 8 and "," not in num:
            continue  # an entity id (campaign / budget ids are long digit strings), checked as an entity
        try:
            v = float(digits)
        except ValueError:
            continue
        mant = digits.lower().split("e")[0]
        decimals = len(mant.split(".")[1]) if "." in mant else 0
        suffix = (m.group("suffix") or "").lower().strip()
        sign = -1.0 if (m.group("sign") or "") in ("-", "−") else 1.0
        scale = 1.0
        if suffix in ("%", "pp", "percent") or suffix.startswith("percentage"):
            unit = "PCT"
        elif suffix in ("day", "days", "-day"):
            unit = "DAYS"
        elif suffix == "x":
            unit = "X"
        elif suffix in SCALE:
            scale, unit = SCALE[suffix], "INR" if m.group("cur") or suffix not in ("k", "mn", "million") else "PLAIN"
            if m.group("cur") or suffix in ("l", "lac", "lacs", "lakh", "lakhs", "cr", "crore", "crores"):
                unit = "INR"
        elif m.group("cur"):
            unit = "INR"
        else:
            unit = "PLAIN"
        out.append(Quantity(sign * v * scale, unit, decimals, scale, m.group(0).strip(), m.start(), m.end()))
    return out


def matches(q: Quantity, value: float, unit: str, rel_tol: float) -> bool:
    """Does the sentence quantity q state the atom value (value, unit)? Magnitudes are compared (the direction is
    checked separately); percent <-> fraction (12.5% <-> 0.125); INR at the displayed rounding (₹1.2 Cr <-> 1.24e7)."""
    a = abs(value)
    cand = []
    if unit == "PCT":
        if q.unit == "PCT":
            cand.append(abs(q.value))
        elif q.unit == "PLAIN" and abs(q.value) <= 1.0:
            cand.append(abs(q.value) * 100)  # fraction stated for a percent
    elif unit == "FRACTION":
        if q.unit == "PCT":
            cand.append(abs(q.value) / 100)
        elif q.unit == "PLAIN":
            cand.append(abs(q.value))
    elif unit == "INR":
        if q.unit in ("INR", "PLAIN"):
            cand.append(abs(q.value))
    elif unit == "DAYS":
        if q.unit in ("DAYS", "PLAIN"):
            cand.append(abs(q.value))
    elif unit == "X":
        if q.unit in ("X", "PLAIN"):
            cand.append(abs(q.value))
    else:  # COUNT / PLAIN
        if q.unit in ("PLAIN", "DAYS"):
            cand.append(abs(q.value))
    scale = q.scale / (100 if unit == "FRACTION" and q.unit == "PCT" else 1)
    tol_round = 0.5 * 10 ** (-q.decimals) * scale
    if unit == "PCT" and q.unit == "PLAIN":
        tol_round *= 100
    for c in cand:
        if abs(c - a) <= max(rel_tol * a, tol_round + 1e-12):
            return True
    return False
