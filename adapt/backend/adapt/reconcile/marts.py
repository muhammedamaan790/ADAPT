"""Marts (A3, spec §4): daily totals per campaign / ad set / creative / channel / SKU / brand, plus reconciliation.

Marts hold additive totals only; ratios (ROAS, CPA, CTR, ...) are computed from window totals with the metric
formulas (B1), never averaged from daily ratios. Campaign marts carry a complete calendar (zero rows on days a
campaign had no delivery), because platforms omit zero-impression rows and detection needs the zeros.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from adapt.reconcile.core import d_literal


def build_marts(cur, as_of: datetime) -> dict[str, int]:
    last = d_literal(as_of.date() - timedelta(days=1))
    cur.execute("CREATE SCHEMA IF NOT EXISTS marts")

    attributed = """
        SELECT analysis_date AS date, {key} AS id, count(DISTINCT order_id) AS attributed_orders,
               sum(qty) AS attributed_units, sum(gross) AS attributed_gross, sum(discount) AS attributed_discount,
               sum(refund) AS attributed_refund, sum(net_revenue) AS attributed_net_revenue,
               sum(cba) AS attributed_cba,
               count(DISTINCT order_id) FILTER (WHERE is_new_customer) AS attributed_new_customers
        FROM core.order_items oi {join} WHERE oi.campaign_id IS NOT NULL GROUP BY 1, 2
    """
    att_cols = ("attributed_orders", "attributed_units", "attributed_gross", "attributed_discount",
                "attributed_refund", "attributed_net_revenue", "attributed_cba", "attributed_new_customers")
    fill = ", ".join(f"coalesce(a.{c}, 0) AS {c}" for c in att_cols)

    cur.execute(f"""
        CREATE OR REPLACE TABLE marts.campaign_daily AS
        WITH ad AS (SELECT date, campaign_id, sum(impressions) AS impressions, sum(clicks) AS clicks,
                           sum(spend_inr) AS spend, sum(platform_conversions) AS platform_conversions,
                           sum(platform_revenue_inr) AS platform_revenue
                    FROM core.ad_metrics_daily GROUP BY 1, 2),
             span AS (SELECT campaign_id, min(date) AS first_date FROM ad GROUP BY 1),
             spine AS (SELECT s.campaign_id, d::DATE AS date
                       FROM span s, generate_series(s.first_date, {last}, INTERVAL 1 DAY) AS g(d)),
             a AS ({attributed.format(key="oi.campaign_id", join="")}),
             ga AS (SELECT date, campaign_id, sum(sessions) AS ga_sessions, sum(purchases) AS ga_purchases,
                           sum(revenue_inr) AS ga_revenue
                    FROM core.ga_daily WHERE campaign_id <> '(not set)' GROUP BY 1, 2)
        SELECT sp.date, sp.campaign_id, c.platform, c.channel_id, c.product_set,
               coalesce(ad.impressions, 0) AS impressions, coalesce(ad.clicks, 0) AS clicks,
               coalesce(ad.spend, 0) AS spend, coalesce(ad.platform_conversions, 0) AS platform_conversions,
               coalesce(ad.platform_revenue, 0) AS platform_revenue, r.reach,
               {fill},
               coalesce(ga.ga_sessions, 0) AS ga_sessions, coalesce(ga.ga_purchases, 0) AS ga_purchases,
               coalesce(ga.ga_revenue, 0) AS ga_revenue
        FROM spine sp
        JOIN core.campaigns c USING (campaign_id)
        LEFT JOIN ad USING (date, campaign_id)
        LEFT JOIN a ON a.date = sp.date AND a.id = sp.campaign_id
        LEFT JOIN ga USING (date, campaign_id)
        LEFT JOIN core.campaign_reach_daily r USING (date, campaign_id)
        ORDER BY sp.date, sp.campaign_id
    """)
    cur.execute(f"""
        CREATE OR REPLACE TABLE marts.adset_daily AS
        WITH ad AS (SELECT date, adset_id, any_value(campaign_id) AS campaign_id, sum(impressions) AS impressions,
                           sum(clicks) AS clicks, sum(spend_inr) AS spend,
                           sum(platform_conversions) AS platform_conversions
                    FROM core.ad_metrics_daily GROUP BY 1, 2),
             a AS ({attributed.format(key="ads.adset_id", join="JOIN core.ads ads ON ads.ad_id = oi.ad_id")})
        SELECT ad.*, {fill} FROM ad LEFT JOIN a ON a.date = ad.date AND a.id = ad.adset_id
        ORDER BY ad.date, ad.adset_id
    """)
    cur.execute(f"""
        CREATE OR REPLACE TABLE marts.creative_daily AS
        WITH ad AS (SELECT date, ad_id, any_value(adset_id) AS adset_id, any_value(campaign_id) AS campaign_id,
                           sum(impressions) AS impressions, sum(clicks) AS clicks, sum(spend_inr) AS spend,
                           sum(platform_conversions) AS platform_conversions
                    FROM core.ad_metrics_daily GROUP BY 1, 2),
             a AS ({attributed.format(key="oi.ad_id", join="")})
        SELECT ad.*, {fill} FROM ad LEFT JOIN a ON a.date = ad.date AND a.id = ad.ad_id
        ORDER BY ad.date, ad.ad_id
    """)
    cur.execute("""
        CREATE OR REPLACE TABLE marts.channel_daily AS
        WITH paid AS (SELECT date, channel_id, sum(impressions) AS impressions, sum(clicks) AS clicks,
                             sum(spend) AS spend, sum(platform_conversions) AS platform_conversions
                      FROM marts.campaign_daily GROUP BY 1, 2),
             o AS (SELECT analysis_date AS date, channel_id, count(DISTINCT order_id) AS orders, sum(qty) AS units,
                          sum(net_revenue) AS net_revenue, sum(cba) AS cba,
                          count(DISTINCT order_id) FILTER (WHERE is_new_customer) AS new_customers
                   FROM core.order_items GROUP BY 1, 2)
        SELECT coalesce(o.date, paid.date) AS date, coalesce(o.channel_id, paid.channel_id) AS channel_id,
               coalesce(paid.impressions, 0) AS impressions, coalesce(paid.clicks, 0) AS clicks,
               coalesce(paid.spend, 0) AS spend, coalesce(paid.platform_conversions, 0) AS platform_conversions,
               coalesce(o.orders, 0) AS orders, coalesce(o.units, 0) AS units,
               coalesce(o.net_revenue, 0) AS net_revenue, coalesce(o.cba, 0) AS cba,
               coalesce(o.new_customers, 0) AS new_customers
        FROM o FULL JOIN paid ON paid.date = o.date AND paid.channel_id = o.channel_id
        ORDER BY 1, 2
    """)
    cur.execute("""
        CREATE OR REPLACE TABLE marts.sku_daily AS
        WITH s AS (SELECT analysis_date AS date, sku, sum(qty) AS units, sum(gross) AS gross,
                          sum(net_revenue) AS net_revenue, sum(cba) AS cba
                   FROM core.order_items GROUP BY 1, 2)
        SELECT i.date, i.sku, k.category, coalesce(s.units, 0) AS units, coalesce(s.gross, 0) AS gross,
               coalesce(s.net_revenue, 0) AS net_revenue, coalesce(s.cba, 0) AS cba,
               i.on_hand, i.reserved, i.inbound_qty, i.expected_arrival, i.receipts, i.mean_daily_units,
               i.safety_stock, i.reorder_point, i.lead_time_days, i.inbound_confidence
        FROM core.inventory_daily i
        LEFT JOIN s USING (date, sku)
        LEFT JOIN core.skus k USING (sku)
        ORDER BY i.date, i.sku
    """)
    cur.execute("""
        CREATE OR REPLACE TABLE marts.brand_daily AS
        WITH o AS (SELECT analysis_date AS date, count(DISTINCT order_id) AS orders,
                          count(DISTINCT order_id) FILTER (WHERE is_new_customer) AS new_customers,
                          sum(qty) AS units, sum(gross) AS gmv, sum(discount) AS discount, sum(refund) AS refund,
                          sum(net_revenue) AS net_revenue, sum(cogs) AS cogs, sum(ship_cost) AS ship_cost,
                          sum(payment_fee) AS payment_fee, sum(cba) AS cba
                   FROM core.order_items GROUP BY 1),
             sp AS (SELECT date, sum(spend_inr) AS spend FROM core.ad_metrics_daily GROUP BY 1)
        SELECT o.*, coalesce(sp.spend, 0) AS spend, o.cba - coalesce(sp.spend, 0) AS caa
        FROM o LEFT JOIN sp USING (date) ORDER BY date
    """)
    cur.execute("""
        CREATE OR REPLACE TABLE marts.recon_daily AS
        WITH p AS (SELECT date, platform, sum(clicks) AS clicks, sum(spend) AS spend,
                          sum(platform_conversions) AS platform_conversions, sum(platform_revenue) AS platform_revenue,
                          sum(attributed_orders) AS store_attributed_orders,
                          sum(attributed_net_revenue) AS store_attributed_net_revenue,
                          sum(ga_sessions) AS ga_sessions, sum(ga_purchases) AS ga_purchases
                   FROM marts.campaign_daily GROUP BY 1, 2)
        SELECT * FROM p ORDER BY date, platform
    """)
    return {f"marts.{t}": cur.execute(f"SELECT count(*) FROM marts.{t}").fetchone()[0]
            for t in ("campaign_daily", "adset_daily", "creative_daily", "channel_daily", "sku_daily",
                      "brand_daily", "recon_daily")}
