"""Run level-1 diagnosis for the open incidents (B3): funnel decomposition + rate/mix drill-down, persisted in
intel.decompositions. Windows: post = the incident window, pre = the 28 days before it (window totals only)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from adapt.diagnose.decomposition import drilldown, roas_decomposition
from adapt.diagnose.drivers import rank
from adapt.diagnose.evidence import MODULES, Incident

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
CREATE TABLE IF NOT EXISTS intel.evidence (
    evidence_id VARCHAR NOT NULL, anomaly_id VARCHAR NOT NULL, as_of TIMESTAMP NOT NULL, module VARCHAR NOT NULL,
    status VARCHAR NOT NULL, score DOUBLE NOT NULL, levers JSON, "values" JSON, reason VARCHAR,
    PRIMARY KEY (evidence_id, as_of)
);
CREATE TABLE IF NOT EXISTS intel.diagnoses (
    anomaly_id VARCHAR NOT NULL, as_of TIMESTAMP NOT NULL, top_driver VARCHAR, top_level VARCHAR NOT NULL,
    method VARCHAR NOT NULL, ranking JSON NOT NULL, PRIMARY KEY (anomaly_id, as_of)
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


def decompose(db, inc: Incident) -> tuple[dict, dict | None]:
    """Funnel decomposition + drill-down for one incident (reads only)."""
    ids = inc.campaign_ids
    with db.read() as cur:
        funnel = roas_decomposition(_totals(cur, ids, inc.pre_start, inc.pre_end),
                                    _totals(cur, ids, inc.post_start, inc.post_end))
        dd = None
        if inc.metric in DRILL:
            num, den, scale = DRILL[inc.metric]
            dims = {dim: (_segments(cur, table, key, num, den, ids, inc.pre_start, inc.pre_end),
                          _segments(cur, table, key, num, den, ids, inc.post_start, inc.post_end))
                    for dim, table, key in (("ad_set", "marts.adset_daily", "adset_id"),
                                            ("creative", "marts.creative_daily", "ad_id"))}
            dd = drilldown(dims, scale)
        elif inc.metric == "AOV":
            dd = drilldown({"sku": (_sku_segments(cur, ids, inc.pre_start, inc.pre_end),
                                    _sku_segments(cur, ids, inc.post_start, inc.post_end))})
    dd_json = None if dd is None else {
        "dimension": dd.dimension, "explained_share": dd.explained_share, "parent_delta": dd.parent_delta,
        "segments": [{**asdict(s), "combined": s.combined} for s in dd.segments]}
    return asdict(funnel), dd_json


def diagnose_incident(db, inc: Incident) -> dict:
    """Level 1 (exact accounting) + level 2 (evidence modules) for one incident, ranked (reads only)."""
    funnel, dd = decompose(db, inc)
    evidence = [module(db, inc) for module in MODULES.values()]
    ranking = rank(evidence, inc.metric, funnel)
    return {"funnel": funnel, "drilldown": dd, "evidence": evidence, "ranking": ranking}


def incident_from_row(aid, scope, ids_json, platform, metric, lo, hi) -> Incident:
    ids = json.loads(ids_json)
    if scope == "creative":
        return Incident(aid, platform, [ids[0]], ids[1], metric, lo, hi)
    return Incident(aid, platform, ids, None, metric, lo, hi)


def run_diagnosis(db, as_of: datetime) -> dict:
    db.write(lambda cur: cur.execute(DDL))
    rows = db.query("""SELECT anomaly_id, scope, entity_ids, platform, metric, window_start, window_end
                       FROM intel.anomalies WHERE is_incident AND status <> 'resolved' AND last_detected_at = ?""",
                    [as_of])
    results = [(incident_from_row(*r), None) for r in rows]
    results = [(inc, diagnose_incident(db, inc)) for inc, _ in results]

    def persist(cur) -> dict:
        for inc, d in results:
            cur.execute("INSERT OR REPLACE INTO intel.decompositions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [inc.anomaly_id, as_of, inc.pre_start, inc.pre_end, inc.post_start, inc.post_end,
                         json.dumps(d["funnel"]), json.dumps(d["drilldown"])])
            for e in d["evidence"]:
                cur.execute("INSERT OR REPLACE INTO intel.evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [f"EVD-{inc.anomaly_id}-{e.module}", inc.anomaly_id, as_of, e.module, e.status, e.score,
                             json.dumps(e.levers), json.dumps(e.values, default=str), e.reason])
            r = d["ranking"]
            cur.execute("INSERT OR REPLACE INTO intel.diagnoses VALUES (?, ?, ?, ?, ?, ?)",
                        [inc.anomaly_id, as_of, r["top_driver"], r["top_level"], r["method"], json.dumps(r)])
        return {"decomposed": len(results), "diagnosed": len(results),
                "top_drivers": {inc.anomaly_id: d["ranking"]["top_driver"] for inc, d in results}}

    return db.write(persist)
