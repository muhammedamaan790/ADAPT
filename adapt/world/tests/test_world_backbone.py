"""A1 subset 'done when': the backbone is correct on a hand-built fixture and reproducible by checksum."""

import csv
import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from world.backbone import BackboneConfig, extract

P_HDR = ["id", "cost", "category", "name", "brand", "retail_price", "department", "sku", "distribution_center_id"]
O_HDR = ["order_id", "user_id", "status", "gender", "created_at", "returned_at", "shipped_at", "delivered_at",
         "num_of_item"]
I_HDR = ["id", "order_id", "user_id", "product_id", "inventory_item_id", "status", "created_at", "shipped_at",
         "delivered_at", "returned_at", "sale_price"]
E_HDR = ["id", "user_id", "sequence_number", "session_id", "created_at", "ip_address", "city", "state", "postal_code",
         "browser", "traffic_source", "uri", "event_type"]
U_HDR = ["id", "first_name", "last_name", "email", "age", "gender", "state", "street_address", "postal_code", "city",
         "country", "latitude", "longitude", "traffic_source", "created_at"]

CFG = BackboneConfig(window_days=30, n_categories=2, groups_per_category=2)


def _write(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def ts(s: str) -> str:
    return s + "+00:00"


@pytest.fixture
def raw(tmp_path) -> Path:
    d = tmp_path / "raw" / "thelook"
    _write(d / "products.csv", P_HDR, [
        [1, 10, "Jeans", "J1", "B", 25, "Men", "x1", 1],
        [2, 20, "Jeans", "J2", "B", 50, "Men", "x2", 1],
        [3, 30, "Jeans", "J3", "B", 80, "Men", "x3", 1],
        [4, 40, "Jeans", "J4", "B", 120, "Men", "x4", 1],
        [5, 10, "Dresses", "D1", "B", 30, "Women", "x5", 1],
        [6, 20, "Dresses", "D2", "B", 60, "Women", "x6", 1],
        [7, 5, "Jeans", "WJ", "B", 15, "Women", "x7", 1],
        [9, 400, "Suits", "S1", "B", 1000, "Men", "x9", 1],
    ])
    # last order 2023-03-31 (IST) -> window = 2023-03-01 .. 2023-03-30
    orders = [
        [1, 1, "Complete", "M", ts("2023-03-10 10:00:00"), "", "", "", 2],
        [2, 2, "Returned", "F", ts("2023-03-11 10:00:00"), ts("2023-03-20 10:00:00"), "", "", 1],
        [3, 3, "Cancelled", "M", ts("2023-03-12 10:00:00"), "", "", "", 1],
        [4, 1, "Complete", "M", ts("2023-03-13 10:00:00"), "", "", "", 1],
        [5, 4, "Complete", "M", ts("2023-02-28 18:40:00"), "", "", "", 1],  # 2023-03-01 00:10 IST -> inside
        [6, 5, "Complete", "M", ts("2023-02-28 18:00:00"), "", "", "", 1],  # 2023-02-28 23:30 IST -> outside
        [7, 5, "Complete", "M", ts("2023-03-31 10:00:00"), "", "", "", 1],  # last (partial) day -> outside
    ]
    _write(d / "orders.csv", O_HDR, orders)
    items = [
        [11, 1, 1, 4, 101, "Complete", orders[0][4], "", "", "", 120],
        [12, 1, 1, 2, 102, "Complete", orders[0][4], "", "", "", 50],
        [13, 2, 2, 6, 103, "Returned", orders[1][4], "", "", ts("2023-03-20 10:00:00"), 60],
        [14, 3, 3, 9, 104, "Cancelled", orders[2][4], "", "", "", 1000],
        [15, 4, 1, 7, 105, "Complete", orders[3][4], "", "", "", 15],
        [16, 5, 4, 1, 106, "Complete", orders[4][4], "", "", "", 25],
        [17, 6, 5, 3, 107, "Complete", orders[5][4], "", "", "", 80],
        [18, 7, 5, 3, 108, "Complete", orders[6][4], "", "", "", 80],
    ]
    _write(d / "order_items.csv", I_HDR, items)
    ev = lambda i, u, seq, s, t, src, uri, et: [i, u, seq, s, ts(t), "1.1.1.1", "Pune", "MH", "411001", "Chrome",  # noqa: E731
                                                src, uri, et]
    _write(d / "events.csv", E_HDR, [
        ev(1, 1, 2, "s1", "2023-03-10 09:55:00", "Adwords", "/product/4", "product"),
        ev(2, 1, 1, "s1", "2023-03-10 09:50:00", "Adwords", "/home", "home"),
        ev(3, 1, 3, "s1", "2023-03-10 10:00:00", "Adwords", "/purchase", "purchase"),
        ev(4, "", 1, "s2", "2023-03-05 08:00:00", "Facebook", "/department/men", "department"),
        ev(5, 6, 1, "s3", "2023-02-20 08:00:00", "Email", "/home", "home"),
    ])
    _write(d / "users.csv", U_HDR, [
        [u, "a", "b", "e", 30, "M", "MH", "st", "411001", "Pune", "India", 18.5, 73.8, "Search",
         ts("2022-01-01 00:00:00")]
        for u in (1, 2, 3, 4, 5, 6)
    ])
    return tmp_path / "raw"


def rows(out: Path, table: str, sql: str = "SELECT * FROM t"):
    con = duckdb.connect()
    con.execute(f"CREATE VIEW t AS SELECT * FROM '{(out / f'{table}.parquet').as_posix()}'")
    return con.execute(sql).fetchall()


def test_window_and_timezone_boundaries(raw, tmp_path):
    out = tmp_path / "bb"
    m = extract(raw, out, CFG)
    assert m["window"] == {"start": "2023-03-01", "end": "2023-03-30"}
    assert sorted(r[0] for r in rows(out, "orders", "SELECT order_id FROM t")) == [1, 2, 3, 4, 5]
    assert sorted(r[0] for r in rows(out, "order_items", "SELECT line_id FROM t")) == [11, 12, 13, 14, 15, 16]


def test_top_categories_exclude_cancelled_revenue(raw, tmp_path):
    out = tmp_path / "bb"
    extract(raw, out, CFG)
    cats = rows(out, "categories", "SELECT rank, category_code, revenue FROM t ORDER BY rank")
    assert cats == [(1, "M_JEANS", 195.0), (2, "W_DRESSES", 60.0)]  # the 1000 Suits line was cancelled


def test_price_band_skus_cover_every_product_of_a_promoted_category(raw, tmp_path):
    out = tmp_path / "bb"
    extract(raw, out, CFG)
    mapping = dict(rows(out, "product_sku", "SELECT product_id, sku FROM t"))
    assert mapping == {1: "M_JEANS-P1", 2: "M_JEANS-P1", 3: "M_JEANS-P2", 4: "M_JEANS-P2",
                       5: "W_DRESSES-P1", 6: "W_DRESSES-P2"}
    item_sku = dict(rows(out, "order_items", "SELECT line_id, sku FROM t"))
    assert item_sku[15] is None and item_sku[14] is None  # women's jeans and suits are unmapped (warehouse)


def test_sku_unit_economics(raw, tmp_path):
    out = tmp_path / "bb"
    extract(raw, out, CFG)
    skus = {r[0]: r[1:] for r in rows(
        out, "skus", "SELECT sku, units_window, unit_price, unit_cogs, return_rate, n_products FROM t")}
    assert skus["M_JEANS-P1"] == (2, 37.5, 15.0, 0.0, 2)  # lines at 50 (cost 20) and 25 (cost 10)
    assert skus["M_JEANS-P2"] == (1, 120.0, 40.0, 0.0, 2)
    assert skus["W_DRESSES-P2"] == (1, 60.0, 20.0, 1.0, 1)  # its only line was returned
    assert skus["W_DRESSES-P1"] == (0, 30.0, 10.0, None, 1)  # never sold: catalogue price, unknown return rate


def test_sessions_are_aggregated_from_events(raw, tmp_path):
    out = tmp_path / "bb"
    extract(raw, out, CFG)
    s = {r[0]: r[1:] for r in rows(
        out, "sessions",
        "SELECT session_id, user_id, traffic_source, landing_uri, purchased, n_events, product_views FROM t")}
    assert set(s) == {"s1", "s2"}  # s3 started before the window
    assert s["s1"] == (1, "Adwords", "/home", True, 3, 1)
    assert s["s2"] == (None, "Facebook", "/department/men", False, 1, 0)


def test_users_restricted_to_window_activity(raw, tmp_path):
    out = tmp_path / "bb"
    extract(raw, out, CFG)
    assert sorted(r[0] for r in rows(out, "users", "SELECT user_id FROM t")) == [1, 2, 3, 4]


def test_reproducible_by_checksum_and_raw_untouched(raw, tmp_path):
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (raw / "thelook").iterdir()}
    a = extract(raw, tmp_path / "a", CFG)
    b = extract(raw, tmp_path / "b", CFG)
    assert a["tables"] == b["tables"]
    assert json.loads((tmp_path / "a" / "manifest.json").read_text())["tables"] == a["tables"]
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (raw / "thelook").iterdir()}
    assert before == after
