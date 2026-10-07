"""Data health per source (A3, spec §4/§5): freshness x completeness x consistency -> 0-100 + GREEN/YELLOW/RED.

freshness    = 1 if the newest complete day's report is within the source SLA, linear to 0 at 3x SLA (logical time)
completeness = 1 - (null-key rate + clip(|rows on the newest day / trailing mean - 1| - band, 0, 1))
consistency  = share of contract checks passed (ranges, referential integrity, currency)
A hard failure (connector FAILED on its last run, negative spend, wrong currency, no data at all) forces RED.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from adapt.reconcile.core import d_literal, ts_literal

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "health.yaml"

# source -> (stg table holding its dated facts, date column, null-key expression)
SOURCE_FACTS = {
    "meta_ads": ("stg.meta_ad_daily", "date", "ad_id IS NULL OR campaign_id IS NULL"),
    "google_ads": ("stg.google_ad_daily", "date", "ad_id IS NULL OR campaign_id IS NULL"),
    "store": ("stg.store_order_lines", "date", "sku IS NULL OR customer_id IS NULL"),
    "ga4": ("stg.ga4_daily", "date", "source IS NULL"),
    "erp": ("stg.erp_stock_daily", "date", "sku IS NULL"),
    "finance": ("stg.sku_economics", "snapshot_date", "sku IS NULL"),
    "tiktok_ads": ("stg.tiktok_ad_daily", "date", "ad_id IS NULL OR campaign_id IS NULL"),
    "amazon_ads": ("stg.amazon_sp_daily", "date", "ad_group_id IS NULL OR campaign_id IS NULL"),
    "amazon_marketplace": ("stg.amazon_order_lines", "date", "sku IS NULL"),
}
OPTIONAL = {"tiktok_ads", "amazon_ads", "amazon_marketplace"}  # Stage 2: absent from a world -> not reported


@lru_cache
def health_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def band(score: float, hard_failure: bool) -> str:
    b = health_config()["bands"]
    if hard_failure:
        return "RED"
    return "GREEN" if score >= b["green"] else "YELLOW" if score >= b["yellow"] else "RED"


def freshness(age_hours: float | None, sla_hours: float) -> float:
    if age_hours is None:
        return 0.0
    if age_hours <= sla_hours:
        return 1.0
    return max(0.0, 1.0 - (age_hours - sla_hours) / (2 * sla_hours))


def _checks(cur, source: str, asof: str) -> list[tuple[str, bool, str]]:
    """(check id, passed, detail) contract checks on the data visible at as_of."""
    out: list[tuple[str, bool, str]] = []

    def one(sql: str) -> float:
        return cur.execute(sql).fetchone()[0] or 0

    if source in ("meta_ads", "google_ads", "tiktok_ads", "amazon_ads"):
        t = SOURCE_FACTS[source][0]
        spend = "cost_inr" if source in ("google_ads", "amazon_ads") else "spend_inr"
        bad = one(f"SELECT count(*) FROM {t} WHERE available_at <= {asof} AND ({spend} < 0 OR clicks > impressions "
                  "OR impressions < 0)")
        out.append(("ads.ranges", bad == 0, f"{bad} rows with negative spend or clicks > impressions"))
        platform = source.split("_")[0]
        orphans = one(f"SELECT count(DISTINCT f.campaign_id) FROM {t} f WHERE f.available_at <= {asof} AND NOT EXISTS "
                      f"(SELECT 1 FROM stg.entity_snapshots s WHERE s.platform = '{platform}' "
                      "AND s.entity_type = 'campaign' AND s.entity_id = f.campaign_id)")
        out.append(("ads.referential", orphans == 0, f"{orphans} campaigns in reports but not in the account"))
        if source in ("meta_ads", "tiktok_ads"):
            cur_bad = one(f"SELECT count(*) FROM {t} WHERE available_at <= {asof} AND currency <> 'USD'")
            out.append(("ads.currency", cur_bad == 0, f"{cur_bad} rows in an unexpected currency"))
    elif source == "store":
        bad = one(f"SELECT count(*) FROM stg.store_order_lines WHERE available_at <= {asof} AND "
                  "(unit_price < 0 OR qty <= 0 OR line_subtotal_ex_tax < 0)")
        out.append(("store.ranges", bad == 0, f"{bad} lines with negative price/qty"))
        cur_bad = one(f"SELECT count(*) FROM stg.store_order_lines WHERE available_at <= {asof} AND currency <> 'INR'")
        out.append(("store.currency", cur_bad == 0, f"{cur_bad} lines in an unexpected currency"))
        unknown = one(f"SELECT count(DISTINCT utm_campaign) FROM stg.store_order_lines l WHERE available_at <= {asof} "
                      "AND utm_campaign IS NOT NULL AND NOT EXISTS (SELECT 1 FROM stg.entity_snapshots s "
                      "WHERE s.entity_type = 'campaign' AND s.entity_id = l.utm_campaign)")
        out.append(("store.utm_referential", unknown == 0, f"{unknown} UTM campaigns unknown to the ad accounts"))
        orphan_ref = one(f"SELECT count(*) FROM stg.store_refund_lines r WHERE available_at <= {asof} AND NOT EXISTS "
                         "(SELECT 1 FROM stg.store_order_lines l WHERE l.order_id = r.order_id)")
        out.append(("store.refund_referential", orphan_ref == 0, f"{orphan_ref} refunds for unknown orders"))
    elif source == "ga4":
        bad = one(f"SELECT count(*) FROM stg.ga4_daily WHERE available_at <= {asof} "
                  "AND (sessions < 0 OR purchases < 0)")
        out.append(("ga4.ranges", bad == 0, f"{bad} negative rows"))
    elif source == "erp":
        bad = one(f"SELECT count(*) FROM stg.erp_stock_daily WHERE available_at <= {asof} AND "
                  "(on_hand < 0 OR reserved < 0 OR reserved > on_hand OR inbound_qty < 0)")
        out.append(("erp.ranges", bad == 0, f"{bad} snapshots with negative or inconsistent stock"))
    elif source == "finance":
        bad = one(f"SELECT count(*) FROM stg.sku_economics WHERE available_at <= {asof} AND "
                  "(cogs < 0 OR payment_fee_pct < 0 OR payment_fee_pct > 0.2)")
        out.append(("finance.ranges", bad == 0, f"{bad} SKUs with implausible costs"))
    return out


def build_health(cur, as_of: datetime) -> list[dict]:
    cfg = health_config()
    asof = ts_literal(as_of)
    w = cfg["weights"]
    rows = []
    status = {r[0]: r[1] for r in cur.execute("SELECT connector, status FROM ops.connector_status").fetchall()}
    for source, (table, date_col, null_key) in SOURCE_FACTS.items():
        if source in OPTIONAL and not cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0] \
                and status.get(source) != "FAILED":
            continue  # NOT_CONFIGURED: this world / workspace has no such channel
        newest = cur.execute(f"SELECT max({date_col}), max(available_at) FROM {table} "
                             f"WHERE available_at <= {asof}").fetchone()
        hard: list[str] = []
        if newest[0] is None:
            hard.append("NO_DATA")
            age = None
        else:
            newest_avail = cur.execute(f"SELECT max(available_at) FROM {table} WHERE {date_col} = "
                                       f"{d_literal(newest[0])} AND available_at <= {asof}").fetchone()[0]
            age = (as_of - newest_avail).total_seconds() / 3600
        if status.get(source) == "FAILED":
            hard.append("CONNECTOR_FAILED")
        f = freshness(age, cfg["sla_hours"][source])

        comp = 1.0
        if newest[0] is not None:
            n_new, n_null = cur.execute(f"SELECT count(*), count(*) FILTER (WHERE {null_key}) FROM {table} "
                                        f"WHERE {date_col} = {d_literal(newest[0])} AND available_at <= {asof}"
                                        ).fetchone()
            trailing = cur.execute(
                f"SELECT avg(n) FROM (SELECT {date_col}, count(*) AS n FROM {table} WHERE available_at <= {asof} "
                f"AND {date_col} < {d_literal(newest[0])} AND {date_col} >= "
                f"{d_literal(newest[0] - timedelta(days=cfg['completeness']['trailing_days']))} GROUP BY 1)"
            ).fetchone()[0]
            null_rate = n_null / n_new if n_new else 1.0
            deviation = abs(n_new / trailing - 1) if trailing else 0.0
            excess = min(max(deviation - cfg["completeness"]["row_count_band"], 0.0), 1.0)
            comp = max(0.0, 1.0 - null_rate - excess)

        checks = _checks(cur, source, asof)
        cons = sum(1 for _, ok, _ in checks if ok) / len(checks) if checks else 1.0
        if any(cid.endswith(".currency") and not ok for cid, ok, _ in checks):
            hard.append("CURRENCY")
        if any(cid == "ads.ranges" and not ok for cid, ok, _ in checks):
            hard.append("NEGATIVE_SPEND_OR_RANGE")
        score = round(100 * (w["freshness"] * f + w["completeness"] * comp + w["consistency"] * cons), 1)
        rows.append({
            "as_of": as_of, "source": source, "newest_date": newest[0], "age_hours": None if age is None else
            round(age, 2), "freshness": round(f, 4), "completeness": round(comp, 4), "consistency": round(cons, 4),
            "score": score, "status": band(score, bool(hard)), "hard_failures": json.dumps(hard),
            "checks": json.dumps([{"id": c, "passed": ok, "detail": d} for c, ok, d in checks]),
        })
    cur.execute("""CREATE TABLE IF NOT EXISTS ops.data_health (
        as_of TIMESTAMP NOT NULL, source VARCHAR NOT NULL, newest_date DATE, age_hours DOUBLE, freshness DOUBLE,
        completeness DOUBLE, consistency DOUBLE, score DOUBLE, status VARCHAR, hard_failures JSON, checks JSON,
        PRIMARY KEY (as_of, source))""")
    cur.executemany("INSERT OR REPLACE INTO ops.data_health VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [list(r.values()) for r in rows])
    return rows
