# Contract: demand model, NB2 inventory risk, model governance (Stage 2, spec §7.3, §5, §8.1, §10.2, §22.2, §22.9)

Modules: `predict/demand.py`, `economics/inventory_risk.py`, `learn/governance.py`; config `config/models.yaml`
(`demand`, `governance`), `config/inventory_risk.yaml` (the stage switch `predicate`).

## Inventory risk predicate (one switch for gate, safety candidate, evidence, safety monitor)
- `PROJECTED_SHORTFALL` (Stage 1): shortfall = max(projected − (available − safety stock), 0); at risk iff > 0.
- `STOCKOUT_PROBABILITY` (Stage 2, default): NB2 over the horizon: m = max(Σ P50 baseline + E[effective Δunits], 0),
  available_count = floor(max(on_hand − reserved + inbound_confidence × inbound within H, 0)),
  P = 1 − nbinom.cdf(count, r, r/(r+m)). Gate: at risk iff P > 0.30; evidence / safety monitor: P > 0.5; hysteresis
  recovery: P < 0.2. r per category by method of moments on 56 days of daily counts, **moments pooled within SKUs**
  (r = Σ mean² / Σ (var − mean)), capped 1e6. The daily r is applied to the H-day mean (conservative).
- LIMIT gate (x_i in [0.10, 0.50]): an increase may not raise any mapped SKU's risk above max(current risk,
  threshold). BLOCK above 0.50. The S3 safety candidate cuts until the excess (Stage 2: P − 0.30) is gone, reporting
  `risk_kind`, `risk_before`, `remaining_risk`.

## Demand model
Features (known before t): weekday, lags 1/7/14, 7- and 28-day means, known SCD2 price and its ratio to the 28-day
mean, planned spend (the mapped campaigns' spend on t − 1, attribution-weighted; never same-day spend), holiday flag,
SKU and category codes. LightGBM quantile boosters P10/P50/P90 (native API, single thread, deterministic, model strings
content-addressed), sorted per row (non-crossing). **Conformalized quantile regression**: boosters fit on the window
minus its last 28 days, which calibrate Q = the ⌈0.8(n+1)⌉/n quantile of max(P10 − y, y − P90); intervals
[P10 − Q, P90 + Q] (measured: raw coverage 64% → 77% on a weekly-pattern panel). Chronology D 14 / P 28 / Train 120;
promotion (a) ≥ 5% better WAPE than seasonal-naive on P, (b) non-inferiority vs a LightGBM champion, (c) P10–P90
coverage in [70%, 90%]; the promoted candidate is refit on the 120 days ending at the last complete day. Weekly refit
in the pipeline's predict step. Forecast = recursive P50 path, batched over SKUs; `load_state` uses its mean as
`baseline_daily` (seasonal-naive when no LightGBM champion). LightGBM unavailable (e.g. no libomp) → the champion
stays seasonal-naive.

## Governance (champion / challenger)
`learn.model_registry` (spec columns) + `learn.model_events`. `consider()` applies the layered rule; (b) is a paired
moving-block bootstrap (7-day blocks, 1,000 resamples over holdout days) of (Σ cand_err − Σ champ_err) /
max(Σ champ_err, ε), 95% upper bound ≤ +2%. A refit of the champion's own specification (same feature_hash) is a
routine refresh; a different specification is compared by refitting the champion's spec on the same Train window
(identical rows, both out-of-sample). `rollback()` restores `rollback_version`. Seasonal-naive is registered as the
first demand champion (rollback target). Response curves register each refit as champion of the same spec (per-unit
acceptance inside the fit); `load_curves` / decision model hashes follow the registry's champion (rollback-aware).

## TESTS (`backend/tests/test_demand_governance.py`, `integration/test_decide_learn.py`)
NB2 vs scipy and edge cases; method-of-moments r; non-inferiority bootstrap; T32 (worse candidate retained, rollback,
routine same-spec refit); LightGBM promoted on a weekly-pattern panel (58% better WAPE, coverage in band) and rollback
returns the forecast to seasonal-naive; criterion (a) gate; T61 (a row after fit_ts cannot change the fit: same
artifact hash); quantiles never cross (property); Stage 2 gate BLOCKs scale-up on a P > 0.3 SKU, the Stage 1 switch
still reports projected shortfall; the Stage 2 safety candidate; S3 through the real path under either predicate.
