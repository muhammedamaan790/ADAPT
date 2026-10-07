"""The shared process pool (adapt.core.parallel) gives exactly the serial results: detection over every entity and
the optimizer with its sensitivity scenarios, on the S1 fixture world. Exact equality, not a tolerance: decision
hashes depend on every float."""

from datetime import date, timedelta

import pytest

from adapt.core import parallel
from adapt.decide.run import run_optimizer
from adapt.detect.detector import run_detection
from adapt.ingest.sync import run_sync
from adapt.predict.fit_curves import fit_curves
from adapt.reconcile.build import build_canonical, logical_now

DAYS = 4


@pytest.fixture
def s1_world(world, http, db):
    run_sync(db, http)
    world.post("/control/scenario", json={"key": "S1"}, headers={"X-Request-ID": "par-s1"})
    for i in range(DAYS):
        world.post("/control/advance", json={"days": 1}, headers={"X-Request-ID": f"par-adv-{i}"})
        run_sync(db, http)
    as_of = logical_now(date(2026, 10, 1) + timedelta(days=DAYS))
    build_canonical(db, as_of)
    return db, as_of


def _with_workers(monkeypatch, n: int):
    parallel.shutdown()
    monkeypatch.setenv("ADAPT_WORKERS", str(n))


def test_pooled_detection_equals_serial(s1_world, monkeypatch):
    db, as_of = s1_world
    cols = "entity_type, entity_id, metric, window_days, status, direction, actual, expected, signed_impact, " \
           "material, stat_fired, max_abs_z, shift_fired, shift_score, collapse, flagged, daily_z"
    _with_workers(monkeypatch, 0)
    run_detection(db, as_of, "serial")
    _with_workers(monkeypatch, 2)
    try:
        run_detection(db, as_of, "pooled")
    finally:
        parallel.shutdown()
    q = f"SELECT {cols} FROM intel.metric_flags WHERE run_id = ? ORDER BY entity_type, entity_id, metric, window_days"
    serial, pooled = db.query(q, ["serial"]), db.query(q, ["pooled"])
    assert serial and serial == pooled


def test_pooled_optimizer_equals_serial(s1_world, monkeypatch):
    db, as_of = s1_world
    fit_curves(db, as_of)
    _with_workers(monkeypatch, 0)
    a = run_optimizer(db, as_of, persist=False)["result"]
    _with_workers(monkeypatch, 2)
    try:
        b = run_optimizer(db, as_of, persist=False)["result"]
    finally:
        parallel.shutdown()
    assert a["status"] == b["status"] == "OK" and len(a["alternatives"]) == 2
    for key in ("allocation", "expected", "legs", "alternatives", "why_not", "marginal_ladder"):
        assert a.get(key) == b.get(key), key
