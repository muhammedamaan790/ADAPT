# Contract: PROFIT optimizer, why-not, cash reserve, S3 safety candidate (B7)

Modules: `backend/adapt/decide/{optimizer,safety,run}.py`; config `config/guardrails.yaml` (the feasible set, which
the policy engine C4 re-validates) and `config/objectives.yaml` (PROFIT λ, draws, steps).
Entry point: `run_optimizer(db, as_of)`. It loads the state, derives policy flags, solves, builds safety
candidates, applies the calibration factor, then persists `intel.optimizer_runs` and each proposal's
`learn.measurement_basis`.

## ALGORITHM (spec §8.2, §8.3, §22.3)
**Objective (PROFIT)**: max E[ΔCAA] − 0.5·(E − P10), computed on a fixed 50-draw subset. Reported E / P10 / P90 /
model P(loss) use all 200 joint draws.

**Constraints** (one per unit, generated from `guardrails.yaml`):
- Σ s′ ≤ B − R, with B = the current total budget by default and R = max(₹, % × B), default 0
- box [s0 (1 − 0.2), s0 (1 + 0.2)], and the per-unit minimum of ₹500
- Σ |s′ − s0| ≤ 25% × Σ s0
- channel share bounds
- no increase on MODEL_UNAVAILABLE units or on MIX_UNCERTAIN units (u > 0.20)
- units are fixed at s0 under DATA_DEPENDENCY (YELLOW/RED source), TRACKING_FREEZE (open tracking incident),
  EXECUTION_FREEZE or SAFETY_COOLDOWN (`ops.entity_freezes`)
- inventory gate on every increased unit: x_i > 0.50 → no increase (BLOCK); 0.10 ≤ x_i ≤ 0.50 → the increase may
  not worsen any mapped SKU's projected shortfall (LIMIT)

**Solve**:
1. Greedy ₹500 marginal moves (increase or decrease, best improvement first), which also produces the marginal ladder.
2. SLSQP polish.
3. Validator: the better feasible result wins.
4. Round to the ₹100 allocation increment → repair → revalidate, or return ROUNDING_INFEASIBLE.

The result reports the greedy-vs-SLSQP gap and a per-unit second-difference concavity check. No global-optimality
claim is made.

**Fast exact evaluation**: with T = 0, a draw's mapped contribution is
Σ_k uc_k (min(req_k, max(ATP_k + rel_k, 0)) − rel_k). A one-unit move therefore updates two (n, K) arrays; a test
asserts this equals `Portfolio.evaluate`.

**Cash reserve**:
- `unallocated = B − Σ s′ ≥ R` and `reserve_floor = R` are reported separately.
- When budget is left over, the result names the best feasible remaining receiver and its marginal value.
- If Σ s0 > B − R, the result is `RESERVE_BASELINE_INFEASIBLE`: no change may raise total spend, and no cuts are
  invented just to build the reserve.

**Why-not**: computed at the final allocation, for every unit not increased. The probe is the largest
increment-aligned increase of at most one step that the box still allows. The reason is the first of:
1. the unit's flag (MODEL_UNAVAILABLE, MIX_UNCERTAIN, DATA_DEPENDENCY, …)
2. MAX_DAILY_CHANGE / UNIT_MAX
3. INVENTORY_GATE, with the exposure, gate and risk kind
4. DAILY_RUPEES_MOVED_CAP
5. TOTAL_BUDGET / CASH_RESERVE, with the best **feasible transfer** comparison ("moving ₹X from U to V would change
   risk-adjusted expected CAA by …"; no shadow-price language)
6. CHANNEL_SHARE_MAX
7. otherwise MARGINAL_VALUE_NONPOSITIVE, with the ₹ value

There is never a generic reason.

**S3 safety candidate** (Stage 1, REQUIRES_REVIEW):
- Triggered for every SKU whose projected shortfall over H is > 0 at the status quo.
- The candidate is the smallest common cut fraction (bisection) on the units mapped to that SKU that removes the
  shortfall.
- The cut is capped at the largest feasible decrease (box, per-unit minimum; fixed units untouched).
- It is rounded down to the increment, and `remaining_shortfall` is reported when even the largest cut is not
  enough.
- Freed budget stays unallocated. The trigger is BASELINE_STOCK_DEFICIT when ATP < 0.

**Calibrated prediction**: `calibrated_pred = optimism_correction_factor × raw_pred` if raw_pred > 0 (factor from B8,
initially 0.9).

## Measured (seed 42, fit_ts 2026-10-01 12:00; 11 s)
| | Value |
|---|---|
| Recommendation | cut 40 of 47 units, each by up to the 20% box |
| Spend | −₹5.78 lakh/day, left unallocated: the best remaining receiver would lose ₹145 per extra ₹500 |
| E[ΔCAA] over 7 days | +₹21.5 lakh (P10 +₹20.6 lakh, model P(loss) 0) |
| Why-not | 26 MARGINAL_VALUE_NONPOSITIVE, 17 MODEL_UNAVAILABLE, 1 INVENTORY_GATE |
| Inventory gates | 43 ALLOW, 1 LIMIT, 3 BLOCK |
| One safety candidate | W_INTIMATES-P5 (BASELINE_STOCK_DEFICIT); shortfall 539 → 497 units at the largest feasible cut |
| Greedy vs SLSQP gap | ₹0 |
| Concavity | 7 of 40 changed units sit in a locally convex part of their curve (reported) |

The direction matches the world: paid spend there returns about 0.7 rupees of contribution per rupee (POAS 0.7), so
the profitable move is to spend less. ADAPT says so and leaves the cash unallocated rather than forcing a
reallocation.

## TESTS
- Unit (`backend/tests/test_optimizer.py`):
  - fast evaluation equals portfolio_economics
  - ROAS trap: the highest-ROAS, thin-margin unit loses budget to the high-margin unit
  - ±20% box and ₹100 increments
  - decreases ordered first
  - why-not reports the best feasible transfer
  - cash reserve, including RESERVE_BASELINE_INFEASIBLE
  - a MODEL_UNAVAILABLE unit is never increased
  - inventory BLOCK
  - unallocated budget when no rupee is worth spending
  - fixed flags
  - S3 safety candidate: capped cut, freed budget
- Integration (`integration/test_decide_learn.py`), fixture world through the real path:
  - feasibility and the reserve
  - no MODEL_UNAVAILABLE scale-up
  - no generic why-not
  - calibrated_pred
  - S3 → the stocked-out SKU AT_RISK or SHORT, gated units (BLOCK units not scaled), and a safety candidate on
    that SKU

## FAILURE STATES
`ROUNDING_INFEASIBLE` (no decision is created); `SOLVER_FAILED` for SLSQP, in which case the greedy result is used
if feasible.
