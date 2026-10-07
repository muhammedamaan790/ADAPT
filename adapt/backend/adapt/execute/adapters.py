"""Platform adapters for execution (C5, spec §9.3, §9.4). Absolute setters only: set budget = X. A relative or
non-idempotent mutation does not exist here (T: adapters expose no relative operation).

MOCK adapters speak each platform's native wire format to the world service's mock APIs:
- Google Ads v25: campaignBudgets:mutate with amountMicros (multiples of 10,000 micros = 0.01 INR), read-back
  through a separate GAQL googleAds:search call (never the request payload echoed back)
- Meta v25.0: POST /{campaign_id} daily_budget in USD cents, read-back GET ?fields=daily_budget,status
Adapters never retry by themselves: the saga owns retries, read-after-write and verification. Request/response
bodies are redacted before they reach the ledger.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config"
SECRET_KEYS = ("authorization", "developer-token", "developer_token", "access_token", "refresh_token")


@lru_cache
def platforms_config() -> dict:
    return yaml.safe_load((CONFIG / "platforms.yaml").read_text(encoding="utf-8"))


@lru_cache
def _sources() -> dict:
    return yaml.safe_load((CONFIG / "sources.yaml").read_text(encoding="utf-8"))


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if k.lower() in SECRET_KEYS or k.lower().endswith("_secret") else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


class AdapterUnavailable(RuntimeError):
    """The platform cannot be executed in its configured mode (e.g. live adapter not built): fail closed."""


class ReadError(RuntimeError):
    pass


@dataclass
class SendResult:
    ok: bool
    http_status: int | None
    code: str | None = None
    transient: bool = False
    ambiguous: bool = False       # the change may or may not have been applied (timeout / 504 / network)
    request: dict = field(default_factory=dict)
    response: dict = field(default_factory=dict)


class MockGoogleAdapter:
    platform, mode = "google", "MOCK"

    def __init__(self, client: httpx.Client, base_url: str = ""):
        cfg = _sources()["sources"]["google_ads"]
        self.client, self.base = client, base_url.rstrip("/")
        self.cid, self.ver = cfg["customer_id"], cfg["api_version"]

    def tolerance(self) -> float:
        return 0.01 + 1e-6

    def read(self, budget_id: str) -> dict:
        q = ("SELECT campaign_budget.id, campaign_budget.amount_micros, campaign_budget.status FROM campaign_budget "
             f"WHERE campaign_budget.id = {budget_id}")
        try:
            r = self.client.post(f"{self.base}/google/{self.ver}/customers/{self.cid}/googleAds:search",
                                 json={"query": q})
        except httpx.HTTPError as exc:
            raise ReadError(f"google read failed: {exc}") from exc
        if r.status_code != 200:
            raise ReadError(f"google read HTTP {r.status_code}: {r.text[:200]}")
        rows = r.json().get("results", [])
        if not rows:
            raise ReadError(f"google budget {budget_id} not found")
        b = rows[0]["campaignBudget"]
        return {"amount_inr": int(b["amountMicros"]) / 1_000_000, "status": b.get("status")}

    def set_budget(self, budget_id: str, amount_inr: float, request_id: str | None = None) -> SendResult:
        micros = int(round(amount_inr * 100)) * 10_000
        body = {"operations": [{"update": {"resourceName": f"customers/{self.cid}/campaignBudgets/{budget_id}",
                                           "amountMicros": str(micros)}, "updateMask": "amount_micros"}]}
        headers = {"X-Request-ID": request_id or f"adapt-{uuid.uuid4()}"}
        try:
            r = self.client.post(f"{self.base}/google/{self.ver}/customers/{self.cid}/campaignBudgets:mutate",
                                 json=body, headers=headers)
        except httpx.TimeoutException:
            return SendResult(False, None, "TIMEOUT", True, True, redact(body))
        except httpx.HTTPError as exc:
            return SendResult(False, None, f"NETWORK: {exc}", True, True, redact(body))
        resp = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"text": r.text}
        if r.status_code == 200:
            return SendResult(True, 200, None, False, False, redact(body), redact(resp))
        status = (resp.get("error") or {}).get("status")
        return SendResult(False, r.status_code, status, r.status_code in (429, 500, 502, 503, 504),
                          r.status_code in (500, 502, 504), redact(body), redact(resp))


class MockMetaAdapter:
    platform, mode = "meta", "MOCK"

    def __init__(self, client: httpx.Client, base_url: str = ""):
        cfg = _sources()
        self.client, self.base = client, base_url.rstrip("/")
        self.ver = cfg["sources"]["meta_ads"]["api_version"]
        self.fx = float(cfg["fx"][cfg["sources"]["meta_ads"]["currency"]])

    def tolerance(self) -> float:
        return self.fx / 100 + 1e-6   # one minor unit (a US cent) of the account currency

    def read(self, budget_id: str) -> dict:
        try:
            r = self.client.get(f"{self.base}/meta/{self.ver}/{budget_id}", params={"fields": "id,daily_budget,status"})
        except httpx.HTTPError as exc:
            raise ReadError(f"meta read failed: {exc}") from exc
        if r.status_code != 200:
            raise ReadError(f"meta read HTTP {r.status_code}: {r.text[:200]}")
        b = r.json()
        return {"amount_inr": int(b["daily_budget"]) / 100 * self.fx,
                "status": {"ACTIVE": "ENABLED", "PAUSED": "PAUSED"}.get(b.get("status"), b.get("status"))}

    def set_budget(self, budget_id: str, amount_inr: float, request_id: str | None = None) -> SendResult:
        body = {"daily_budget": str(int(round(amount_inr / self.fx * 100)))}
        headers = {"X-Request-ID": request_id or f"adapt-{uuid.uuid4()}"}
        try:
            r = self.client.post(f"{self.base}/meta/{self.ver}/{budget_id}", json=body, headers=headers)
        except httpx.TimeoutException:
            return SendResult(False, None, "TIMEOUT", True, True, redact(body))
        except httpx.HTTPError as exc:
            return SendResult(False, None, f"NETWORK: {exc}", True, True, redact(body))
        resp = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"text": r.text}
        if r.status_code == 200:
            return SendResult(True, 200, None, False, False, redact(body), redact(resp))
        err = resp.get("error") or {}
        transient = bool(err.get("is_transient")) or r.status_code >= 500
        return SendResult(False, r.status_code, str(err.get("code")), transient, r.status_code in (500, 502, 504),
                          redact(body), redact(resp))


class LiveNotBuilt:
    """Live mode configured for a platform whose live adapter is not built: every leg is BLOCKED (no fallback)."""

    def __init__(self, platform: str):
        self.platform, self.mode = platform, "LIVE"

    def tolerance(self) -> float:
        return 0.0

    def read(self, budget_id: str) -> dict:
        raise AdapterUnavailable(f"{self.platform} live adapter not built (Stage 2); execution mode is fixed, "
                                 "no mock fallback")

    set_budget = read  # type: ignore[assignment]


def build_adapters(client: httpx.Client, base_url: str = "") -> dict:
    modes = platforms_config()["execution_mode"]
    out = {}
    for platform, cls in (("google", MockGoogleAdapter), ("meta", MockMetaAdapter)):
        out[platform] = cls(client, base_url) if modes.get(platform) == "mock" else LiveNotBuilt(platform)
    return out
