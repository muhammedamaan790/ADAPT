# Contract: anomaly detector (B2) and level-1 diagnosis (B3)

Modules: `backend/adapt/detect/{stats,detector}.py`, `backend/adapt/diagnose/{decomposition,run}.py`; config
`config/metric_catalog.yaml` (metric definitions, transforms, eligibility), `config/materiality.yaml` (business
floors + detector parameters). Entry points: `run_detection(db, as_of, run_id)`, `run_diagnosis(db, as_of)`.

## INPUT
`marts.campaign_daily` (complete calendar), `marts.creative_daily` (zero-filled per creative), `marts.sku_daily`,
`core.budget_history` (observed budget changes) — all built as of the same logical time.

## ALGORITHM (spec §6, with the corrections below)
Per entity (campaign: CTR, CPM, CVR, AOV, ROAS, POAS, CPA, sessions/click, spend; creative: CTR; SKU: units) and
per evaluated window (last 3 and last 7 complete days):
1. Eligibility on PRE-window totals only (28 days); a post window at zero volume is the COLLAPSE path.
2. Transform (logit / log with half-event pseudo counts / raw for signed POAS / log1p), STL fit on the 56 days
   before the window only, expected_t = OLS trend of the last 14 fitted trend values extrapolated + the same
   weekday's seasonal value from the last complete week.
3. Window direction on aggregates (FLAT within 1% of expected, absolute floor for POAS); relative change vs the
   expected window value; ₹ impact = Σ_t ImpactCBA_t (§22.6, day-t quantities, pre-window cm).
4. MATERIAL = not FLAT, |impact| ≥ max(₹2,000, 5% of pre daily CBA), |relative change| ≥ the metric floor.
5. STAT (robust z ≥ 3.5 on a day, or ≥ 2.5 three days running, same sign) and SHIFT (penalised L2 change point
   starting inside the window, β = 8) — computed only when MATERIAL, since flag = MATERIAL AND (STAT OR SHIFT).
6. COLLAPSE: the critical numerator/denominator is zero in the window with an eligible baseline → MATERIAL from
   ImpactCBA alone, STAT skipped, direction = business effect (DOWN).
7. Per (entity, metric) the flagged window with the largest |impact| is kept.
Classification: spend change ≥ 25% with an observed budget change → `budget_change` (and efficiency moves with
|z| < 2 on that campaign are absorbed); spend change without one → `delivery_change`; sessions/click DOWN with
clicks within ±15% → `tracking_issue`; configured holiday → `seasonal_expected`; otherwise `efficiency_anomaly`.
Only `efficiency_anomaly` and `tracking_issue` are incidents. ≥ 70% of a platform's eligible campaigns flagging
the same metric and direction → one platform incident. A repeat detection within 7 days updates the open anomaly.
SKU unit signals are stored as evidence (`sku_signal`), not incidents.

## Corrections to spec §6 (measured on seed 42; each would otherwise break detection)
1. **Error scale = out-of-sample backtest errors, not STL's in-sample residuals.** In-sample residuals of a
   flexible STL understate the forecast error several-fold: with them, ~160 incidents fired on a quiet year. The
   z and PELT scale now use the MAD of 42 errors from 6 weekly backtest origins of the *same* forecast rule,
   ending before the longest evaluated window (fallback: in-sample residuals when history is short).
2. **Stable STL components** (seasonal smoother 15, locally constant per weekday; 21-day trend). With statsmodels'
   defaults the last week's "seasonal" values echo that week's noise and the forecast repeats an outlier day 7
   days later (a synthetic noise-only campaign produced a +71% "anomaly").
3. **Creatives are evaluated** (spec §6 lists them as entities): creative fatigue is diluted at campaign level
   (DEMO_01: top creative −48% CTR, campaign only −14%, under the 15% floor). Creative eligibility = 40,000 pre-
   window impressions (≈ the median creative); smaller creatives swing ±40% by chance.

## B3 decomposition (spec §7.1)
Funnel identity on window totals (pre = 28 days before the incident window): signed log contributions of CTR, CVR,
AOV and −CPM sum exactly to ln(ROAS_post/ROAS_pre); also % effects and share of movement. Any zero factor →
`COLLAPSE` at that factor with absolute deltas, no logs. Rate/mix midpoint drill-down by ad set and creative
(ROAS/POAS/CPA/CTR/CPM/CVR) or SKU (AOV); Adtributor-style minimal set (≥ 80% of absolute effect or 5 segments),
dimension with the smallest set wins. Persisted in `intel.decompositions`.

## OUTPUT
`intel.metric_flags` (every evaluation: status, direction, actual/expected/pre, relative change, impact, threshold,
STAT/SHIFT/COLLAPSE, daily z), `intel.anomalies` (incidents and classified non-incidents), `intel.decompositions`.

## Measured (seed 42 real data, as of 2026-10-01 12:00)
1,628 evaluations. DEMO_01 is detected: creative 2380000000270301 CTR −28% (z 4.0), with sibling creatives
2380000000270101 (−28%) and 2380000000270102 (−18%) of the same campaign also flagged. 15 incidents in total before
threshold tuning (z1/z2/β are tuned on seeds 1–20 in the Stage 2 eval).

Re-measured after the history manager gained weekly budget tweaks (response_curves.md, v2.4.5). The earlier world
gave −48% (z 6.0) and 13 incidents. Spend now varies week to week, so frequency, and with it CTR, is noisier, and the
fatigued creative's decline reads smaller against a noisier baseline.

## TESTS
Unit/property (`backend/tests/test_detect_diagnose.py`): transforms invert exactly, the forecast continues a trend
+ weekly pattern out of sample, robust z incl. MAD = 0, STAT run rule, exact segmentation never worse than
ruptures' PELT (hypothesis found a case where PELT is suboptimal), ImpactCBA hand checks, a 40% CTR drop flagged
with the right magnitude, 0 of 16 noise-only evaluations flagged, COLLAPSE, eligibility, FLAT never material,
classifier rules, funnel identity (property), funnel hand check + collapse, rate/mix sums (property, incl.
new/vanished segments), drill-down.
Integration (`integration/test_detection_scenarios.py`, world → connectors → canonical → detector, day-by-day
syncs): S1 → CPM UP platform incident on Meta only; S5 → `tracking_issue` on Google; S7 → `budget_change`, never
an incident; quiet world → no CPM/tracking/budget signals; diagnosis on S1 shows CPM pushing ROAS down.
