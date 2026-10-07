"""world-service public listener: control routes, idempotency, conflicts, and no truth on the public port (T37)."""

import pytest
from fastapi.testclient import TestClient

from world.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "sim_state.duckdb")) as c:
        yield c


def test_health_before_and_after_seeding(client):
    assert client.get("/health").json() == {"seeded": False, "seed": None, "day": None}
    r = client.post("/control/reset", json={"seed": 42}, headers={"X-Request-ID": "r0"})
    assert r.status_code == 200 and r.json()["seq"] == 1
    assert client.get("/health").json() == {"seeded": True, "seed": 42, "day": 0}


def test_advance_is_idempotent_per_request_id(client):
    client.post("/control/reset", json={"seed": 42}, headers={"X-Request-ID": "r0"})
    a = client.post("/control/advance", json={"days": 2}, headers={"X-Request-ID": "adv1"}).json()
    b = client.post("/control/advance", json={"days": 2}, headers={"X-Request-ID": "adv1"}).json()
    assert (a["replayed"], b["replayed"]) == (False, True)
    assert client.get("/health").json()["day"] == 2


def test_advance_before_reset_is_409(client):
    r = client.post("/control/advance", json={"days": 1}, headers={"X-Request-ID": "a"})
    assert r.status_code == 409


def test_mutations_require_a_request_id(client):
    assert client.post("/control/reset", json={"seed": 1}).status_code == 422


def test_invalid_days_rejected(client):
    client.post("/control/reset", json={"seed": 42}, headers={"X-Request-ID": "r0"})
    assert client.post("/control/advance", json={"days": 0}, headers={"X-Request-ID": "z"}).status_code == 422


@pytest.mark.parametrize("path", ["/truth", "/truth/gt_incidents", "/truth/params", "/eval/truth"])
def test_truth_routes_are_not_on_the_public_listener(client, path):
    assert client.get(path).status_code == 404


def test_log_exposes_committed_order(client):
    client.post("/control/reset", json={"seed": 7}, headers={"X-Request-ID": "r0", "X-Actor-ID": "lab"})
    client.post("/control/advance", json={"days": 1}, headers={"X-Request-ID": "r1"})
    log = client.get("/control/log").json()
    entries = [(e["seq"], e["operation"], e["actor_id"]) for e in log]
    assert entries == [(1, "reset", "lab"), (2, "advance", "scenario-lab")]
