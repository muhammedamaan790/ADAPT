"""Session login, CSRF, roles, throttling and the audit trail (spec §9.5) through the HTTP API."""

import itertools
import json

import pytest
from fastapi.testclient import TestClient

from adapt.api.auth import FAILURE_WINDOW, MAX_FAILURES, required_role
from adapt.api.main import create_app
from adapt.config.settings import Settings
from adapt.core.db import Database

PW = "correct horse battery"
REQUEST_IDS = itertools.count()


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path, env="test", auth_enabled=True, seed_password=PW, session_secret="t" * 32)
    with TestClient(create_app(settings=settings, db=Database(":memory:"))) as c:
        yield c


def login(c, user, pw=PW):
    r = c.post("/api/v1/auth/login", json={"user_id": user, "password": pw})
    return r, (r.json().get("csrf_token") if r.status_code == 200 else None)


def write(c, path, csrf=None, method="post"):
    rid = next(REQUEST_IDS)
    headers = {"X-Request-ID": f"t-{rid}", "Idempotency-Key": f"t-{rid}"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    return getattr(c, method)(f"/api/v1{path}", json={}, headers=headers)


def test_reads_need_a_session_and_health_stays_public(client):
    assert client.get("/api/v1/health").status_code == 200
    r = client.get("/api/v1/decisions")
    assert r.status_code == 401 and r.json()["detail"].startswith("AUTH_REQUIRED")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_sets_an_httponly_cookie_and_returns_the_csrf_token(client):
    bad, _ = login(client, "maria", "wrong")
    assert bad.status_code == 401
    assert login(client, "nobody")[0].status_code == 401
    r, csrf = login(client, "maria")
    assert r.status_code == 200 and r.json()["user"] == {"user_id": "maria", "display_name": "Maria (Growth Manager)",
                                                         "role": "manager"}
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/v1/decisions").status_code == 200
    me = client.get("/api/v1/auth/me").json()
    assert me["user"]["user_id"] == "maria" and me["csrf_token"] == csrf
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/decisions").status_code == 401


def test_writes_need_the_csrf_token(client):
    _, csrf = login(client, "maria")
    r = write(client, "/sim/scenario/NOPE")
    assert r.status_code == 403 and r.json()["detail"].startswith("CSRF_FAILED")
    assert write(client, "/sim/scenario/NOPE", "0" * 64).status_code == 403
    assert write(client, "/sim/scenario/NOPE", csrf).status_code == 422  # past the guard: the route's own answer


def test_roles(client):
    _, csrf = login(client, "viewer")
    r = write(client, "/sim/advance", csrf)
    assert r.status_code == 403 and "manager role" in r.json()["detail"]
    assert write(client, "/decisions/D-1/simulate", csrf).status_code == 422  # read-only: past the guard
    _, csrf = login(client, "maria")
    assert write(client, "/objective", csrf, "put").status_code == 403
    _, csrf = login(client, "admin")
    assert write(client, "/objective", csrf, "put").status_code == 422  # admin reaches the (NOT_BUILT) route
    assert required_role("GET", "/api/v1/policy") == "viewer"
    assert required_role("PUT", "/api/v1/policy") == "admin"
    assert required_role("POST", "/api/v1/decisions/x/approve") == "manager"
    assert required_role("POST", "/api/v1/models/response_curve/rollback") == "admin"


def test_failed_logins_are_throttled_per_user(client):
    for _ in range(MAX_FAILURES):
        assert login(client, "maria", "wrong")[0].status_code == 401
    r, _ = login(client, "maria")
    assert r.status_code == 429 and FAILURE_WINDOW >= 60
    assert login(client, "admin")[0].status_code == 200  # other users are unaffected


def test_users_file_holds_hashes_only_and_writes_are_audited(client, tmp_path):
    users = (tmp_path / "auth" / "users.json").read_text(encoding="utf-8")
    assert PW not in users and "$argon2" in users
    assert not (tmp_path / "auth" / "initial_credentials.txt").exists()  # the password came from settings
    _, csrf = login(client, "viewer")
    write(client, "/sim/advance", csrf)
    lines = [json.loads(x) for x in (tmp_path / "auth" / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {"actor_id": "viewer", "role": "viewer", "method": "POST", "path": "/api/v1/sim/advance",
            "status": 403}.items() <= lines[-1].items()


def test_without_a_seed_password_random_ones_are_written_once(tmp_path):
    settings = Settings(data_dir=tmp_path, env="test", auth_enabled=True)
    with TestClient(create_app(settings=settings, db=Database(":memory:"))):
        pass
    creds = (tmp_path / "auth" / "initial_credentials.txt").read_text(encoding="utf-8")
    pw = next(line.split(": ", 1)[1] for line in creds.splitlines() if line.startswith("maria"))
    with TestClient(create_app(settings=settings, db=Database(":memory:"))) as c:  # a restart reuses users.json
        assert login(c, "maria", pw)[0].status_code == 200
