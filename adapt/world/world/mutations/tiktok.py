"""Mock TikTok Business API v1.3 (Stage 2 SIMULATED channel): campaign budget update (absolute setter).

POST /tiktok/v1.3/campaign/update/  {"advertiser_id", "campaign_id", "budget"}   budget in the advertiser currency
(USD, 2 decimals). The budget lives on the campaign (BUDGET_MODE_DAY). TikTok answers HTTP 200 with a `code` in the
body; injected faults use the HTTP statuses the adapter contract tests (429 / 503 / 400 / 504 lost response).
Read-back: GET /tiktok/v1.3/campaign/get/ (reporting/tiktok.py).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from world.accounts import TIKTOK_ADVERTISER_ID, usd_per_inr
from world.state import UnknownEntity

router = APIRouter(prefix="/tiktok/v1.3", tags=["mock-tiktok"])
FAULT_HTTP = {"rate_limit": (429, 40100, "Too many requests"), "unavailable": (503, 50002, "Service unavailable"),
              "bad_request": (400, 40002, "Invalid parameter"), "timeout_after_success": (504, 50000, "Timeout")}


class CampaignUpdate(BaseModel):
    advertiser_id: str
    campaign_id: str
    budget: float | None = None
    operation_status: str | None = None


def tiktok_error(http: int, code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=http, content={"code": code, "message": message, "request_id": "mock",
                                                   "data": {}})


@router.post("/campaign/update/")
def update(body: CampaignUpdate, request: Request, x_request_id: str | None = Header(default=None)) -> JSONResponse:
    if body.advertiser_id != TIKTOK_ADVERTISER_ID:
        return tiktok_error(400, 40001, "advertiser_id does not exist")
    payload: dict = {"platform": "tiktok", "budget_id": body.campaign_id}
    if body.budget is not None:
        if body.budget < 0 or round(body.budget, 2) != body.budget:
            return tiktok_error(400, 40002, "budget must be >= 0 with at most 2 decimals")
        payload["amount"] = body.budget / usd_per_inr()
    if body.operation_status is not None:
        payload["status"] = {"ENABLE": "ENABLED", "DISABLE": "PAUSED"}.get(body.operation_status, "?")
    try:
        result = request.app.state.store.commit("platform_set_budget", x_request_id or f"tiktok-{uuid.uuid4()}",
                                                "adapter:tiktok", payload).result
    except UnknownEntity:
        return tiktok_error(400, 40002, f"campaign {body.campaign_id} not found")
    if result["fault"] is not None:
        return tiktok_error(*FAULT_HTTP[result["fault"]])
    return JSONResponse({"code": 0, "message": "OK", "request_id": "mock", "data": {"campaign_id": body.campaign_id}})
