# Contract: gated synthetic control (Stage 2 ★, spec §7.1 level 3, §22.9 "Causal")

Modules: `backend/adapt/diagnose/causal/{synthetic_control,estimate}.py`; parameters `config/causal.yaml`.
Entry points: `estimate(db, Incident, materiality_m)` (reads only), `build_panel(db, Incident)`; called by
`diagnose_incident` for every incident and persisted by `run_diagnosis` in `intel.causal_estimates`
(evidence id `CSL-<anomaly_id>`).

## INPUT
`marts.campaign_daily` (attributed net revenue, spend, attributed orders, attributed CBA), `core.campaigns`
(product sets), app-observable events: `core.budget_history` / `core.campaign_state_history` (observed changes),
`intel.anomalies` (incidents), `core.pricing_snapshots` + `core.campaign_sku` (price changes on mapped SKUs).
Hidden injected scenarios are never read (truth isolation).

## OUTPUT
`{method, metric, claim_level: QUASI_EXPERIMENTAL, estimand, assumptions[], status: ESTIMATED | NOT_ESTIMABLE,
reason, gates{history, positivity, controls, pre_fit, placebo, post_conversions}, controls[{campaign_id, weight}],
effect_pct, ci[90%], observed_roas, counterfactual_roas, inr_translation, inr_label}`. When any gate fails the
effect is NOT returned ("No causal estimate: <reason>"); gate values are always returned.

## ALGORITHM
- Estimand: % effect on the treated units' reconciled ROAS (window totals, original scale):
  effect% = Σ_t spend_t·ROAS_t / Σ_t spend_t·ROAŜ_t − 1. ROAS-family incidents (ROAS, POAS, CPA) use the ROAS series
  (POAS can be ≤ 0 and is never logged); every other family is NOT_ESTIMABLE in Stage 2.
- Windows (intervention = incident window start s): fit = pre days 1–21, holdout = pre days 22–28 (only the
  sMAPE gate), post = the incident window; in-time placebo = the identical procedure at s − 14.
- Controls: all campaigns minus (a) the treated product sets (shared SKUs / audiences), (b) any app-observable event
  in the full interval [s − 42, post end], (c) any non-positive day. Top K = 10 by Pearson correlation over the fit
  days (ties: entity id).
- Weights: min ‖y_c − X_c w‖² + λ‖w‖², w ≥ 0, Σw = 1 (SLSQP), λ = 0.01·tr(X_cᵀX_c)/J, on log series **demeaned by
  their fit-window means** (counterfactual = treated fit mean + weighted control deviations).
- Interval (90%): moving-block bootstrap (7-day blocks, 500 draws) of the pre-period log residuals **with refit**
  (each draw rebuilds the fit window, refits weights and level, adds a resampled post residual path); fit-window
  residuals are inflated by √(n/(n − p)), p = active weights + level.
- ₹ accounting translation = effect% × counterfactual revenue × observed cm_before_ads (pre window), labelled
  "accounting translation of the estimated effect (not causal CAA)".

## PARAMETERS (`config/causal.yaml`)
fit 21, holdout 7, placebo offset 14, top K 10, ≥ 3 controls, ridge 0.01, sMAPE ≤ 15%, 500 draws, 7-day blocks,
90% CI, ≥ 50 post conversions.

## GATES (all required; spec §7.1)
history ≥ 42 pre days (28 pre incl. holdout + 14 placebo offset) · positive treated series · ≥ 3 eligible controls ·
holdout sMAPE ≤ 15% · placebo: its % CI includes 0 AND |its ₹ translation| < the incident's materiality m ·
≥ 50 post-period conversions.

## Deviations (stated)
1. **Demeaned level** instead of plain simplex weights on log levels: convex weights cannot reproduce a treated ROAS
   level that differs from every control's, so the plain variant fails the pre-fit gate on nearly every campaign.
   The demeaned variant (Doudchenko & Imbens 2016; Ferman & Pinto 2021) keeps convex weights and every gate.
2. **Refit bootstrap + df inflation** instead of adding residual noise to one fixed counterfactual: measured on 40
   hand-made panels, the no-refit interval was ±1 pp around estimates 2–3 pp off (coverage near 0 at the edges);
   with refit + inflation, 90% intervals cover the injected effect in 92.5% of panels (placebo false-effect ≈ 7.5%).

## MEASURED (fixture world, 6× volume)
- S6 (+40% category demand) after 42 stable-budget live days: point estimate +20.0% vs the paired world truth
  +18.2% on the treated Google Search campaign's ROAS. The gates refuse it (holdout sMAPE 21% > 15%; placebo ₹
  translation −₹2.6 lakh ≥ m): the world adds ~14% independent daily ROAS noise per campaign (`benchmarks.yaml`
  daily_log_sigma), and m = 5% of one day's CBA means a 7-day placebo must land within ≈ 0.7%. Expect most
  single-campaign incidents to be NOT_ESTIMABLE in this world; the eval reports the share that passes.
- Right after the seeded history (weekly ±15% budget tweaks per campaign): the placebo finds +8.4% (CI excludes 0)
  and the estimator refuses. This is the unit-specific confounding the gate exists for.

## FAILURE STATES
NOT_ESTIMABLE with the first failing gate as the reason: history, non-positive treated series, fewer than 3
controls, pre-fit sMAPE, placebo, post conversions, or "no causal estimator for the <metric> family".

## TESTS
Unit (`backend/tests/test_causal.py`): recovery of −20% / 0 / +15% on 20 panels (median error < 2.5 pp, coverage
80–100%), convex weights + top-K/tie-break selection, sMAPE bounds, ESTIMATED when all gates pass, T51 (non-positive
treated → NOT_ESTIMABLE; non-positive control excluded, never logged), < 3 controls, placebo catches a shift that
began 14 days earlier, bad pre-fit, short history, other families, few post conversions.
Integration (`integration/test_causal_scenarios.py`): the S6 point estimate on the product's own panel vs the paired
world truth (±10 pp, interval covers it), the gated output either equals it or names its reason; the confounded
history case is refused by the placebo.

## UI EVIDENCE
Incident view, level 3: "Estimated effect of X% (90% interval …; accounting translation ≈ ₹Y) under the stated
assumptions", or "No causal estimate: <reason>" with the gate table.
