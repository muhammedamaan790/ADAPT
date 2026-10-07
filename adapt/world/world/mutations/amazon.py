"""Mock Amazon Ads API v3 Sponsored Products (Stage 2 SIMULATED channel): campaign budget update (absolute setter).

PUT /amazon/v3/sp/campaigns  {"campaigns": [{"campaignId", "budget": {"budget": X, "budgetType": "DAILY"}}]}  (INR)
Per-item success / error lists as Amazon returns them; injected faults use HTTP statuses (429 / 503 / 400 / 504).
Read-back: GET /amazon/v3/sp/campaigns (reporting/amazon.py).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from world.state import UnknownEntity

router = APIRouter(prefix="/amazon/v3/sp", tags=["mock-amazon"])
FAULT_HTTP = {"rate_limit": (429, "TOO_MANY_REQUESTS"), "unavailable": (503, "SERVICE_UNAVAILABLE"),
              "bad_request": (400, "INVALID_ARGUMENT"), "timeout_after_success": (504, "GATEWAY_TIMEOUT")}


class Budget(BaseModel):
    budget: float
    budgetType: str = "DAILY"


class CampaignBudget(BaseModel):
    campaignId: str
    budget: Budget | None = None
    state: str | None = None


class Update(BaseModel):
    campaigns: list[CampaignBudget]


def amazon_error(http: int, code: str, details: str) -> JSONResponse:
    return JSONResponse(status_code=http, content={"code": code, "details": details})


@router.put("/campaigns")
def update(body: Update, request: Request, x_request_id: str | None = Header(default=None)) -> JSONResponse:
    if len(body.campaigns) != 1:
        return amazon_error(400, "INVALID_ARGUMENT", "the mock accepts exactly one campaign per request")
    c = body.campaigns[0]
    payload: dict = {"platform": "amazon", "budget_id": c.campaignId}
    if c.budget is not None:
        if c.budget.budgetType != "DAILY" or c.budget.budget < 0:
            return amazon_error(400, "INVALID_ARGUMENT", "budget must be DAILY and >= 0")
        payload["amount"] = round(c.budget.budget, 2)
    if c.state is not None:
        payload["status"] = {"ENABLED": "ENABLED", "PAUSED": "PAUSED"}.get(c.state, "?")
    try:
        result = request.app.state.store.commit("platform_set_budget", x_request_id or f"amazon-{uuid.uuid4()}",
                                                "adapter:amazon", payload).result
    except UnknownEntity:
        return JSONResponse({"campaigns": {"success": [], "error": [
            {"index": 0, "errors": [{"errorType": "entityNotFound", "message": c.campaignId}]}]}}, status_code=207)
    if result["fault"] is not None:
        return amazon_error(*FAULT_HTTP[result["fault"]], "injected fault")
    return JSONResponse({"campaigns": {"success": [{"index": 0, "campaignId": c.campaignId}], "error": []}})
