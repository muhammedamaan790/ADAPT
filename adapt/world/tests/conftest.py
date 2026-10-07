"""Shared world fixtures: a dense synthetic backbone and a Global Ads sample (no real data needed in CI)."""

import csv
import json
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pytest

BACKBONE_DAYS = 120
CATEGORIES = [("Men", "Jeans", "M_JEANS"), ("Women", "Dresses", "W_DRESSES"), ("Men", "Swim", "M_SWIM")]
SOURCES = {"Email": 45, "Adwords": 30, "YouTube": 10, "Facebook": 10, "Organic": 5}


def make_backbone(out: Path, days: int = BACKBONE_DAYS, seed: int = 0) -> Path:
    """Writes the backbone parquet tables world.truth reads: 3 categories x 2 price bands, dense daily sales."""
    rng = np.random.default_rng(seed)
    out.mkdir(parents=True, exist_ok=True)
    start = date(2023, 1, 1)
    con = duckdb.connect()
    con.execute("CREATE TABLE categories (rank INT, department VARCHAR, category VARCHAR, category_code VARCHAR, "
                "revenue DOUBLE, lines BIGINT)")
    con.execute("CREATE TABLE skus (sku VARCHAR, department VARCHAR, category VARCHAR, price_band INT, "
                "n_products BIGINT, retail_price_min DOUBLE, retail_price_max DOUBLE, units_window BIGINT, "
                "unit_price DOUBLE, unit_cogs DOUBLE, return_rate DOUBLE)")
    con.execute("CREATE TABLE product_sku (product_id BIGINT, sku VARCHAR, department VARCHAR, category VARCHAR, "
                "price_band INT, retail_price DOUBLE, cost DOUBLE)")
    con.execute("CREATE TABLE order_items (line_id BIGINT, order_id BIGINT, product_id BIGINT, status VARCHAR, "
                "sale_price DOUBLE, cost DOUBLE, returned_at TIMESTAMP, analysis_date DATE, sku VARCHAR)")
    con.execute("CREATE TABLE sessions (session_id VARCHAR, traffic_source VARCHAR, purchased BOOLEAN)")
    line = 0
    for rank, (dept, cat, code) in enumerate(CATEGORIES, start=1):
        con.execute("INSERT INTO categories VALUES (?, ?, ?, ?, ?, ?)", [rank, dept, cat, code, 1000.0 / rank, 100])
        for band, (price, cost) in enumerate([(40.0, 20.0), (90.0, 40.0)], start=1):
            pid = rank * 10 + band
            sku = f"{code}-P{band}"
            con.execute("INSERT INTO product_sku VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [pid, sku, dept, cat, band, price, cost])
            units = 0
            for d in range(days):
                trend = 1.0 + 0.5 * d / days  # growing demand
                for _ in range(rng.poisson(6 * trend / rank)):
                    line += 1
                    units += 1
                    returned = "2023-06-01" if rng.random() < 0.1 else None
                    con.execute("INSERT INTO order_items VALUES (?, ?, ?, 'Complete', ?, ?, ?, ?, ?)",
                                [line, line, pid, price, cost, returned, start + timedelta(days=d), sku])
            con.execute("INSERT INTO skus VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 0.1)",
                        [sku, dept, cat, band, price, price, units, price, cost])
    for d in range(days):  # warehouse (unmapped) sales
        line += 1
        con.execute("INSERT INTO order_items VALUES (?, ?, 999, 'Complete', 30, 15, NULL, ?, NULL)",
                    [line, line, start + timedelta(days=d)])
    n = 0
    for src, purchases in SOURCES.items():
        for i in range(purchases * 2):
            n += 1
            con.execute("INSERT INTO sessions VALUES (?, ?, ?)", [f"s{n}", src, i < purchases])
    for t in ("categories", "skus", "product_sku", "order_items", "sessions"):
        con.execute(f"COPY {t} TO '{(out / f'{t}.parquet').as_posix()}' (FORMAT parquet)")
    con.close()
    end = start + timedelta(days=days - 1)
    (out / "manifest.json").write_text(json.dumps({"window": {"start": str(start), "end": str(end)}}))
    return out


def make_global_ads(path: Path) -> Path:
    """Enough E-commerce rows for Google Search and Meta; too few for Google Video (exercises the fallback)."""
    rng = np.random.default_rng(1)
    rows = []
    for platform, ctype, n, ctr, cvr, cpm in [("Google Ads", "Search", 15, 0.04, 0.05, 70.0),
                                              ("Google Ads", "Video", 3, 0.04, 0.05, 70.0),
                                              ("Meta Ads", "Display", 8, 0.025, 0.045, 30.0),
                                              ("Meta Ads", "Video", 8, 0.025, 0.045, 30.0)]:
        for i in range(n):
            impressions = 100_000
            clicks = int(impressions * ctr * rng.uniform(0.7, 1.3))
            conv = max(1, int(clicks * cvr * rng.uniform(0.7, 1.3)))
            spend = impressions * cpm * rng.uniform(0.7, 1.3) / 1000
            rows.append([f"2024-01-{i + 1:02d}", platform, ctype, "E-commerce", "India", impressions, clicks,
                         clicks / impressions, spend / clicks, spend, conv, spend / conv, conv * 100.0,
                         conv * 100.0 / spend])
    rows.append(["2024-01-01", "Google Ads", "Video", "SaaS", "India", 100_000, 4000, 0.04, 1, 4000, 200, 20, 1, 1])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "platform", "campaign_type", "industry", "country", "impressions", "clicks", "CTR", "CPC",
                    "ad_spend", "conversions", "CPA", "revenue", "ROAS"])
        w.writerows(rows)
    return path


@pytest.fixture(scope="session")
def backbone_dir(tmp_path_factory) -> Path:
    return make_backbone(tmp_path_factory.mktemp("backbone"))


@pytest.fixture(scope="session")
def global_ads_csv(tmp_path_factory) -> Path:
    return make_global_ads(tmp_path_factory.mktemp("ga") / "global_ads.csv")


@pytest.fixture(scope="session")
def seeded_world_dir(backbone_dir, global_ads_csv, tmp_path_factory) -> Path:
    """A seeded fixture world (truth + history + baseline). Copy it before mutating (see world_copy)."""
    from world.seed import seed_world
    from world.truth import WorldConfig

    out = tmp_path_factory.mktemp("seeded")
    store, _ = seed_world(WorldConfig(seed=42, backbone_dir=backbone_dir, global_ads_csv=global_ads_csv,
                                      history_end_date=date(2026, 9, 30)), out)
    store.close()
    return out


@pytest.fixture
def world_copy(seeded_world_dir, tmp_path) -> Path:
    import shutil

    dst = tmp_path / "world"
    shutil.copytree(seeded_world_dir, dst)
    return dst
