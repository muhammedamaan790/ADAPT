"""Backbone extraction (component A1): TheLook raw CSVs -> a small, checksummed parquet subset the world seeds from.

Run once after download:  uv run python -m world.backbone --raw-dir data/raw --out data/world/backbone

Design decisions (docs/contracts/world_backbone.md):
- A "category" is the (department, category) pair, because TheLook categories such as Jeans exist in both
  Men and Women. The top N pairs by non-cancelled revenue in the window are promoted.
- A promoted SKU is a *price-band style group*: every product of a promoted category falls into exactly one of
  `groups_per_category` bands (ntile by retail_price, ties by product id). A single TheLook product sells
  <= 14 units a year, far too few for inventory risk, and mapping campaigns to a handful of products would
  leave ~97% of their revenue unmapped. Products outside the promoted categories keep sku = NULL (unmapped).
- The window is the last `window_days` complete days in Asia/Kolkata, ending the day before the last order.
- Inventory is NOT taken from inventory_items: TheLook never runs out of stock, so the ERP is simulated
  by the world (labelled CALIBRATED) from this demand.

Raw tables are never modified. The output is reproducible: the manifest stores, per table, the row count and a
sha256 over the canonical row contents (sorted), so a re-run must produce identical hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

BRAND_TZ = "Asia/Kolkata"


@dataclass(frozen=True)
class BackboneConfig:
    window_days: int = 365
    n_categories: int = 12
    groups_per_category: int = 5


TABLES = ("categories", "skus", "product_sku", "orders", "order_items", "sessions", "users")
SORT_KEYS = {
    "categories": "rank",
    "skus": "sku",
    "product_sku": "product_id",
    "orders": "order_id",
    "order_items": "line_id",
    "sessions": "session_id",
    "users": "user_id",
}


def _src(raw_dir: Path, table: str) -> str:
    path = (raw_dir / "thelook" / f"{table}.csv").as_posix().replace("'", "''")
    return f"read_csv_auto('{path}', header = true)"


def _build(con: duckdb.DuckDBPyConnection, raw_dir: Path, cfg: BackboneConfig) -> tuple[str, str]:
    for t in ("products", "orders", "order_items", "events", "users"):
        con.execute(f"CREATE TEMP VIEW raw_{t} AS SELECT * FROM {_src(raw_dir, t)}")

    last_day = con.execute(
        f"SELECT max(CAST(timezone('{BRAND_TZ}', created_at) AS DATE)) FROM raw_orders"
    ).fetchone()[0]
    if last_day is None:
        raise ValueError("orders.csv has no rows")
    window_end = con.execute("SELECT ?::DATE - 1", [last_day]).fetchone()[0]
    window_start = con.execute("SELECT ?::DATE - ?", [window_end, cfg.window_days - 1]).fetchone()[0]
    con.execute("CREATE TEMP TABLE win AS SELECT ?::DATE AS start_d, ?::DATE AS end_d", [window_start, window_end])

    con.execute(f"""
        CREATE TEMP TABLE orders AS
        SELECT o.order_id, o.user_id, o.status, o.created_at, o.shipped_at, o.delivered_at, o.returned_at,
               o.num_of_item, CAST(timezone('{BRAND_TZ}', o.created_at) AS DATE) AS analysis_date
        FROM raw_orders o, win
        WHERE CAST(timezone('{BRAND_TZ}', o.created_at) AS DATE) BETWEEN win.start_d AND win.end_d
    """)

    # Items of window orders, with product attributes (items belong to their order's window, never split).
    con.execute(f"""
        CREATE TEMP TABLE items_all AS
        SELECT i.id AS line_id, i.order_id, i.user_id, i.product_id, i.inventory_item_id, i.status,
               i.created_at, i.shipped_at, i.delivered_at, i.returned_at, CAST(i.sale_price AS DOUBLE) AS sale_price,
               CAST(p.cost AS DOUBLE) AS cost, p.department, p.category,
               CAST(timezone('{BRAND_TZ}', i.created_at) AS DATE) AS analysis_date
        FROM raw_order_items i
        JOIN orders o USING (order_id)
        JOIN raw_products p ON p.id = i.product_id
    """)

    con.execute(f"""
        CREATE TEMP TABLE categories AS
        SELECT row_number() OVER (ORDER BY revenue DESC, department, category) AS rank, department, category,
               regexp_replace(upper(left(department, 1) || '_' || category), '[^A-Z0-9]+', '_', 'g') AS category_code,
               revenue, lines
        FROM (
            SELECT department, category, sum(sale_price) AS revenue, count(*) AS lines
            FROM items_all WHERE status <> 'Cancelled' GROUP BY 1, 2
        )
        ORDER BY rank
        LIMIT {int(cfg.n_categories)}
    """)

    con.execute(f"""
        CREATE TEMP TABLE product_sku AS
        SELECT p.id AS product_id, c.category_code || '-P' || band AS sku, p.department, p.category, band AS price_band,
               CAST(p.retail_price AS DOUBLE) AS retail_price, CAST(p.cost AS DOUBLE) AS cost
        FROM (
            SELECT *, ntile({int(cfg.groups_per_category)}) OVER (
                PARTITION BY department, category ORDER BY retail_price, id) AS band
            FROM raw_products
        ) p
        JOIN categories c USING (department, category)
    """)

    con.execute("""
        CREATE TEMP TABLE order_items AS
        SELECT a.*, ps.sku FROM items_all a LEFT JOIN product_sku ps USING (product_id)
    """)

    # Unit economics per SKU from the window's non-cancelled lines; catalogue prices if a band never sold.
    con.execute("""
        CREATE TEMP TABLE skus AS
        SELECT ps.sku, any_value(ps.department) AS department, any_value(ps.category) AS category,
               any_value(ps.price_band) AS price_band, count(*) AS n_products,
               min(ps.retail_price) AS retail_price_min, max(ps.retail_price) AS retail_price_max,
               coalesce(any_value(s.units), 0) AS units_window,
               coalesce(any_value(s.unit_price), avg(ps.retail_price)) AS unit_price,
               coalesce(any_value(s.unit_cogs), avg(ps.cost)) AS unit_cogs,
               any_value(s.return_rate) AS return_rate
        FROM product_sku ps
        LEFT JOIN (
            SELECT sku, count(*) AS units, avg(sale_price) AS unit_price, avg(cost) AS unit_cogs,
                   avg(CASE WHEN returned_at IS NOT NULL THEN 1.0 ELSE 0.0 END) AS return_rate
            FROM order_items WHERE status <> 'Cancelled' AND sku IS NOT NULL GROUP BY sku
        ) s USING (sku)
        GROUP BY ps.sku
    """)

    con.execute(f"""
        CREATE TEMP TABLE sessions AS
        SELECT * FROM (
            SELECT session_id,
                   max(user_id) AS user_id,
                   min(traffic_source) AS traffic_source,
                   count(DISTINCT traffic_source) AS n_traffic_sources,
                   min(created_at) AS started_at,
                   max(created_at) AS ended_at,
                   min(city) AS city, min(state) AS state, min(browser) AS browser,
                   count(*) AS n_events,
                   arg_min(uri, sequence_number) AS landing_uri,
                   bool_or(event_type = 'purchase') AS purchased,
                   count(*) FILTER (WHERE event_type = 'product') AS product_views
            FROM raw_events GROUP BY session_id
        ) s, win
        WHERE CAST(timezone('{BRAND_TZ}', s.started_at) AS DATE) BETWEEN win.start_d AND win.end_d
    """)
    con.execute("ALTER TABLE sessions DROP COLUMN start_d")
    con.execute("ALTER TABLE sessions DROP COLUMN end_d")
    con.execute("ALTER TABLE sessions ADD COLUMN analysis_date DATE")
    con.execute(f"UPDATE sessions SET analysis_date = CAST(timezone('{BRAND_TZ}', started_at) AS DATE)")

    con.execute("""
        CREATE TEMP TABLE users AS
        SELECT u.id AS user_id, u.age, u.gender, u.state, u.city, u.country, u.traffic_source, u.created_at
        FROM raw_users u
        WHERE u.id IN (SELECT user_id FROM orders UNION SELECT user_id FROM sessions WHERE user_id IS NOT NULL)
    """)
    return str(window_start), str(window_end)


def content_sha256(con: duckdb.DuckDBPyConnection, table: str) -> tuple[int, str]:
    """Canonical content hash: column names + every row (as DuckDB text, session TZ = UTC) in sorted order.

    Independent of parquet bytes and of Python-side type conversion.
    """
    cols = [d[0] for d in con.execute(f"SELECT * FROM {table} LIMIT 0").description]
    cur = con.execute(f"SELECT CAST(COLUMNS(*) AS VARCHAR) FROM {table} ORDER BY ALL")
    digest = hashlib.sha256(json.dumps(cols).encode())
    n = 0
    while batch := cur.fetchmany(50_000):
        for row in batch:
            digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
            digest.update(b"\n")
        n += len(batch)
    return n, digest.hexdigest()


def extract(raw_dir: str | Path, out_dir: str | Path, cfg: BackboneConfig | None = None) -> dict:
    cfg = cfg or BackboneConfig()
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    try:
        start, end = _build(con, raw_dir, cfg)
        tables = {}
        for t in TABLES:
            target = (out_dir / f"{t}.parquet").as_posix().replace("'", "''")
            con.execute(f"COPY (SELECT * FROM {t} ORDER BY {SORT_KEYS[t]}) TO '{target}' (FORMAT parquet)")
            rows, sha = content_sha256(con, t)
            tables[t] = {"rows": rows, "content_sha256": sha}
    finally:
        con.close()
    manifest = {
        "source": "TheLook eCommerce (mustafakeser4/looker-ecommerce-bigquery-dataset)",
        "provenance": "PUBLIC-SAMPLE",
        "brand_timezone": BRAND_TZ,
        "window": {"start": start, "end": end},
        "config": asdict(cfg),
        "tables": tables,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--out", default="data/world/backbone")
    args = ap.parse_args()
    m = extract(args.raw_dir, args.out)
    print(f"backbone {m['window']['start']}..{m['window']['end']} -> {args.out}")
    for t, info in m["tables"].items():
        print(f"  {t:12s} {info['rows']:>9,d} rows  {info['content_sha256'][:12]}")


if __name__ == "__main__":
    main()
