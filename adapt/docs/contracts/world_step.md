# Contract: world.step and seeding (A4)

Modules: `world/world/step.py` (one simulated day, inside the world-state transaction of `advance`),
`world/world/seed.py` (truth + history). CLI: `uv run python -m world.seed --seed 42 [--overwrite]`.

## Day order (all randomness keyed by (seed, day, entity, purpose); call order never matters)
1. **Receipts**: open purchase orders whose arrival day has come are added to on-hand.
2. **Paid**, per campaign: spend = budget x budget share x pacing U(0.95, 1.0); CPM = response-model CPM x daily
   LogNormal noise x weekday profile; impressions = floor(1000 spend / CPM); each prospect is served one live
   creative (delivery weighted by creative quality = CTR multiplier x wear-out) and clicks at
   base CTR x quality x freq^-gamma x noise; then the conditional funnel (`funnel.py`). p_buy includes
   demand^0.5, noise and the category price factor (sum of mix x (price / base price)^elasticity).
   Purchases land on the category's SKUs by mix, or on the unmapped warehouse with the cross-sell share.
3. **Unpaid**: Email / Organic orders per category ~ Poisson(rate x demand index x price factor); the warehouse
   (non-promoted products) ~ Poisson(mean x store-wide index), split Email/Organic by the data's shares.
4. **Stock rationing**: per SKU, purchase attempts are filled in a fixed random order (keyed uniform) until
   on-hand runs out; the rest are lost (`fact_lost_demand`, world-internal, eval only). Warehouse never rationed.
5. **Orders** (one unit each; TheLook lines become orders), returns (`return_day` = day + U(3, 21)),
   new-customer flag by channel, platform-claimed conversions (click-through + Poisson view-through), GA4
   (records 95% of sessions and purchases), ERP (s, Q): reorder when on-hand <= trailing 28-day sales x
   (lead + 5 days), quantity = 30 days of trailing sales, arriving after the SKU's lead time; creative wear.

## State and facts (sim_state.duckdb)
State: `budgets_state`, `campaign_status`, `creative_state` (cumulative impressions), `price_state`,
`inventory_state`. Facts ("platform databases" the reporting endpoints render): `fact_ad_campaign_daily`
(impressions, reach, clicks, sessions, purchase attempts, filled orders, spend, CPM), `fact_ad_creative_daily`
(platform-claimed conversions), `fact_orders`, `fact_ga_daily`, `fact_erp_daily`, `fact_lost_demand`.

## Operations
`reset {seed, start_day}` clears all state, `init_world` (prices, creative wear, stock = 35 days of expected
demand), `advance {days}` (simulates [day, day + n)), `set_budget`, `set_inventory {sku, on_hand,
cancel_inbound}` (scenario S3), `set_fault`, `platform_set_budget`.

## Seeding
reset to day -365 -> init_world -> for each world month: the history manager's budgets (`set_budget`, actor
`history-manager`) -> `advance` through the month. Request IDs are deterministic, so the same seed always
gives the same semantic state hash, and replaying the log reproduces it.

## TESTS (`world/tests/test_world_step.py`)
History complete + manager edits logged; funnel invariants on the daily facts (clicks <= impressions,
sessions <= clicks, purchases <= sessions, orders <= purchases, creative rows sum to campaign rows, every paid
order from a campaign-day with spend); inventory conservation (on_hand = prev + receipts - sold, never
negative, sold = orders); paid volume calibrated within 15%; seeding reproducible; log replay reproduces the
state (T55); common random numbers: a budget change on one campaign leaves other categories' campaigns
identical (T35/T46); stockout loses demand and triggers a reorder; wear-out halves per half-life; platforms
over-claim (Meta > Google > 1) and GA captures ~95%.
