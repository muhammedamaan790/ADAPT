"""Canonical state (A3): core.* rebuilt from stg.* as of a logical time (spec §4, §5, §22.9).

Only rows with available_at <= as_of are used, so the canonical state at any decision time contains nothing the
brand could not yet have known. The rebuild is deterministic and idempotent (CREATE OR REPLACE), so running it
twice for the same as_of gives identical tables.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from scipy.stats import norm

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "reconcile.yaml"
UNMAPPED = "__unmapped__"


@lru_cache
def reconcile_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def ts_literal(t: datetime) -> str:
    return f"TIMESTAMP '{t.strftime('%Y-%m-%d %H:%M:%S')}'"


def d_literal(d: date) -> str:
    return f"DATE '{d.isoformat()}'"


def _quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def build_core(cur, as_of: datetime) -> dict[str, int]:
    cfg = reconcile_config()
    inv = cfg["inventory"]
    asof = ts_literal(as_of)
    today = as_of.date()
    last = today - timedelta(days=1)  # last complete day
    matured_before = d_literal(today - timedelta(days=cfg["return_window_days"]))
    rr_from = d_literal(today - timedelta(days=cfg["return_window_days"] + cfg["return_rate_window_days"]))
    warehouse = ", ".join(_quote(s) for s in cfg["warehouse_skus"])
    sep = _quote(cfg["campaign_product_set_separator"])
    z = float(norm.ppf(inv["service_level"]))

    cur.execute("CREATE SCHEMA IF NOT EXISTS core")

    # -- entities: the latest snapshot of each object observed by as_of --------------------------------------------
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE snap AS
        SELECT * FROM stg.entity_snapshots WHERE available_at <= {asof}
        QUALIFY row_number() OVER (PARTITION BY platform, entity_type, entity_id ORDER BY snapshot_date DESC) = 1
    """)
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.campaigns AS
        SELECT entity_id AS campaign_id, platform,
               CASE WHEN platform = 'meta' THEN 'meta'
                    WHEN channel_type = 'SEARCH' THEN 'google_search'
                    WHEN channel_type = 'VIDEO' THEN 'google_video'
                    ELSE platform || '_other' END AS channel_id,
               budget_id, name, status, budget_amount_inr AS current_budget_inr,
               nullif(trim(split_part(name, {sep}, 2)), '') AS product_set, snapshot_date AS observed_at
        FROM snap WHERE entity_type = 'campaign' ORDER BY campaign_id
    """)
    cur.execute("""
        CREATE OR REPLACE TABLE core.ad_sets AS
        SELECT entity_id AS adset_id, campaign_id, platform, name, status,
               json_extract_string(attributes, '$.audience') AS audience
        FROM snap WHERE entity_type = 'adset' ORDER BY adset_id
    """)
    cur.execute("""
        CREATE OR REPLACE TABLE core.ads AS
        SELECT entity_id AS ad_id, parent_id AS adset_id, campaign_id, platform, name, status,
               json_extract_string(attributes, '$.creative.title') AS headline,
               json_extract_string(attributes, '$.creative.call_to_action_type') AS cta,
               json_extract_string(attributes, '$.creative.object_type') AS format,
               json_extract_string(attributes, '$.creative.image_url') AS asset_uri
        FROM snap WHERE entity_type = 'ad' ORDER BY ad_id
    """)
    cur.execute("""
        CREATE OR REPLACE TABLE core.budgets AS
        SELECT budget_id, platform,
               CASE WHEN platform = 'google' THEN 'average_daily' ELSE 'daily' END AS delivery_semantics,
               coalesce(budget_is_shared, false) AS is_shared, budget_amount_inr AS current_amount_inr, status
        FROM snap
        WHERE (platform = 'google' AND entity_type = 'budget') OR (platform = 'meta' AND entity_type = 'campaign')
        ORDER BY budget_id
    """)
    for table, value, entity_filter in (
        ("budget_history", "budget_amount_inr",
         "(platform = 'google' AND entity_type = 'budget') OR (platform = 'meta' AND entity_type = 'campaign')"),
        ("campaign_state_history", "status", "entity_type = 'campaign'"),
    ):
        key = "coalesce(budget_id, entity_id)" if table == "budget_history" else "entity_id"
        cmp = "abs(v - prev) > 0.005" if table == "budget_history" else "v <> prev"
        cur.execute(f"""
            CREATE OR REPLACE TABLE core.{table} AS
            WITH s AS (SELECT platform, {key} AS entity_id, snapshot_date, {value} AS v
                       FROM stg.entity_snapshots WHERE available_at <= {asof} AND ({entity_filter})),
                 c AS (SELECT *, lag(v) OVER (PARTITION BY platform, entity_id ORDER BY snapshot_date) AS prev FROM s),
                 e AS (SELECT * FROM c WHERE prev IS NULL OR {cmp})
            SELECT platform, entity_id, snapshot_date AS effective_from,
                   lead(snapshot_date) OVER (PARTITION BY platform, entity_id ORDER BY snapshot_date) AS effective_to,
                   v AS value, CASE WHEN prev IS NULL THEN 'first_observed' ELSE 'observed_change' END AS source,
                   prev AS previous_value
            FROM e ORDER BY platform, entity_id, effective_from
        """)

    # -- products, costs, return rates, SCD2 prices --------------------------------------------------------------
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.skus AS
        SELECT sku, product_type AS category, title, price AS current_price_inr, sku NOT IN ({warehouse}) AS promoted
        FROM stg.products WHERE available_at <= {asof}
        QUALIFY row_number() OVER (PARTITION BY sku ORDER BY snapshot_date DESC) = 1
        ORDER BY sku
    """)
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE sku_costs AS
        SELECT sku, cogs, coalesce(ship_cost, 0) AS ship_cost, payment_fee_pct AS fee_pct
        FROM stg.sku_economics WHERE available_at <= {asof}
        QUALIFY row_number() OVER (PARTITION BY sku ORDER BY snapshot_date DESC) = 1
    """)
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE refunds AS
        SELECT order_id, line_item_id, sum(amount_ex_tax) AS realized, sum(quantity) AS qty
        FROM stg.store_refund_lines WHERE available_at <= {asof} GROUP BY 1, 2
    """)
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE lines AS SELECT * FROM stg.store_order_lines WHERE available_at <= {asof}
    """)
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.sku_return_rates AS
        WITH m AS (SELECT l.sku, sum(l.qty) AS units, sum(coalesce(r.qty, 0)) AS returned
                   FROM lines l LEFT JOIN refunds r USING (order_id, line_item_id)
                   WHERE l.date >= {rr_from} AND l.date < {matured_before} GROUP BY 1),
             brand AS (SELECT sum(returned) / nullif(sum(units), 0) AS rate FROM m)
        SELECT s.sku, coalesce(m.returned / nullif(m.units, 0), brand.rate, 0) AS return_rate,
               coalesce(m.units, 0) AS matured_units, m.units IS NULL OR m.units = 0 AS is_fallback
        FROM core.skus s LEFT JOIN m USING (sku), brand ORDER BY s.sku
    """)
    # Empirical return-lag CDF F(a) = P(refund within a days | the line is returned), from matured lines.
    window_days = int(cfg["return_window_days"])
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.return_lag_cdf AS
        WITH lag AS (SELECT r.date - l.date AS lag_days
                     FROM lines l JOIN stg.store_refund_lines r USING (order_id, line_item_id)
                     WHERE r.available_at <= {asof} AND l.date >= {rr_from} AND l.date < {matured_before}),
             n AS (SELECT count(*) AS n FROM lag)
        SELECT a.age, CASE WHEN n.n = 0 THEN 0.0
                           ELSE (SELECT count(*) FROM lag WHERE lag_days <= a.age) / n.n END AS cdf
        FROM range(0, {window_days + 1}) AS a(age), n ORDER BY a.age
    """)
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.pricing_snapshots AS
        SELECT p.sku, p.valid_from, p.valid_to, p.price, c.cogs, c.ship_cost, c.fee_pct, r.return_rate,
               p.available_at
        FROM stg.price_history p LEFT JOIN sku_costs c USING (sku) LEFT JOIN core.sku_return_rates r USING (sku)
        WHERE p.available_at <= {asof} AND p.valid_from <= {d_literal(today)}
        ORDER BY p.sku, p.valid_from
    """)

    # -- order lines with the return-accounting contract (spec §5) and last-paid-click attribution ----------------
    utm_cases = " ".join(
        f"WHEN l.utm_source IS NOT DISTINCT FROM {_quote(m['source']) if m['source'] else 'NULL'} "
        f"AND l.utm_medium IS NOT DISTINCT FROM {_quote(m['medium']) if m['medium'] else 'NULL'} "
        f"THEN {_quote(m['channel'])}" for m in cfg["utm_channels"])
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.order_items AS
        WITH base AS (
            SELECT l.order_id, l.line_item_id AS line_id, l.date AS analysis_date, l.created_at AS event_ts,
                   l.customer_id, l.is_new_customer, l.sku, l.qty, l.unit_price, l.line_discount AS discount,
                   l.unit_price * l.qty AS gross, l.line_subtotal_ex_tax AS subtotal,
                   coalesce(c.cogs, 0) * l.qty AS cogs, coalesce(c.ship_cost, 0) AS ship_cost,
                   coalesce(c.fee_pct, 0) * l.line_subtotal_ex_tax AS payment_fee,
                   coalesce(r.realized, 0) AS realized_refund,
                   -- remaining return probability after `age` days without a return:
                   --   p = r (1 - F(age)) / (1 - r F(age))   (unconditional r would double-count recent periods)
                   coalesce(rr.return_rate, 0) * (1 - coalesce(f.cdf, 0))
                     / greatest(1 - coalesce(rr.return_rate, 0) * coalesce(f.cdf, 0), 1e-9)
                     * l.line_subtotal_ex_tax AS expected_refund,
                   l.date < {matured_before} AS matured,
                   camp.campaign_id, CASE WHEN camp.campaign_id IS NOT NULL THEN l.utm_content END AS ad_id,
                   CASE WHEN camp.campaign_id IS NOT NULL THEN camp.channel_id {utm_cases}
                        ELSE 'direct' END AS channel_id,
                   l.utm_source, l.utm_medium, l.utm_campaign, l.utm_content,
                   l.available_at, l.ingested_at, l.provenance
            FROM lines l
            LEFT JOIN sku_costs c USING (sku)
            LEFT JOIN refunds r USING (order_id, line_item_id)
            LEFT JOIN core.sku_return_rates rr USING (sku)
            LEFT JOIN core.return_lag_cdf f
                   ON f.age = least(greatest({d_literal(last)} - l.date, 0), {window_days})
            LEFT JOIN core.campaigns camp ON camp.campaign_id = l.utm_campaign
        )
        SELECT *,
               CASE WHEN matured THEN realized_refund ELSE greatest(realized_refund, expected_refund) END AS refund,
               CASE WHEN matured THEN 'realized' ELSE 'liability_max' END AS refund_basis,
               subtotal - (CASE WHEN matured THEN realized_refund
                                ELSE greatest(realized_refund, expected_refund) END) AS net_revenue
        FROM base ORDER BY order_id, line_id
    """)
    cur.execute("ALTER TABLE core.order_items ADD COLUMN cba DOUBLE")
    cur.execute("UPDATE core.order_items SET cba = net_revenue - cogs - ship_cost - payment_fee")
    cur.execute("""
        CREATE OR REPLACE TABLE core.orders AS
        SELECT order_id, any_value(event_ts) AS order_ts, any_value(analysis_date) AS analysis_date,
               any_value(customer_id) AS customer_id, bool_or(is_new_customer) AS is_new_customer,
               any_value(channel_id) AS channel_id, any_value(campaign_id) AS campaign_id, 'web' AS sales_channel,
               sum(gross) AS gmv, sum(discount) AS discount, sum(refund) AS refund, sum(net_revenue) AS net_revenue,
               sum(cogs) AS cogs, sum(ship_cost) AS ship_cost, sum(payment_fee) AS payment_fee, sum(cba) AS cba,
               any_value(available_at) AS available_at
        FROM core.order_items GROUP BY order_id ORDER BY order_id
    """)
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.attribution AS
        SELECT order_id, campaign_id, any_value(ad_id) AS ad_id, 'last_paid_click' AS method, 1.0 AS weight,
               {_quote(cfg['attribution_version'])} AS attribution_version
        FROM core.order_items WHERE campaign_id IS NOT NULL GROUP BY order_id, campaign_id ORDER BY order_id
    """)

    # -- ads, GA4, reach -------------------------------------------------------------------------------------
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.ad_metrics_daily AS
        SELECT date, 'meta' AS platform, 'meta' AS channel_id, campaign_id, adset_id, ad_id, impressions, clicks,
               spend_inr, platform_conversions::DOUBLE AS platform_conversions,
               platform_conversion_value_inr AS platform_revenue_inr, available_at, provenance
        FROM stg.meta_ad_daily WHERE available_at <= {asof}
        UNION ALL
        SELECT g.date, 'google', coalesce(c.channel_id, 'google_other'), g.campaign_id, g.ad_group_id, g.ad_id,
               g.impressions, g.clicks, g.cost_inr, g.platform_conversions, g.platform_conversion_value_inr,
               g.available_at, g.provenance
        FROM stg.google_ad_daily g LEFT JOIN core.campaigns c USING (campaign_id) WHERE g.available_at <= {asof}
        ORDER BY date, platform, ad_id
    """)
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.campaign_reach_daily AS
        SELECT date, campaign_id, reach, impressions FROM stg.meta_campaign_daily WHERE available_at <= {asof}
    """)
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.ga_daily AS
        SELECT date, source, medium, campaign_id, sessions, purchases, revenue_inr, available_at
        FROM stg.ga4_daily WHERE available_at <= {asof}
    """)

    # -- inventory with configured lead time / inbound confidence and data-driven safety stock --------------------
    window = int(inv["demand_window_days"])
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.inventory_daily AS
        WITH sold AS (SELECT analysis_date AS date, sku, sum(qty) AS units FROM core.order_items GROUP BY 1, 2),
             s AS (SELECT e.date, e.sku, e.on_hand, e.reserved, e.inbound_qty, e.expected_arrival,
                          coalesce(r.quantity, 0) AS receipts, coalesce(sold.units, 0) AS units_sold, e.available_at
                   FROM stg.erp_stock_daily e
                   LEFT JOIN stg.erp_receipts r ON r.date = e.date AND r.sku = e.sku AND r.available_at <= {asof}
                   LEFT JOIN sold ON sold.date = e.date AND sold.sku = e.sku
                   WHERE e.available_at <= {asof})
        SELECT *, {_quote(inv['dc_id'])} AS dc_id, {int(inv['lead_time_days'])} AS lead_time_days,
               {float(inv['inbound_confidence'])} AS inbound_confidence,
               avg(units_sold) OVER w AS mean_daily_units,
               coalesce(stddev_samp(units_sold) OVER w, 0) AS sigma_daily_units,
               {z} * coalesce(stddev_samp(units_sold) OVER w, 0) * sqrt({int(inv['lead_time_days'])}) AS safety_stock,
               avg(units_sold) OVER w * {int(inv['lead_time_days'])}
                 + {z} * coalesce(stddev_samp(units_sold) OVER w, 0) * sqrt({int(inv['lead_time_days'])})
                 AS reorder_point
        FROM s
        WINDOW w AS (PARTITION BY sku ORDER BY date ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW)
        ORDER BY date, sku
    """)

    build_campaign_sku(cur, today)
    counts = {}
    for t in ("campaigns", "ad_sets", "ads", "budgets", "budget_history", "campaign_state_history", "skus",
              "sku_return_rates", "return_lag_cdf", "pricing_snapshots", "order_items", "orders", "attribution",
              "ad_metrics_daily", "campaign_reach_daily", "ga_daily", "inventory_daily", "campaign_sku"):
        counts[f"core.{t}"] = cur.execute(f"SELECT count(*) FROM core.{t}").fetchone()[0]
    counts["_last_complete_day"] = last.isoformat()
    return counts


def build_campaign_sku(cur, today: date) -> None:
    """campaign_sku weights (spec §4): allocation = equal share of the product set; attribution = smoothed observed
    share of the campaign's attributed net revenue over the trailing window, with __unmapped__ absorbing revenue on
    SKUs outside the product set. Weights sum to 1 per campaign; R = 0 falls back to the allocation weights."""
    cfg = reconcile_config()
    s = float(cfg["attribution_smoothing"])
    lo = d_literal(today - timedelta(days=cfg["attribution_window_days"]))
    hi = d_literal(today - timedelta(days=1))
    cur.execute(f"""
        CREATE OR REPLACE TABLE core.campaign_sku AS
        WITH ps AS (SELECT c.campaign_id, k.sku FROM core.campaigns c
                    JOIN core.skus k ON k.category = c.product_set AND k.promoted),
             n AS (SELECT c.campaign_id, count(ps.sku) AS n FROM core.campaigns c LEFT JOIN ps USING (campaign_id)
                   GROUP BY 1),
             rev AS (SELECT campaign_id, sku, sum(net_revenue) AS rev FROM core.order_items
                     WHERE campaign_id IS NOT NULL AND analysis_date BETWEEN {lo} AND {hi} GROUP BY 1, 2),
             tot AS (SELECT c.campaign_id, coalesce(sum(rev.rev), 0) AS r FROM core.campaigns c
                     LEFT JOIN rev USING (campaign_id) GROUP BY 1),
             mapped AS (SELECT ps.campaign_id, ps.sku, 1.0 / n.n AS a, coalesce(rev.rev, 0) AS r
                        FROM ps JOIN n USING (campaign_id) LEFT JOIN rev USING (campaign_id, sku)),
             unm AS (SELECT c.campaign_id, coalesce(sum(rev.rev) FILTER (WHERE ps.sku IS NULL), 0) AS r,
                            CASE WHEN n.n = 0 THEN 1.0 ELSE 0.0 END AS a
                     FROM core.campaigns c JOIN n USING (campaign_id) LEFT JOIN rev USING (campaign_id)
                     LEFT JOIN ps ON ps.campaign_id = rev.campaign_id AND ps.sku = rev.sku
                     GROUP BY c.campaign_id, n.n)
        SELECT m.campaign_id, m.sku, m.a AS allocation_weight,
               CASE WHEN t.r <= 0 THEN m.a ELSE (m.r + {s} * t.r * m.a) / (t.r * (1 + {s})) END AS attribution_weight,
               m.r AS attributed_revenue, {lo} AS active_from, {hi} AS active_to
        FROM mapped m JOIN tot t USING (campaign_id)
        UNION ALL
        SELECT u.campaign_id, {_quote(UNMAPPED)}, u.a,
               CASE WHEN t.r <= 0 THEN u.a ELSE (u.r + {s} * t.r * u.a) / (t.r * (1 + {s})) END,
               u.r, {lo}, {hi}
        FROM unm u JOIN tot t USING (campaign_id)
        ORDER BY 1, 2
    """)
