"""Workspace tables written by the connectors (spec §4 raw.* + normalised staging + ops).

raw.api_pages keeps every API response page verbatim (audit, lineage, re-normalisation). stg.* holds the
normalised, typed records: money in INR, dates in the brand time zone, micros/minor units/FX/tax handled.
Every stg row carries run_id, ingested_at (wall clock), available_at (logical simulation time, spec §22.9) and
provenance. Business keys are primary keys, so re-pulling a day upserts instead of duplicating (T28).
Timestamps are naive TIMESTAMPs in the brand time zone (Asia/Kolkata).
"""

META_COLS = "run_id VARCHAR NOT NULL, ingested_at TIMESTAMP NOT NULL, available_at TIMESTAMP NOT NULL, " \
            "provenance VARCHAR NOT NULL"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS raw.api_pages (
    run_id VARCHAR NOT NULL, call_seq INTEGER NOT NULL, source VARCHAR NOT NULL, endpoint VARCHAR NOT NULL,
    request JSON NOT NULL, payload JSON NOT NULL, extraction_ts TIMESTAMP NOT NULL, provenance VARCHAR NOT NULL,
    source_schema_version VARCHAR NOT NULL, connector_version VARCHAR NOT NULL,
    source_account_id_hash VARCHAR, source_timezone VARCHAR NOT NULL, source_currency VARCHAR NOT NULL,
    PRIMARY KEY (run_id, source, call_seq)
);

CREATE TABLE IF NOT EXISTS stg.meta_ad_daily (
    date DATE NOT NULL, campaign_id VARCHAR NOT NULL, adset_id VARCHAR NOT NULL, ad_id VARCHAR NOT NULL,
    impressions BIGINT NOT NULL, clicks BIGINT NOT NULL, spend_native DOUBLE NOT NULL, currency VARCHAR NOT NULL,
    fx_rate DOUBLE NOT NULL, spend_inr DOUBLE NOT NULL, platform_conversions BIGINT NOT NULL,
    platform_conversion_value_inr DOUBLE NOT NULL, {META_COLS}, PRIMARY KEY (date, ad_id)
);
CREATE TABLE IF NOT EXISTS stg.meta_campaign_daily (
    date DATE NOT NULL, campaign_id VARCHAR NOT NULL, impressions BIGINT NOT NULL, reach BIGINT NOT NULL,
    {META_COLS}, PRIMARY KEY (date, campaign_id)
);
CREATE TABLE IF NOT EXISTS stg.google_ad_daily (
    date DATE NOT NULL, campaign_id VARCHAR NOT NULL, ad_group_id VARCHAR NOT NULL, ad_id VARCHAR NOT NULL,
    impressions BIGINT NOT NULL, clicks BIGINT NOT NULL, cost_micros BIGINT NOT NULL, cost_inr DOUBLE NOT NULL,
    platform_conversions DOUBLE NOT NULL, platform_conversion_value_inr DOUBLE NOT NULL, {META_COLS},
    PRIMARY KEY (date, ad_id)
);
CREATE TABLE IF NOT EXISTS stg.entity_snapshots (
    snapshot_date DATE NOT NULL, platform VARCHAR NOT NULL, entity_type VARCHAR NOT NULL, entity_id VARCHAR NOT NULL,
    parent_id VARCHAR, campaign_id VARCHAR, budget_id VARCHAR, name VARCHAR, status VARCHAR, channel_type VARCHAR,
    budget_amount_inr DOUBLE, budget_is_shared BOOLEAN, attributes JSON, {META_COLS},
    PRIMARY KEY (snapshot_date, platform, entity_type, entity_id)
);
CREATE TABLE IF NOT EXISTS stg.store_order_lines (
    order_id BIGINT NOT NULL, line_item_id BIGINT NOT NULL, created_at TIMESTAMP NOT NULL, date DATE NOT NULL,
    customer_id BIGINT NOT NULL, is_new_customer BOOLEAN NOT NULL, sku VARCHAR NOT NULL, qty INTEGER NOT NULL,
    unit_price DOUBLE NOT NULL, line_discount DOUBLE NOT NULL, line_subtotal_ex_tax DOUBLE NOT NULL,
    order_tax DOUBLE NOT NULL, order_total DOUBLE NOT NULL, currency VARCHAR NOT NULL, utm_source VARCHAR,
    utm_medium VARCHAR, utm_campaign VARCHAR, utm_content VARCHAR, landing_path VARCHAR, {META_COLS},
    PRIMARY KEY (order_id, line_item_id)
);
CREATE TABLE IF NOT EXISTS stg.store_refund_lines (
    refund_id BIGINT NOT NULL, line_item_id BIGINT NOT NULL, order_id BIGINT NOT NULL, created_at TIMESTAMP NOT NULL,
    date DATE NOT NULL, quantity INTEGER NOT NULL, amount_ex_tax DOUBLE NOT NULL, tax DOUBLE NOT NULL, {META_COLS},
    PRIMARY KEY (refund_id, line_item_id)
);
CREATE TABLE IF NOT EXISTS stg.products (
    sku VARCHAR NOT NULL, snapshot_date DATE NOT NULL, title VARCHAR, product_type VARCHAR, price DOUBLE NOT NULL,
    {META_COLS}, PRIMARY KEY (snapshot_date, sku)
);
CREATE TABLE IF NOT EXISTS stg.sku_economics (
    sku VARCHAR NOT NULL, snapshot_date DATE NOT NULL, cogs DOUBLE NOT NULL, ship_cost DOUBLE,
    payment_fee_pct DOUBLE NOT NULL, {META_COLS}, PRIMARY KEY (snapshot_date, sku)
);
CREATE TABLE IF NOT EXISTS stg.price_history (
    sku VARCHAR NOT NULL, valid_from DATE NOT NULL, valid_to DATE, price DOUBLE NOT NULL, {META_COLS},
    PRIMARY KEY (sku, valid_from)
);
CREATE TABLE IF NOT EXISTS stg.ga4_daily (
    date DATE NOT NULL, source VARCHAR NOT NULL, medium VARCHAR NOT NULL, campaign_id VARCHAR NOT NULL,
    sessions BIGINT NOT NULL, purchases BIGINT NOT NULL, revenue_inr DOUBLE NOT NULL, {META_COLS},
    PRIMARY KEY (date, source, medium, campaign_id)
);
CREATE TABLE IF NOT EXISTS stg.erp_stock_daily (
    date DATE NOT NULL, sku VARCHAR NOT NULL, on_hand BIGINT NOT NULL, reserved BIGINT NOT NULL,
    inbound_qty BIGINT NOT NULL, expected_arrival DATE, {META_COLS}, PRIMARY KEY (date, sku)
);
CREATE TABLE IF NOT EXISTS stg.erp_receipts (
    date DATE NOT NULL, sku VARCHAR NOT NULL, quantity BIGINT NOT NULL, {META_COLS}, PRIMARY KEY (date, sku)
);

CREATE TABLE IF NOT EXISTS stg.tiktok_ad_daily (
    date DATE NOT NULL, campaign_id VARCHAR NOT NULL, adgroup_id VARCHAR NOT NULL, ad_id VARCHAR NOT NULL,
    impressions BIGINT NOT NULL, clicks BIGINT NOT NULL, spend_native DOUBLE NOT NULL, currency VARCHAR NOT NULL,
    fx_rate DOUBLE NOT NULL, spend_inr DOUBLE NOT NULL, platform_conversions BIGINT NOT NULL,
    platform_conversion_value_inr DOUBLE NOT NULL, {META_COLS}, PRIMARY KEY (date, ad_id)
);
CREATE TABLE IF NOT EXISTS stg.amazon_sp_daily (
    date DATE NOT NULL, campaign_id VARCHAR NOT NULL, ad_group_id VARCHAR NOT NULL, ad_id VARCHAR,
    impressions BIGINT NOT NULL, clicks BIGINT NOT NULL, cost_inr DOUBLE NOT NULL, purchases7d BIGINT NOT NULL,
    sales7d_inr DOUBLE NOT NULL, {META_COLS}, PRIMARY KEY (date, ad_group_id)
);
CREATE TABLE IF NOT EXISTS stg.amazon_purchased_product (
    date DATE NOT NULL, campaign_id VARCHAR NOT NULL, ad_group_id VARCHAR NOT NULL, sku VARCHAR NOT NULL,
    purchases7d BIGINT NOT NULL, sales7d_inr DOUBLE NOT NULL, {META_COLS}, PRIMARY KEY (date, ad_group_id, sku)
);
CREATE TABLE IF NOT EXISTS stg.amazon_order_lines (
    order_id BIGINT NOT NULL, line_item_id BIGINT NOT NULL, created_at TIMESTAMP NOT NULL, date DATE NOT NULL,
    sku VARCHAR NOT NULL, qty INTEGER NOT NULL, unit_price DOUBLE NOT NULL, line_subtotal_ex_tax DOUBLE NOT NULL,
    currency VARCHAR NOT NULL, {META_COLS}, PRIMARY KEY (order_id, line_item_id)
);
CREATE TABLE IF NOT EXISTS stg.amazon_returns (
    order_id BIGINT NOT NULL, line_item_id BIGINT NOT NULL, created_at TIMESTAMP NOT NULL, date DATE NOT NULL,
    amount DOUBLE NOT NULL, {META_COLS}, PRIMARY KEY (order_id, line_item_id)
);

CREATE TABLE IF NOT EXISTS ops.connector_status (
    connector VARCHAR PRIMARY KEY, status VARCHAR NOT NULL, last_success_ts TIMESTAMP, last_failure_ts TIMESTAMP,
    last_error_code VARCHAR, last_synced_date DATE, last_run_id VARCHAR
);
CREATE TABLE IF NOT EXISTS ops.sync_runs (
    run_id VARCHAR PRIMARY KEY, started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP, world_date DATE NOT NULL,
    status VARCHAR NOT NULL, details JSON
);
"""


def ensure_schema(con) -> None:
    con.execute(DDL)
