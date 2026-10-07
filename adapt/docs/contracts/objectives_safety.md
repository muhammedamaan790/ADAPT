# Contract: Stage 2 objectives, sensitivity scenarios, automatic safety monitor (spec §8.2, §8.3, §9.1, §9.3, §10)

Modules: `decide/optimizer.py` (modes), `decide/alternatives.py` (selected objective, sensitivity, choose),
`policy/safety_monitor.py`; config `objectives.yaml`, `guardrails.yaml` (`safety`).

## Objectives (selected = admin setting `ops.objective_setting`; EXACT staleness input)
PROFIT max E[ΔCAA] − λ(E − P10) · GROWTH max E[Δnet revenue] s.t. E[abs CAA] ≥ CAA0 − max(5%|CAA0|, ₹2,000)
(sign-safe; why-not `OBJECTIVE_CAA_FLOOR`) · INVENTORY_CLEARANCE max E[ΔCAA] + h·E[effective units sold on EXCESS SKUs]
(cover > 45 days or unbounded; h = ₹25/unit). CAA0 = E[abs CAA] at the current allocation. The same feasible set,
validator and policy apply to every mode; why-not texts name the mode ("Marginal GROWTH value …").

## Sensitivity (PROFIT only)
Conservative λ 1.0, ±10%/day; Aggressive λ 0.2, ±20%/day; each re-solved with the same optimizer (shared revenue
caches), policy-validated, reported with E, P10, P90, P(loss), Δnet, max inventory risk (stage kind), unallocated —
labelled "sensitivity scenario (a different risk preference), not a competing recommendation". Stored in the decision's
`alternatives` (hashed); replay recomputes them exactly when the decision carried them. `choose_alternative()` (manager)
creates a new decision with its own snapshot and hash that supersedes the recommended one.

## Safety monitor (pipeline step `safety_monitor`)
Executed OPTIMIZATION decisions inside their measurement window, on their treated units: CPA > 1.25 × guardrail
(guardrail = pre-action 28-day CPA); a mapped SKU at risk (Stage 2 P > 0.5); cumulative realized ΔCAA < the
decision-time cumulative P10 path (stored in the measurement basis) on 2 consecutive checks. Action: a SAFETY decision
— REVERT the increases to pre-action budgets, else REDUCE the treated units by the max daily change from their current
budgets (₹100 rounding inside the box), floored at the unit minimum; never a pause (T58). Approve mode: PENDING
review + a P1 `safety_alert` event. Precedence 0: an EXECUTION_UNCERTAINTY freeze → alert only, no decision (T47).
Hysteresis: an executed SAFETY decision → SAFETY_COOLDOWN freeze; cleared after 5 days AND 3 consecutive days of CPA <
1.10 × guardrail AND inventory recovered. > 2 automated reversals per entity in 7 days → `ops.autonomy_pins` (T31).
A safety action inside an open OPTIMIZATION window → `learn.outcome_flags CONTAMINATED_BY_SAFETY`; that outcome is
measured but never calibrates. Checks are logged in `ops.safety_checks`; one decision per (decision, day).

## TESTS
`backend/tests/test_objectives.py` (GROWTH vs PROFIT and the floor, sign-safe tolerance, CLEARANCE toward EXCESS (S11),
bounded scenarios, admin setting + fingerprint, choose → supersede, replay with scenarios);
`integration/test_safety_monitor.py` (CPA runaway → reduce decision + P1 alert, idempotent, executed → cooldown +
contaminated outcome; T47; T31); `integration/test_pipeline_cycle.py` (the step is live).
