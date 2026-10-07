# Contract: evidence modules and driver ranking (B4)

Modules: `backend/adapt/diagnose/{evidence,drivers,run}.py`; thresholds `config/evidence.yaml` (pre-tuning
defaults, tuned only on seeds 1–20). Entry points: `diagnose_incident(db, Incident)` (reads only) and
`run_diagnosis(db, as_of)` (all open incidents of the run; persists `intel.decompositions`, `intel.evidence`,
`intel.diagnoses`).

## Modules (Stage 1; spec §22.1). score = gates × one magnitude term, in [0, 1]
| Module | Lever | Gates × magnitude | Minimum sample | Values |
|---|---|---|---|---|
| auction | CPM | share of the platform's campaigns with CPM up > 15% ≥ 0.6 × min(1, median ln-rise / ln 1.30) | ≥ 3 campaigns with ≥ 5,000 impressions in both windows | share_up, median and own CPM change % |
| fatigue | CTR | per creative: 14-day impression-weighted ln-CTR slope t ≤ −2 AND frequency rise ≥ 20% AND specificity (decline − median sibling decline) ≥ 0.15 × min(1, decline vs first 7 delivery days / 0.40); campaign = spend-weighted; a creative incident uses its own creative | creative ≥ 14 days old, ≥ 5,000 impressions and ≥ 100 clicks in its first 7 and last 7 days; < 2 eligible siblings → cap 0.69 | per-creative decline, slope t, frequency rise, specificity |
| inventory | CVR | CVR-drop gap (exposed − other SKUs) ≥ 0.20 × min(1, exposed share / 0.30); exposed = stocked out in the window or Stage 1 projected shortfall > 0 | ≥ 200 pre-window clicks per SKU group | exposed SKUs, shortfall per SKU, wasted spend |
| price | CVR, AOV | priced share ≥ 0.20 AND CVR moved opposite to price × min(1, \|Δln CVR_A − Δln CVR_other\| / 0.15) | ≥ 30 pre-window purchases on repriced SKUs | price change %, elasticity, CVR changes |
| tracking | CVR (measurement) | \|Δln clicks\| ≤ ln 1.15 AND sessions/click drop ≥ 30% × min(1, drop / 0.50) | ≥ 300 clicks per window | session/click pre/post, orders-vs-GA divergence |

Failure states are explicit: INSUFFICIENT_DATA, NOT_APPLICABLE (with a reason), never a fabricated value.
Demand and saturation are Stage 2; budget changes are handled by the B2 classifier.

**Stage 1 inventory predicate:** projected shortfall = max(projected − (available − safety stock), 0) with
available = on hand − reserved + inbound confidence × inbound arriving within 7 days, projected = the
seasonal-naive baseline (last 7 days of sales repeated over the horizon). Status OK / AT_RISK / SHORT.

**Data proxies (sources have no sessions by landing SKU):** the CVR of a SKU group = attributed orders on those
SKUs / the campaign's clicks; fatigue's frequency uses the campaign's reach (Meta); Google reports no reach, so
there the frequency gate cannot pass. "First 7 days" = the first 7 *delivery* days, with the 5,000-impression
minimum applied to the 7-day total (a per-day reading excludes every creative of a mid-size campaign).

## Ranking (spec §7.1 level 2)
ROAS-family incidents (ROAS, POAS, CPA) with an OK funnel: each driver's evidence-weighted signed contribution =
Σ over its levers of c_L × score / Σ scores claiming L; contributions + unexplained = ln(ROAS_post/ROAS_pre)
exactly; drivers with the opposite sign are OFFSETTING and never top-1. Other metrics: rank by score among modules
whose levers include the incident's lever (others are NOT_APPLICABLE — inventory cannot explain a CTR move).
Levels: STRONG EVIDENCE ≥ 0.7, WEAK 0.4–0.7, NOT_SUPPORTED < 0.4, NOT_ASSESSABLE. No supporting driver at ≥ 0.4 →
top driver UNKNOWN (stated, not guessed).

## Measured
Seed 42 (real data, re-measured on the world with weekly budget tweaks):
- DEMO_01's creative incident → fatigue STRONG; it is the only creative that passes all the fatigue gates.
- The 14 other incidents are honestly UNKNOWN.
- The earlier world also had a Men·Hoodies retargeting CVR drop diagnosed as inventory STRONG. That incident does
  not occur in this history.

## TESTS
Integration (`integration/test_evidence_scenarios.py`, on each scenario's true incident): S1 → auction top-1
(median CPM change ≈ +45%), S2 → fatigue top-1 at the injected creative (on a 6× fixture world: in the base
fixture each creative is below the minimum sample and the module correctly says INSUFFICIENT_DATA), S3 →
inventory top-1 (the SKU stocked out, wasted spend > 0), S4 → price top-1 (+18% ± 0.5 pp, negative elasticity),
S5 → tracking top-1 (sessions/click −60% ± 8 pp). Unit (`backend/tests/test_drivers.py`): signed contributions +
unexplained = total exactly, CVR shared in proportion to scores, offsetting never leads, lever relevance,
NOT_ASSESSABLE never wins, COLLAPSE falls back to scores.
