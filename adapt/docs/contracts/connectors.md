# Contract: connectors (A2)

Modules: `backend/adapt/ingest/{http,schema,sync}.py`, `ingest/connectors/{base,ads,commerce}.py`, config
`backend/adapt/config/sources.yaml`. CLI: `uv run python -m adapt.ingest.sync [--only meta_ads ...]` (reads
`ADAPT_WORLD_URL`, writes the workspace DuckDB).

## INPUT
The sources' native APIs (served by the world service in simulation; the same code targets real APIs):
Meta insights + objects, Google Ads GAQL, store orders/refunds/products, finance SKU economics + price history,
GA4 runReport, ERP stock + receipts (`world_reporting.md`). "Today" = the world's `/health` date.

## OUTPUT
- `raw.api_pages`: every response page verbatim + source contract fields (schema version, connector version,
  extraction_ts, hashed account id, source timezone, source currency, provenance).
- `stg.*` typed tables (INR, IST-naive timestamps, business-key primary keys, upserted):
  `meta_ad_daily`, `meta_campaign_daily`, `google_ad_daily`, `entity_snapshots` (campaigns/budgets/ad
  sets/ads per sync date), `store_order_lines`, `store_refund_lines`, `products`, `sku_economics`,
  `price_history`, `ga4_daily`, `erp_stock_daily`, `erp_receipts`.
- `ops.connector_status` (OK/FAILED, last success/failure, error code, last synced date), `ops.sync_runs`.

## ALGORITHM / normalisation rules
| Source | Rule |
|---|---|
| Meta | strings -> numbers; spend and action values USD x simulation FX (83.0) -> INR; purchases = `offsite_conversion.fb_pixel_purchase` (platform-claimed); `daily_budget` USD cents -> INR |
| Google | int64 strings -> ints; `cost_micros` / 1e6 -> INR; conversions kept as doubles (claimed); budget resource name -> budget id |
| Store | `taxes_included=true` -> error `TAX_INCLUDED_PRICES`; revenue = ex-tax line subtotal, GST kept separately; timestamps must be +05:30 (else `UNEXPECTED_TIMEZONE`) and become IST-naive; UTMs parsed from `landing_site`; refunds are separate dated records |
| GA4 | date `YYYYMMDD`; `(not set)` campaign kept as a value |
| ERP | one snapshot per date; a 404 for a date = no snapshot (before go-live / incomplete), not a failure |

**Logical time (spec §22.9):** `available_at` = event date + 1 day at 00:00 + source report lag (Meta 6 h,
Google 3 h, store 0, GA4 12 h, ERP 2 h); snapshots are available at the sync date's 00:00. `ingested_at` is wall
clock and never used for leakage control.

**Windows:** first run = `backfill_days` (365); later runs = `last_synced_date - lookback_days + 1` (3-day
re-pull, so late/corrected rows upsert and re-dirty their dates).

## FAILURE STATES
Reads retry 429/5xx/network 3 times with exponential backoff; other HTTP errors fail immediately. Each connector
commits in its own single-writer transaction: a failure rolls back only that source, is recorded as FAILED with
its code, keeps the previous `last_success_ts` / `last_synced_date`, and the run is `PARTIAL`. A world without a
seeded clock -> `WORLD_NOT_SEEDED`.

## Measured (seed 42, real data, full backfill over HTTP on the reference laptop)
125 s: 800,695 order lines, 87,721 refund lines, 43,876 Meta ad-days, 36,953 Google ad-days, 18,250 GA4 rows,
21,900 ERP SKU-days, 501 entity snapshots; 4,042 API pages.

## TESTS
Unit (`backend/tests/test_ingest_normalize.py`): Meta USD->INR incl. rows without actions, Google micros, GST
strip + UTM parse + IST date, tax-inclusive and foreign-offset rejection, refunds, GA4, `available_at`, retry
policy (transient retried, 404 not retried, persistent 429 gives up with a code).
Integration (`integration/test_connectors_sync.py`, the only tests importing both packages): full backfill
reconciles exactly with the world's facts per source; logical time + provenance + raw pages; re-sync is
idempotent and the lookback window applies; `advance` brings exactly the new day; a failing GA4 is isolated and
recorded while the others commit; unseeded world refused.

## Stage 2 SIMULATED channels (optional sources)
Enabled per world at seeding (`python -m world.seed --channels tiktok,amazon_sp`); a world without them answers with
empty reports, and data health reports them NOT_CONFIGURED (never RED). `backend/adapt/ingest/connectors/marketplaces.py`.

| Source | Native format | Normalisation | Staging |
|---|---|---|---|
| tiktok_ads | Business API v1.3 `report/integrated/get` (AUCTION_AD, pages), `campaign/get`, `adgroup/get`, `ad/get` | spend / conversion value USD strings → INR at the simulation FX; `stat_time_day` is labelled UTC: the source date is kept as the analysis date (the simulation shares one day grid; a real UTC account would need hourly data to re-bucket); campaign budget (USD, BUDGET_MODE_DAY) on the campaign | `stg.tiktok_ad_daily`, snapshots |
| amazon_ads | Sponsored Products v3: `reports/spCampaigns` (ad group daily cost, purchases7d, sales7d), `reports/spPurchasedProduct` (SKU mix of SP sales), `sp/campaigns` / `adGroups` / `productAds` | INR; platform-claimed sales (incl. ~0–5% view-through) | `stg.amazon_sp_daily`, `stg.amazon_purchased_product`, snapshots |
| amazon_marketplace | SP-API `orders/v0/orders`, `returns/v0/returns` (NextToken pages) | the SEPARATE marketplace order population (TheLook orders are never reassigned); SellerSKU = SKU, ex-tax INR | `stg.amazon_order_lines`, `stg.amazon_returns` |

Canonical state: TikTok / Amazon campaigns (`channel_id` tiktok / amazon_sp) with budgets on the campaign; ad metrics
union; marketplace lines join `core.order_items` with `sales_channel = amazon` (no UTM, never last-click attributed);
returns join the refund contract. Amazon has no click path to an order, so Amazon campaigns' attributed sales come
from the purchased-product report (`core.platform_attributed_daily`: net of the SKU's return rate, contribution from
the SKU economics) and feed `marts.campaign_daily` and `core.campaign_sku`. Mock adapters: `MockTikTokAdapter`
(USD, 2 decimals), `MockAmazonAdapter` (INR), absolute setters with separate read-back.
Tests: `world/tests/test_world_channels.py`, `integration/test_channels_e2e.py`.
