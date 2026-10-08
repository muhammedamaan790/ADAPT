"""Brand workspaces through the HTTP API: create (empty, all zero), activate, upload a CSV, the overview changes, the
simulator is refused there, and switching back restores the engine workspace."""

import httpx
from fastapi.testclient import TestClient

from adapt.api.main import create_app
from adapt.config.settings import Settings
from adapt.core.db import Database


def test_create_upload_and_switch(tmp_path):
    world = httpx.Client(base_url="http://world", transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"seed": 42, "day": 0, "today": "2026-10-01"})))
    settings = Settings(data_dir=tmp_path, env="test", auth_enabled=False)
    with TestClient(create_app(settings, db=Database(":memory:"), world_client=world)) as api:
        def post(path, body, n):
            return api.post(f"/api/v1{path}", json=body, headers={"X-Request-ID": f"r{n}", "Idempotency-Key": f"k{n}"})

        ws = post("/workspaces", {"name": "Acme Apparel"}, 1).json()
        assert ws["id"].startswith("brand-")
        act = post(f"/workspaces/{ws['id']}/activate", {}, 2).json()
        assert act["active_id"] == ws["id"] and len(act["items"]) == 2

        o = api.get("/api/v1/overview").json()
        assert o["workspace"] == "Acme Apparel" and all(m["value"] == 0 for m in o["metrics"])
        assert api.get("/api/v1/decisions").json() == []
        assert post("/sim/advance?days=1", {}, 3).status_code == 409

        rows = [{"date": "2026-09-30", "budget_id": "b1", "platform": "Meta", "spend": "2500", "impressions": "900",
                 "clicks": "30"}]
        st = post("/ingest/upload", {"type": "ads", "records": rows, "source_currency": "INR",
                                     "source_timezone": "Asia/Kolkata"}, 4).json()
        ack = post("/ingest/mapping/confirm", {"import_id": st["import_id"], "mapping": {k: k for k in rows[0]}}, 5)
        assert ack.status_code == 200 and "Acme Apparel" in ack.json()["message"]
        spend = {m["key"]: m["value"] for m in api.get("/api/v1/overview").json()["metrics"]}["spend"]
        assert spend == 2500

        post(f"/workspaces/{settings.workspace}/activate", {}, 6)
        assert api.get("/api/v1/workspaces").json()["active_id"] == settings.workspace
        assert post("/workspaces", {}, 7).status_code == 422
