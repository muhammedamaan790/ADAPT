"""HTTP access to source APIs for the connectors (reads only; mutations live in execute/adapters).

Reads are safe to retry: 429 and 5xx are retried with exponential backoff (max 3 retries); anything else
raises ConnectorError with a code that ends up in ops.connector_status.last_error_code.
The underlying httpx.Client is injectable (tests pass the world app's TestClient, which is an httpx.Client).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


class ConnectorError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class SourceHttp:
    def __init__(self, base_url: str, client: httpx.Client | None = None, retries: int = 3,
                 backoff_s: float = 0.25, sleep=time.sleep) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=httpx.Timeout(30.0))
        self.retries = retries
        self.backoff_s = backoff_s
        self._sleep = sleep

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base_url}{path}"

    def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self.client.request(method, self._url(path), params=params, json=json)
            except httpx.TransportError as exc:
                last = ConnectorError("NETWORK", str(exc))
            else:
                if r.status_code < 400:
                    return r.json()
                if r.status_code not in RETRY_STATUS:
                    raise ConnectorError(f"HTTP_{r.status_code}", r.text[:300])
                last = ConnectorError(f"HTTP_{r.status_code}", r.text[:300])
            if attempt < self.retries:
                self._sleep(self.backoff_s * 2 ** attempt)
        assert last is not None
        raise last

    def get(self, path: str, params: dict | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, json=body)
