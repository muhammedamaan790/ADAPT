"""Google Ads API v25 LIVE adapter (Stage 2 ★, spec §9.4): the control plane goes to a real Google Ads TEST account.

Same wire format as the world's mock (REST): `POST /v25/customers/{cid}/campaignBudgets:mutate` with amountMicros
(absolute setter, multiples of 10,000 micros) and read-back through a SEPARATE GAQL `googleAds:search` call (the
request payload is never echoed back as the observed state).

- Auth: OAuth 2.0 desktop client + refresh token (GOOGLE_ADS_CLIENT_ID / _SECRET / _REFRESH_TOKEN in the backend
  .env), refreshed at https://oauth2.googleapis.com/token; `login-customer-id` = the test manager account. No
  developer token is sent unless GOOGLE_ADS_DEVELOPER_TOKEN is set (spec §9.4: optional; the redaction filter strips
  it either way).
- Health (checked once per adapter, before the first call): token refresh works, the configured API version answers,
  the customer is accessible, IS A TEST ACCOUNT (this build refuses to mutate a serving account), and bills in INR.
  Any failure -> AdapterUnavailable: the leg is BLOCKED and the decision preserved; NEVER a fallback to mock (T23,
  T30).
- Sim budget ids map to live budget ids through ops.live_entity_map (written by scripts/google_ads_setup.py); an
  unmapped budget is BLOCKED (fail closed).
- `mirror`: after a LIVE leg is VERIFIED, the saga mirrors the same absolute budget into the world service so the
  simulated data plane reacts (execute/mirror.py). A mirror problem never touches the verified Google change.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field

import httpx

from adapt.execute.adapters import AdapterUnavailable, ReadError, SendResult, redact

OAUTH_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://googleads.googleapis.com"
MICROS = 1_000_000

MAP_DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.live_entity_map (
    platform VARCHAR NOT NULL, entity_type VARCHAR NOT NULL, sim_entity_id VARCHAR NOT NULL,
    live_entity_id VARCHAR NOT NULL, live_customer_id VARCHAR NOT NULL, created_at TIMESTAMP,
    PRIMARY KEY (platform, entity_type, sim_entity_id)
);
"""


@dataclass(frozen=True)
class GoogleAdsCredentials:
    client_id: str | None
    client_secret: str | None
    refresh_token: str | None
    login_customer_id: str | None
    customer_id: str | None
    developer_token: str | None = None

    @classmethod
    def from_env(cls, env: dict | None = None) -> GoogleAdsCredentials:
        e = os.environ if env is None else env
        clean = lambda k: (e.get(k) or "").replace("-", "").strip() or None  # noqa: E731
        return cls(e.get("GOOGLE_ADS_CLIENT_ID") or None, e.get("GOOGLE_ADS_CLIENT_SECRET") or None,
                   e.get("GOOGLE_ADS_REFRESH_TOKEN") or None, clean("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
                   clean("GOOGLE_ADS_CUSTOMER_ID"), e.get("GOOGLE_ADS_DEVELOPER_TOKEN") or None)

    def missing(self) -> list[str]:
        names = {"client_id": "GOOGLE_ADS_CLIENT_ID", "client_secret": "GOOGLE_ADS_CLIENT_SECRET",
                 "refresh_token": "GOOGLE_ADS_REFRESH_TOKEN", "login_customer_id": "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
                 "customer_id": "GOOGLE_ADS_CUSTOMER_ID"}
        return [env for f, env in names.items() if not getattr(self, f)]


def load_mapping(db, platform: str = "google") -> dict[str, str]:
    db.write(lambda cur: cur.execute(MAP_DDL))
    return dict(db.query("SELECT sim_entity_id, live_entity_id FROM ops.live_entity_map WHERE platform = ? "
                         "AND entity_type = 'budget'", [platform]))


@dataclass
class GoogleAdsLiveAdapter:
    creds: GoogleAdsCredentials
    mapping: dict[str, str]
    version: str = "v25"
    http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=httpx.Timeout(20.0)))
    api_base: str = API_BASE
    oauth_url: str = OAUTH_URL
    clock: object = time.time
    mirror: object = None                       # callable(budget_id, amount_inr, request_id) -> None, or None
    platform: str = "google"
    mode: str = "LIVE"
    _token: str | None = field(default=None, init=False, repr=False)
    _token_exp: float = field(default=0.0, init=False, repr=False)
    _health: dict | None = field(default=None, init=False, repr=False)

    def tolerance(self) -> float:
        return 0.01 + 1e-6

    # ---- auth + health -----------------------------------------------------------------------------------------------
    def _access_token(self) -> str:
        if self._token and self.clock() < self._token_exp - 60:
            return self._token
        try:
            r = self.http.post(self.oauth_url, data={"client_id": self.creds.client_id,
                                                     "client_secret": self.creds.client_secret,
                                                     "refresh_token": self.creds.refresh_token,
                                                     "grant_type": "refresh_token"})
        except httpx.HTTPError as exc:
            raise AdapterUnavailable(f"google auth: token endpoint unreachable ({exc})") from exc
        if r.status_code != 200 or "access_token" not in r.json():
            raise AdapterUnavailable(f"google auth failed: HTTP {r.status_code}")
        body = r.json()
        self._token, self._token_exp = body["access_token"], self.clock() + float(body.get("expires_in", 3600))
        return self._token

    def _headers(self, request_id: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {self._access_token()}", "login-customer-id": self.creds.login_customer_id or "",
             "Content-Type": "application/json"}
        if self.creds.developer_token:
            h["developer-token"] = self.creds.developer_token
        if request_id:
            h["X-Request-ID"] = request_id
        return h

    def _url(self, path: str) -> str:
        return f"{self.api_base.rstrip('/')}/{self.version}/customers/{self.creds.customer_id}/{path}"

    def health(self, force: bool = False) -> dict:
        """{ok, checks{credentials, auth, api_version, account, test_account, currency}, reason}; cached."""
        if self._health is not None and not force:
            return self._health
        checks: dict[str, bool] = {}
        reason = None
        missing = self.creds.missing()
        checks["credentials"] = not missing
        if missing:
            reason = f"missing {', '.join(missing)}"
        else:
            try:
                self._access_token()
                checks["auth"] = True
                r = self.http.post(self._url("googleAds:search"), headers=self._headers(), json={
                    "query": "SELECT customer.id, customer.test_account, customer.currency_code FROM customer"})
                checks["api_version"] = r.status_code != 404
                if r.status_code == 404:
                    reason = f"API version {self.version} not reachable (sunset or misconfigured)"
                elif r.status_code != 200:
                    checks["account"] = False
                    reason = f"customer {self.creds.customer_id} not accessible: HTTP {r.status_code}"
                else:
                    rows = r.json().get("results", [])
                    cust = rows[0].get("customer", {}) if rows else {}
                    checks["account"] = bool(rows)
                    checks["test_account"] = bool(cust.get("testAccount"))
                    checks["currency"] = cust.get("currencyCode") == "INR"
                    if not rows:
                        reason = "customer not found"
                    elif not checks["test_account"]:
                        reason = "not a Google Ads TEST account: this build refuses to mutate a serving account"
                    elif not checks["currency"]:
                        reason = f"account currency {cust.get('currencyCode')} != INR"
            except AdapterUnavailable as exc:
                checks["auth"] = False
                reason = str(exc)
            except httpx.HTTPError as exc:
                checks["api_version"] = False
                reason = f"Google Ads API unreachable ({exc})"
        self._health = {"ok": reason is None, "checks": checks, "reason": reason, "mode": "LIVE",
                        "version": self.version, "label": "LIVE · Google Ads (test account) — performance data "
                                                          "simulated"}
        return self._health

    def _require_healthy(self) -> None:
        h = self.health()
        if not h["ok"]:
            raise AdapterUnavailable(f"google live health check failed: {h['reason']} (no mock fallback)")

    def _live_id(self, budget_id: str) -> str:
        live = self.mapping.get(str(budget_id))
        if live is None:
            raise AdapterUnavailable(f"google budget {budget_id} has no live test-account mapping "
                                     "(run scripts/google_ads_setup.py); no mock fallback")
        return live

    # ---- the adapter contract (absolute setters + separate read-back) ------------------------------------------------
    def read(self, budget_id: str) -> dict:
        self._require_healthy()
        live = self._live_id(budget_id)
        q = ("SELECT campaign_budget.id, campaign_budget.amount_micros, campaign_budget.status FROM campaign_budget "
             f"WHERE campaign_budget.id = {live}")
        try:
            r = self.http.post(self._url("googleAds:search"), headers=self._headers(), json={"query": q})
        except httpx.HTTPError as exc:
            raise ReadError(f"google live read failed: {exc}") from exc
        if r.status_code != 200:
            raise ReadError(f"google live read HTTP {r.status_code}")
        rows = r.json().get("results", [])
        if not rows:
            raise ReadError(f"google live budget {live} not found")
        b = rows[0]["campaignBudget"]
        return {"amount_inr": int(b["amountMicros"]) / MICROS, "status": b.get("status")}

    def set_budget(self, budget_id: str, amount_inr: float, request_id: str | None = None) -> SendResult:
        self._require_healthy()
        live = self._live_id(budget_id)
        micros = int(round(amount_inr * 100)) * 10_000
        body = {"operations": [{"update": {
            "resourceName": f"customers/{self.creds.customer_id}/campaignBudgets/{live}",
            "amountMicros": str(micros)}, "updateMask": "amount_micros"}]}
        try:
            r = self.http.post(self._url("campaignBudgets:mutate"), json=body,
                               headers=self._headers(request_id or f"adapt-{uuid.uuid4()}"))
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


def world_mirror(world_client: httpx.Client, base_url: str = ""):
    """The hybrid mirror target: set the same absolute budget in the world service (control plane, actor 'mirror').
    Idempotent per request id. Raises on any failure (the mirror retrier owns retries)."""
    base = base_url.rstrip("/")

    def mirror(budget_id: str, amount_inr: float, request_id: str) -> None:
        r = world_client.post(f"{base}/control/budget", json={"platform": "google", "budget_id": str(budget_id),
                                                               "amount": float(amount_inr), "status": "ENABLED"},
                              headers={"X-Request-ID": request_id, "X-Actor-ID": "adapt-mirror"})
        if r.status_code != 200:
            raise RuntimeError(f"world mirror HTTP {r.status_code}: {r.text[:200]}")

    return mirror
