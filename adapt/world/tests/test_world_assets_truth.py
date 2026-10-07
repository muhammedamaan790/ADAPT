"""Creative asset registry + SVG cards, and the eval-only truth listener (T37: truth unreachable on the public
listener, 401 without the eval token on the loopback listener)."""

import socket
import time
import xml.etree.ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient

from world.main import create_app
from world.step import make_store
from world.truth import load_truth
from world.truth_api import TOKEN_FILE, create_truth_app


@pytest.fixture
def client(world_copy):
    with TestClient(create_app(world_dir=world_copy)) as c:
        yield c


def test_registry_and_svg_cards(client):
    reg = client.get("/assets/creatives").json()["creative_assets"]
    assert reg and all(r["provenance"] == "SIMULATED" and r["content_type"] == "image/svg+xml" for r in reg)
    first = reg[0]
    r = client.get(first["asset_uri"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    root = ET.fromstring(r.text)  # well-formed XML
    assert root.get("width") == "600" and first["headline_text"] in r.text
    assert client.get(first["asset_uri"]).text == r.text  # deterministic
    thumb = client.get(first["thumbnail_uri"])
    assert ET.fromstring(thumb.text).get("width") == "160"
    assert client.get("/assets/creatives/999.svg").status_code == 404
    assert client.get("/assets/creatives/abc").status_code == 404


def test_meta_ads_image_urls_resolve(client):
    ads = client.get("/meta/v25.0/act_1029384756/ads", params={"fields": "id,creative", "limit": 5}).json()["data"]
    for ad in ads:
        assert client.get(ad["creative"]["image_url"]).status_code == 200


@pytest.fixture
def truth_client(world_copy):
    truth = load_truth(world_copy / "sim_truth.duckdb")
    store = make_store(world_copy / "sim_state.duckdb", truth)
    store.commit("activate_scenario", "t:s1", "test", {"key": "S1"})
    with TestClient(create_truth_app(store, "secret-token")) as c:
        yield c
    store.close()


def test_truth_routes_require_the_eval_token(truth_client):
    for path in ("/truth/gt_incidents", "/truth/scenarios", "/truth/campaigns", "/truth/lost_demand"):
        assert truth_client.get(path).status_code == 401
        assert truth_client.get(path, headers={"X-Eval-Token": "wrong"}).status_code == 401
        assert truth_client.get(path, headers={"X-Eval-Token": "secret-token"}).status_code == 200
    gt = truth_client.get("/truth/gt_incidents", headers={"X-Eval-Token": "secret-token"}).json()
    assert gt[0]["scenario"] == "S1" and isinstance(gt[0]["entity_ids"], list)
    assert truth_client.get("/openapi.json").status_code == 404  # no schema advertised


def test_public_listener_never_serves_truth(client):
    for path in ("/truth/gt_incidents", "/truth/scenarios", "/truth/campaigns", "/truth/lost_demand"):
        assert client.get(path).status_code == 404
        assert client.get(path, headers={"X-Eval-Token": "anything"}).status_code == 404


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_eval_mode_starts_a_loopback_listener_with_a_per_run_token(world_copy):
    port = _free_port()
    with TestClient(create_app(world_dir=world_copy, eval_port=port)):
        token = (world_copy / TOKEN_FILE).read_text(encoding="utf-8")
        assert len(token) >= 32
        url = f"http://127.0.0.1:{port}/truth/scenarios"
        for _ in range(50):  # wait for the background server
            try:
                r = httpx.get(url, headers={"X-Eval-Token": token}, timeout=2)
                break
            except httpx.TransportError:
                time.sleep(0.1)
        assert r.status_code == 200
        assert httpx.get(url, timeout=2).status_code == 401
    assert not (world_copy / TOKEN_FILE).exists()  # token removed on shutdown


def test_no_listener_without_eval_mode(client):
    assert client.app.state.truth_listener is None
