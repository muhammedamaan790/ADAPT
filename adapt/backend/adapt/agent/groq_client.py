"""Groq client (Stage 2 ★, spec §11): fallback chain openai/gpt-oss-120b -> openai/gpt-oss-20b -> offline templates.

- At first use, GET /openai/v1/models filters the configured chain to the models that are available; an empty chain
  (or no API key) means LLM offline mode, and every caller falls back to deterministic templates.
- The NARRATOR request configuration is non-streaming, never activates tool calling, and uses Groq strict structured
  outputs (response_format json_schema, strict: true). The Copilot (Stage 3) gets its own configuration object;
  strict schemas cannot be combined with streaming or tools in one request.
- 20 s timeout, 429 -> exponential backoff (max 2 retries) then the next model; any other failure -> next model.
- Responses are cached per (workspace_id, model_id, prompt_hash, schema_version, evidence_package_hash) in
  ops.llm_cache, so a replayed narrative costs nothing and never changes.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from adapt.agent.atoms import narrative_config

CACHE_DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.llm_cache (
    workspace_id VARCHAR NOT NULL, model_id VARCHAR NOT NULL, prompt_hash VARCHAR NOT NULL,
    schema_version VARCHAR NOT NULL, evidence_package_hash VARCHAR NOT NULL, response JSON NOT NULL,
    created_at TIMESTAMP NOT NULL,
    PRIMARY KEY (workspace_id, model_id, prompt_hash, schema_version, evidence_package_hash)
);
"""


class LLMUnavailable(RuntimeError):
    """No model in the chain answered: the caller uses its deterministic template."""


@dataclass(frozen=True)
class RequestConfig:
    """A request profile. The narrator and the copilot never share one (spec §11)."""
    stream: bool
    tools: bool
    strict_schema: bool


NARRATOR_REQUEST = RequestConfig(stream=False, tools=False, strict_schema=True)
COPILOT_REQUEST = RequestConfig(stream=True, tools=True, strict_schema=False)


class GroqClient:
    def __init__(self, api_key: str | None, http: httpx.Client | None = None, sleep=time.sleep,
                 cfg: dict | None = None, db=None, workspace_id: str = "demo"):
        self.cfg = cfg or narrative_config()["llm"]
        self.key = api_key
        self.http = http or httpx.Client(timeout=httpx.Timeout(float(self.cfg["timeout_s"])))
        self.sleep = sleep
        self.db, self.workspace_id = db, workspace_id
        self._chain: list[str] | None = None

    @property
    def base(self) -> str:
        return self.cfg["base_url"].rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def chain(self) -> list[str]:
        """The configured chain filtered to available models (checked once); [] = offline."""
        if self._chain is None:
            if not self.key:
                self._chain = []
            else:
                try:
                    r = self.http.get(f"{self.base}/models", headers=self._headers())
                    live = {m["id"] for m in r.json().get("data", [])} if r.status_code == 200 else set()
                except (httpx.HTTPError, ValueError):
                    live = set()
                self._chain = [m for m in self.cfg["models"] if m in live]
        return self._chain

    @property
    def offline(self) -> bool:
        return not self.chain()

    # ---- cache -------------------------------------------------------------------------------------------------------
    def _cache_get(self, model: str, prompt_hash: str, pkg_hash: str) -> dict | None:
        if self.db is None:
            return None
        self.db.write(lambda cur: cur.execute(CACHE_DDL))
        row = self.db.query("""SELECT response FROM ops.llm_cache WHERE workspace_id = ? AND model_id = ?
                               AND prompt_hash = ? AND schema_version = ? AND evidence_package_hash = ?""",
                            [self.workspace_id, model, prompt_hash, self.cfg["schema_version"], pkg_hash])
        return json.loads(row[0][0]) if row else None

    def _cache_put(self, model: str, prompt_hash: str, pkg_hash: str, response: dict) -> None:
        if self.db is None:
            return

        def work(cur):
            cur.execute(CACHE_DDL)
            cur.execute("INSERT OR REPLACE INTO ops.llm_cache VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [self.workspace_id, model, prompt_hash, self.cfg["schema_version"], pkg_hash,
                         json.dumps(response), datetime.now()])

        self.db.write(work)

    # ---- one structured narrator call over the chain -----------------------------------------------------------------
    def structured(self, messages: list[dict], schema: dict, name: str, pkg_hash: str,
                   request: RequestConfig = NARRATOR_REQUEST) -> tuple[str, dict, bool]:
        """(model, parsed JSON, from_cache). Raises LLMUnavailable when no model answers."""
        if request.stream or request.tools or not request.strict_schema:
            raise ValueError("the narrator request is non-streaming, tool-free and strict-schema only")
        prompt_hash = hashlib.sha256(json.dumps([messages, schema], sort_keys=True).encode()).hexdigest()
        errors = []
        for model in self.chain():
            hit = self._cache_get(model, prompt_hash, pkg_hash)
            if hit is not None:
                return model, hit, True
            body = {"model": model, "messages": messages, "stream": False, "temperature": self.cfg["temperature"],
                    "max_completion_tokens": self.cfg["max_tokens"],
                    "response_format": {"type": "json_schema",
                                        "json_schema": {"name": name, "schema": schema, "strict": True}}}
            for attempt in range(int(self.cfg["max_retries_429"]) + 1):
                try:
                    r = self.http.post(f"{self.base}/chat/completions", headers=self._headers(), json=body)
                except httpx.HTTPError as exc:
                    errors.append(f"{model}: {exc}")
                    break
                if r.status_code == 429 and attempt < int(self.cfg["max_retries_429"]):
                    self.sleep(float(self.cfg["backoff_s"]) * 2 ** attempt)
                    continue
                if r.status_code != 200:
                    errors.append(f"{model}: HTTP {r.status_code}")
                    break
                try:
                    content = r.json()["choices"][0]["message"]["content"]
                    parsed = json.loads(content)
                except (KeyError, IndexError, ValueError, TypeError) as exc:
                    errors.append(f"{model}: unparseable response ({exc})")
                    break
                self._cache_put(model, prompt_hash, pkg_hash, parsed)
                return model, parsed, False
        raise LLMUnavailable("; ".join(errors) or "no model available")


def from_settings(settings, db=None) -> GroqClient:
    """The app's narrator client: GROQ_API_KEY from the backend .env (no key -> offline templates)."""
    return GroqClient(settings.groq_api_key, db=db, workspace_id=settings.workspace)
