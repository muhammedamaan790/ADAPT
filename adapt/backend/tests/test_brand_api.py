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


def test_inventory_page_reads_uploads(tmp_path):
    world = httpx.Client(base_url="http://world", transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"seed": 42, "day": 0})))
    settings = Settings(data_dir=tmp_path, env="test", auth_enabled=False)
    with TestClient(create_app(settings, db=Database(":memory:"), world_client=world)) as api:
        def post(path, body, n):
            return api.post(f"/api/v1{path}", json=body, headers={"X-Request-ID": f"i{n}", "Idempotency-Key": f"i{n}"})

        ws = post("/workspaces", {"name": "Stock Co"}, 1).json()
        post(f"/workspaces/{ws['id']}/activate", {}, 2)
        assert api.get("/api/v1/data/inventory").status_code == 503
        for n, (kind, rows) in enumerate([
                ("inventory", [{"sku": "S1", "on_hand": "5", "reserved": "0", "safety_stock": "10"},
                               {"sku": "S2", "on_hand": "500", "reserved": "0", "safety_stock": "10"}]),
                ("orders", [{"date": "2026-09-30", "order_id": "o1", "sku": "S1", "quantity": "28",
                             "net_revenue": "2800"}])]):
            st = post("/ingest/upload", {"type": kind, "records": rows, "source_currency": "INR",
                                         "source_timezone": "Asia/Kolkata"}, 10 + n).json()
            post("/ingest/mapping/confirm", {"import_id": st["import_id"], "mapping": {k: k for k in rows[0]}}, 20 + n)
        inv = api.get("/api/v1/data/inventory").json()
        by = {s["sku"]: s for s in inv["skus"]}
        assert by["S1"]["action"] == "RESTOCK" and by["S1"]["units_28d_avg"] == 1.0
        assert by["S2"]["action"] == "CLEAR_EXCESS"


def test_rule_loop_proposes_records_measures_and_learns(tmp_path):
    """The demo files: signals and proposals after upload, approval records a change list, week 2 measures it."""
    import csv
    from pathlib import Path

    demo = Path(__file__).resolve().parents[2] / "demo_data"
    world = httpx.Client(base_url="http://world", transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"seed": 42, "day": 0})))
    settings = Settings(data_dir=tmp_path, env="test", auth_enabled=False)
    with TestClient(create_app(settings, db=Database(":memory:"), world_client=world)) as api:
        n = iter(range(1000))

        def post(path, body):
            i = next(n)
            return api.post(f"/api/v1{path}", json=body, headers={"X-Request-ID": f"l{i}", "Idempotency-Key": f"l{i}"})

        def upload(kind, name):
            rows = list(csv.DictReader(open(demo / name, encoding="utf-8")))
            st = post("/ingest/upload", {"type": kind, "records": rows, "source_currency": "INR",
                                         "source_timezone": "Asia/Kolkata"}).json()
            ack = post("/ingest/mapping/confirm", {"import_id": st["import_id"], "mapping": {k: k for k in rows[0]}})
            assert ack.status_code == 200, ack.text
            return ack.json()["message"]

        ws = post("/workspaces", {"name": "Loop Co"}).json()
        post(f"/workspaces/{ws['id']}/activate", {})
        for kind in ("ads", "orders", "margins", "inventory"):
            upload(kind, f"{kind}.csv")

        anomalies = api.get("/api/v1/anomalies").json()
        assert any(a["metric"] == "ROAS" and a["direction"] == "DOWN" and "20000000009" in a["entity"]
                   for a in anomalies)
        decisions = {d["type"]: d for d in api.get("/api/v1/decisions").json() if d["status"] == "PENDING_APPROVAL"}
        realloc = decisions["reallocate"]
        assert realloc["status"] == "PENDING_APPROVAL" and "restock_alert" in decisions
        assert sum(leg["after"] for leg in realloc["legs"]) <= sum(leg["before"] for leg in realloc["legs"])
        assert all(abs(leg["after"] - leg["before"]) <= 0.2 * leg["before"] + 100 for leg in realloc["legs"])
        assert api.get(f"/api/v1/decisions/{realloc['decision_id']}/evidence").json()["decomposition_kind"] == "ROAS"
        overview = api.get("/api/v1/overview").json()
        assert overview["attention"] and overview["calibration"] == 1.0

        bad = post(f"/decisions/{realloc['decision_id']}/approve", {"decision_hash": "x"})
        assert bad.status_code == 409
        ok = post(f"/decisions/{realloc['decision_id']}/approve", {"decision_hash": realloc["decision_hash"]})
        assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
        execs = api.get("/api/v1/executions").json()
        assert execs[0]["state"] == "RESOLVED_MANUALLY" and len(api.get("/api/v1/ledger").json()) == len(
            realloc["legs"])
        r = post(f"/decisions/{decisions['restock_alert']['decision_id']}/reject",
                 {"decision_hash": decisions["restock_alert"]["decision_hash"], "reason": "reorder already placed"})
        assert r.json()["status"] == "REJECTED"

        upload("ads", "ads_week2.csv")
        upload("orders", "orders_week2.csv")
        outs = api.get("/api/v1/outcomes").json()
        assert len(outs) == 1 and outs[0]["verdict"] == "SUCCESS" and outs[0]["calibration_applied"]
        cal = api.get("/api/v1/learning/calibration").json()
        assert cal["factor"] != 1.0 and len(cal["updates"]) == 1
        assert api.get("/api/v1/data/reconciliation").json()["platform_revenue"] > 0
        assert all(s["provenance"] == "UPLOADED" for s in api.get("/api/v1/data/health").json())
