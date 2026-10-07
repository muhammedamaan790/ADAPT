"""Run level-1 diagnosis for the open incidents (B3): funnel decomposition + rate/mix drill-down, persisted in
intel.decompositions. Windows: post = the incident window, pre = the 28 days before it (window totals only)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta

from adapt.diagnose.decomposition import drilldown, roas_decomposition

# metric -> (numerator column, denominator column, scale) for the drill-down; AOV drills by SKU from order lines
DRILL = {
    "ROAS": ("attributed_net_revenue", "spend", 1.0), "POAS": ("attributed_cba", "spend", 1.0),
    "CPA": ("spend", "attributed_orders", 1.0), "CTR": ("clicks", "impressions", 1.0),
    "CPM": ("spend", "impressions", 1000.0), "CVR": ("attributed_orders", "clicks", 1.0),
}

DDL = """
CREATE SCHEMA IF NOT EXISTS intel;
CREATE TABLE IF NOT EXISTS intel.decompositions (
    anomaly_id VARCHAR NOT NULL, as_of TIMESTAMP NOT NULL, pre_start DATE, pre_end DATE, post_start DATE,
    post_end DATE, funnel JSON, drilldown JSON, PRIMARY KEY (anomaly_id, as_of)
);
"""


def _totals(cur, ids: list[str], lo, hi) -> dict[str, float]:
    marks = ", ".join("?" * len(ids))
    r = cur.execute(f"""SELECT sum(impressions), sum(clicks), sum(attributed_orders), sum(attributed_net_revenue),
                               sum(spend) FROM marts.campaign_daily
                        WHERE campaign_id IN ({marks}) AND date BETWEEN ? AND ?""", [*ids, lo, hi]).fetchone()
    return dict(zip(("impressions", "clicks", "orders", "revenue", "spend"), (float(v or 0) for v in r), strict=True))


def _segments(cur, table: str, key: str, num: str, den: str, ids: list[str], lo, hi) -> dict[str, tuple[float, float]]:
    marks = ", ".join("?" * len(ids))
    rows = cur.execute(f"""SELECT {key}, sum({den}), sum({num}) FROM {table}
                           WHERE campaign_id IN ({marks}) AND date BETWEEN ? AND ? GROUP BY 1""",
                       [*ids, lo, hi]).fetchall()
    return {str(k): (float(d or 0), float(n or 0)) for k, d, n in rows}


def _sku_segments(cur, ids: list[str], lo, hi) -> dict[str, tuple[float, float]]:
    marks = ", ".join("?" * len(ids))
    rows = cur.execute(f"""SELECT sku, count(DISTINCT order_id), sum(net_revenue) FROM core.order_items
                           WHERE campaign_id IN ({marks}) AND analysis_date BETWEEN ? AND ? GROUP BY 1""",
                       [*ids, lo, hi]).fetchall()
    return {s: (float(o), float(r or 0)) for s, o, r in rows}


def run_diagnosis(db, as_of: datetime) -> dict:
    def work(cur) -> dict:
        cur.execute(DDL)
        incidents = cur.execute("""SELECT anomaly_id, entity_ids, metric, window_start, window_end
                                   FROM intel.anomalies WHERE is_incident AND status <> 'resolved'
                                   AND last_detected_at = ?""", [as_of]).fetchall()
        done = 0
        for aid, ids_json, metric, post_lo, post_hi in incidents:
            ids = json.loads(ids_json)
            pre_hi = post_lo - timedelta(days=1)
            pre_lo = post_lo - timedelta(days=28)
            funnel = roas_decomposition(_totals(cur, ids, pre_lo, pre_hi), _totals(cur, ids, post_lo, post_hi))
            dims: dict = {}
            if metric in DRILL:
                num, den, scale = DRILL[metric]
                for dim, table, key in (("ad_set", "marts.adset_daily", "adset_id"),
                                        ("creative", "marts.creative_daily", "ad_id")):
                    dims[dim] = (_segments(cur, table, key, num, den, ids, pre_lo, pre_hi),
                                 _segments(cur, table, key, num, den, ids, post_lo, post_hi))
                dd = drilldown(dims, scale)
            elif metric == "AOV":
                dd = drilldown({"sku": (_sku_segments(cur, ids, pre_lo, pre_hi),
                                        _sku_segments(cur, ids, post_lo, post_hi))})
            else:
                dd = None
            dd_json = None if dd is None else {
                "dimension": dd.dimension, "explained_share": dd.explained_share, "parent_delta": dd.parent_delta,
                "segments": [{**asdict(s), "combined": s.combined} for s in dd.segments]}
            cur.execute("INSERT OR REPLACE INTO intel.decompositions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [aid, as_of, pre_lo, pre_hi, post_lo, post_hi, json.dumps(asdict(funnel)),
                         json.dumps(dd_json)])
            done += 1
        return {"decomposed": done}

    return db.write(work)
