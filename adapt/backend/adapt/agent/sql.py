"""Guarded read-only SQL for the Copilot and the SQL inspector (Stage 3, spec §9.5 "Copilot SQL").

- Parsed with sqlglot (DuckDB dialect): exactly ONE statement, a SELECT (CTEs and set operations of SELECTs allowed).
- Every table referenced must be an allowlisted marts table (unqualified or `marts.`-qualified); CTE names are local.
  No other schema (intel / exec / ops / auth / core / raw) is reachable.
- No table functions and no function that reads files, the network or settings (read_*, glob, *_scan, httpfs ...).
- A forced LIMIT 500 (the query is wrapped; one extra row detects truncation) and a 3 s timeout (interrupt).
- It runs against a SEPARATE file, `<workspace>.copilot_marts.duckdb`, refreshed from the marts after each pipeline run
  (refresh_copy) and opened per query with read_only=True and enable_external_access=false: the copilot physically
  cannot see other tables or touch the workspace file, and its settings cannot affect the writer connection.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

MAX_ROWS = 500
TIMEOUT_S = 3.0
MARTS = ("campaign_daily", "adset_daily", "creative_daily", "channel_daily", "sku_daily", "geo_daily", "brand_daily",
         "recon_daily")
FORBIDDEN_FUNCTIONS = ("read_", "glob", "_scan", "httpfs", "load", "install", "attach", "copy", "export",
                       "current_setting", "getenv", "pragma", "duckdb_", "query_table")


class SqlRejected(ValueError):
    """The query is not allowed; the message says why (shown to the user)."""


def copy_path(workspace_path: str | Path) -> Path:
    p = Path(workspace_path)
    return p.with_name(f"{p.stem}.copilot_marts.duckdb")


def refresh_copy(db) -> dict:
    """Write the marts into a fresh copy file, then atomically replace the served one (readers open per query)."""
    if db.path == ":memory:":
        return {"status": "SKIPPED", "reason": "in-memory workspace"}
    target = copy_path(db.path)
    tmp = target.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    present = [t for (t,) in db.query("SELECT table_name FROM information_schema.tables WHERE table_schema = 'marts'")
               if t in MARTS]

    def work(cur):
        cur.execute(f"ATTACH '{tmp.as_posix()}' AS cm")
        try:
            for t in present:
                cur.execute(f"CREATE TABLE cm.main.{t} AS SELECT * FROM marts.{t}")
        finally:
            cur.execute("DETACH cm")

    # ATTACH cannot run inside the write transaction, so it uses a plain cursor under the writer lock
    with db._gate.shared(), db._write_lock:
        work(db._con.cursor())
    os.replace(tmp, target)
    return {"status": "OK", "tables": present, "path": str(target)}


def validate(query: str) -> exp.Expression:
    q = query.strip().rstrip(";").strip()
    if not q:
        raise SqlRejected("empty query")
    try:
        stmts = sqlglot.parse(q, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise SqlRejected(f"not valid SQL: {str(exc).splitlines()[0]}") from exc
    stmts = [s for s in stmts if s is not None]
    if len(stmts) != 1:
        raise SqlRejected("exactly one statement is allowed")
    tree = stmts[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SqlRejected("only SELECT queries are allowed")
    if any(isinstance(n, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Command))
           for n in tree.walk()):
        raise SqlRejected("only SELECT queries are allowed")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        if not t.name or isinstance(t.this, exp.Func):
            raise SqlRejected("table functions are not allowed")
        name, schema = t.name.lower(), (t.db or "").lower()
        if t.catalog:
            raise SqlRejected("catalog-qualified tables are not allowed")
        if name in ctes and not schema:
            continue
        if schema not in ("", "marts", "main") or name not in MARTS:
            raise SqlRejected(f"table {t.sql()} is not allowed; tables: {', '.join('marts.' + m for m in MARTS)}")
    for f in tree.find_all(exp.Func):
        fname = (f.sql_name() if not isinstance(f, exp.Anonymous) else f.name).lower()
        if any(bad in fname for bad in FORBIDDEN_FUNCTIONS):
            raise SqlRejected(f"function {fname} is not allowed")
    return tree


def _strip_schema(tree: exp.Expression) -> str:
    """The copy holds the marts in its main schema: marts.x -> x."""
    def fix(node):
        if isinstance(node, exp.Table) and (node.db or "").lower() == "marts":
            node.set("db", None)
        return node

    return tree.transform(fix).sql(dialect="duckdb")


def run(workspace_path: str | Path, query: str, limit: int = MAX_ROWS, timeout_s: float = TIMEOUT_S) -> dict:
    tree = validate(query)
    path = copy_path(workspace_path)
    if not path.exists():
        raise SqlRejected("the marts copy does not exist yet (it is refreshed after each pipeline run)")
    limit = max(1, min(int(limit), MAX_ROWS))
    sql = f"SELECT * FROM ({_strip_schema(tree)}) AS q LIMIT {limit + 1}"
    con = duckdb.connect(str(path), read_only=True, config={"enable_external_access": False})
    holder: dict = {}

    def work():
        try:
            cur = con.execute(sql)
            cols, seen = [], {}
            for d in cur.description:  # the result contract needs unique column names
                seen[d[0]] = seen.get(d[0], 0) + 1
                cols.append(d[0] if seen[d[0]] == 1 else f"{d[0]}_{seen[d[0]]}")
            holder["cols"] = cols
            holder["rows"] = cur.fetchall()
        except Exception as exc:  # noqa: BLE001 - reported to the user
            holder["error"] = exc

    t0 = time.time()
    try:
        th = threading.Thread(target=work, daemon=True)
        th.start()
        th.join(timeout_s)
        if th.is_alive():
            con.interrupt()
            th.join(1.0)
            raise SqlRejected(f"query exceeded the {timeout_s:.0f} s timeout")
        if "error" in holder:
            raise SqlRejected(f"query failed: {str(holder['error']).splitlines()[0]}")
    finally:
        con.close()
    rows = holder["rows"]

    def cell(v):
        if v is None or isinstance(v, (bool, int, str)):
            return v
        if isinstance(v, Decimal):
            v = float(v)
        if isinstance(v, float):
            return v if v == v and abs(v) != float("inf") else None
        return str(v)

    return {"query": query, "columns": holder["cols"], "rows": [[cell(v) for v in r] for r in rows[:limit]],
            "truncated": len(rows) > limit, "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "as_of": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat().replace("+00:00", "Z")}
