# Contract: portfolio economics (B6)

Modules: `backend/adapt/economics/{portfolio,state}.py`; parameters `config/objectives.yaml` (`economics`).
Entry points:
- `load_state(db, as_of)` → `PortfolioState` (canonical tables only, so every input has `available_at ≤ as_of`)
- `portfolio_economics(state, allocation, draws)` / `Portfolio(state).evaluate(...)` → `Economics`
- `inputs_manifest(state)`: the EXACT-class inputs that C3 hashes into `economics_hash`

## INPUT
- **Units** (one per budget; a shared Google budget is one unit):
  - s0 = the current budget
  - pacing = delivered spend / budget over the last 7 days (from `core.budget_history`)
  - the curve artifact (B5)
  - observed ROAS over 28 days
  - the SKU mix: `campaign_sku` attribution weights, with a shared budget's campaigns weighted by attributed revenue
  - u_i = the `__unmapped__` share
  - ucr_i = CBA / net revenue of the unit's unmapped attributed lines (28 days)
- **SKUs**:
  - nrpu = price × (1 − discount) × (1 − return rate), using the latest SCD2 price and the 28-day discount rate
  - unit contribution = nrpu − COGS − ship cost − fee% × nrpu
  - available = on hand − reserved + inbound confidence × inbound arriving within H
  - safety stock
  - baseline = the seasonal-naive P50 (last 7 days' mean)
  - a SKU without a positive nrpu joins the unmapped share
- **Non-modelled baseline**: contribution and net revenue of unattributed orders (organic, email, direct), per day.

## ALGORITHM (spec §8.1, Stage 1: T = 0)
Over horizon H (7 days), per joint bootstrap draw:

| Step | Formula |
|---|---|
| Curve increment | dR_i = R_i(s′) − R_i(s), daily paths with adstock continued from fit_ts |
| SKU mix | du_ik = dR_i · w_ik / nrpu_k (mapped SKUs only; u_i creates no units) |
| Rationing (net of releases) | H_k = max(ATP_k + released_k, 0); increases on k scaled by min(1, H_k / requested_k); decreases are never scaled; rationed-away units earn nothing and are reported as wasted spend |
| ΔCAA | Σ du_eff · unit contribution + Σ dR_i · u_i · ucr_i − Σ (s′ − s) · pacing · H |
| Δnet revenue | Σ du_eff · nrpu + Σ dR_i · u_i |
| Absolute values | status quo (curve revenue × contribution margin per rupee − spend + non-modelled contribution) + Δ |

- `s′ = s` gives Δ = 0, while abs_CAA = CAA₀.
- The daily ΔCAA path spreads each unit's booked contribution over days in proportion to its daily increments, and
  sums to the horizon total.
- **Inventory after the change**: projected = baseline × H + E[effective Δunits]. Status is OK / AT_RISK / SHORT;
  `kind = PROJECTED_SHORTFALL`.
- **Exposure**: x_i = Σ_k w_ik · 1[shortfall_k > 0] + u_i.
- **BASELINE_STOCK_DEFICIT**: SKUs with ATP < 0 before any action.
- **MODEL_UNAVAILABLE units** use revenue = observed ROAS × spend. The optimizer only lets them decrease, so a cut
  conservatively assumes revenue falls in proportion.

## FAILURE STATES
- A SKU without a usable price or stock row is excluded from units: its weight becomes unmapped exposure.
- Non-finite values never enter (all ratios are guarded).

## TESTS (`backend/tests/test_economics.py`)
- **nrpu hand check through the portfolio**: price 100, COGS 50, returns 20% → nrpu 80, ₹80 = 1 unit, CBA 30. A 10%
  discount gives nrpu 90.
- **Zero change**: Δ = 0, with abs_CAA ≠ 0.
- **Deficit headroom** (spec examples):
  - ATP −100 with releases of 120 → headroom 20
  - A −100, B +150 at ATP 50 → B gets all 150
- **Rationing**: wasted spend is computed correctly.
- **Unmapped share**: enters ΔCAA and the gate but creates no units.
- **Hypothesis invariants (150 cases)**:
  - Δspend is exact
  - effective increases ≤ max(ATP + released, 0) per draw and SKU
  - no rationing → Δnet revenue = curve increment
  - the daily path sums to the total
  - absolute = status quo + Δ
- **Currency neutrality**.
- **Integration** (`integration/test_decide_learn.py`): built from the real canonical state, weights sum to 1 and
  nrpu > 0.

## UI EVIDENCE
Decision Center: E / P10 / P90 / model P(loss), Δnet revenue, inventory risk after (projected shortfall per SKU),
wasted spend.
