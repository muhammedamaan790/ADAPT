# Contract: world scenarios + ground truth (A4, spec §14, §16)

Module: `world/world/scenarios.py`. Activate: `POST /control/scenario {"key", "start_day"?, "params"?}` (default start =
current clock day; past starts are refused), or schedule during seeding (`seed_world(..., scenarios=[...])`,
CLI `--demo` = DEMO_01 at day -10). Each activation stores resolved targets in `scenario_activation` and its GT
incidents in `gt_incidents` (sim_state; never on the public listener, never readable by ADAPT). sim_truth stays
write-once, so runtime activations are recorded in sim_state instead (deviation from §4's "gt_incidents in
sim_truth"; the isolation guarantee is unchanged).

| Key | Injection (defaults) | Default target | GT driver / metrics (direction) | Window, onset |
|---|---|---|---|---|
| S1 | Meta CPM x1.45, platform-wide | all Meta campaigns | auction / CPM, CPC up, ROAS, POAS down, CPA up | 6 days, onset = start |
| S2 | top-delivered creative CTR -40% (linear over 10 days) + campaign audience to 0.75 (frequency up) | Meta prospecting, rank-1 category | creative_fatigue / CTR, ROAS, POAS down, CPA up | 14 days, onset = start + 4 |
| S3 | hero SKU on-hand -> 0, open PO cancelled, reorders blocked | Google Search category rank 2, highest-mix SKU | inventory / CVR, ROAS, POAS, SKU units down | 7 days, onset = start |
| S4 | category prices +18% (SCD2 price history), restored after the window | category rank 3 | price / CVR down, AOV up | 14 days, onset = start |
| S5 | GA4 records 40% of Google sessions and purchases; clicks and real orders unchanged | Google Search + Video | tracking / session/click down | 7 days, onset = start |
| S7 | human cuts a Google Search budget by 40% | category rank 4 | budget_change / spend down (must not open an efficiency incident) | one-shot |
| DEMO_01 | Meta creative CTR -50% + audience to 0.60 (10-day ramp); hero SKU set to 13 days of cover with reorders blocked; Google category demand +25% (ramp) with 60 days of stock | Meta prospecting of the largest women's category; Google Search of the rank-1 men's category | creative_fatigue (Meta), demand (Google, up) | 21 days, onset = start + 4 |

| S6 | category demand x1.40 (3-day ramp) | rank-1 category | demand / ROAS, POAS, CVR, SKU units up | 10 days, onset = start + 1 |
| S8 | Meta retargeting audience shrunk so daily frequency reaches ~5 (7-day ramp); reach falls, CTR falls on every creative via the frequency response | the retargeting campaign with the steepest truth CTR-frequency response | audience_saturation / CTR, ROAS, POAS down, CPA up | 14 days, onset = start + 3 |
| S10 | every category's demand x1.6 (a festival) | all | seasonal_expected / ROAS, POAS, CVR, units up (must be labelled seasonal_expected when the date is in ADAPT's holiday calendar) | 2 days |
| S11 | a category's stock raised to 120 days of cover | rank-2 category | excess_inventory (CLEARANCE objective case) | one-shot |
| S12 | S2-style creative fatigue (-40%, audience to 0.75) + category demand +30%, same Meta campaign (10-day ramps); `params.without = "fatigue" \| "demand"` removes one driver (eval-only GT effect-order forks) | Meta prospecting, rank-1 category | creative_fatigue + demand | 14 days, onset = start + 4 |

Onset = first day the cumulative effect reaches 50% of full magnitude; the injection end is the last day of the
window (spec §16 labelling contract).

## Measured on seed 42 (real data, `--demo`)
DEMO_01 resolves to META Prospecting · Women·Intimates (creative 2380000000270301), hero SKU W_INTIMATES-P5 and
GOOGLE Search · Men·Outerwear & Coats. Meta campaign CTR 0.0150 -> 0.0118 (-21%) by days -5..-1; hero cover
15.9 -> 2.6 days by day -1; Men·Outerwear stock ≈ 70 days with sales 150 -> 212/day; the two treated campaigns
take ≈ 66 orders/day (≈ 197 in 3 days >= the 100-order maturity rule). The Meta campaign has ~10 orders/day, so
its 5-day ROAS is noisy (±20%); whether seed 42's detection window clears the 15% ROAS floor is checked when the
detector (B2) exists.

## World-model assumption added for scenarios
`step.delivery_optimization = 0.5`: impressions split across live creatives proportional to quality^0.5 (platforms
re-optimise gradually). With instant optimisation a fatigued creative would lose its delivery at once and a
creative-level decay would barely move campaign metrics.

## TESTS (`world/tests/test_world_scenarios.py`)
Control vs scenario forks of the same world (common random numbers): S1 Meta CPM exactly x1.45 for 6 days and
back after, Google identical; S2 creative CTR ≈ 0.6x and campaign frequency up, nothing else moves; S3 zero
orders on the SKU, lost demand, stock 0 through the block, reorder the day after, clicks unchanged; S4 prices
x1.18 then restored, fewer units, SCD2 rows; S5 GA sessions ≈ 0.40x with identical orders and clicks; S7 budget
and spend -40%; S6 unpaid orders x1.40 and paid up, nothing else moves; S8 frequency ≈ 5, reach flat or down, every
creative's CTR down; S10 a 2-day x1.6 spike then back; S11 stock ≥ 120 days of cover; S12 both effects, and its
`without` forks remove exactly one driver; DEMO_01 hero cover < 7 days, Google demand up, two GT drivers; activation rules; DEMO_01 scheduled
during seeding replays to the same state hash.
