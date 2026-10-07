# Contract: reconciliation, canonical state, marts, data health (A3)

Modules: `backend/adapt/reconcile/{core,marts,health,build}.py`; config `config/reconcile.yaml`,
`config/health.yaml`. CLI: `uv run python -m adapt.reconcile.build [--as-of 2026-10-01T12:00]` (default: the
world's today at the pipeline hour, 12:00 logical time). API (early C6): `/api/v1/data/sources`, `/health`,
`/mapping-coverage`, `/reconciliation`.

## INPUT / AS-OF RULE
`stg.*` from the connectors. A build "as of" T reads only rows with `available_at <= T` (no leakage), and runs as
one single-writer transaction (readers see the old or the new canonical state, never a mix). Rebuilding for the
same T is byte-identical (tested).

## OUTPUT
- **core**: `campaigns` (channel from platform + Google channel type; product set from the naming convention
  "… | <product_type>"), `ad_sets`, `ads`, `budgets` (Google average-daily incl. shared, Meta daily),
  `budget_history` and `campaign_state_history` (SCD2 from observed snapshots: `first_observed` /
  `observed_change`), `skus`, `sku_return_rates`, `return_lag_cdf`, `pricing_snapshots` (SCD2 prices + costs +
  return rate), `order_items` / `orders` (refund contract below), `attribution` (last paid click via UTMs,
  version `v1-last-paid-click`), `ad_metrics_daily`, `campaign_reach_daily`, `ga_daily`, `inventory_daily`
  (configured lead time 14 d, inbound confidence 0.9, SS = z(0.95)·σ_28d·√L, ROP = d̄·L + SS), `campaign_sku`.
- **marts** (additive totals only; ratios from window totals via B1): `campaign_daily` (complete calendar, zero
  days filled), `adset_daily`, `creative_daily`, `channel_daily` (paid + email/organic/direct), `sku_daily`,
  `brand_daily` (GMV → net revenue → CBA → CAA), `recon_daily` (platform-claimed vs store vs GA4).
- **ops**: `data_health` (per source, per as-of), `run_lineage` (rows per table per run).

## Refund contract — correction to spec §5
Matured lines (older than 30 days): realised refund. Immature lines: `max(realised, expected)`, never added. The
spec's `expected = return_rate × value` is the *unconditional* rate; applied to a line already observed for `a`
days without a return it double-counts (measured on seed 42: recent windows showed 17.5% refunds against a true
12%, a fake decline in every recent ROAS). ADAPT uses the conditional remaining probability
`p = r·(1 − F(a)) / (1 − r·F(a))`, with F the empirical return-lag CDF from matured lines. Measured after the fix:
12.17% (last 8 days), 12.10% (9–30 days), 12.07% (matured) — unbiased by order age.

## campaign_sku (spec §4)
Allocation = equal share of the product set. Attribution over the trailing 28 days of last-click net revenue:
`w_k = (rev_k + 0.01·R·a_k) / (1.01·R)`; `__unmapped__` absorbs revenue on SKUs outside the set; R = 0 → allocation
weights; a campaign with no product set puts weight 1 on `__unmapped__`. Weights sum to 1 (tested).

## Data health (spec §4)
score = 100 × (0.4·freshness + 0.3·completeness + 0.3·consistency); freshness 1 within the source SLA, linear to
0 at 3× SLA (logical age of the newest complete day's report); completeness penalises null keys and row counts
beyond ±40% of the trailing 7-day mean; consistency = share of contract checks passed (ranges, currency,
referential: report campaigns exist in the account, UTM campaigns known, refunds reference orders). Hard failures
force RED: connector FAILED on its last run, no data, wrong currency, negative spend / clicks > impressions.

## Measured (seed 42, as of 2026-10-01 12:00)
Build 8 s: 800,695 order lines, 169,456 attributed orders, 80,829 ad-days, 17,520 campaign-days; all six sources
GREEN (100). Last 28 days: net revenue ≈ ₹1.95 Cr/day, spend ≈ ₹30 lakh/day; paid ROAS 1.75–2.01, POAS
0.66–0.77; over-attribution Google 1.136, Meta 1.362; GA4 session/click ≈ 0.85; unmapped (cross-sell) share 8–11%.

## TESTS
Hand-calculated (`backend/tests/test_reconcile_core.py`): return rate + lag CDF, refund contract (immature
liability, conditional expectation, realised > expected, matured), line economics, channel mapping
(paid/email/organic/direct), last-click attribution, no leakage after as_of, campaign_sku weights incl. R = 0,
SCD2 budget/status history, mart spine + totals, recon, health formula + hard failures, idempotent rebuild.
Integration (`integration/test_reconcile.py`, `test_data_api.py`): per-campaign spend and last-click orders equal
the world's facts, brand orders equal, over-attribution equals the world's claimed/orders ratio, GA4 capture
≈ 0.95, refund estimate within 2.5 pp of the true rate in recent and mid windows, inventory equals the ERP,
Data Hub endpoints match the frontend contracts.
