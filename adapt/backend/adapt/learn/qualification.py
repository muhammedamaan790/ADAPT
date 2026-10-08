"""Simulation autonomy qualification (spec §8.4, §16 splits, §22.9): the evidence that can authorize autonomy.

Records: one per (matured OPTIMIZATION outcome, channel it touched), carrying the decision-time raw confidence region
([0, 0.6) R1, [0.6, 0.8) R2, [0.8, 1] R3), model P(loss), verdict and realized effect, tagged world = SIMULATED and
the world id. Local outcomes come from this workspace; warm-up track records (worlds 901-903, reliability world 904)
are imported as a versioned, hashed artifact (export_track_record / import_track_record), never their curves.

A (channel, region) is QUALIFIED iff
  - pooled over the warm-up worlds 901-903: the Wilson 95% lower bound of the SUCCESS rate >= 0.60 (INCONCLUSIVE
    outcomes are excluded and counted), with outcomes from >= 3 independent worlds and no world holding more than
    half of them (one world cannot dominate);
  - the held-out reliability check on world 904 is PASS: >= N_reliability (15) matured eligible outcomes and an
    observed SUCCESS rate inside the warm-up Wilson interval. Fewer outcomes -> INCONCLUSIVE, which never qualifies.
Simulated and real outcomes are never pooled: production qualification would use world = REAL only (none exist).

The P(loss) reliability monitor (T63): among matured outcomes whose decision had model P(loss) <= 0.2, an observed loss
frequency > 0.3 with >= 15 outcomes suspends autonomy for the channel.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime

WARMUP_WORLDS = (901, 902, 903)
RELIABILITY_WORLD = 904
MIN_LOWER = 0.60
N_RELIABILITY = 15
REGIONS = ("R1", "R2", "R3")
DECISIVE = ("SUCCESS", "NEUTRAL", "FAILED")
PLOSS_BIN, PLOSS_MAX_OBSERVED, PLOSS_MIN_N = 0.2, 0.3, 15

DDL = """
CREATE SCHEMA IF NOT EXISTS learn;
CREATE TABLE IF NOT EXISTS learn.track_record_imports (
    artifact_sha256 VARCHAR PRIMARY KEY, world_ids JSON NOT NULL, label VARCHAR NOT NULL, imported_at TIMESTAMP,
    artifact JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS learn.track_record (
    artifact_sha256 VARCHAR NOT NULL, world VARCHAR NOT NULL, world_id INTEGER NOT NULL, decision_id VARCHAR NOT NULL,
    channel VARCHAR NOT NULL, region VARCHAR, raw_index DOUBLE, prob_loss DOUBLE, verdict VARCHAR NOT NULL,
    realized DOUBLE, executed_by VARCHAR, guardrail_violation BOOLEAN NOT NULL,
    PRIMARY KEY (artifact_sha256, world_id, decision_id, channel)
);
"""


def _has(db, schema: str, table: str) -> bool:
    return bool(db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                         [schema, table]))


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    """Wilson score interval for k successes in n trials; (None, None) without trials."""
    if n <= 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


# ---- records -----------------------------------------------------------------------------------------------------
def local_records(db, world_id: int) -> list[dict]:
    """This workspace's matured OPTIMIZATION outcomes, one record per channel the decision touched."""
    if not (_has(db, "learn", "outcomes") and _has(db, "intel", "decisions")):
        return []
    out = []
    for did, verdict, realized, payload in db.query(
            """SELECT o.decision_id, o.verdict, o.realized, d.payload FROM learn.outcomes o
               JOIN intel.decisions d USING (decision_id) WHERE o.class = 'OPTIMIZATION' ORDER BY o.decision_id"""):
        p = json.loads(payload)
        conf = p.get("confidence") or {}
        actor = None
        if _has(db, "ops", "decision_events"):
            row = db.query("SELECT actor FROM ops.decision_events WHERE decision_id = ? AND event = 'approved' "
                           "ORDER BY seq LIMIT 1", [did])
            actor = row[0][0] if row else None
        for channel in sorted({leg["platform"] for leg in p["legs"]}):
            out.append({"world": "SIMULATED", "world_id": int(world_id), "decision_id": did, "channel": channel,
                        "region": conf.get("region"), "raw_index": conf.get("overall"),
                        "prob_loss": p["expected"].get("prob_loss"), "verdict": verdict,
                        "realized": None if realized is None else float(realized), "executed_by": actor,
                        "guardrail_violation": False})
    return out


def export_track_record(db, world_id: int, label: str) -> dict:
    """A versioned, hashed artifact of one warm-up world's outcome track record (no curves, no world parameters)."""
    recs = local_records(db, world_id)
    body = {"version": 1, "world_ids": [int(world_id)], "label": label, "records": recs}
    sha = hashlib.sha256(json.dumps(body, sort_keys=True, default=float).encode()).hexdigest()
    return {**body, "artifact_sha256": sha}


def merge_artifacts(artifacts: list[dict], label: str) -> dict:
    recs = [r for a in artifacts for r in a["records"]]
    worlds = sorted({w for a in artifacts for w in a["world_ids"]})
    body = {"version": 1, "world_ids": worlds, "label": label, "records": recs}
    sha = hashlib.sha256(json.dumps(body, sort_keys=True, default=float).encode()).hexdigest()
    return {**body, "artifact_sha256": sha}


def verify_artifact(a: dict) -> bool:
    body = {k: a[k] for k in ("version", "world_ids", "label", "records")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=float).encode()).hexdigest() == a["artifact_sha256"]


def import_track_record(db, artifact: dict, at: datetime) -> dict:
    if not verify_artifact(artifact):
        raise ValueError("track-record artifact hash mismatch: refusing to import")
    sha = artifact["artifact_sha256"]

    def work(cur):
        cur.execute(DDL)
        cur.execute("INSERT OR REPLACE INTO learn.track_record_imports VALUES (?, ?, ?, ?, ?)",
                    [sha, json.dumps(artifact["world_ids"]), artifact["label"], at,
                     json.dumps(artifact, default=float)])
        cur.execute("DELETE FROM learn.track_record WHERE artifact_sha256 = ?", [sha])
        for r in artifact["records"]:
            cur.execute("INSERT OR REPLACE INTO learn.track_record VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [sha, r["world"], r["world_id"], r["decision_id"], r["channel"], r["region"], r["raw_index"],
                         r["prob_loss"], r["verdict"], r["realized"], r.get("executed_by"),
                         bool(r.get("guardrail_violation"))])

    db.write(work)
    return {"artifact_sha256": sha, "records": len(artifact["records"]), "world_ids": artifact["world_ids"]}


def imported_records(db) -> list[dict]:
    if not _has(db, "learn", "track_record"):
        return []
    cols = ("world", "world_id", "decision_id", "channel", "region", "raw_index", "prob_loss", "verdict", "realized",
            "executed_by", "guardrail_violation")
    return [dict(zip(cols, r, strict=True)) for r in db.query(f"SELECT {', '.join(cols)} FROM learn.track_record")]


def imports(db) -> list[dict]:
    if not _has(db, "learn", "track_record_imports"):
        return []
    return [{"artifact_sha256": s, "world_ids": json.loads(w), "label": lab, "imported_at": t}
            for s, w, lab, t in db.query("SELECT artifact_sha256, world_ids, label, imported_at "
                                         "FROM learn.track_record_imports ORDER BY imported_at")]


# ---- qualification -------------------------------------------------------------------------------------------------
def _pool(recs: list[dict], channel: str, region: str, worlds) -> list[dict]:
    return [r for r in recs if r["world"] == "SIMULATED" and r["channel"] == channel and r["region"] == region
            and r["world_id"] in worlds]


def qualify(recs: list[dict], channel: str, region: str) -> dict:
    warm = _pool(recs, channel, region, WARMUP_WORLDS)
    decisive = [r for r in warm if r["verdict"] in DECISIVE]
    n, k = len(decisive), sum(r["verdict"] == "SUCCESS" for r in decisive)
    lo, hi = wilson(k, n)
    per_world = {w: sum(r["world_id"] == w for r in decisive) for w in WARMUP_WORLDS}
    worlds = sum(1 for c in per_world.values() if c > 0)
    dominated = n > 0 and max(per_world.values()) > n / 2
    rel = [r for r in _pool(recs, channel, region, (RELIABILITY_WORLD,)) if r["verdict"] in DECISIVE]
    m, km = len(rel), sum(r["verdict"] == "SUCCESS" for r in rel)
    if m < N_RELIABILITY:
        rel_status = "INCONCLUSIVE"
    elif lo is not None and lo <= km / m <= hi:
        rel_status = "PASS"
    else:
        rel_status = "FAIL"
    reasons = []
    if lo is None or lo < MIN_LOWER:
        reasons.append(f"Wilson lower bound {lo:.2f} < {MIN_LOWER}" if lo is not None else "no warm-up outcomes")
    if worlds < 3:
        reasons.append(f"outcomes from {worlds} of 3 required warm-up worlds")
    if dominated:
        reasons.append("one warm-up world holds more than half of the outcomes")
    if rel_status != "PASS":
        reasons.append(f"held-out reliability (world {RELIABILITY_WORLD}) {rel_status} ({m} outcomes)")
    status = "QUALIFIED" if not reasons else ("INCONCLUSIVE" if rel_status == "INCONCLUSIVE" and lo is not None
                                              and lo >= MIN_LOWER and worlds >= 3 and not dominated
                                              else "UNQUALIFIED")
    return {"channel": channel, "region": region, "status": status, "n": n, "successes": k,
            "inconclusive": sum(r["verdict"] == "INCONCLUSIVE" for r in warm),
            "rate": k / n if n else None, "wilson_lower": lo, "wilson_upper": hi, "worlds": worlds,
            "per_world": per_world, "reliability": {"status": rel_status, "n": m, "rate": km / m if m else None},
            "reasons": reasons}


def ploss_monitor(recs: list[dict], channel: str) -> dict:
    """T63: observed loss frequency among matured outcomes with model P(loss) <= 0.2 on this channel."""
    rows = [r for r in recs if r["channel"] == channel and r["prob_loss"] is not None
            and r["prob_loss"] <= PLOSS_BIN and r["verdict"] in DECISIVE and r["realized"] is not None]
    n = len(rows)
    losses = sum(r["realized"] < 0 for r in rows)
    observed = losses / n if n else None
    tripped = n >= PLOSS_MIN_N and observed is not None and observed > PLOSS_MAX_OBSERVED
    return {"channel": channel, "n": n, "losses": losses, "observed_loss_rate": observed, "tripped": tripped,
            "rule": f"suspend if > {PLOSS_MAX_OBSERVED:.0%} with >= {PLOSS_MIN_N} outcomes"}


def all_records(db, local_world_id: int) -> list[dict]:
    return imported_records(db) + local_records(db, local_world_id)
