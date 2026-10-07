"""Stage 2 ★ Google Ads v25 LIVE adapter + hybrid mirror (spec §9.4), against a fake Google Ads REST API (OAuth token
endpoint, GAQL search, campaignBudgets:mutate; recorded-fixture style) with the world service as the simulated data
plane. T23 unsupported version -> health fails, legs BLOCKED, no mock fallback | T30 live failure -> never VERIFIED,
never mirrored | T41 verified live change + mirror failure -> MIRROR_PENDING, Google change NOT compensated, world
advance blocked (409), retry -> MIRRORED; > 1 h -> MIRROR_FAILED -> manual resolution after a fresh read-back;
saga SUCCEEDED and decision EXECUTED throughout | timeout-after-success on live -> read-after-write, one mutation |
anti-hollow: verification is a separate GAQL read | test-account and INR safety checks | redaction."""

import json
import re
from datetime import timedelta

import httpx
import pytest
from test_execution_saga import G2, M0, NOSLEEP, approved, copy, leg, mutations, state, world_amount
from test_execution_saga import db as db  # noqa: F401  (fixtures shared with the C5 saga tests)
from test_execution_saga import world as world  # noqa: F401
from tests.test_optimizer import T0

from adapt.decide import decisions as dec
from adapt.execute import mirror as sim_mirror
from adapt.execute import saga
from adapt.execute.adapters import MockMetaAdapter, build_adapters
from adapt.execute.google_ads_live import GoogleAdsCredentials, GoogleAdsLiveAdapter, world_mirror

LIVE_ID = "9000001"
CREDS = GoogleAdsCredentials("cid.apps.googleusercontent.com", "secret", "good-refresh", "1112223333", "4445556666",
                             "dev-token-xyz")


class FakeGoogle:
    """A recorded-fixture Google Ads API: one INR test account with one budget."""

    def __init__(self, test_account=True, currency="INR", version="v25", amount=40000.0):
        self.budgets = {LIVE_ID: int(amount * 1_000_000)}
        self.test_account, self.currency, self.version = test_account, currency, version
        self.calls: list[tuple[str, str]] = []
        self.headers: list[dict] = []
        self.fault: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.headers.append(dict(request.headers))
        if request.url.host == "oauth2.googleapis.com":
            form = dict(x.split("=") for x in request.content.decode().split("&"))
            self.calls.append(("token", form.get("refresh_token", "")))
            if form.get("refresh_token") != "good-refresh":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "ya29.secret-access", "expires_in": 3600})
        if not path.startswith(f"/{self.version}/"):
            return httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND"}})
        body = json.loads(request.content)
        if path.endswith("googleAds:search"):
            q = body["query"]
            self.calls.append(("search", q))
            if "FROM customer" in q:
                return httpx.Response(200, json={"results": [{"customer": {
                    "id": "4445556666", "testAccount": self.test_account, "currencyCode": self.currency}}]})
            bid = re.search(r"campaign_budget.id = (\d+)", q).group(1)
            if bid not in self.budgets:
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"results": [{"campaignBudget": {
                "id": bid, "amountMicros": str(self.budgets[bid]), "status": "ENABLED"}}]})
        if path.endswith("campaignBudgets:mutate"):
            op = body["operations"][0]["update"]
            self.calls.append(("mutate", op["amountMicros"]))
            if self.fault == "unavailable":
                self.fault = None
                return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE"}})
            self.budgets[op["resourceName"].rsplit("/", 1)[1]] = int(op["amountMicros"])
            if self.fault == "timeout_after_success":
                self.fault = None
                return httpx.Response(504, json={"error": {"code": 504, "status": "DEADLINE_EXCEEDED"}})
            return httpx.Response(200, json={"results": [{"resourceName": op["resourceName"]}]})
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    def live_amount(self) -> float:
        return self.budgets[LIVE_ID] / 1_000_000

    def mutates(self) -> int:
        return sum(1 for c in self.calls if c[0] == "mutate")


def live_adapter(fake: FakeGoogle, world_client=None, creds=CREDS, version="v25", mirror="world"):
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    m = world_mirror(world_client) if mirror == "world" else mirror
    return GoogleAdsLiveAdapter(creds, {G2: LIVE_ID}, version, http=http, mirror=m)


def adapters_live(world_client, fake, **kw):
    return {"google": live_adapter(fake, world_client, **kw), "meta": MockMetaAdapter(world_client)}


def test_health_checks():
    assert live_adapter(FakeGoogle()).health()["ok"]
    h = live_adapter(FakeGoogle(test_account=False)).health()
    assert not h["ok"] and "TEST account" in h["reason"]
    assert "currency" in live_adapter(FakeGoogle(currency="USD")).health()["reason"]
    h = live_adapter(FakeGoogle(), version="v19").health()                                    # T23
    assert not h["ok"] and not h["checks"]["api_version"]
    bad = GoogleAdsCredentials("x", "y", "revoked", "1", "2")
    assert not live_adapter(FakeGoogle(), creds=bad).health()["checks"]["auth"]
    assert "GOOGLE_ADS_REFRESH_TOKEN" in live_adapter(FakeGoogle(), creds=GoogleAdsCredentials(
        "x", "y", None, "1", "2")).health()["reason"]


def test_hybrid_happy_path_verified_live_then_mirrored(world, db):
    fake = FakeGoogle()
    st = state()
    did = approved(db, st, [leg(G2, 44000.0), leg(M0, 26000.0)])
    out = saga.execute_decision(db, did, adapters_live(world, fake), "maria", T0, copy(st), NOSLEEP)
    assert out["state"] == "SUCCEEDED"
    g = next(x for x in out["legs"] if x["budget_id"] == G2)
    assert g["mode"] == "LIVE" and g["state"] == "VERIFIED"
    assert fake.live_amount() == pytest.approx(44000.0) and fake.mutates() == 1
    assert world_amount(world, G2) == pytest.approx(44000.0)                                   # mirrored into the sim
    assert db.query("SELECT sim_sync_state FROM exec.saga_legs WHERE budget_id = ?", [G2])[0][0] == "MIRRORED"
    assert db.query("SELECT sim_sync_state FROM exec.saga_legs WHERE budget_id = ?", [M0])[0][0] == "NOT_REQUIRED"
    searches_after = [c for c in fake.calls[fake.calls.index(("mutate", "44000000000")):] if c[0] == "search"]
    assert searches_after, "verification must be a separate GAQL read, never the request echoed back"


def test_t41_mirror_failure_never_touches_the_verified_google_change(world, db):
    fake = FakeGoogle()
    broken = {"down": True}
    real = world_mirror(world)

    def flaky(budget_id, amount, request_id):
        if broken["down"]:
            raise RuntimeError("world service unreachable")
        real(budget_id, amount, request_id)

    st = state()
    did = approved(db, st, [leg(G2, 36000.0)])
    out = saga.execute_decision(db, did, adapters_live(world, fake, mirror=flaky), "maria", T0, copy(st), NOSLEEP)
    assert out["state"] == "SUCCEEDED" and dec.get_decision(db, did)["status"] == "EXECUTED"
    assert db.query("SELECT external_state, sim_sync_state FROM exec.saga_legs WHERE budget_id = ?",
                    [G2])[0] == ("VERIFIED", "MIRROR_PENDING")
    assert fake.live_amount() == pytest.approx(36000.0) and fake.mutates() == 1               # not compensated
    assert world_amount(world, G2) == pytest.approx(40000.0)                                    # sim still behind
    with pytest.raises(sim_mirror.SimOutOfSync):
        sim_mirror.advance_world(db, world, 1, "adv-1")
    assert sim_mirror.retry_pending(db, flaky, T0 + timedelta(minutes=10))[0]["state"] == "MIRROR_PENDING"
    broken["down"] = False
    assert sim_mirror.retry_pending(db, flaky, T0 + timedelta(minutes=20))[0]["state"] == "MIRRORED"
    assert world_amount(world, G2) == pytest.approx(36000.0)
    assert sim_mirror.divergent(db) == []
    with pytest.raises(RuntimeError, match="world not seeded"):  # the guard lets it through; this bare world has no
        sim_mirror.advance_world(db, world, 1, "adv-2")          # clock (seeded worlds advance normally)
    assert saga.saga_summary(db, db.query("SELECT saga_id FROM exec.sagas")[0][0])["state"] == "SUCCEEDED"


def test_t41_mirror_failed_after_an_hour_then_resolved_manually(world, db):
    fake = FakeGoogle()

    def down(*_):
        raise RuntimeError("world service unreachable")

    st = state()
    did = approved(db, st, [leg(G2, 36000.0)])
    ad = adapters_live(world, fake, mirror=down)
    saga.execute_decision(db, did, ad, "maria", T0, copy(st), NOSLEEP)
    assert sim_mirror.retry_pending(db, down, T0 + timedelta(minutes=61))[0]["state"] == "MIRROR_FAILED"
    lid = db.query("SELECT leg_id FROM exec.saga_legs WHERE budget_id = ?", [G2])[0][0]
    later = T0 + timedelta(minutes=70)
    with pytest.raises(dec.DecisionError):
        sim_mirror.resolve_manually(db, lid, ad["google"], world_mirror(world), "maria", "viewer", later)
    fake.budgets[LIVE_ID] = 37_000_000_000  # someone changed Google since: the read-back must confirm first
    with pytest.raises(dec.DecisionError):
        sim_mirror.resolve_manually(db, lid, ad["google"], world_mirror(world), "maria", "manager", later)
    fake.budgets[LIVE_ID] = 36_000_000_000
    out = sim_mirror.resolve_manually(db, lid, ad["google"], world_mirror(world), "maria", "manager", later)
    assert out["state"] == "MIRROR_RESOLVED_MANUALLY" and world_amount(world, G2) == pytest.approx(36000.0)
    assert sim_mirror.divergent(db) == [] and dec.get_decision(db, did)["status"] == "EXECUTED"
    states = [r[0] for r in db.query("SELECT to_state FROM exec.saga_transitions WHERE to_state LIKE 'MIRROR:%' "
                                     "ORDER BY at_ts, to_state")]
    assert states[-1] == "MIRROR:MIRROR_RESOLVED_MANUALLY"


@pytest.mark.parametrize("why", ["auth", "version", "not_test", "unmapped"])
def test_t23_t30_live_failures_block_and_never_fall_back(world, db, why):
    fake = FakeGoogle(test_account=why != "not_test")
    kw = {"creds": GoogleAdsCredentials("x", "y", "revoked", "1", "2")} if why == "auth" else {}
    if why == "version":
        kw["version"] = "v19"
    ad = adapters_live(world, fake, **kw)
    if why == "unmapped":
        ad["google"].mapping = {}
    st = state()
    did = approved(db, st, [leg(G2, 36000.0)])
    out = saga.execute_decision(db, did, ad, "maria", T0, copy(st), NOSLEEP)
    assert out["state"] == "BLOCKED" and out["legs"][0]["state"] == "PLANNED"
    assert "no mock fallback" in out["legs"][0]["error"]
    assert fake.mutates() == 0 and len(mutations(world, G2)) == 0                            # nothing sent anywhere
    assert db.query("SELECT count(*) FROM exec.saga_legs WHERE external_state = 'VERIFIED'")[0][0] == 0
    assert sim_mirror.divergent(db) == []                                                      # never mirrored


def test_live_timeout_after_success_is_verified_by_read_after_write(world, db):
    fake = FakeGoogle()
    fake.fault = "timeout_after_success"
    st = state()
    did = approved(db, st, [leg(G2, 36000.0)])
    out = saga.execute_decision(db, did, adapters_live(world, fake), "maria", T0, copy(st), NOSLEEP)
    assert out["state"] == "SUCCEEDED" and fake.mutates() == 1 and world_amount(world, G2) == pytest.approx(36000.0)


def test_ledger_is_redacted_and_no_token_is_persisted(world, db):
    fake = FakeGoogle()
    st = state()
    did = approved(db, st, [leg(G2, 36000.0)])
    saga.execute_decision(db, did, adapters_live(world, fake), "maria", T0, copy(st), NOSLEEP)
    assert any(h.get("developer-token") == "dev-token-xyz" for h in fake.headers)            # sent when configured
    blob = json.dumps(db.query("SELECT request, response, error FROM exec.saga_legs"))
    for secret in ("ya29.secret-access", "good-refresh", "dev-token-xyz", "secret"):
        assert secret not in blob


def test_build_adapters_live_mode_reads_the_mapping(world, db):
    from types import SimpleNamespace

    from adapt.execute.google_ads_live import MAP_DDL

    db.write(lambda cur: (cur.execute(MAP_DDL),
                          cur.execute("INSERT INTO ops.live_entity_map VALUES ('google', 'budget', ?, ?, '1', NULL)",
                                      [G2, LIVE_ID])))
    env = {"GOOGLE_ADS_CLIENT_ID": "a", "GOOGLE_ADS_CLIENT_SECRET": "b", "GOOGLE_ADS_REFRESH_TOKEN": "c",
           "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "111-222-3333", "GOOGLE_ADS_CUSTOMER_ID": "444-555-6666"}
    ad = build_adapters(world, settings=SimpleNamespace(google_execution_mode="live"), db=db, env=env)
    g = ad["google"]
    assert isinstance(g, GoogleAdsLiveAdapter) and g.mode == "LIVE" and g.mapping == {G2: LIVE_ID}
    assert g.creds.login_customer_id == "1112223333" and g.mirror is not None
    assert build_adapters(world, settings=SimpleNamespace(google_execution_mode="mock"), db=db)["google"].mode == "MOCK"
