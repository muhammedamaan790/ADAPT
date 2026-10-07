"""Mock Meta Marketing API v25.0 (Graph shapes): campaign daily_budget/status update + read-back (spec §9.3).

daily_budget is in minor units (paise for INR) as a string; status is ACTIVE | PAUSED on the wire.
Graph errors are HTTP 400 with an error code, except a service outage (503) and a lost response (504).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from world.state import UnknownEntity, WorldStore

router = APIRouter(prefix="/meta/v25.0", tags=["mock-meta"])

MINOR_UNITS = 100
WIRE_TO_STATE = {"ACTIVE": "ENABLED", "PAUSED": "PAUSED"}
STATE_TO_WIRE = {v: k for k, v in WIRE_TO_STATE.items()}
READ_FIELDS = {"id", "daily_budget", "status"}

FAULT_HTTP = {
    "rate_limit": (400, 17, "User request limit reached", True),
    "unavailable": (503, 2, "Service temporarily unavailable", True),
    "bad_request": (400, 100, "Invalid parameter", False),
    "timeout_after_success": (504, 1, "Request timed out", True),
}


class CampaignUpdate(BaseModel):
    daily_budget: str | None = None
    status: str | None = None


def meta_error(http: int, code: int, message: str, transient: bool) -> JSONResponse:
    return JSONResponse(
        status_code=http,
        content={"error": {"message": message, "type": "OAuthException", "code": code,
                           "is_transient": transient, "fbtrace_id": "mock"}},
    )


def _store(request: Request) -> WorldStore:
    return request.app.state.store


@router.post("/{campaign_id}")
def update_campaign(
    campaign_id: str, body: CampaignUpdate, request: Request, x_request_id: str | None = Header(default=None)
) -> JSONResponse:
    if body.daily_budget is None and body.status is None:
        return meta_error(400, 100, "No fields to update", False)
    payload: dict = {"platform": "meta", "budget_id": campaign_id}
    if body.daily_budget is not None:
        try:
            minor = int(body.daily_budget)
        except ValueError:
            return meta_error(400, 100, "daily_budget must be an integer in minor units", False)
        if minor < 0:
            return meta_error(400, 100, "daily_budget must be >= 0", False)
        payload["amount"] = minor / MINOR_UNITS
    if body.status is not None:
        if body.status not in WIRE_TO_STATE:
            return meta_error(400, 100, "status must be ACTIVE or PAUSED", False)
        payload["status"] = WIRE_TO_STATE[body.status]
    try:
        result = _store(request).commit(
            "platform_set_budget", x_request_id or f"meta-{uuid.uuid4()}", "adapter:meta", payload
        ).result
    except UnknownEntity:
        return meta_error(400, 100, f"Object with ID '{campaign_id}' does not exist", False)
    if result["fault"] is not None:
        return meta_error(*FAULT_HTTP[result["fault"]])
    return JSONResponse({"success": True})


@router.get("/{campaign_id}")
def read_campaign(campaign_id: str, request: Request, fields: str = Query(default="id")) -> JSONResponse:
    wanted = [f.strip() for f in fields.split(",") if f.strip()]
    unknown = [f for f in wanted if f not in READ_FIELDS]
    if unknown:
        return meta_error(400, 100, f"Tried accessing nonexisting field ({unknown[0]})", False)
    rows = _store(request).read(
        "SELECT amount, status FROM budgets_state WHERE platform = 'meta' AND budget_id = ?", [campaign_id]
    )
    if not rows:
        return meta_error(400, 100, f"Object with ID '{campaign_id}' does not exist", False)
    amount, status = rows[0]
    full = {"id": campaign_id, "daily_budget": str(round(amount * MINOR_UNITS)), "status": STATE_TO_WIRE[status]}
    return JSONResponse({f: full[f] for f in wanted})
