"""Shared helpers for the mock reporting endpoints.

Only complete days are reportable: a fact for world day d is visible once the clock has moved past d
(clock day = the day being simulated next). Dates are world calendar dates in the brand time zone.
"""

from __future__ import annotations

import base64
from datetime import date

from fastapi import Request
from fastapi.responses import JSONResponse

from world.state import WorldStore
from world.truth import Truth


class NotSeeded(RuntimeError):
    """Reporting needs a seeded world (truth + history); a bare control-plane store has none."""


def world_of(request: Request) -> tuple[WorldStore, Truth]:
    store: WorldStore = request.app.state.store
    truth = store.ctx.truth
    if truth is None:
        raise NotSeeded("world not seeded: reporting is unavailable")
    return store, truth


def date_of(truth: Truth, day: int) -> date:
    return truth.config.world_date(day)


def day_of(truth: Truth, d: date) -> int:
    return (d - truth.config.history_end_date).days - 1


def reportable_days(store: WorldStore, truth: Truth, since: date | None, until: date | None) -> tuple[int, int]:
    """Clip a requested date range to [first history day, last complete day]. Empty range -> lo > hi."""
    clock = store.clock()
    last_complete = (clock[1] if clock else 0) - 1
    lo = max(-truth.history_days, day_of(truth, since) if since else -truth.history_days)
    hi = min(last_complete, day_of(truth, until) if until else last_complete)
    return lo, hi


def last_complete_date(store: WorldStore, truth: Truth) -> date:
    clock = store.clock()
    return date_of(truth, (clock[1] if clock else 0) - 1)


def iso_local(truth: Truth, day: int, minute: int) -> str:
    d = date_of(truth, day)
    return f"{d.isoformat()}T{minute // 60:02d}:{minute % 60:02d}:00+05:30"


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor") from exc


def daterange(lo: int, hi: int):
    return range(lo, hi + 1)


def not_seeded_response(exc: NotSeeded) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})
