"""Structured creative prior: categorical attributes -> expected early CTR, never text/image analysis."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from adapt.decide.hashing import content_hash

FEATURES = ("format", "hook", "cta", "category", "channel", "price_band")
DDL = """
CREATE SCHEMA IF NOT EXISTS models;
CREATE TABLE IF NOT EXISTS models.creative_prior (
 version VARCHAR PRIMARY KEY, fit_ts TIMESTAMP, input_hash VARCHAR, payload JSON, metrics JSON
);
"""


def train(rows):
    """One early-seven-day CTR per creative, sorted by launch, chronological train/holdout."""
    import lightgbm as lgb

    df = pd.DataFrame(rows).sort_values(["launch_date", "creative_id"])
    if len(df) < 60 or df.creative_id.nunique() != len(df):
        return {"status": "NOT_ESTIMABLE", "reason": "at least 60 distinct early-life creatives are required"}
    split = int(len(df) * 0.7)
    before, after = df.iloc[:split], df.iloc[split:]
    if before.launch_date.max() >= after.launch_date.min():
        return {"status": "NOT_ESTIMABLE", "reason": "need disjoint launch-date training and holdout periods"}
    x_train = pd.get_dummies(before[list(FEATURES)].astype(str), dtype=float)
    x_holdout = pd.get_dummies(after[list(FEATURES)].astype(str), dtype=float).reindex(
        columns=x_train.columns, fill_value=0
    )
    target = np.asarray(df.clicks / df.impressions, dtype=float)
    if not np.isfinite(target).all() or (target < 0).any() or (target > 1).any():
        raise ValueError("invalid creative click/impression totals")
    model = lgb.train(
        {
            "objective": "regression",
            "verbosity": -1,
            "num_leaves": 7,
            "min_data_in_leaf": 5,
            "learning_rate": 0.05,
            "seed": 42,
            "num_threads": 1,
            "deterministic": True,
            "force_col_wise": True,
        },
        lgb.Dataset(x_train.to_numpy(), label=target[:split]),
        num_boost_round=100,
    )
    means = before.assign(ctr=target[:split]).groupby("category").ctr.mean().to_dict()
    global_mean = float(target[:split].mean())
    baseline = np.array([means.get(c, global_mean) for c in after.category])
    pred = np.clip(model.predict(x_holdout.to_numpy()), 0, 1)
    mae, baseline_mae = float(np.abs(pred - target[split:]).mean()), float(np.abs(baseline - target[split:]).mean())
    rho = spearmanr(pred, target[split:]).statistic if np.std(pred) and np.std(target[split:]) else 0.0
    promote = bool(mae < baseline_mae and np.isfinite(rho) and rho > 0)
    return {
        "status": "AVAILABLE",
        "kind": "LIGHTGBM" if promote else "CATEGORY_MEAN",
        "booster": model.model_to_string() if promote else None,
        "columns": list(x_train.columns),
        "means": means,
        "global_mean": global_mean,
        "domains": {f: sorted(set(before[f].astype(str))) for f in FEATURES},
        "metrics": {
            "mae": mae,
            "baseline_mae": baseline_mae,
            "spearman": float(rho),
            "train_creatives": len(before),
            "holdout_creatives": len(after),
        },
        "reason": "LightGBM beat category-mean early CTR on a disjoint holdout"
        if promote
        else "LightGBM not promoted; category-mean baseline retained",
    }


def fit(db, as_of):
    from adapt.policy.autonomy import has

    if not all(
        has(db, s, t) for s, t in (("stg", "creative_assets"), ("marts", "creative_daily"), ("core", "campaigns"))
    ):
        return {"status": "NOT_ESTIMABLE", "reason": "creative metadata/reporting unavailable"}
    rows = db.query(
        """
      WITH first_seen AS (SELECT ad_id, min(date) AS launch_date FROM marts.creative_daily GROUP BY 1),
      early AS (SELECT m.ad_id, f.launch_date, sum(m.impressions) AS impressions, sum(m.clicks) AS clicks,
                       any_value(m.campaign_id) AS campaign_id
        FROM marts.creative_daily m JOIN first_seen f USING(ad_id)
        WHERE m.date < f.launch_date + INTERVAL 7 DAY AND m.date < CAST(? AS DATE)
        GROUP BY 1,2 HAVING count(DISTINCT m.date)>=7 AND sum(m.impressions)>=1000)
      SELECT e.ad_id, e.launch_date, e.impressions, e.clicks, a.format, a.hook, a.cta,
             c.product_set, c.channel_id, CASE WHEN p.price<2000 THEN 'LOW' WHEN p.price<5000 THEN 'MID' ELSE 'HIGH' END
      FROM early e JOIN stg.creative_assets a ON a.creative_id=e.ad_id JOIN core.campaigns c USING(campaign_id)
      JOIN (SELECT category, avg(current_price_inr) AS price FROM core.skus GROUP BY 1) p ON p.category=c.product_set
      WHERE a.available_at<=? ORDER BY e.launch_date, e.ad_id
    """,
        [as_of, as_of],
    )
    keys = ("creative_id", "launch_date", "impressions", "clicks", *FEATURES)
    data = [{k: str(v) if k == "launch_date" else v for k, v in zip(keys, r, strict=True)} for r in rows]
    if not data:
        return {"status": "NOT_ESTIMABLE", "reason": "no mature seven-day creative training targets"}
    sha = content_hash(data)
    db.write(lambda cur: cur.execute(DDL))
    old = db.query("SELECT version, payload FROM models.creative_prior WHERE input_hash=?", [sha])
    if old:
        return json.loads(old[0][1]) | {"version": old[0][0]}
    result = train(data)
    version = "creative-" + sha[:16]
    db.write(
        lambda cur: cur.execute(
            "INSERT INTO models.creative_prior VALUES (?, ?, ?, ?, ?)",
            [version, as_of, sha, json.dumps(result), json.dumps(result.get("metrics", {}))],
        )
    )
    return result | {"version": version}


def score(db, attributes):
    from adapt.policy.autonomy import has

    missing = {"status": "NOT_ESTIMABLE", "score": None}
    if set(attributes) != set(FEATURES) or any(not isinstance(v, str) or not v for v in attributes.values()):
        return missing | {"explanation": "Provide all six structured attributes. Text and images are not analysed."}
    row = (
        db.query("SELECT version, payload FROM models.creative_prior ORDER BY fit_ts DESC LIMIT 1")
        if has(db, "models", "creative_prior")
        else []
    )
    if not row:
        return missing | {"explanation": "No trained structured creative prior is available yet."}
    version, raw = row[0]
    model = json.loads(raw)
    if model["status"] != "AVAILABLE" or any(attributes[f] not in model["domains"][f] for f in FEATURES):
        return missing | {"explanation": "Insufficient training data or out-of-domain attribute; no score invented."}
    if model["kind"] == "LIGHTGBM":
        import lightgbm as lgb

        vector = np.array([[float(col in {f"{k}_{v}" for k, v in attributes.items()}) for col in model["columns"]]])
        value = float(lgb.Booster(model_str=model["booster"]).predict(vector)[0])
    else:
        value = model["means"].get(attributes["category"], model["global_mean"])
    return {
        "status": "AVAILABLE",
        "score": float(np.clip(value, 0, 1)),
        "explanation": f"Expected early CTR from {model['kind']} ({version}), not a success probability. "
        f"{model['reason']}. No image, copy or headline analysis.",
    }
