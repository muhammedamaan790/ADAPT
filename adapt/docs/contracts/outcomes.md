# Contract: outcome measurement and impact calibration (B8)

Modules: `backend/adapt/learn/{outcomes,calibration}.py`. The measurement basis is frozen at decision time by
`decide/run.py`. Entry points:
- `measure_outcome(db, decision_id, as_of)`: PENDING until matured; once matured it is stored and idempotent
- `current_factor(db)`
- `apply_outcome(...)`: applied exactly once per outcome id

## INPUT
**Frozen at decision time** (`learn.measurement_basis`), for every treated unit:
- no-action revenue paths for all 200 joint draws at the pre-action budget over 14 days
- the contribution margin per rupee of attributed net revenue
- pacing
- the pre-action budget
- the out-of-sample relative residual pool of the model that produced the counterfactual
- the predicted daily ΔCAA path, and the total budget B

**At measurement**: realized attributed net revenue and spend of the treated campaigns (`marts.campaign_daily`),
and unique orders (`core.order_items`), all as of the measurement time.

## ALGORITHM (spec §10)
**Effect**: observed CAA − counterfactual CAA_k for each draw k, where:
- counterfactual_k = Σ_units Σ_days revenue_k × (1 + residual path_k) × margin − pre-action spend
- residual paths are moving-block resampled (7-day blocks; the same indices for every unit, seeded by the decision
  id)
- 90% CI = [P5, P95]; realized = the mean effect
- method label: `MODEL_ESTIMATE (forecast counterfactual)`

**Verdicts**, with m = max(₹2,000, 5% of the treated units' expected CBA over the window):

| Verdict | Rule |
|---|---|
| SUCCESS | lower bound > +m |
| FAILED | upper bound < −m |
| NEUTRAL | the CI lies inside [−m, +m] |
| INCONCLUSIVE | anything else, or the minimum sample was not reached by day 14 |

**Maturity**:
- OPTIMIZATION: ≥ 100 unique orders on the treated campaigns and ≥ 3 days, or 14 days at most
- SAFETY: the end of its 3-day window; the effect is the avoided loss, and it never calibrates the curves

**Calibration** (family BUDGET_REALLOCATION, the decision as a whole):
- `calibrated_pred = factor × raw_pred` if raw_pred > 0
- Updated only for SUCCESS / NEUTRAL / FAILED with raw_pred ≥ max(₹2,000, 1% of B):
  - ρ = clip(realized / raw_pred, 0, 1.2)
  - factor ← clip(0.8 · factor + 0.2 · ρ, 0.3, 1.0); the initial factor is 0.9
- raw_pred is the prediction for the **same days** as the measured window, and dividing by the raw value (never the
  calibrated one) avoids double discounting.
- It is idempotent through the `learn.calibration_log` primary key.
- INCONCLUSIVE, immaterial and raw_pred ≤ 0 (cuts) never update the factor.

**Stage 1 deviation (stated)**: the residual pool is the curve candidate's out-of-sample errors on its P + D windows
(42 days), or a proportional-ROAS model for units without a curve. The spec's 56 rolling-origin days need the
pipeline's stored daily forecasts (C2).

## FAILURE STATES
- PENDING: not yet matured
- INCONCLUSIVE: the reason is stored
- a missing measurement basis raises

## Measured (seed 42)
The PROFIT recommendation (40 legs, about 20% cuts) was applied in the world, which stands in for C5's saga, then the
world was advanced and ingested daily. The outcome matured on day 3 with 2,284 orders. Both runs below are verdict
INCONCLUSIVE, so the factor stays at 0.9 and the replay is idempotent.

| Run | Realized over 3 days | 90% CI | Predicted for the same days |
|---|---|---|---|
| First run (old world) | −₹1.6 lakh | −₹8.7 lakh to +₹6.1 lakh | +₹9.3 lakh |
| Rerun on the reseeded world | +₹2.7 lakh | −₹4.4 lakh to +₹10.4 lakh | +₹9.3 lakh |

The first run's measurement exposed a world defect. Demand dropped about 18% at day 0 because the world carried the
last-28-day mean trend forward instead of the recent level: unpaid orders fell from 14,214 to 11,656 per 3 days. The
world now carries forward the last-14-day trend, with a regression test for no cliff at day 0. This is what the
outcome loop is for: it measured revenue falling 24% on a 21% spend cut, more than the model's elasticity explains,
and declined to calibrate on an inconclusive result.

## TESTS
- Unit (`backend/tests/test_outcomes.py`):
  - verdict boundaries (T45), including strict inequalities at ±m
  - effect = observed − counterfactual exactly
  - a reproducible residual-widened CI
  - calibration arithmetic: realized loss → ρ = 0, ρ cap, factor floor
  - **a negative outcome lowers the factor exactly once** (idempotent)
  - INCONCLUSIVE, cuts and immaterial predictions are excluded
- Integration (`integration/test_decide_learn.py`), fixture world:
  - S3 safety candidate applied → PENDING before the window ends → MATURED after 3 days
  - the CI contains the realized value
  - SAFETY never calibrates
  - the replay is idempotent, with one stored outcome

## UI EVIDENCE
Outcomes / Decision Center: prediction vs reality (calibrated_pred frozen at decision time, realized with the 90%
CI), all four verdict counts, and the optimism correction factor with its update history.
