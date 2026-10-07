# Contract: world truth and seeding (A4)

Modules: `world/world/priors.py`, `world/world/catalog.py`, `world/world/truth.py`, assumptions in
`world/world/benchmarks.yaml`. Output: `data/world/<seed>/sim_truth.duckdb` (immutable after seeding).
ADAPT never reads any of this (import-linter + separate process); only `evalharness` may.

## INPUT
Backbone parquet (`world_backbone.md`), Global Ads CSV (E-commerce rows), `WorldConfig(seed, brand_scale,
history_end_date)`.

## Catalog (seed-independent)
12 categories x {Google Search, Google Video, Meta prospecting, Meta retargeting} = 48 campaigns; ad sets per
channel (2–3); 2–4 creatives per ad set (first one live from history start, others rotate in). The two
lowest-ranked categories' Google Video campaigns share one budget. Meta budgets live on the campaign (CBO).
IDs are numeric strings (Google 2000000000x / budgets 3000000000x, Meta 238000000xxx).
SKU economics in INR at the fixed `fx_usd_inr`; ship cost = max(₹60, 4% of price); payment fee 2% (assumptions).

## Response model (one campaign-day at spend s)
```
CPM(s)  = base_cpm x (1 + s / s_ref)^eta
I(s)    = 1000 s / CPM(s)
freq(I) = I / (A (1 - exp(-I / A)))
CTR     = base_ctr x creative_mult x fatigue x freq^-gamma
E[purchases] = I x CTR x csr x min(1, p_buy x demand^0.5)
```
Strictly increasing and concave in s (property-tested), so targets are hit by bisection.

## Truth draws (per seed, keyed RNG: `(seed, 0, entity_id, "truth:<purpose>")`)
| Parameter | Source |
|---|---|
| base CTR / CVR / CPM | one Global Ads E-commerce row per campaign, jointly (bootstrap); retargeting multipliers |
| click->session rate | Beta(102.37, 13.96) truncated to [0.80, 0.95] |
| gamma, eta, target daily frequency, cross-sell share | uniform ranges in benchmarks.yaml |
| audience A, s_ref | solved so the unsaturated reference spend gives the target frequency |
| creative CTR multiplier | LogNormal(0, 0.15), normalised to mean 1 per campaign |
| creative fatigue half-life | 365–1095 days at reference delivery |
| price elasticity | per category, U(-2.5, -0.8) |

**Survivorship:** prior rows are redrawn (up to 500 times) until the campaign's fresh-creative revenue ROAS at
the reference spend lies in `reference_roas_band` [2.0, 10.0]; a brand does not keep running campaigns at
ROAS 0.5. Wear-out and saturation pull realised ROAS ~1.5x below this (see measured values). A channel whose
prior is a single fallback row cannot be redrawn; the closest draw is kept.

## Demand and volume
- demand index per category = 28-day centred trend (floored at 0.05) x weekday factor (by world weekday).
  After history it continues at the mean trend of the last 28 days x weekday factor.
- Expected paid purchases per campaign-day = k x category mean units x source share x index, with sources
  Adwords -> Google Search, YouTube -> Google Video, Facebook -> Meta (60% prospecting / 40% retargeting).
- Unpaid orders (Email, Organic) and the unmapped warehouse demand use the same index and k.
- `truth.history_budgets` is the fresh-creative budget plan. The budgets actually applied during seeding come
  from the history "human manager" (`world.step.manager_budgets`): on the 1st of each world month it solves for
  the month's target using the creatives' *current* wear plus the average pacing/CPM/noise effects, rounded to
  ₹100. Shared budgets split by the reference-month need, held fixed.

## Measured (seed 42, real data, k = 10, simulated history, last 28 days)
48 campaigns, 322 creatives. Paid purchase attempts / expected = 0.91–0.95 per channel (within-month wear is not
fully offset by a monthly manager). Campaign ROAS 1.25–7.9 (median 2.15); blended paid ROAS 2.1, paid POAS 0.7
(break-even ROAS ≈ 3.1 at ~50% gross margin, 12% returns): many campaigns lose money on the first order, as is
common for D2C, which leaves ADAPT real profit decisions. ≈ 2,190 store orders/day (≈ 465 paid), revenue
≈ ₹1.08 Cr/day, ad spend ≈ ₹15.5 lakh/day; 92 stockout SKU-days in the year. Seeding takes ≈ 130 s.

## FAILURE STATES
A channel with no purchases in the backbone sessions -> ValueError (no silent zero campaign).
Existing truth file -> FileExistsError unless `overwrite=True` (explicit reseed).

## TESTS
`world/tests/test_world_truth.py`: priors source + fallback, catalog structure/determinism, frequency limits,
audience solve, response monotone/concave/invertible, per-seed determinism, ROAS band, monthly budgets hit
targets (1%; 15% for the fixed shared split), demand index shape + continuation, creative normalisation,
truth file write-once + read-only.
