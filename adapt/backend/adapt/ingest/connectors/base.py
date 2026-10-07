"""Connector framework: run context, raw page capture, logical-time stamps, typed upserts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from adapt.ingest.http import SourceHttp

CONNECTOR_VERSION = "1.0.0"
SOURCES_PATH = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"


@lru_cache
def sources_config() -> dict:
    return yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))


def fx_to_inr(currency: str) -> float:
    try:
        return float(sources_config()["fx"][currency])
    except KeyError as exc:
        raise ValueError(f"no simulation FX rate for {currency}") from exc


def available_at(source: str, event_date: date) -> datetime:
    """First logical moment ADAPT could have known a fact of `event_date` (spec §22.9): next 00:00 + report lag."""
    lag = sources_config()["sources"][source]["report_lag_hours"]
    return datetime.combine(event_date + timedelta(days=1), datetime.min.time()) + timedelta(hours=lag)


@dataclass
class SyncContext:
    """One sync run. `cur` is a cursor inside the single-writer transaction."""

    cur: Any
    http: SourceHttp
    run_id: str
    world_date: date  # the simulation's "today" (data is complete up to world_date - 1)
    ingested_at: datetime
    call_seq: int = 0
    pages: dict[str, int] = field(default_factory=dict)

    def record_page(self, source: str, endpoint: str, request: dict, payload: Any) -> None:
        cfg = sources_config()["sources"][source]
        account = cfg.get("account_id") or cfg.get("customer_id") or cfg.get("property_id")
        self.call_seq += 1
        self.pages[source] = self.pages.get(source, 0) + 1
        self.cur.execute(
            "INSERT INTO raw.api_pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [self.run_id, self.call_seq, source, endpoint, json.dumps(request, sort_keys=True), json.dumps(payload),
             self.ingested_at, cfg["provenance"], cfg["schema_version"], CONNECTOR_VERSION,
             hashlib.sha256(account.encode()).hexdigest()[:16] if account else None, cfg["timezone"],
             cfg["currency"]],
        )

    def stamp(self, df: pd.DataFrame, source: str, date_col: str | None = "date") -> pd.DataFrame:
        """Adds run_id, ingested_at, available_at (from the row's business date) and provenance."""
        if df.empty:
            return df
        out = df.copy()
        out["run_id"] = self.run_id
        out["ingested_at"] = self.ingested_at
        out["provenance"] = sources_config()["sources"][source]["provenance"]
        if date_col is None:
            out["available_at"] = available_at(source, self.world_date - timedelta(days=1))
        else:
            out["available_at"] = [available_at(source, d) for d in out[date_col]]
        return out

    def upsert(self, table: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        self.cur.register("_frame", df)
        try:
            self.cur.execute(f"INSERT OR REPLACE INTO {table} BY NAME SELECT * FROM _frame")
        finally:
            self.cur.unregister("_frame")
        return len(df)


@dataclass
class ConnectorResult:
    connector: str
    rows: dict[str, int] = field(default_factory=dict)
    pages: int = 0


def daterange(since: date, until: date):
    d = since
    while d <= until:
        yield d
        d += timedelta(days=1)
