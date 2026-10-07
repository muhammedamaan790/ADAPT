"""M0 profiling gate: pass on a well-formed fixture, fail on each required defect."""

import csv
import datetime as dt
from pathlib import Path

from adapt.ingest.profile import profile


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def make_thelook(raw: Path, *, drop_sale_price=True, orphan=False, days=400, unlinked_purchase=False):
    d = raw / "thelook"
    _write(d / "products.csv",
           ["id", "cost", "category", "name", "brand", "retail_price", "department", "sku", "distribution_center_id"],
           [[1, 10, "Jeans", "J1", "B", 25, "Women", "SKU1", 1], [2, 5, "Tops", "T1", "B", 12, "Women", "SKU2", 1]])
    last = (dt.datetime(2022, 1, 1, 10) + dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    _write(d / "orders.csv", ["order_id", "user_id", "status", "created_at", "num_of_item"],
           [[1, 7, "Complete", "2022-01-01 10:00:00", 1], [2, 7, "Complete", last, 1]])
    oi_header = ["id", "order_id", "user_id", "product_id", "inventory_item_id", "status", "created_at"]
    if not drop_sale_price:
        oi_header.append("sale_price")
    _write(d / "order_items.csv", oi_header,
           [[1, 1, 7, 1, 1, "Complete", "2022-01-01"] + ([25] if not drop_sale_price else [])])
    _write(d / "inventory_items.csv", ["id", "product_id", "created_at", "sold_at", "cost"],
           [[1, 99 if orphan else 1, "2021-12-01", "2022-01-01", 10]])
    _write(d / "events.csv",
           ["id", "user_id", "session_id", "created_at", "traffic_source", "uri", "event_type", "city", "state",
            "browser"],
           [[1, "" if unlinked_purchase else 7, "s1", "2022-01-01", "Facebook", "/product/1", "purchase", "Pune",
             "MH", "Chrome"]])
    _write(d / "users.csv", ["id", "country", "created_at", "traffic_source"], [[7, "India", "2021-01-01", "Search"]])


def test_gate_passes_with_documented_fallbacks(tmp_path):
    make_thelook(tmp_path)
    r = profile(tmp_path)
    assert r["gate"] == "PASS", r["failures"]
    # sale_price absent -> documented fallback, not failure
    assert any("sale_price" in f for f in r["fallbacks"])
    # calibration datasets absent -> fallbacks, not failures
    assert any("global_ads" in f for f in r["fallbacks"])
    assert r["datasets"]["thelook"]["checks"]["orders_history_days"] >= 365


def test_gate_fails_on_missing_required_column(tmp_path):
    make_thelook(tmp_path)
    p = tmp_path / "thelook" / "users.csv"
    _write(p, ["id", "country", "created_at"], [[7, "India", "2021-01-01"]])  # traffic_source removed
    r = profile(tmp_path)
    assert r["gate"] == "FAIL"
    assert any("traffic_source" in f for f in r["failures"])


def test_gate_fails_on_orphans_short_history_and_unlinked_purchases(tmp_path):
    make_thelook(tmp_path, orphan=True, days=100, unlinked_purchase=True)
    r = profile(tmp_path)
    assert r["gate"] == "FAIL"
    msgs = " ".join(r["failures"])
    assert "orphan" in msgs and "history" in msgs and "purchase events" in msgs


def test_global_ads_columns_discovered_by_synonym(tmp_path):
    make_thelook(tmp_path)
    _write(tmp_path / "global_ads" / "ads.csv", ["Date", "Platform", "Impressions", "Clicks", "Cost", "Revenue"],
           [["2024-01-01", "Meta", 1000, 20, 50.0, 120.0]])
    r = profile(tmp_path)
    m = r["datasets"]["global_ads"]["field_mapping"]
    assert m["platform"] == "Platform" and m["spend"] == "Cost" and m["impressions"] == "Impressions"
    assert not any(f.startswith("global_ads: no column") for f in r["fallbacks"])
