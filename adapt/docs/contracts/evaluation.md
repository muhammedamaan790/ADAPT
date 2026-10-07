# Contract: Stage-1-scope evaluation harness (Stage 2 ★, spec §16, §22.7, §22.8)

Package `evalharness/evalharness/` (the only code that imports both `adapt` and `world`): `seeds.py`,
`matching.py` (GT labelling), `strategies.py` (six strategies + the common envelope), `runner.py` (forks, day loop,
realized metrics, detection run, oracle perturbation check), `report.py`; `world/world/truth_economics.py` (the
oracle's truth-side economics, independent of `adapt.economics`); `scripts/run_eval.py` (CLI).

## Seeds (fixed before tuning)
Tuning 1–20 · PRIMARY_EVAL 101–120 (reduced subset 101–110, chosen before any result) · warm-up 901–903 + reliability
904 (Stage 3) · stress worlds reported separately, never pooled.

## Strategies (one continuously evolving fork each, same seed → common random numbers, T35)
safe-static · roas-rank (7-day reconciled ROAS, ≥ ₹10,000 spend, bottom-3 → top-3 pairs moving 10% of the bottom
unit, ties by budget id) · contribution-rank (14-day POAS; units with mapped cover < 7 days never receive) ·
safe-contribution (+ POAS < 1.0 cut up to 20%/day without reallocation when no unit has POAS > 1.2) · adapt (the full
pipeline; a scripted sim manager approves every pending decision, executed through the C5 saga) · oracle (greedy on
`truth_economics.expected_caa` under hard feasibility only, no ADAPT epistemic gates). Common envelope: the day-0 total
B, R = 0, ±20%/day box, unit minimum, 3-day cooldown, rupees-moved cap, rounding to ₹100 inside the box, proportional
shrink to fit. Every non-ADAPT strategy applies the common safety policy (the S3 safety candidate) as a logged forced
intervention. Same mock executor (absolute setters + read-back).

## Metrics (truth, from the world's facts over the evaluation days)
CAA = Σ orders (price − discount − COGS − ship − fee − price if returned) − spend, spend, CAA per rupee, stock-risk days
(SKU-days with demand lost to a stockout), guardrail breaches by executed mutations (world-log replay; must be 0),
ADAPT replay rate and outcome verdict counts (all four + INCONCLUSIVE), oracle perturbation check (weekly: 10 random
feasible perturbations of the oracle's allocation held 7 days in short-lived sub-forks; suboptimality rate).
Detection fork (observe mode, default schedule S1 d3, S7 d8, S2 d12, S3 d26, S5 d34, S4 d42): §16 matching →
precision / recall / F1, median latency (≥ onset only), early warnings, diagnosis top-1 / top-3 on TPs, negative set.

## Report (`evidence/eval.json`)
Primary U(adapt vs safe-static) with a paired bootstrap (over seeds) 95% CI; secondary U(adapt vs safe-contribution);
every other comparator; oracle capture (median over interpretable seeds; U_oracle ≤ 0 → NOT_INTERPRETABLE, counted);
safety violations per strategy; replay; verdicts; detection / diagnosis / negative set; the targets. Numbers are shown
even when a target is missed.

## Running it
`uv run python scripts/run_eval.py --bench` (one seed, 3 days → `evidence/bench.json` + an hours-per-seed estimate),
then `uv run python scripts/run_eval.py --seeds eval --days 60 -j 8` overnight (or `--seeds reduced`). Needs the real
backbone and Global Ads CSV. **Not run in this build** (no Kaggle data on the dev machine); the harness is tested
end-to-end on the fixture world.

## TESTS (`integration/test_eval_harness.py`)
T57 (before injection = FP, early warning, outside window, duplicates, wrong direction / metric / entity, maximum
cardinality + determinism), diagnosis and negative set, T52 (rank order, 10% step, spend preserved, no receiver →
unallocated, cooldown, eligibility), report maths (paired CI, oracle capture with NOT_INTERPRETABLE), all six
strategies on fixture forks (T35 common random numbers, 0 breaches, ADAPT replay 100%), the detection fork finds S1,
the oracle perturbation sub-forks.
