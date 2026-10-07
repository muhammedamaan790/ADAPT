"""A3 entry point: rebuild core + marts + data health as of a logical time, in one atomic write.

  uv run python -m adapt.reconcile.build            (as of the world's today at the pipeline hour)

The logical "now" is the world date at `pipeline_run_hour` (after every source's report lag). The whole rebuild
is one single-writer transaction: readers see either the previous canonical state or the new one, never a mix.
Row flows are recorded in ops.run_lineage.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import date, datetime, time

from adapt.config.settings import get_settings
from adapt.core.db import Database
from adapt.ingest.http import SourceHttp
from adapt.ingest.schema import ensure_schema
from adapt.ingest.sync import world_today
from adapt.reconcile.core import build_core
from adapt.reconcile.health import build_health, health_config
from adapt.reconcile.marts import build_marts


def logical_now(world_date: date) -> datetime:
    return datetime.combine(world_date, time(hour=int(health_config()["pipeline_run_hour"])))


def build_canonical(db: Database, as_of: datetime, run_id: str | None = None) -> dict:
    run_id = run_id or f"canon-{uuid.uuid4().hex[:12]}"
    db.write(ensure_schema)

    def work(cur) -> dict:
        counts = build_core(cur, as_of)
        counts |= build_marts(cur, as_of)
        health = build_health(cur, as_of)
        cur.execute("""CREATE TABLE IF NOT EXISTS ops.run_lineage (
            run_id VARCHAR NOT NULL, as_of TIMESTAMP NOT NULL, table_name VARCHAR NOT NULL, rows_out BIGINT NOT NULL,
            PRIMARY KEY (run_id, table_name))""")
        cur.executemany("INSERT INTO ops.run_lineage VALUES (?, ?, ?, ?)",
                        [(run_id, as_of, t, n) for t, n in counts.items() if not t.startswith("_")])
        return {"run_id": run_id, "as_of": as_of.isoformat(), "rows": counts,
                "health": {h["source"]: {"score": h["score"], "status": h["status"]} for h in health}}

    return db.write(work)


def main() -> None:
    ap = argparse.ArgumentParser(description="Rebuild the canonical state, marts and data health (A3)")
    ap.add_argument("--as-of", help="logical timestamp YYYY-MM-DDTHH:MM (default: world today at the pipeline hour)")
    args = ap.parse_args()
    s = get_settings()
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else logical_now(world_today(SourceHttp(s.world_url)))
    db = Database(s.workspace_db_path)
    try:
        print(json.dumps(build_canonical(db, as_of), indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
