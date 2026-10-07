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


class MockTikTokAdapter:
    """TikTok Business API v1.3 (Stage 2 SIMULATED channel): campaign budget in USD (2 decimals) via
    campaign/update/, read-back through a separate campaign/get/ call."""
    platform, mode = "tiktok", "MOCK"

    def __init__(self, client: httpx.Client, base_url: str = ""):
        cfg = _sources()
        self.client, self.base = client, base_url.rstrip("/")
        self.adv = cfg["sources"]["tiktok_ads"]["advertiser_id"]
        self.ver = cfg["sources"]["tiktok_ads"]["api_version"]
        self.fx = float(cfg["fx"][cfg["sources"]["tiktok_ads"]["currency"]])

    def tolerance(self) -> float:
        return self.fx / 100 + 1e-6

    def read(self, budget_id: str) -> dict:
        try:
            r = self.client.get(f"{self.base}/tiktok/{self.ver}/campaign/get/", params={"advertiser_id": self.adv})
        except httpx.HTTPError as exc:
            raise ReadError(f"tiktok read failed: {exc}") from exc
        if r.status_code != 200 or r.json().get("code") != 0:
            raise ReadError(f"tiktok read HTTP {r.status_code}")
        c = next((x for x in r.json()["data"]["list"] if x["campaign_id"] == budget_id), None)
        if c is None:
            raise ReadError(f"tiktok campaign {budget_id} not found")
        return {"amount_inr": float(c["budget"]) * self.fx,
                "status": "ENABLED" if c["operation_status"] == "ENABLE" else "PAUSED"}

    def set_budget(self, budget_id: str, amount_inr: float, request_id: str | None = None) -> SendResult:
        body = {"advertiser_id": self.adv, "campaign_id": budget_id, "budget": round(amount_inr / self.fx, 2)}
        try:
            r = self.client.post(f"{self.base}/tiktok/{self.ver}/campaign/update/", json=body,
                                 headers={"X-Request-ID": request_id or f"adapt-{uuid.uuid4()}"})
        except httpx.TimeoutException:
            return SendResult(False, None, "TIMEOUT", True, True, redact(body))
        except httpx.HTTPError as exc:
            return SendResult(False, None, f"NETWORK: {exc}", True, True, redact(body))
        resp = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"text": r.text}
        if r.status_code == 200 and resp.get("code") == 0:
            return SendResult(True, 200, None, False, False, redact(body), redact(resp))
        return SendResult(False, r.status_code, str(resp.get("code")), r.status_code in (429, 500, 502, 503, 504),
                          r.status_code in (500, 502, 504), redact(body), redact(resp))


class MockAmazonAdapter:
    """Amazon Ads Sponsored Products v3 (Stage 2 SIMULATED channel): campaign daily budget in INR via PUT
    /sp/campaigns, read-back through a separate GET with campaignIdFilter."""
    platform, mode = "amazon", "MOCK"

    def __init__(self, client: httpx.Client, base_url: str = ""):
        self.client, self.base = client, base_url.rstrip("/")

    def tolerance(self) -> float:
        return 0.01 + 1e-6

    def read(self, budget_id: str) -> dict:
        try:
            r = self.client.get(f"{self.base}/amazon/v3/sp/campaigns", params={"campaignIdFilter": budget_id})
        except httpx.HTTPError as exc:
            raise ReadError(f"amazon read failed: {exc}") from exc
        rows = r.json().get("campaigns", []) if r.status_code == 200 else []
        if not rows:
            raise ReadError(f"amazon campaign {budget_id} not found (HTTP {r.status_code})")
        return {"amount_inr": float(rows[0]["budget"]["budget"]), "status": rows[0]["state"]}

    def set_budget(self, budget_id: str, amount_inr: float, request_id: str | None = None) -> SendResult:
        body = {"campaigns": [{"campaignId": budget_id, "budget": {"budget": round(amount_inr, 2),
                                                                   "budgetType": "DAILY"}}]}
        try:
            r = self.client.put(f"{self.base}/amazon/v3/sp/campaigns", json=body,
                                headers={"X-Request-ID": request_id or f"adapt-{uuid.uuid4()}"})
        except httpx.TimeoutException:
            return SendResult(False, None, "TIMEOUT", True, True, redact(body))
        except httpx.HTTPError as exc:
            return SendResult(False, None, f"NETWORK: {exc}", True, True, redact(body))
        resp = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"text": r.text}
        if r.status_code == 200 and resp.get("campaigns", {}).get("success"):
            return SendResult(True, 200, None, False, False, redact(body), redact(resp))
        return SendResult(False, r.status_code, resp.get("code"), r.status_code in (429, 500, 502, 503, 504),
                          r.status_code in (500, 502, 504), redact(body), redact(resp))


class LiveNotBuilt:
    """Live mode configured for a platform without a live adapter (Meta: sandbox out of scope), or Google live without a
    workspace to read its live mapping from: every leg is BLOCKED (no fallback)."""

    def __init__(self, platform: str):
        self.platform, self.mode = platform, "LIVE"

    def tolerance(self) -> float:
        return 0.0

    def read(self, budget_id: str) -> dict:
        raise AdapterUnavailable(f"{self.platform} live adapter unavailable; execution mode is fixed, "
                                 "no mock fallback")

    set_budget = read  # type: ignore[assignment]


def build_adapters(client: httpx.Client, base_url: str = "", settings=None, db=None,
                   live_http: httpx.Client | None = None, env: dict | None = None) -> dict:
    """Execution mode is fixed here, once, per platform (settings env override, else platforms.yaml); never an automatic
    fallback. Google `live` = the v25 test-account adapter, mirrored into the world service after verification."""
    modes = dict(platforms_config()["execution_mode"])
    if settings is not None:
        modes["google"] = settings.google_execution_mode
    out = {}
    for platform, cls in (("google", MockGoogleAdapter), ("meta", MockMetaAdapter), ("tiktok", MockTikTokAdapter),
                          ("amazon", MockAmazonAdapter)):
        if modes.get(platform) == "mock":
            out[platform] = cls(client, base_url)
        elif platform == "google" and db is not None:
            from adapt.execute.google_ads_live import (
                GoogleAdsCredentials,
                GoogleAdsLiveAdapter,
                load_mapping,
                world_mirror,
            )

            version = platforms_config().get("api_versions", {}).get("google", "v25")
            kw = {"http": live_http} if live_http is not None else {}
            out[platform] = GoogleAdsLiveAdapter(GoogleAdsCredentials.from_env(env), load_mapping(db), version,
                                                 mirror=world_mirror(client, base_url), **kw)
        else:
            out[platform] = LiveNotBuilt(platform)
    return out


def platform_health(adapters: dict) -> dict:
    """GET /platforms/health (spec §9.4): per platform, the fixed execution mode and whether it can execute. In live
    mode a failing check disables Approve for that platform's legs, with the reason."""
    out = {}
    for platform, ad in sorted(adapters.items()):
        mode = getattr(ad, "mode", "MOCK")
        if hasattr(ad, "health"):
            out[platform] = ad.health()
        elif mode == "MOCK":
            out[platform] = {"ok": True, "mode": "MOCK", "label": f"MOCK · {platform} (simulated platform API)"}
        else:
            out[platform] = {"ok": False, "mode": mode, "reason": f"{platform} live adapter unavailable; no mock "
                                                                  "fallback"}
    return out
