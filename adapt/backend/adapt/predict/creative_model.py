"""Structured creative prior (Stage 3, spec §7.3, §22.2): expected EARLY CTR of a creative from its structured
attributes, before spend. Not "creative intelligence": it reads no image or text content, and the UI says so.

Data: one row per creative with >= 5,000 impressions in its first 7 delivering days; target = early CTR (clicks /
impressions over those days); features = format and hook (the ad name "<format> · <hook>"), CTA (Meta objects; else
"unknown"), category (the campaign's product set) and channel (meta / google_search / google_video / ...). Price band is
not attributable to a creative in the sources, so it is left out (stated).
Model: LightGBM regression on logit(early CTR) over one-hot attributes. Baseline: the category-mean early CTR.
Acceptance (§22.2, layered rule (a)): on the holdout (the latest 30% of creatives by launch date) the model's Spearman
correlation with the observed early CTR must beat the baseline's; otherwise the prior is NOT promoted and the score
endpoint answers NOT_ESTIMABLE (the feature is hidden, never a fabricated score).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

import numpy as np
from scipy.stats import spearmanr

MIN_IMPRESSIONS = 5000
EARLY_DAYS = 7
HOLDOUT_SHARE = 0.30
FEATURES = ("format", "hook", "cta", "category", "channel")
PARAMS = {"objective": "regression", "learning_rate": 0.05, "num_leaves": 7, "min_data_in_leaf": 8,
          "lambda_l2": 1.0, "verbose": -1, "seed": 20261005, "deterministic": True, "num_threads": 1}
ROUNDS = 150


def creatives(db) -> list[dict]:
    rows = db.query(f"""
        WITH f AS (SELECT ad_id, min(date) AS d0 FROM marts.creative_daily WHERE impressions > 0 GROUP BY 1),
             e AS (SELECT c.ad_id, any_value(f.d0) AS d0, sum(c.impressions) AS imp, sum(c.clicks) AS clk
                   FROM marts.creative_daily c JOIN f USING (ad_id)
                   WHERE c.date < f.d0 + {EARLY_DAYS} GROUP BY 1)
        SELECT a.ad_id, a.name, a.cta, a.platform, k.product_set, k.channel_id, e.d0, e.imp, e.clk
        FROM core.ads a JOIN e USING (ad_id) JOIN core.campaigns k USING (campaign_id)
        WHERE e.imp >= {MIN_IMPRESSIONS} ORDER BY e.d0, a.ad_id""")
    out = []
    for ad_id, name, cta, platform, cat, channel, d0, imp, clk in rows:
        parts = [p.strip() for p in re.split(r"\s*·\s*", name or "")]
        out.append({"ad_id": ad_id, "format": (parts[0] if parts else "unknown").lower() or "unknown",
                    "hook": (parts[1] if len(parts) > 1 else "unknown").lower(),
                    "cta": (cta or "unknown").lower().replace("_", " "), "category": cat or "unknown",
                    "channel": channel or platform, "launch": d0, "ctr": float(clk) / float(imp)})
    return out


def _levels(rows: list[dict]) -> dict[str, list[str]]:
    return {f: sorted({r[f] for r in rows}) for f in FEATURES}


def _matrix(rows: list[dict], levels: dict) -> np.ndarray:
    cols = [(f, v) for f in FEATURES for v in levels[f]]
    return np.array([[1.0 if r[f] == v else 0.0 for f, v in cols] for r in rows]) if rows else np.zeros((0, len(cols)))


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit(rows: list[dict]) -> dict:
    import lightgbm as lgb

    levels = _levels(rows)
    booster = lgb.train(PARAMS, lgb.Dataset(_matrix(rows, levels), _logit(np.array([r["ctr"] for r in rows]))),
                        num_boost_round=ROUNDS)
    return {"levels": levels, "model": booster.model_to_string()}


def predict(model: dict, rows: list[dict]) -> np.ndarray:
    import lightgbm as lgb

    b = lgb.Booster(model_str=model["model"])
    z = b.predict(_matrix(rows, model["levels"]), num_threads=1)
    return 1 / (1 + np.exp(-z))


def evaluate(rows: list[dict]) -> dict:
    """Train on the earliest 70% by launch date, compare Spearman on the latest 30% with the category-mean baseline."""
    n_hold = max(int(round(len(rows) * HOLDOUT_SHARE)), 1)
    train, hold = rows[:-n_hold], rows[-n_hold:]
    if len(train) < 30 or len(hold) < 10:
        return {"status": "INSUFFICIENT_DATA", "train": len(train), "holdout": len(hold), "passed": False}
    m = fit(train)
    y = np.array([r["ctr"] for r in hold])
    cat_mean = {}
    for r in train:
        cat_mean.setdefault(r["category"], []).append(r["ctr"])
    overall = float(np.mean([r["ctr"] for r in train]))
    base = np.array([float(np.mean(cat_mean[r["category"]])) if r["category"] in cat_mean else overall for r in hold])
    rho_m = float(spearmanr(predict(m, hold), y).statistic)
    rho_b = float(spearmanr(base, y).statistic) if np.ptp(base) > 0 else 0.0
    rho_m, rho_b = (0.0 if np.isnan(rho_m) else rho_m), (0.0 if np.isnan(rho_b) else rho_b)
    return {"status": "OK", "train": len(train), "holdout": len(hold), "spearman_model": round(rho_m, 4),
            "spearman_category_mean": round(rho_b, 4), "passed": bool(rho_m > rho_b)}


def fit_creative_prior(db, as_of: datetime) -> dict:
    from adapt.learn import governance

    rows = creatives(db)
    acc = evaluate(rows) if rows else {"status": "INSUFFICIENT_DATA", "passed": False}
    model = fit(rows) if len(rows) >= 30 else None
    sha = governance.put_artifact(db, model) if model else None
    version = f"creative-{as_of:%Y%m%dT%H%M}"
    decision = governance.consider(
        db, "creative_prior", version, as_of, kind="LIGHTGBM_ATTRIBUTES", artifact_sha256=sha,
        feature_hash=hashlib.sha256(json.dumps([FEATURES, PARAMS, ROUNDS]).encode()).hexdigest()[:16],
        validation=acc, baseline={"spearman": acc.get("spearman_category_mean")},
        criteria={"a_beats_baseline": bool(acc.get("passed"))}, spec={"features": list(FEATURES)})
    return {"status": acc["status"], "creatives": len(rows), "acceptance": acc, "promotion": decision["reason"]}


KEYWORDS = {
    "format": {"video": "video", "carousel": "carousel", "image": "image", "text": "text", "search": "text"},
    "hook": {"discount": "discount", "sale": "discount", "% off": "discount", "new": "new arrival",
             "review": "social proof", "loved by": "social proof", "rated": "social proof", "benefit": "benefit",
             "comfort": "benefit", "last chance": "urgency", "hurry": "urgency", "today only": "urgency",
             "urgency": "urgency"},
    "cta": {"shop now": "shop now", "buy now": "buy now", "learn more": "learn more"},
}


def _champion(db) -> dict | None:
    """The accepted prior's model ({levels, model}), or None when no prior beat the baseline."""
    from adapt.learn import governance

    champ = governance.champion(db, "creative_prior") if db.query(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'learn' AND table_name = 'model_registry'") \
        else None
    if champ is None or not champ.get("artifact_sha256"):
        return None
    return governance.load_artifact(db, champ["artifact_sha256"])


NO_PRIOR = ("No accepted structured creative prior: on the latest holdout it did not beat the category-mean baseline "
            "(or has not been fitted), so no score is shown.")


def _score(model: dict, rows: list[dict], known: dict) -> dict:
    """Predicted early CTR of `known` (other attributes at their most common value), as a percentile of the pool."""
    mode = {f: max({r[f] for r in rows}, key=lambda v: sum(r[f] == v for r in rows)) for f in FEATURES}
    p = float(predict(model, [{**mode, **known}])[0])
    pct = float((predict(model, rows) < p).mean())
    used = ", ".join(f"{k}={v}" for k, v in known.items())
    return {"status": "AVAILABLE", "score": round(pct, 4),
            "explanation": f"Predicted early CTR {p:.2%} from {used} (other attributes at their most common value); "
                           f"higher than {pct:.0%} of existing creatives. A structured prior from attributes only: it "
                           "reads no image or text content."}


def attribute_domains(db) -> dict:
    """GET /creatives/attributes: the levels the champion prior was trained on, one list per feature."""
    model = _champion(db)
    if model is None:
        return {"status": "NOT_ESTIMABLE", "domains": {}, "note": NO_PRIOR}
    return {"status": "AVAILABLE", "domains": {f: list(model["levels"].get(f, [])) for f in FEATURES},
            "note": "Levels observed in the training creatives; a value outside them cannot be scored."}


def score_attributes(db, attributes: dict) -> dict:
    """POST /creatives/score with explicit attributes: every value must be a trained level of its feature."""
    model = _champion(db)
    if model is None:
        return {"status": "NOT_ESTIMABLE", "score": None, "explanation": NO_PRIOR}
    unknown = sorted(set(attributes) - set(FEATURES))
    if unknown:
        raise ValueError(f"unknown attribute(s): {', '.join(unknown)}; attributes: {', '.join(FEATURES)}")
    bad = [f"{k}={v}" for k, v in attributes.items() if v not in model["levels"].get(k, [])]
    if bad:
        raise ValueError(f"not a trained level: {', '.join(bad)}")
    if not attributes:
        raise ValueError("choose at least one attribute")
    return _score(model, creatives(db), dict(attributes))


def score_text(db, text: str) -> dict:
    """POST /creatives/score: attributes read from the brief's words (no content model), scored by the CHAMPION
    prior as a percentile among existing creatives. NOT_ESTIMABLE without an accepted prior."""
    model = _champion(db)
    if model is None:
        return {"status": "NOT_ESTIMABLE", "score": None, "explanation": NO_PRIOR}
    rows = creatives(db)
    t = text.lower()
    attrs = {f: next((v for k, v in KEYWORDS.get(f, {}).items() if k in t), None) for f in ("format", "hook", "cta")}
    cats = sorted({r["category"] for r in rows}, key=len, reverse=True)  # "Men·Jeans": match the full name or the tail
    attrs["category"] = next((c for c in cats if c.lower() in t), None) or next(
        (c for c in cats if re.search(r"\b" + re.escape(c.split("·")[-1].strip().lower()) + r"\b", t)), None)
    attrs["channel"] = next((c for c in sorted({r["channel"] for r in rows}) if c.replace("_", " ") in t
                             or c.split("_")[0] in t), None)
    known = {k: v for k, v in attrs.items() if v is not None}
    if not known:
        return {"status": "NOT_ESTIMABLE", "score": None,
                "explanation": "Name a format (video, carousel, image, text), a hook (discount, new arrival, social "
                               "proof, benefit, urgency), a CTA, a category or a channel: the prior scores structured "
                               "attributes only, never the image or the wording."}
    return _score(model, rows, known)
