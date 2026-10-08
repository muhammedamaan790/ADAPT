"""CVR prior (Stage 3, spec §7.3 "CVR", §22.2): Beta-Binomial per SKU x channel with an empirical-Bayes category prior.

Data (window totals): purchases = attributed order lines of the SKU on the channel's campaigns; clicks = the channel's
campaign clicks apportioned to SKUs by the feed allocation weight (core.campaign_sku). There is no click-level SKU in
the sources, so the denominator is an apportioned estimate (labelled as such).

Prior per (category, channel), method of moments over the cells with >= 50 clicks:
  mu = click-weighted mean CVR; sigma^2 = click-weighted variance of the cell CVRs - mu(1-mu) K / N (K cells, N clicks;
  an approximation that treats mu as known), floored at 1e-6; kappa = mu(1-mu)/sigma^2 - 1 clamped to [2, 1000];
  alpha0 = mu kappa, beta0 = (1-mu) kappa. Fallbacks: mu in {0, 1} or K < 5 -> the channel-level prior; then Beta(1, 1).
Posterior Beta(alpha0 + purchases, beta0 + clicks - purchases): a true conjugate posterior.
Acceptance (§22.2): fitted on the 28 days before the holdout, the 80% Beta-Binomial posterior PREDICTIVE interval of
holdout purchases (given holdout clicks) covers the observed holdout purchases in 70-90% of cells. Used for SKU mix and
creative scoring evidence only; never multiplied onto curve revenue.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
from scipy.stats import beta as beta_dist
from scipy.stats import betabinom

MIN_CLICKS = 50
MIN_CELLS = 5
WINDOW = 28
COVERAGE_BAND = (0.70, 0.90)

DDL = """
CREATE SCHEMA IF NOT EXISTS models;
CREATE TABLE IF NOT EXISTS models.cvr_prior (
    fit_ts TIMESTAMP NOT NULL, sku VARCHAR NOT NULL, channel VARCHAR NOT NULL, category VARCHAR, clicks DOUBLE,
    purchases DOUBLE, alpha DOUBLE, beta DOUBLE, mean DOUBLE, lo80 DOUBLE, hi80 DOUBLE, prior_level VARCHAR,
    PRIMARY KEY (fit_ts, sku, channel)
);
"""


def cells(db, lo, hi) -> list[dict]:
    """(sku, channel, category, clicks, purchases) over [lo, hi]."""
    rows = db.query("""
        WITH clk AS (
            SELECT m.platform AS channel, cs.sku, sum(m.clicks * cs.allocation_weight) AS clicks
            FROM core.ad_metrics_daily m JOIN core.campaign_sku cs USING (campaign_id)
            WHERE m.date BETWEEN ? AND ? GROUP BY 1, 2),
        buy AS (
            SELECT c.platform AS channel, oi.sku, count(*) AS purchases
            FROM core.order_items oi JOIN core.campaigns c USING (campaign_id)
            WHERE oi.analysis_date BETWEEN ? AND ? GROUP BY 1, 2)
        SELECT clk.channel, clk.sku, k.category, clk.clicks, coalesce(buy.purchases, 0)
        FROM clk LEFT JOIN buy USING (channel, sku) JOIN core.skus k USING (sku)
        WHERE clk.clicks > 0 ORDER BY 1, 2""", [lo, hi, lo, hi])
    return [{"channel": c, "sku": s, "category": cat, "clicks": float(n), "purchases": float(min(p, n))}
            for c, s, cat, n, p in rows]


def mom_prior(group: list[dict]) -> tuple[float, float] | None:
    q = [g for g in group if g["clicks"] >= MIN_CLICKS]
    if len(q) < MIN_CELLS:
        return None
    n = np.array([g["clicks"] for g in q])
    r = np.array([g["purchases"] / g["clicks"] for g in q])
    N, K = float(n.sum()), len(q)
    mu = float((n * r).sum() / N)
    if mu <= 0.0 or mu >= 1.0:
        return None
    var = float((n * (r - mu) ** 2).sum() / N) - mu * (1 - mu) * K / N
    var = max(var, 1e-6)
    kappa = float(np.clip(mu * (1 - mu) / var - 1, 2.0, 1000.0))
    return mu * kappa, (1 - mu) * kappa


def priors(data: list[dict]) -> dict:
    """{(category, channel): (a0, b0, level)} with the channel-level and Beta(1, 1) fallbacks."""
    by_channel: dict[str, list[dict]] = {}
    by_cat: dict[tuple, list[dict]] = {}
    for d in data:
        by_channel.setdefault(d["channel"], []).append(d)
        by_cat.setdefault((d["category"], d["channel"]), []).append(d)
    chan = {c: mom_prior(g) for c, g in by_channel.items()}
    out = {}
    for key, g in by_cat.items():
        p = mom_prior(g)
        if p is not None:
            out[key] = (*p, "category")
        elif chan.get(key[1]) is not None:
            out[key] = (*chan[key[1]], "channel")
        else:
            out[key] = (1.0, 1.0, "uniform")
    return out


def coverage(train: list[dict], hold: list[dict]) -> dict:
    """Share of holdout cells whose purchases fall inside the 80% posterior predictive interval."""
    pr = priors(train)
    post = {(d["sku"], d["channel"]): d for d in train}
    hits = n = 0
    for h in hold:
        key = (h["sku"], h["channel"])
        if key not in post or h["clicks"] < 1:
            continue
        a0, b0, _ = pr[(post[key]["category"], post[key]["channel"])]
        a, b = a0 + post[key]["purchases"], b0 + post[key]["clicks"] - post[key]["purchases"]
        m = int(round(h["clicks"]))
        lo, hi = betabinom.ppf(0.10, m, a, b), betabinom.ppf(0.90, m, a, b)
        hits += lo <= h["purchases"] <= hi
        n += 1
    cov = float(hits / n) if n else None
    return {"cells": int(n), "coverage_80": cov,
            "passed": bool(cov is not None and COVERAGE_BAND[0] <= cov <= COVERAGE_BAND[1])}


def fit_cvr(db, as_of: datetime) -> dict:
    """Fit on the last 28 days, accept on the 28 days before (posterior predictive coverage); persist the posteriors."""
    end = (as_of - timedelta(days=1)).date()
    hold = cells(db, end - timedelta(days=WINDOW - 1), end)
    train = cells(db, end - timedelta(days=2 * WINDOW - 1), end - timedelta(days=WINDOW))
    acc = coverage(train, hold)
    pr = priors(hold)
    rows = []
    for d in hold:
        a0, b0, level = pr[(d["category"], d["channel"])]
        a, b = a0 + d["purchases"], b0 + d["clicks"] - d["purchases"]
        rows.append([as_of, d["sku"], d["channel"], d["category"], d["clicks"], d["purchases"], a, b, a / (a + b),
                     float(beta_dist.ppf(0.10, a, b)), float(beta_dist.ppf(0.90, a, b)), level])

    def work(cur):
        cur.execute(DDL)
        cur.execute("DELETE FROM models.cvr_prior WHERE fit_ts = ?", [as_of])
        if rows:
            cur.executemany("INSERT INTO models.cvr_prior VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    db.write(work)
    from adapt.learn import governance

    version = f"cvr-{as_of:%Y%m%dT%H%M}"
    decision = governance.consider(db, "cvr_prior", version, as_of, kind="BETA_BINOMIAL_EB", artifact_sha256=None,
                                   feature_hash="beta-binomial-mom-v1", validation=acc, baseline=None,
                                   criteria={"c_coverage": bool(acc["passed"])},
                                   spec={"min_clicks": MIN_CLICKS, "min_cells": MIN_CELLS, "window_days": WINDOW})
    return {"status": "OK", "cells": len(rows), "acceptance": acc, "promotion": decision["reason"],
            "note": json.dumps({"clicks": "apportioned by feed allocation weight"})}


def posterior(db, sku: str, channel: str) -> dict | None:
    rows = db.query("""SELECT mean, lo80, hi80, clicks, purchases, prior_level FROM models.cvr_prior
                       WHERE sku = ? AND channel = ? ORDER BY fit_ts DESC LIMIT 1""", [sku, channel]) \
        if db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'models' "
                    "AND table_name = 'cvr_prior'") else []
    if not rows:
        return None
    m, lo, hi, n, p, level = rows[0]
    return {"mean": m, "lo80": lo, "hi80": hi, "clicks": n, "purchases": p, "prior_level": level}
