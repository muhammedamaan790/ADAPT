"""Connector sync runner (A2): pulls every source over HTTP into raw.api_pages + stg.* for one run.

  uv run python -m adapt.ingest.sync            (world service at ADAPT_WORLD_URL)

The simulation's "today" comes from the world's /health (data is complete up to today - 1). Each connector runs
in its own single-writer transaction: a failing source rolls back only its own rows, is recorded in
ops.connector_status (FAILED + error code) and never blocks the others (fail closed per source, spec §1).
Window per connector: first run = backfill_days of history; later runs = from last_synced_date - lookback_days.
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta

from adapt.config.settings import Settings, get_settings
from adapt.core.db import Database
from adapt.ingest.connectors.ads import sync_google, sync_meta
from adapt.ingest.connectors.base import ConnectorResult, SyncContext, sources_config
from adapt.ingest.connectors.commerce import sync_erp, sync_finance, sync_ga4, sync_store
from adapt.ingest.http import ConnectorError, SourceHttp
from adapt.ingest.schema import ensure_schema

CONNECTORS: dict[str, Callable[[SyncContext, date, date], ConnectorResult]] = {
    "meta_ads": sync_meta,
    "google_ads": sync_google,
    "store": sync_store,
    "finance": sync_finance,
    "ga4": sync_ga4,
    "erp": sync_erp,
}


def world_today(http: SourceHttp) -> date:
    h = http.get("/health")
    if not h.get("seeded") or not h.get("date"):
        raise ConnectorError("WORLD_NOT_SEEDED", "the world service has no seeded world (WORLD_DIR)")
    return date.fromisoformat(h["date"])


def _window(db: Database, connector: str, until: date) -> tuple[date, date]:
    cfg = sources_config()["sync"]
    rows = db.query("SELECT last_synced_date FROM ops.connector_status WHERE connector = ?", [connector])
    if rows and rows[0][0] is not None:
        since = rows[0][0] - timedelta(days=cfg["lookback_days"] - 1)
    else:
        since = until - timedelta(days=cfg["backfill_days"] - 1)
    return min(since, until), until


def run_sync(db: Database, http: SourceHttp, connectors: list[str] | None = None,
             run_id: str | None = None) -> dict:
    db.write(ensure_schema)
    run_id = run_id or f"sync-{uuid.uuid4().hex[:12]}"
    today = world_today(http)
    until = today - timedelta(days=1)
    started = datetime.now()
    db.write(lambda cur: cur.execute("INSERT INTO ops.sync_runs VALUES (?, ?, NULL, ?, 'RUNNING', NULL)",
                                     [run_id, started, today]))
    report: dict = {"run_id": run_id, "world_date": today.isoformat(), "connectors": {}}
    for name in connectors or list(CONNECTORS):
        since, until_c = _window(db, name, until)
        result: dict = {"since": since.isoformat(), "until": until_c.isoformat()}

        def work(cur, name=name, since=since, until_c=until_c) -> ConnectorResult:
            ctx = SyncContext(cur=cur, http=http, run_id=run_id, world_date=today, ingested_at=datetime.now())
            out = CONNECTORS[name](ctx, since, until_c)
            cur.execute("INSERT OR REPLACE INTO ops.connector_status VALUES (?, 'OK', ?, "
                        "(SELECT last_failure_ts FROM ops.connector_status WHERE connector = ?), NULL, ?, ?)",
                        [name, datetime.now(), name, until_c, run_id])
            return out

        try:
            out = db.write(work)
            result |= {"status": "OK", "rows": out.rows, "pages": out.pages}
        except ConnectorError as exc:
            code = exc.code

            def fail(cur, name=name, code=code) -> None:
                prev = cur.execute("SELECT last_success_ts, last_synced_date FROM ops.connector_status "
                                   "WHERE connector = ?", [name]).fetchone()
                cur.execute("INSERT OR REPLACE INTO ops.connector_status VALUES (?, 'FAILED', ?, ?, ?, ?, ?)",
                            [name, prev[0] if prev else None, datetime.now(), code, prev[1] if prev else None,
                             run_id])

            db.write(fail)
            result |= {"status": "FAILED", "error": code}
        report["connectors"][name] = result
    status = "OK" if all(c["status"] == "OK" for c in report["connectors"].values()) else "PARTIAL"
    db.write(lambda cur: cur.execute("UPDATE ops.sync_runs SET finished_at = ?, status = ?, details = ? "
                                     "WHERE run_id = ?", [datetime.now(), status, json.dumps(report), run_id]))
    report["status"] = status
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Pull every source into the workspace (A2 connectors)")
    ap.add_argument("--only", nargs="*", choices=list(CONNECTORS))
    args = ap.parse_args()
    settings: Settings = get_settings()
    db = Database(settings.workspace_db_path)
    try:
        report = run_sync(db, SourceHttp(settings.world_url), args.only)
    finally:
        db.close()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
