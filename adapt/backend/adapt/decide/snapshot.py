"""Decision snapshots (C3, spec §8.4, §22.4): content-addressed artifacts + the replay-environment record.

Artifacts are exact JSON (Python float repr round-trips bit for bit) written once under
<workspace dir>/artifacts/sha256/<hash>.json and never moved or overwritten. A snapshot's manifest maps artifact
names to hashes; manifest_sha256 identifies the snapshot. Replay loads ONLY these artifacts (state, flags, policy
config, objective config, calibration factor), never the current mutable state or policy.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np

from adapt.decide.hashing import content_hash, sha256_hex
from adapt.economics.portfolio import PortfolioState, SkuState, UnitState
from adapt.predict.curves import CurveArtifact

REPO = Path(__file__).resolve().parents[3]


def artifacts_dir(db) -> Path:
    base = Path(db.path).parent if db.path != ":memory:" else Path.cwd() / "data"
    d = base / "artifacts" / "sha256"
    d.mkdir(parents=True, exist_ok=True)
    return d


def put_artifact(db, obj) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    h = sha256_hex(raw)
    path = artifacts_dir(db) / f"{h}.json"
    if not path.exists():
        path.write_bytes(raw)
    return h


def get_artifact(db, h: str):
    raw = (artifacts_dir(db) / f"{h}.json").read_bytes()
    if sha256_hex(raw) != h:
        raise ValueError(f"artifact {h} is corrupted")
    return json.loads(raw)


# ---- state (de)serialisation ----------------------------------------------------------------------------------------
def _arr(a):
    return None if a is None else np.asarray(a, dtype=float).tolist()


def curve_to_dict(a: CurveArtifact | None) -> dict | None:
    if a is None:
        return None
    return {"unit_id": a.unit_id, "status": a.status, "w": a.w, "median_spend": a.median_spend,
            "median_revenue": a.median_revenue, "baseline_mean": a.baseline_mean, "baseline_level": a.baseline_level,
            "terminal_adstock": a.terminal_adstock, "params": _arr(a.params), "draws": _arr(a.draws),
            "pooled_params": _arr(a.pooled_params), "pooled_draws": _arr(a.pooled_draws),
            "pooled_terminal_adstock": a.pooled_terminal_adstock,
            "diagnostics": {"oos_rel_residuals": a.diagnostics.get("oos_rel_residuals"),
                            "oos_model": a.diagnostics.get("oos_model")}}


def curve_from_dict(d: dict | None) -> CurveArtifact | None:
    if d is None:
        return None
    arr = lambda x: None if x is None else np.array(x, dtype=float)  # noqa: E731
    return CurveArtifact(d["unit_id"], d["status"], d["w"], d["median_spend"], d["median_revenue"], d["baseline_mean"],
                         d["baseline_level"], d["terminal_adstock"], arr(d["params"]), arr(d["draws"]),
                         arr(d["pooled_params"]), arr(d["pooled_draws"]), d["pooled_terminal_adstock"],
                         d.get("diagnostics") or {})


def state_to_dict(s: PortfolioState) -> dict:
    return {
        "as_of": s.as_of.isoformat(), "horizon": s.horizon, "n_draws": s.n_draws,
        "other_cba_daily": s.other_cba_daily, "other_net_revenue_daily": s.other_net_revenue_daily, "meta": s.meta,
        "units": [{"unit_id": u.unit_id, "channel": u.channel, "platform": u.platform, "campaign_ids": u.campaign_ids,
                   "budget": u.budget, "pacing": u.pacing, "curve": curve_to_dict(u.curve),
                   "observed_roas": u.observed_roas, "sku_weights": u.sku_weights, "unmapped_share": u.unmapped_share,
                   "unmapped_cr": u.unmapped_cr, "is_shared": u.is_shared} for u in s.units],
        "skus": {k: {"nrpu": v.nrpu, "unit_contribution": v.unit_contribution, "available": v.available,
                     "safety_stock": v.safety_stock, "baseline_daily": v.baseline_daily, "on_hand": v.on_hand}
                 for k, v in s.skus.items()},
    }


def state_from_dict(d: dict) -> PortfolioState:
    units = [UnitState(u["unit_id"], u["channel"], u["platform"], u["campaign_ids"], u["budget"], u["pacing"],
                       curve_from_dict(u["curve"]), u["observed_roas"], u["sku_weights"], u["unmapped_share"],
                       u["unmapped_cr"], u["is_shared"]) for u in d["units"]]
    skus = {k: SkuState(k, v["nrpu"], v["unit_contribution"], v["available"], v["safety_stock"],
                        v["baseline_daily"], v["on_hand"]) for k, v in d["skus"].items()}
    return PortfolioState(datetime.fromisoformat(d["as_of"]), d["horizon"], units, skus, d["other_cba_daily"],
                          d["other_net_revenue_daily"], d["n_draws"], d["meta"])


# ---- replay environment (spec §22.9) ---------------------------------------------------------------------------------
def code_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=10)
        sha = out.stdout.strip() or "unknown"
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "backend"], cwd=REPO, capture_output=True,
                               text=True, timeout=10).stdout.strip()
        return f"{sha}+dirty" if dirty else sha
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def replay_environment(config_hashes: dict[str, str]) -> dict:
    lock = REPO / "uv.lock"
    npm = REPO / "web" / "package-lock.json"
    import platform

    env = {"code_sha": code_sha(),
           "uv_lock_hash": sha256_hex(lock.read_bytes()) if lock.exists() else None,
           "npm_lock_hash": sha256_hex(npm.read_bytes()) if npm.exists() else None,
           "python_version": platform.python_version(), "config_artifact_hashes": config_hashes}
    env["replay_environment_fingerprint"] = content_hash(env)
    return env
