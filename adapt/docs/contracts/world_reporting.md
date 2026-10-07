# Contract: world service reporting (A5 + connector targets for A2)

Run a seeded world: `$env:WORLD_DIR="data/world/seed42"; uv run uvicorn world.main:app --app-dir world --port 8100`.
On startup the service rebuilds the truth from the config recorded in `sim_truth.duckdb` and refuses to start
if the rebuilt fingerprint differs (`TruthMismatch`). `POST /control/reset {"seed": 42}` restores the
post-history baseline snapshot (`sim_state.baseline.duckdb`); another seed -> 409.

**Visibility:** only complete days are reported (a world day is reportable once the clock has moved past it).
Nothing here exposes truth: no hidden parameters, no lost demand, no scenario ground truth.

| Source | Endpoint | Native quirks ADAPT's connector must handle |
|---|---|---|
| Meta Marketing API v25.0 | `GET /meta/v25.0/act_1029384756/insights?level=campaign\|adset\|ad&time_range={"since","until"}&time_increment=1&fields=&limit=&after=` | strings everywhere; money in **USD** (account currency; world keeps INR at the fixed sim rate); CTR in percent; purchases in `actions[]` (`offsite_conversion.fb_pixel_purchase`, platform-claimed incl. view-through); reach only at campaign level; cursor paging |
| Meta objects | `/act_{id}/campaigns`, `/adsets`, `/ads` | `daily_budget` in USD cents; status ACTIVE/PAUSED; ad creative title/body/CTA/image URL |
| Google Ads API v25 | `POST /google/v25/customers/{cid}/googleAds:search` (GAQL: customer, campaign_budget, campaign, ad_group, ad_group_ad; `segments.date` BETWEEN/=/>=/<=; LIMIT) | int64 as strings, `cost_micros` in INR micros; conversions are doubles (claimed); rows with zero impressions omitted; `nextPageToken` beyond 10,000 rows; shared budget has `explicitly_shared` |
| Store (Shopify-like 2025-07) | `GET /store/admin/api/2025-07/orders.json?created_at_min&created_at_max&since_id&limit` | tax-exclusive prices + 12% GST in `total_tax` (use `subtotal_price`); UTMs in `landing_site`; one line per order; `OTHER-ASSORTED` = unmapped products; IST timestamps |
| Store refunds | `GET /store/admin/api/2025-07/refunds.json` | separate records dated on the return day |
| Store products / finance | `/store/admin/api/2025-07/products.json`, `/finance/v1/sku_economics`, `/finance/v1/price_history` (SCD2 `valid_from`/`valid_to`) | COGS, ship cost, payment-fee % are finance master data |
| GA4 Data API | `POST /ga4/v1beta/properties/412345678:runReport` | dims date/sessionSource/sessionMedium/sessionCampaignId (`(not set)` for unpaid); metrics sessions/ecommercePurchases/purchaseRevenue; records ~95% of sessions and purchases |
| ERP | `GET /erp/v1/stock?date=`, `GET /erp/v1/receipts?from=&to=` | end-of-day snapshot with open POs (`expected_arrival`); 404 for an incomplete day |

## Creative assets
`GET /assets/creatives` = the creative_assets registry (`creative_asset_id, creative_id, asset_uri, content_type,
thumbnail_uri, format, hook, cta, headline_text, provenance=SIMULATED`); `GET /assets/creatives/{id}.svg` and
`{id}_thumb.svg` render a deterministic SVG card (category headline, hook line, CTA, format badge). Meta ads'
`creative.image_url` points here.

## Eval-only truth listener (`world/world/truth_api.py`)
Started only with `WORLD_EVAL_MODE=1` (port `WORLD_EVAL_PORT`, default 8101), bound to **127.0.0.1**, same process
as the public listener (shared store, so DuckDB's single-writer lock holds). Every route needs `X-Eval-Token`, a
per-run `secrets.token_urlsafe(32)` written to `<world_dir>/.eval_token` and deleted on shutdown; comparison is
constant-time; no OpenAPI is published. Routes: `/truth/gt_incidents`, `/truth/scenarios`, `/truth/campaigns`,
`/truth/lost_demand`. The public listener returns 404 for all of them (T37).

## TESTS (`world/tests/test_world_reporting.py`, `test_world_assets_truth.py`)
Each report reconciles exactly with the world's facts: Meta ad-level insights paged with cursors (no duplicates,
impressions/clicks/claimed conversions exact, USD spend x fx = INR spend); Google campaign- and ad-level totals
equal and equal the facts (cost micros exact); store orders paged by `since_id` equal the day's orders with GST
on top and UTMs on every paid order; refunds on their return day; GA4 totals; ERP snapshot and receipts;
complete-days-only (+ advance reveals the next day); reset restores the baseline hash; tampered truth fingerprint
refuses to load; truth paths 404.
