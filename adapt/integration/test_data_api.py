"""C6 (early) Data Hub endpoints on a synced + built workspace: shapes match the frontend contracts and the numbers
come from the canonical state."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from adapt.api.main import create_app
from adapt.ingest.sync import run_sync
from adapt.reconcile.build import build_canonical, logical_now


@pytest.fixture
def api(world, http, db):
    run_sync(db, http)
    build_canonical(db, logical_now(date(2026, 10, 1)))
    with TestClient(create_app(db=db)) as c:
        yield c


def test_sources_match_the_frontend_source_schema(api):
    rows = api.get("/api/v1/data/sources").json()
    assert {r["id"] for r in rows} == {"meta_ads", "google_ads", "store", "finance", "ga4", "erp"}
    for r in rows:
        assert set(r) == {"id", "name", "kind", "score", "status", "freshness", "provenance"}
        assert 0 <= r["score"] <= 100 and r["status"] == "GREEN"
        assert r["provenance"] in {"PUBLIC-SAMPLE", "CALIBRATED", "SIMULATED", "LIVE"}
    detail = api.get("/api/v1/data/health").json()
    store = next(d for d in detail if d["id"] == "store")
    assert store["checks"] and all(c["passed"] for c in store["checks"]) and store["hard_failures"] == []


def test_mapping_coverage(api, db):
    r = api.get("/api/v1/data/mapping-coverage").json()
    assert set(r) == {"coverage", "unmapped", "note"}
    total, unm = db.query("SELECT sum(attributed_revenue), sum(attributed_revenue) FILTER "
                          "(WHERE sku = '__unmapped__') FROM core.campaign_sku")[0]
    assert r["coverage"] == pytest.approx(1 - unm / total, abs=1e-4)
    assert 0.8 < r["coverage"] < 1.0 and r["unmapped"] == []  # cross-sell only, no campaign above 20%


def test_reconciliation_window_and_excess(api, db):
    r = api.get("/api/v1/data/reconciliation").json()
    assert r["window_end"] == "2026-09-30" and r["window_start"] == "2026-09-03"
    assert r["attribution_excess"] == pytest.approx(r["platform_revenue"] - r["store_revenue"], abs=0.02)
    assert r["attribution_excess"] > 0  # platforms over-claim
    p = {x["platform"]: x for x in r["platforms"]}
    assert p["meta"]["over_attribution"] > p["google"]["over_attribution"] > 1
    assert 0.7 < p["meta"]["session_click_ratio"] < 1.0


def test_openapi_lists_the_data_routes(api):
    paths = api.get("/openapi.json").json()["paths"]
    assert {"/api/v1/data/sources", "/api/v1/data/health", "/api/v1/data/mapping-coverage",
            "/api/v1/data/reconciliation"} <= set(paths)
