"""Model governance: champion / challenger (Stage 2, spec §10.2, T32).

Every refit registers a CANDIDATE in learn.model_registry (model, version, role[champion|candidate|retired], kind,
fit_ts, feature_hash, spec, artifact_sha256, validation_metrics, baseline_metrics, criteria, promoted_at,
promotion_reason, rollback_version). Layered promotion: ALL applicable criteria must hold, else the champion is kept
and the rejection is logged:
  (a) the family-specific baseline requirement (passed in by the model: demand beats seasonal-naive WAPE by >= 5%,
      curves holdout skill >= 0, ...)
  (b) non-inferiority against the champion when one exists: a paired MOVING-BLOCK bootstrap (7-day blocks, 1,000
      resamples over holdout days) of the relative error difference
          (sum candidate_err - sum champion_err) / max(sum champion_err, eps),  eps = max(0.01 mean|y|, 1e-9)
      must have a 95% upper bound <= +2%. Both errors are on IDENTICAL holdout rows. A refit of the champion's own
      specification (same feature_hash) is a routine refresh: (b) is "same specification", criteria (a) and (c) decide.
  (c) the family-specific coverage / acceptance contract (passed in)
A promotion records promotion_reason and rollback_version; rollback() restores the previous champion.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config" / "models.yaml"

DDL = """
CREATE SCHEMA IF NOT EXISTS learn;
CREATE TABLE IF NOT EXISTS learn.model_registry (
    model VARCHAR NOT NULL, version VARCHAR NOT NULL, role VARCHAR NOT NULL, kind VARCHAR, fit_ts TIMESTAMP,
    training_snapshot_hash VARCHAR, feature_hash VARCHAR, spec JSON, artifact_sha256 VARCHAR,
    validation_metrics JSON, baseline_metrics JSON, criteria JSON, promoted_at TIMESTAMP, promotion_reason VARCHAR,
    rollback_version VARCHAR, PRIMARY KEY (model, version)
);
CREATE TABLE IF NOT EXISTS learn.model_events (
    model VARCHAR NOT NULL, version VARCHAR NOT NULL, event VARCHAR NOT NULL, event_at TIMESTAMP NOT NULL,
    actor VARCHAR, details JSON
);
"""


@lru_cache
def governance_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["governance"]


def ensure(db) -> None:
    db.write(lambda cur: cur.execute(DDL))


def put_artifact(db, obj) -> str:
    from adapt.decide.snapshot import put_artifact as put

    return put(db, obj)


def load_artifact(db, sha: str):
    from adapt.decide.snapshot import get_artifact

    return get_artifact(db, sha)


COLS = ["model", "version", "role", "kind", "fit_ts", "training_snapshot_hash", "feature_hash", "spec",
        "artifact_sha256", "validation_metrics", "baseline_metrics", "criteria", "promoted_at", "promotion_reason",
        "rollback_version"]


def _row(r) -> dict:
    d = dict(zip(COLS, r, strict=True))
    for k in ("spec", "validation_metrics", "baseline_metrics", "criteria"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def champion(db, model: str) -> dict | None:
    ensure(db)
    rows = db.query(f"SELECT {', '.join(COLS)} FROM learn.model_registry WHERE model = ? AND role = 'champion'",
                    [model])
    return _row(rows[0]) if rows else None


def history(db, model: str) -> list[dict]:
    ensure(db)
    return [_row(r) for r in db.query(f"SELECT {', '.join(COLS)} FROM learn.model_registry WHERE model = ? "
                                      "ORDER BY fit_ts, version", [model])]


def noninferiority(cand_err: np.ndarray, champ_err: np.ndarray, days: np.ndarray, y_mean: float,
                   cfg: dict | None = None) -> dict:
    """Paired moving-block bootstrap over holdout DAYS of the relative error difference; returns the observed value,
    the 95% upper bound and pass (upper <= tolerance)."""
    cfg = cfg or governance_config()
    cand_err, champ_err = np.asarray(cand_err, dtype=float), np.asarray(champ_err, dtype=float)
    uniq = sorted(set(days.tolist()))
    pos = {d: i for i, d in enumerate(uniq)}
    di = np.array([pos[d] for d in days.tolist()])
    n_days = len(uniq)
    cand_day = np.bincount(di, weights=cand_err, minlength=n_days)
    champ_day = np.bincount(di, weights=champ_err, minlength=n_days)
    eps = max(0.01 * y_mean, 1e-9)

    def rel(idx):
        c, h = cand_day[idx].sum(), champ_day[idx].sum()
        return (c - h) / max(h, eps)

    observed = rel(np.arange(n_days))
    rng = np.random.default_rng(int(cfg["seed"]))
    b = min(int(cfg["block_days"]), n_days)
    n_blocks = int(np.ceil(n_days / b))
    vals = np.empty(int(cfg["bootstrap"]))
    for k in range(len(vals)):
        starts = rng.integers(0, n_days - b + 1, size=n_blocks)
        idx = (starts[:, None] + np.arange(b)[None, :]).reshape(-1)[:n_days]
        vals[k] = rel(idx)
    upper = float(np.quantile(vals, 0.95))
    return {"relative_error_difference": float(observed), "upper_95": upper,
            "tolerance": float(cfg["noninferiority_tolerance"]),
            "passed": upper <= float(cfg["noninferiority_tolerance"]), "holdout_days": n_days}


def consider(db, model: str, version: str, at: datetime, kind: str, artifact_sha256: str | None,
             feature_hash: str | None, validation: dict, baseline: dict | None, criteria: dict[str, bool],
             paired_errors: dict | None = None, y_mean: float = 0.0, spec: dict | None = None,
             training_snapshot_hash: str | None = None) -> dict:
    """Register a candidate and apply the layered promotion rule. Returns {role, promoted, reason, criteria}."""
    ensure(db)
    champ = champion(db, model)
    crit = dict(criteria)
    ni = None
    if champ is None:
        crit["b_noninferiority"] = True
        why_b = "no champion yet"
    elif champ.get("feature_hash") == feature_hash and champ.get("kind") == kind:
        crit["b_noninferiority"] = True
        why_b = "same specification as the champion (routine refit)"
    elif paired_errors and paired_errors.get("champion") is not None:
        ni = noninferiority(paired_errors["candidate"], paired_errors["champion"], paired_errors["days"], y_mean)
        crit["b_noninferiority"] = ni["passed"]
        why_b = f"non-inferiority upper bound {ni['upper_95']:+.1%} vs tolerance {ni['tolerance']:+.0%}"
    else:
        crit["b_noninferiority"] = champ.get("kind") != kind and crit.get("a_beats_baseline", False)
        why_b = "champion is a different family: the baseline requirement (a) decides"
    promoted = all(crit.values())
    failed = [k for k, v in crit.items() if not v]
    reason = ("promoted: " + "; ".join([why_b] + [k for k in crit if k != "b_noninferiority"])) if promoted else \
        "retained champion: failed " + ", ".join(failed) + (f" ({why_b})" if "b_noninferiority" in failed else "")

    def work(cur):
        cur.execute(DDL)
        if promoted and champ is not None:
            cur.execute("UPDATE learn.model_registry SET role = 'retired' WHERE model = ? AND role = 'champion'",
                        [model])
        cur.execute(f"INSERT OR REPLACE INTO learn.model_registry ({', '.join(COLS)}) VALUES "
                    f"({', '.join('?' * len(COLS))})",
                    [model, version, "champion" if promoted else "candidate", kind, at, training_snapshot_hash,
                     feature_hash, json.dumps(spec, default=str), artifact_sha256,
                     json.dumps(validation, default=float), json.dumps(baseline, default=float),
                     json.dumps({**crit, "noninferiority": ni}, default=float), at if promoted else None, reason,
                     champ["version"] if (promoted and champ) else None])
        cur.execute("INSERT INTO learn.model_events VALUES (?, ?, ?, ?, ?, ?)",
                    [model, version, "promoted" if promoted else "rejected", at, "governance",
                     json.dumps({"reason": reason, "criteria": crit}, default=float)])

    db.write(work)
    return {"role": "champion" if promoted else "candidate", "promoted": promoted, "reason": reason,
            "criteria": crit, "noninferiority": ni}


def register_champion(db, model: str, version: str, at: datetime, kind: str, reason: str, **fields) -> None:
    """Install a model as champion directly (e.g. the Stage 1 seasonal-naive demand model, or the first curve fit)."""
    ensure(db)
    champ = champion(db, model)

    def work(cur):
        cur.execute(DDL)
        cur.execute("UPDATE learn.model_registry SET role = 'retired' WHERE model = ? AND role = 'champion'", [model])
        cur.execute(f"INSERT OR REPLACE INTO learn.model_registry ({', '.join(COLS)}) VALUES "
                    f"({', '.join('?' * len(COLS))})",
                    [model, version, "champion", kind, at, fields.get("training_snapshot_hash"),
                     fields.get("feature_hash"), json.dumps(fields.get("spec")), fields.get("artifact_sha256"),
                     json.dumps(fields.get("validation"), default=float), json.dumps(fields.get("baseline")),
                     json.dumps(fields.get("criteria")), at, reason, champ["version"] if champ else None])
        cur.execute("INSERT INTO learn.model_events VALUES (?, ?, 'promoted', ?, 'governance', ?)",
                    [model, version, at, json.dumps({"reason": reason})])

    db.write(work)


def rollback(db, model: str, actor: str, at: datetime) -> dict:
    """POST /models/{model}/rollback: the current champion is retired and its rollback_version restored."""
    champ = champion(db, model)
    if champ is None or not champ.get("rollback_version"):
        raise ValueError(f"{model}: no previous champion to roll back to")
    prev = champ["rollback_version"]

    def work(cur):
        cur.execute("UPDATE learn.model_registry SET role = 'retired' WHERE model = ? AND version = ?",
                    [model, champ["version"]])
        cur.execute("UPDATE learn.model_registry SET role = 'champion', promoted_at = ?, promotion_reason = ? "
                    "WHERE model = ? AND version = ?", [at, f"rollback by {actor} from {champ['version']}", model,
                                                        prev])
        cur.execute("INSERT INTO learn.model_events VALUES (?, ?, 'rolled_back', ?, ?, ?)",
                    [model, champ["version"], at, actor, json.dumps({"restored": prev})])

    db.write(work)
    return {"model": model, "retired": champ["version"], "champion": prev}
