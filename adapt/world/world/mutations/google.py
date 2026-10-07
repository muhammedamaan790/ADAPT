"""Mock Google Ads API v25 (REST shapes): campaign budget mutate (spec §9.3, §9.4). Read-back: reporting/google.py.

Budgets are stored in brand currency (INR); the wire format is amountMicros as an int64 string.
The mock accepts one operation per mutate request (the ADAPT adapter sends one leg per request).
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from world.state import UnknownEntity, WorldStore

router = APIRouter(prefix="/google/v25", tags=["mock-google"])

MICROS = 1_000_000
MICROS_INCREMENT = 10_000  # Google requires budget amounts in multiples of 0.01 currency units
RESOURCE_RE = re.compile(r"^customers/(?P<cid>\d+)/campaignBudgets/(?P<bid>[^/]+)$")

FAULT_HTTP = {
    "rate_limit": (429, "RESOURCE_EXHAUSTED", "Too many requests."),
    "unavailable": (503, "UNAVAILABLE", "The service is currently unavailable."),
    "bad_request": (400, "INVALID_ARGUMENT", "Request contains an invalid argument."),
    "timeout_after_success": (504, "DEADLINE_EXCEEDED", "Deadline exceeded."),
}


class BudgetUpdate(BaseModel):
    resourceName: str
    amountMicros: str


class MutateOperation(BaseModel):
    update: BudgetUpdate
    updateMask: str


class MutateRequest(BaseModel):
    operations: list[MutateOperation]


def google_error(code: int, status: str, message: str, error_code: str | None = None) -> JSONResponse:
    details = [{"errors": [{"errorCode": {"mutateError": error_code}, "message": message}]}] if error_code else []
    return JSONResponse(
        status_code=code, content={"error": {"code": code, "message": message, "status": status, "details": details}}
    )


def _store(request: Request) -> WorldStore:
    return request.app.state.store


@router.post("/customers/{customer_id}/campaignBudgets:mutate")
def mutate_campaign_budgets(
    customer_id: str, body: MutateRequest, request: Request, x_request_id: str | None = Header(default=None)
) -> JSONResponse:
    if len(body.operations) != 1:
        return google_error(400, "INVALID_ARGUMENT", "mock supports exactly one operation per request")
    op = body.operations[0]
    if "amount_micros" not in {f.strip() for f in op.updateMask.split(",")}:
        return google_error(400, "INVALID_ARGUMENT", "updateMask must include amount_micros")
    m = RESOURCE_RE.match(op.update.resourceName)
    if m is None or m["cid"] != customer_id:
        return google_error(400, "INVALID_ARGUMENT", "resourceName does not belong to this customer")
    try:
        micros = int(op.update.amountMicros)
    except ValueError:
        return google_error(400, "INVALID_ARGUMENT", "amountMicros must be an int64")
    if micros < 0 or micros % MICROS_INCREMENT:
        return google_error(400, "INVALID_ARGUMENT", "amountMicros must be >= 0 and a multiple of 10000",
                            "NON_MULTIPLE_OF_MINIMUM_CURRENCY_UNIT")
    try:
        result = _store(request).commit(
            "platform_set_budget",
            x_request_id or f"google-{uuid.uuid4()}",
            "adapter:google",
            {"platform": "google", "budget_id": m["bid"], "amount": micros / MICROS},
        ).result
    except UnknownEntity as exc:
        return google_error(400, "INVALID_ARGUMENT", str(exc), "RESOURCE_NOT_FOUND")
    if result["fault"] is not None:
        return google_error(*FAULT_HTTP[result["fault"]])
    return JSONResponse({"results": [{"resourceName": op.update.resourceName}]})


# Read-back (googleAds:search, GAQL) lives in world.reporting.google: verification and reporting share one engine.
