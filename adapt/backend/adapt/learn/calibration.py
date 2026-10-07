"""Impact calibration (B8, spec §10 learning 1): the optimism_correction_factor of a calibration family.

- calibrated_pred = factor x raw_pred if raw_pred > 0, else raw_pred (never applied to what it was not trained on)
- on a matured SUCCESS / NEUTRAL / FAILED OPTIMIZATION outcome with raw_pred >= max(2,000, 1% of B):
      rho = clip(realized / raw_pred, 0, 1.2);  factor <- clip(0.8 factor + 0.2 rho, 0.3, 1.0);  initial 0.9
  dividing by the RAW prediction (never the calibrated one) avoids double discounting; a realized loss gives rho = 0
- idempotent: keyed by outcome id, applied exactly once (learn.calibration_log primary key)
- Stage 1 family = BUDGET_REALLOCATION, the decision as a whole (per-leg credit is not identified)
"""

from __future__ import annotations

from datetime import datetime

FAMILY = "BUDGET_REALLOCATION"
INITIAL, LO, HI, RHO_CAP, MEMORY = 0.9, 0.3, 1.0, 1.2, 0.8

DDL = """
CREATE SCHEMA IF NOT EXISTS learn;
CREATE TABLE IF NOT EXISTS learn.calibration (
    family VARCHAR PRIMARY KEY, factor DOUBLE NOT NULL, n_updates INTEGER NOT NULL, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS learn.calibration_log (
    outcome_id VARCHAR PRIMARY KEY, family VARCHAR NOT NULL, factor_before DOUBLE NOT NULL,
    factor_after DOUBLE NOT NULL, rho DOUBLE, realized DOUBLE, raw_pred DOUBLE, applied_at TIMESTAMP NOT NULL
);
"""


def calibrate(raw_pred: float, factor: float) -> float:
    return factor * raw_pred if raw_pred > 0 else raw_pred


def next_factor(factor: float, realized: float, raw_pred: float) -> tuple[float, float]:
    rho = min(max(realized / raw_pred, 0.0), RHO_CAP)
    return min(max(MEMORY * factor + (1 - MEMORY) * rho, LO), HI), rho


def materiality_floor(total_budget: float) -> float:
    return max(2000.0, 0.01 * total_budget)


def current_factor(db, family: str = FAMILY) -> float:
    if not db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'learn' "
                    "AND table_name = 'calibration'"):
        return INITIAL
    row = db.query("SELECT factor FROM learn.calibration WHERE family = ?", [family])
    return float(row[0][0]) if row else INITIAL


def apply_outcome(db, outcome_id: str, verdict: str, realized: float, raw_pred: float, total_budget: float,
                  at: datetime, family: str = FAMILY) -> dict:
    """Update the family factor from one matured outcome, exactly once. Returns what happened and why."""
    if verdict not in ("SUCCESS", "NEUTRAL", "FAILED"):
        return {"applied": False, "reason": f"{verdict} outcomes do not calibrate"}
    if raw_pred <= 0 or raw_pred < materiality_floor(total_budget):
        return {"applied": False, "reason": "raw prediction not positive and material (tracked separately)"}

    def work(cur):
        cur.execute(DDL)
        if cur.execute("SELECT 1 FROM learn.calibration_log WHERE outcome_id = ?", [outcome_id]).fetchone():
            return {"applied": False, "reason": "already applied (idempotent)"}
        row = cur.execute("SELECT factor, n_updates FROM learn.calibration WHERE family = ?", [family]).fetchone()
        before, n = (float(row[0]), int(row[1])) if row else (INITIAL, 0)
        after, rho = next_factor(before, realized, raw_pred)
        cur.execute("INSERT OR REPLACE INTO learn.calibration VALUES (?, ?, ?, ?)", [family, after, n + 1, at])
        cur.execute("INSERT INTO learn.calibration_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [outcome_id, family, before, after, rho, realized, raw_pred, at])
        return {"applied": True, "factor_before": before, "factor_after": after, "rho": rho}

    return db.write(work)
