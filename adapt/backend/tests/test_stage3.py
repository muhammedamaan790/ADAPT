"""Hand-calculated Stage 3 contracts and refusal paths, no fabricated evaluation evidence."""

import json
from datetime import datetime

import numpy as np
import pytest

from adapt.agent.sql import inspect, validate
from adapt.core.db import Database
from adapt.decide.hashing import content_hash
from adapt.diagnose.causal.did import contrast
from adapt.ingest import uploads
from adapt.learn import qualification as q
from adapt.policy import autonomy
from adapt.policy.engine import current_policy
from adapt.predict import cvr_bayes


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "ws.duckdb")
    yield db
    db.close()


def test_did_exact_and_pretrend_refusal():
    control = 1.0 + 0.03 * np.sin(np.arange(56))
    treated = control + 0.2
    treated[42:] += np.log(0.8)
    result = contrast(treated, control, 42, 7)
    assert result["effect_pct"] == pytest.approx(-0.2)
    assert result["ci"] == pytest.approx([-0.2, -0.2], abs=1e-12)
    assert result["trend_pass"]
    trend = contrast(treated + np.arange(56) * 0.01, control, 42, 7)
    assert not trend["trend_pass"]
    with pytest.raises(ValueError):
        contrast(np.array([1, float("nan")]), np.ones(2), 1, 1)


def test_cvr_exact_empirical_variance_and_posterior():
    rows = [(100, p) for p in (5, 10, 15, 20, 25)]
    a, b = cvr_bayes.prior(rows)
    mu = 0.15
    variance = np.mean((np.arange(5, 26, 5) / 100 - mu) ** 2) - mu * (1 - mu) * 5 / 500
    k = np.clip(mu * (1 - mu) / variance - 1, 2, 1000)
    assert a == pytest.approx(mu * k) and b == pytest.approx((1 - mu) * k)
    post = cvr_bayes.posterior(10, 2, 1, 1)
    assert post["alpha"] == 3 and post["beta"] == 9 and post["mean"] == 0.25
    assert post["p10"] < 0.25 < post["p90"]
    assert cvr_bayes.prior([(50, 0)] * 5) == (1.0, 1.0)
    with pytest.raises(ValueError):
        cvr_bayes.posterior(2, 3)


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM marts.campaign_daily",
        "SELECT 1; DROP TABLE x",
        "SELECT * FROM ops.policy_versions",
        "SELECT * FROM read_csv('/secret')",
        "SELECT * FROM marts.campaign_daily, range(1000000)",
        "SELECT read_blob('/secret') FROM marts.campaign_daily",
        "COPY marts.campaign_daily TO '/tmp/a'",
        "ATTACH 'file' AS a",
        "SELECT * FROM marts.campaign_daily INTO out",
        "WITH RECURSIVE a AS (SELECT 1) SELECT * FROM a",
        "SELECT * FROM other.marts.campaign_daily",
        "SELECT getenv('SECRET') FROM marts.campaign_daily",
    ],
)
def test_sql_refuses_external_access_or_writes(query):
    with pytest.raises(ValueError):
        validate(query)


def test_sql_real_values_snapshot_and_cap(db):
    db.write(
        lambda cur: cur.execute(
            "CREATE SCHEMA marts; CREATE TABLE marts.campaign_daily AS SELECT range AS spend FROM range(600)"
        )
    )
    result = inspect(db, "SELECT sum(spend) AS total FROM marts.campaign_daily")
    assert result["rows"] == [[179700]] and result["columns"] == ["total"]
    result = inspect(db, "SELECT spend FROM marts.campaign_daily ORDER BY spend")
    assert len(result["rows"]) == 500 and result["truncated"]
    assert db.query("SELECT count(*) FROM marts.campaign_daily") == [(600,)]


def test_csv_two_phase_validated_isolated_idempotent(db, tmp_path):
    records = [{"sku": "hero", "on_hand": 50, "reserved": 10, "safety_stock": 5}]
    ack = uploads.stage(db, "inventory", records, "INR", "Asia/Kolkata")
    assert ack["status"] == "STAGED"
    mapping = {k: k for k in uploads.FIELDS["inventory"]}
    done = uploads.confirm(db, ack["import_id"], mapping, tmp_path / "uploads")
    assert done["status"] == "IMPORTED" and done["row_count"] == 1
    assert uploads.confirm(db, ack["import_id"], mapping, tmp_path / "uploads")["row_count"] == 1
    imported = Database(tmp_path / "uploads" / f"{ack['import_id']}.duckdb")
    assert json.loads(imported.query("SELECT payload FROM uploads.records")[0][0])["on_hand"] == 50
    imported.close()
    bad = uploads.stage(db, "inventory", [records[0] | {"reserved": 90}], "INR", "Asia/Kolkata")
    with pytest.raises(ValueError):
        uploads.confirm(db, bad["import_id"], mapping, tmp_path / "uploads")
    assert db.query("SELECT status FROM ops.csv_imports WHERE import_id=?", [bad["import_id"]]) == [("STAGED",)]


def rows(n=30):
    return [
        {
            "world_id": w,
            "channel": "meta",
            "decision_id": f"d{w}-{i}",
            "confidence": 0.85,
            "verdict": "SUCCESS" if i < n * 0.9 else "FAILED",
            "realized": 100.0 if i < n * 0.9 else -100.0,
            "prob_loss": 0.1,
            "violations": 0,
            "class": "OPTIMIZATION",
            "execution_mode": "MOCK",
            "verified": True,
            "measured_at": "2026-10-01T12:00:00",
        }
        for w in (901, 902, 903, 904)
        for i in range(n)
    ]


def test_qualification_wilson_balance_reliability_and_refusals():
    lo, hi = q.wilson(7, 8)
    assert lo == pytest.approx(0.5291, abs=0.001) and hi > 0.95
    assert q.wilson(0, 0) == (None, None)
    assert q.qualify(rows())[2]["qualified"]
    small = [r for r in rows() if r["world_id"] != 904 or int(r["decision_id"].split("-")[1]) < 5]
    assert q.qualify(small)[2]["reliability"] == "INCONCLUSIVE"
    assert not q.qualify(small)[2]["qualified"]
    assert not q.qualify([r for r in rows() if r["world_id"] != 903])[2]["qualified"]
    losses = [r | {"realized": -100} for r in rows()]
    assert q.qualify(losses)[2]["risk_suspended"]


def test_qualification_artifact_hash_bound_and_mode_revision(db):
    payload = {"model_version": q.VERSION, "rows": rows()}
    artifact = {"payload": payload, "sha256": content_hash(payload)}
    q.import_artifact(db, artifact)
    q.import_artifact(db, artifact)
    assert len(q.records(db)) == 120
    db.write(lambda cur: cur.execute("CREATE TABLE ops.data_health(source VARCHAR, status VARCHAR, as_of TIMESTAMP); "
                                    "INSERT INTO ops.data_health VALUES ('store','GREEN', now()), ('erp','GREEN',now()), "
                                    "('finance','GREEN',now()), ('meta_ads','GREEN',now())"))
    pol = current_policy(db)
    out = autonomy.set_mode(
        db,
        "Meta",
        "SIMULATION_AUTONOMOUS",
        pol["policy_version"],
        "admin",
        "Reviewed independent simulated qualification",
        datetime(2026, 10, 8),
    )
    assert out["policy_version"] != pol["policy_version"]
    assert out["channels"][1]["mode"] == "SIMULATION_AUTONOMOUS"
    assert all(not c["production"]["eligible"] for c in out["channels"])
    with pytest.raises(ValueError):
        autonomy.set_mode(
            db,
            "Google",
            "PRODUCTION_AUTONOMOUS",
            out["revision"],
            "admin",
            "Never inherit mocks",
            datetime(2026, 10, 8),
        )
    with pytest.raises(ValueError):
        q.import_artifact(db, artifact | {"sha256": "0" * 64})
    assert q.bands(db)["pools"][1]["scope"] == "HELD_OUT"
