from fastapi.testclient import TestClient

from adapt.api.main import create_app
from adapt.config.settings import Settings
from adapt.core.db import Database


def test_health_reports_ok_and_modes(tmp_path):
    settings = Settings(data_dir=tmp_path, env="test", google_execution_mode="mock", GROQ_API_KEY=None)
    app = create_app(settings=settings, db=Database(":memory:"))
    with TestClient(app) as client:
        r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["execution_modes"] == {"google": "mock", "meta": "mock"}
    assert body["llm_mode"] == "offline"


def test_data_routes_say_503_until_the_canonical_state_exists(tmp_path):
    app = create_app(settings=Settings(data_dir=tmp_path, env="test"), db=Database(":memory:"))
    with TestClient(app) as client:
        for path in ("/api/v1/data/sources", "/api/v1/data/mapping-coverage", "/api/v1/data/reconciliation"):
            r = client.get(path)
            assert r.status_code == 503 and "not built" in r.json()["detail"]


def test_openapi_contract_is_generated(tmp_path):
    app = create_app(settings=Settings(data_dir=tmp_path, env="test"), db=Database(":memory:"))
    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()
    assert "/api/v1/health" in spec["paths"]
    assert "HealthResponse" in spec["components"]["schemas"]


def test_every_connector_has_a_display_name():
    """A connector without a name entry used to crash /overview (KeyError: 'amazon_ads' after Stage 2 added it)."""
    from adapt.api.routers.data import SOURCE_META, source_meta
    from adapt.ingest.sync import CONNECTORS

    assert set(CONNECTORS) <= set(SOURCE_META)
    assert source_meta("new_source") == ("New Source", "other")
