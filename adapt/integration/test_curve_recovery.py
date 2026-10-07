"""B5 integration (fixture world, through world -> ingest -> reconcile -> fit): artifacts and failure states.

The fixture world's synthetic backbone makes saturation genuinely unidentifiable over the observed spend range
(bootstrap CV(K) > 0.5 almost everywhere), so here the contract under test is T40: unstable or rejected fits never
carry their own curve, a channel with no usable pooled curve leaves its units MODEL_UNAVAILABLE (which no prediction
may consume), and every usable artifact predicts finite revenue from persisted state.
Recovery against truth (median marginal-ROAS error <= 30%) is measured on real-backbone seeds by
evalharness.curve_recovery (docs/contracts/response_curves.md)."""

from datetime import date

import numpy as np
import pytest

from adapt.ingest.http import SourceHttp
from adapt.ingest.sync import run_sync
from adapt.predict.fit_curves import fit_curves, load_curves
from adapt.reconcile.build import build_canonical, logical_now
from evalharness.curve_recovery import recovery
from world.truth import load_truth

START = date(2026, 10, 1)  # fixture world day 0


def test_curve_artifacts_and_failure_states(world_large, seeded_world_large, db):
    http = SourceHttp("http://testserver", client=world_large, sleep=lambda s: None)
    run_sync(db, http)
    as_of = logical_now(START)
    build_canonical(db, as_of)
    out = fit_curves(db, as_of)
    curves = load_curves(db)

    n_units = db.query("SELECT count(DISTINCT budget_id) FROM core.campaigns")[0][0]
    assert out["units"] == len(curves) == n_units
    assert 0.0 <= out["family_coverage_P"] <= 1.0
    for a in curves.values():
        reason = a.diagnostics.get("reason") or ""
        if a.status == "MODEL_UNAVAILABLE":
            assert a.params is None and a.draws is None and a.pooled_params is None
            assert "pooled channel curve unavailable" in reason
            with pytest.raises(ValueError):
                a.revenue(np.array([a.median_spend]))
            continue
        if reason.startswith(("UNSTABLE", "REJECTED", "INSUFFICIENT_DATA", "FIT_FAILED")):
            assert a.status == "POOLED" and a.w == 0.0 and a.params is None
        if a.status == "OK":
            assert a.draws.shape == (200, 5) and 0 < a.w <= 1
        assert all(np.isfinite([a.marginal_roas(f * a.median_spend) for f in (0.8, 1.0, 1.2)]))
        assert np.isfinite(a.revenue(np.full(7, a.median_spend))).all()

    rec = recovery(db, load_truth(seeded_world_large / "sim_truth.duckdb"))  # runs end to end on any world
    assert rec["units"] == n_units and rec["usable"] == out["status"]["OK"] + out["status"]["POOLED"]
