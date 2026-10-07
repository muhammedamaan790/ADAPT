"""A5 'done when': mock Google/Meta mutation + read-back contracts, including injected faults (spec §9.3/§9.4)."""

import pytest
from fastapi.testclient import TestClient

from world.main import create_app

CID = "1234567890"
G_RES = f"customers/{CID}/campaignBudgets/555"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "sim_state.duckdb")) as c:
        c.post("/control/reset", json={"seed": 42}, headers={"X-Request-ID": "r0"})
        c.post("/control/budget", json={"platform": "google", "budget_id": "555", "amount": 5000},
               headers={"X-Request-ID": "b1"})
        c.post("/control/budget", json={"platform": "meta", "budget_id": "238000001", "amount": 3000},
               headers={"X-Request-ID": "b2"})
        yield c


def g_mutate(client, micros, rid="m1", resource=G_RES):
    body = {"operations": [{"update": {"resourceName": resource, "amountMicros": str(micros)},
                            "updateMask": "amount_micros"}]}
    return client.post(f"/google/v25/customers/{CID}/campaignBudgets:mutate", json=body, headers={"X-Request-ID": rid})


def g_read(client):
    q = "SELECT campaign_budget.id, campaign_budget.amount_micros FROM campaign_budget WHERE campaign_budget.id = 555"
    r = client.post(f"/google/v25/customers/{CID}/googleAds:search", json={"query": q})
    assert r.status_code == 200
    return r.json()


def fault(client, platform, name, rid, count=1):
    r = client.post("/control/fault", json={"platform": platform, "fault": name, "count": count},
                    headers={"X-Request-ID": rid})
    assert r.status_code == 200


# ---- Google -----------------------------------------------------------------------------------------
def test_google_mutate_then_gaql_read_back(client):
    assert g_read(client)["results"] == [{"campaignBudget": {"id": "555", "amountMicros": "5000000000"}}]
    r = g_mutate(client, 6_000_000_000)
    assert r.status_code == 200 and r.json() == {"results": [{"resourceName": G_RES}]}
    out = g_read(client)
    assert out["results"][0]["campaignBudget"]["amountMicros"] == "6000000000"
    assert out["fieldMask"] == "campaignBudget.id,campaignBudget.amountMicros"


def test_google_rejects_non_multiple_micros_and_foreign_resources(client):
    r = g_mutate(client, 6_000_000_001)
    assert r.status_code == 400
    assert r.json()["error"]["details"][0]["errors"][0]["errorCode"]["mutateError"] == (
        "NON_MULTIPLE_OF_MINIMUM_CURRENCY_UNIT"
    )
    assert g_mutate(client, 6_000_000_000, resource="customers/999/campaignBudgets/555").status_code == 400
    unknown = g_mutate(client, 6_000_000_000, resource=f"customers/{CID}/campaignBudgets/777")
    assert unknown.status_code == 400
    assert g_read(client)["results"][0]["campaignBudget"]["amountMicros"] == "5000000000"


def test_google_unsupported_gaql_is_a_400(client):
    r = client.post(f"/google/v25/customers/{CID}/googleAds:search",
                    json={"query": "SELECT campaign.status FROM campaign"})
    assert r.status_code == 400


@pytest.mark.parametrize("name,http", [("rate_limit", 429), ("unavailable", 503), ("bad_request", 400)])
def test_google_error_faults_do_not_apply_the_mutation(client, name, http):
    fault(client, "google", name, "f1")
    r = g_mutate(client, 7_000_000_000)
    assert r.status_code == http
    assert g_read(client)["results"][0]["campaignBudget"]["amountMicros"] == "5000000000"
    # the fault was consumed: the next attempt succeeds
    assert g_mutate(client, 7_000_000_000, rid="m2").status_code == 200


def test_google_timeout_after_success_applies_and_read_back_shows_it(client):
    """T17 world side: the response is lost but the change went through, so read-after-write finds it."""
    fault(client, "google", "timeout_after_success", "f1")
    r = g_mutate(client, 8_000_000_000)
    assert r.status_code == 504
    assert g_read(client)["results"][0]["campaignBudget"]["amountMicros"] == "8000000000"


def test_google_same_request_id_never_mutates_twice(client):
    g_mutate(client, 6_000_000_000, rid="same")
    client.post("/control/budget", json={"platform": "google", "budget_id": "555", "amount": 1000},
                headers={"X-Request-ID": "human-edit"})
    g_mutate(client, 6_000_000_000, rid="same")  # replayed from the log, not re-applied
    assert g_read(client)["results"][0]["campaignBudget"]["amountMicros"] == "1000000000"


# ---- Meta -------------------------------------------------------------------------------------------
def test_meta_update_and_read_back_in_minor_units(client):
    r = client.get("/meta/v25.0/238000001", params={"fields": "daily_budget,status"})
    assert r.json() == {"daily_budget": "300000", "status": "ACTIVE"}
    r = client.post("/meta/v25.0/238000001", json={"daily_budget": "250000", "status": "PAUSED"},
                    headers={"X-Request-ID": "mm1"})
    assert r.status_code == 200 and r.json() == {"success": True}
    r = client.get("/meta/v25.0/238000001", params={"fields": "id,daily_budget,status"})
    assert r.json() == {"id": "238000001", "daily_budget": "250000", "status": "PAUSED"}


def test_meta_errors(client):
    assert client.post("/meta/v25.0/238000001", json={"status": "DELETED"}).status_code == 400
    assert client.post("/meta/v25.0/238000001", json={}).status_code == 400
    assert client.post("/meta/v25.0/404404", json={"daily_budget": "100"}).status_code == 400
    assert client.get("/meta/v25.0/238000001", params={"fields": "bid_amount"}).status_code == 400


def test_meta_rate_limit_is_graph_code_17(client):
    fault(client, "meta", "rate_limit", "f1")
    r = client.post("/meta/v25.0/238000001", json={"daily_budget": "100000"}, headers={"X-Request-ID": "x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == 17 and r.json()["error"]["is_transient"]
    assert client.get("/meta/v25.0/238000001", params={"fields": "daily_budget"}).json()["daily_budget"] == "300000"


def test_faults_are_per_platform_and_counted(client):
    fault(client, "meta", "unavailable", "f1", count=2)
    assert g_mutate(client, 6_000_000_000).status_code == 200  # google unaffected
    for rid in ("a", "b"):
        assert client.post("/meta/v25.0/238000001", json={"daily_budget": "1"},
                           headers={"X-Request-ID": rid}).status_code == 503
    assert client.post("/meta/v25.0/238000001", json={"daily_budget": "1"},
                       headers={"X-Request-ID": "c"}).status_code == 200


def test_fault_sequence_replays_to_the_same_state(client, tmp_path):
    from world.state import WorldStore, replay

    fault(client, "google", "timeout_after_success", "f1")
    g_mutate(client, 9_000_000_000, rid="t1")
    fault(client, "meta", "rate_limit", "f2")
    client.post("/meta/v25.0/238000001", json={"daily_budget": "1"}, headers={"X-Request-ID": "t2"})
    store = client.app.state.store
    fresh = WorldStore(tmp_path / "replay.duckdb")
    replay(store.log(), fresh)
    assert fresh.semantic_state_hash() == store.semantic_state_hash()
    fresh.close()
