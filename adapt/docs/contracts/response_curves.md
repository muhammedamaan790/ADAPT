# Contract: response curves (B5)

Modules: `backend/adapt/predict/{curves,fit_curves}.py`; parameters `config/models.yaml` (`response_curve`; any
change is a model-version change). Entry points: `fit_curves(db, fit_ts)` (fits, persists, registers the champion)
and `load_curves(db, fit_ts=None)` → `{unit_id: CurveArtifact}`, which B6 consumes.

## INPUT
Per **budget unit** (a campaign, or one shared Google budget): daily spend and last-click attributed net revenue from
`marts.campaign_daily` (built as of fit_ts, so every row has `available_at ≤ fit_ts`; immature refunds use the
conditional expected refund, see reconcile.md). Baseline index = STL trend (period 7, robust) of the product set's
unpaid (email + organic) orders; our sources have no unpaid sessions by category.

## ALGORITHM (spec §7.2, §22.2)
Normalised units: x = spend / median spend, y = revenue / median revenue, b = baseline / its mean.
- a_t = x_t + θ·a_{t−1}; y_t = β·Hill(a_t; K, S) + γ·b_t, Hill(a) = a^S / (K^S + a^S)
- `scipy.optimize.least_squares` (trf, bounds, analytic Jacobian, x_scale = jac)
- bounds: β ∈ [1e-6, 100], K ∈ [0.2, 5] (× median spend), S ∈ [0.5, 3], θ ∈ [0, 0.7], γ ∈ [0, 100]
- adstock initialisation: fitting uses a₀ = mean x of the first 7 days; forecasting uses the artifact's terminal
  adstock state at fit_ts

**Chronology** (last complete day L; no row in two roles): D = the 14 days ending at L, P = the 28 days before D,
Train = the 120 days before P. The candidate is fitted on Train and scored on P and D.

**Acceptance** (Stage 1, criteria (a) + (c) of §10.2):
- (a) per unit: the fit succeeded and the holdout skill R²_P ≥ 0
- (c) per model family: P10–P90 predictive coverage in [70%, 90%], pooled over every candidate's P days

Accepted units are refitted on the 120 days ending at L, with **200 joint moving-block bootstrap** draws (7-day
blocks over residuals; draw d uses the same block indices for every unit, so correlated shocks stay correlated
across the portfolio).

**Stability** (on every deployed fit): the fit is unstable when any of these holds:
- Jacobian condition number > 1e6
- bootstrap CV(K) > 0.5
- |corr(Hill(adstock(x)), b)| > 0.9

**Fallbacks**:
- spend floor (median ≥ ₹500, ≥ 30 spend days), a failed or rejected fit, or an unstable fit → the unit uses the
  **pooled channel curve** with w = 0 (status POOLED)
- the pooled curve is fitted on the channel's units stacked in normalised units, and gets its own bootstrap and
  stability check
- fewer than 3 members, or a pooled fit that fails, is unstable or has R²_P < 0 → **MODEL_UNAVAILABLE**:
  `revenue()` raises, and no scale-up may consume the unit (decreases and safety candidates stay allowed)

**Shrinkage**: ŷ = w·ŷ_unit + (1 − w)·ŷ_pooled, with w = n_eff / (n_eff + 30) and n_eff = the number of spend days in
the 120-day window. An accepted, stable unit whose channel has no pooled curve stands alone (w = 1).

**Prediction** (`CurveArtifact`):
- `revenue(spend_path, draw)`: ₹ per day, with adstock continued from fit_ts and the baseline carried forward at its
  last-14-day mean
- `marginal_roas(spend, draw)`: the steady-state dR/ds, labelled "model-estimated", never "true incremental"

## Spec corrections (v2.4.5, measured on seed 42)
1. **Holdout R² is skill against the naive forecast available at fit time** (the mean of the last 28 training days),
   not against the holdout's own mean. A forecast cannot know the holdout mean. With that benchmark, even a correct
   curve scores below 0 whenever spend barely moves and daily noise dominates: 45 of 47 units were rejected.
2. **Coverage is a family-level criterion**, as §7.3 and §10.2 say ("family coverage"). The previous per-unit check
   used 28 days, where a correctly calibrated 80% interval lands outside [70%, 90%] about 1 time in 5. The
   predictive interval uses relative residuals, because revenue noise scales with its level and budgets grow.
3. **World: the history manager tweaks budgets weekly** around the monthly plan, × LogNormal(−σ²/2, σ) with σ = 0.15
   (`world/benchmarks.yaml` `history_manager`). Before this, spend tracked demand exactly, so no observational curve
   was identifiable: 34 of 47 units were unstable or rejected, with Hill(spend)–baseline correlation 0.85–0.91. Real
   budgets also move for reasons other than demand: tests, pushes and pull-backs.

The CV(K) > 0.5 rule was kept on evidence. Relaxing it admits 18 more units, but their median marginal-ROAS error is
34%, against 13% with the rule.

## Measured (seed 42, fit_ts 2026-10-01 12:00, 62 s)
| | Value |
|---|---|
| Units | 47: 23 OK, 7 POOLED, 17 MODEL_UNAVAILABLE |
| Pooled curves | google_search OK (R²_P 0.61); google_video and meta MODEL_UNAVAILABLE (CV(K) 0.50 and 0.53) |
| Family P10–P90 coverage on P | 83% |
| Curve recovery vs world truth (`evalharness.curve_recovery`) | median relative error of marginal ROAS at current spend **15%** over the 30 usable units (OK 15%, POOLED 16%; target ≤ 30%) |

Known limitation: a few accepted units sit in the convex part of their Hill curve, where the model elasticity is
> 1. One unit reaches 1.7 against a true 0.72. Bootstrap P10 and the outcome calibration factor (§10) are the guards.

## FAILURE STATES
- INSUFFICIENT_DATA (spend floor) → POOLED
- FIT_FAILED / REJECTED (skill < 0, or family coverage out of band) / UNSTABLE → POOLED with the reason stored
- no usable pooled curve → MODEL_UNAVAILABLE

## PERSISTENCE
- `models.response_curves`: one row per (unit, fit_ts) with params, scales, terminal adstock, diagnostics and
  `artifact_sha256`
- `models.curve_draws`: 200 rows per unit, of kind unit and pooled
- `models.registry`: the champion version, which hashes all artifacts

## TESTS
- Unit (`backend/tests/test_curves.py`):
  - adstock recursion and its derivative
  - Hill shape
  - analytic Jacobian matches finite differences
  - marginal response recovered within 25% on synthetic data
  - joint block indices are contiguous and deterministic
  - bootstrap spread
  - shrinkage, diminishing marginal ROAS, and MODEL_UNAVAILABLE raising
- Integration (`integration/test_curve_recovery.py`, fixture world through the real path), T40:
  - every unit has an artifact
  - rejected or unstable fits carry no own curve
  - MODEL_UNAVAILABLE carries nothing and cannot predict
  - usable artifacts predict finite values
- The fixture backbone makes saturation unidentifiable (CV(K) > 0.5 almost everywhere), so recovery is measured on
  real-backbone seeds by `evalharness.curve_recovery`.

## UI EVIDENCE
Curve view (Stage 2, `GET /curves/{budget_id}`): fit status and reason, diagnostics, marginal ROAS at current spend
with its P10–P90 over draws. Decision Center: MODEL_UNAVAILABLE becomes a why-not reason.
