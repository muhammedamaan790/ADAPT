"""Guarded Copilot SQL (Stage 3, spec §9.5): one SELECT over allowlisted marts only, no file / network functions,
a forced LIMIT, a timeout, and execution on a separate read-only copy of the marts."""

import pytest

from adapt.agent import sql
from adapt.core.db import Database


@pytest.fixture
def ws(tmp_path):
    db = Database(tmp_path / "ws.duckdb")
    db.write(lambda cur: cur.execute("""
        CREATE SCHEMA marts; CREATE SCHEMA intel;
        CREATE TABLE marts.brand_daily AS SELECT DATE '2026-10-01' + CAST(i AS INTEGER) AS date,
            1000.0 * i AS net_revenue, 100.0 * i AS spend FROM range(1, 601) t(i);
        CREATE TABLE marts.campaign_daily AS SELECT 'c1' AS campaign_id, 5.0 AS spend;
        CREATE TABLE intel.decisions AS SELECT 'secret' AS payload;"""))
    assert sql.refresh_copy(db)["status"] == "OK"
    yield db
    db.close()


def test_select_on_marts_runs_on_the_copy_with_a_forced_limit(ws):
    out = sql.run(ws.path, "SELECT date, net_revenue FROM marts.brand_daily ORDER BY date")
    assert out["columns"] == ["date", "net_revenue"] and len(out["rows"]) == 500 and out["truncated"]
    small = sql.run(ws.path, "WITH x AS (SELECT spend FROM campaign_daily) SELECT sum(spend) AS s, sum(spend) AS s "
                             "FROM x")
    assert small["rows"] == [[5.0, 5.0]] and len(set(small["columns"])) == 2 and not small["truncated"]


@pytest.mark.parametrize("q, why", [
    ("DELETE FROM marts.brand_daily", "only SELECT"),
    ("SELECT 1; SELECT 2", "exactly one statement"),
    ("SELECT * FROM intel.decisions", "not allowed"),
    ("SELECT * FROM ops.users", "not allowed"),
    ("SELECT * FROM read_csv('C:/Windows/win.ini')", "not allowed"),
    ("SELECT * FROM read_parquet('x.parquet')", "not allowed"),
    ("SELECT getenv('GROQ_API_KEY')", "not allowed"),
    ("SELECT current_setting('threads')", "not allowed"),
    ("SELECT * FROM glob('*')", "not allowed"),
    ("SELECT * FROM (SELECT * FROM core.orders) t", "not allowed"),
    ("ATTACH 'x.duckdb' AS y", "only SELECT"),
    ("", "empty"),
])
def test_rejected_queries(ws, q, why):
    with pytest.raises(sql.SqlRejected, match=why):
        sql.run(ws.path, q)


def test_the_copy_is_read_only_and_holds_only_marts(ws):
    import duckdb

    con = duckdb.connect(str(sql.copy_path(ws.path)), read_only=True)
    try:
        tables = {t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    finally:
        con.close()
    assert tables == {"brand_daily", "campaign_daily"}                          # intel / ops never copied
