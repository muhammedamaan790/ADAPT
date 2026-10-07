"""Cannibalization (Stage 2, spec §8.1 step 2): the symmetric transfer matrix T and the steal / shortfall / recapture
flows that turn each unit's curve increment dR_i into its BOOKED increment.

T: C[i,j] = c_ij for units i != j sharing a category (0.30 same channel + stage, 0.15 otherwise), C symmetric with a
zero diagonal; T = alpha C with ONE global alpha = min(1, 0.5 / max_i sum_j C[i,j]) so every row sum is <= 0.5 and T
stays exactly symmetric (per-row scaling is forbidden: it would break symmetry, T59).

Flows per joint draw (vectorised over draws; P = max(dR, 0), N = max(-dR, 0), R'_j = post-change revenue of j):
  raw steal      S_raw[i<-j] = T[i,j] P_i                     share of i's gain that would come from j
  scale_j        = min(1, max(R'_j, 0) / sum_i S_raw[i<-j])  j can lose at most what it still sells
  realized steal F[i<-j] = S_raw[i<-j] scale_j
  shortfall_i    = sum_j (S_raw - F)[i<-j]                    stolen sales j no longer has: they do not happen
  recapture      G[i<-j] = T[i,j] N_j                         share of j's lost revenue picked up by i
  booked_i       = dR_i - shortfall_i + sum_j G[i<-j] - sum_k F[k<-i]
Consequence: sum_i booked_i = sum dR - sum S_raw + sum G whether or not a cap binds (caps move WHO books revenue,
never how much demand exists). With T = 0 booked = dR exactly (Stage 1).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config" / "cannibalization.yaml"


@lru_cache
def cannibalization_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def coefficient_matrix(units, coefficients: dict) -> np.ndarray:
    """C from unit metadata: categories (product sets), channel, stage."""
    U = len(units)
    C = np.zeros((U, U))
    for i in range(U):
        for j in range(i + 1, U):
            a, b = units[i], units[j]
            if not set(a.categories) & set(b.categories):
                continue
            same = a.channel == b.channel and (a.stage or "") == (b.stage or "")
            c = coefficients["same_category_same_channel_stage"] if same else coefficients["same_category"]
            C[i, j] = C[j, i] = float(c)
    return C


def transfer_matrix(C: np.ndarray, max_row_sum: float) -> tuple[np.ndarray, float]:
    """T = alpha C with one global alpha; returns (T, alpha)."""
    rows = C.sum(axis=1)
    top = float(rows.max()) if rows.size else 0.0
    alpha = min(1.0, max_row_sum / top) if top > 0 else 1.0
    return alpha * C, alpha


def build_T(units, cfg: dict | None) -> np.ndarray | None:
    """The generated T for a state's cannibalization config, or None when disabled (Stage 1: T = 0)."""
    if not cfg or not cfg.get("enabled"):
        return None
    T, _ = transfer_matrix(coefficient_matrix(units, cfg["coefficients"]), float(cfg["max_row_sum"]))
    return T if T.any() else None


def book(dR: np.ndarray, R_new: np.ndarray, T: np.ndarray) -> dict[str, np.ndarray]:
    """dR, R_new: (n, U) horizon curve increments and post-change revenues. Returns booked (n, U) and the flow
    totals per unit: raw_steal (by thief i), realized_steal (by thief), shortfall, recapture (received by i),
    stolen_from (realized steal suffered by i), each (n, U)."""
    P = np.clip(dR, 0.0, None)
    N = np.clip(-dR, 0.0, None)
    raw_by_thief = P * T.sum(axis=1)[None, :]                       # sum_j S_raw[i<-j] = P_i sum_j T[i,j]
    raw_on_victim = P @ T                                           # sum_i S_raw[i<-j] (T symmetric)
    cap = np.clip(R_new, 0.0, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(raw_on_victim > cap, cap / np.where(raw_on_victim > 0, raw_on_victim, 1.0), 1.0)
    realized_by_thief = P * (scale @ T)                             # sum_j F[i<-j] = P_i sum_j T[i,j] scale_j
    shortfall = raw_by_thief - realized_by_thief
    stolen_from = raw_on_victim * scale                             # sum_k F[k<-i]
    recapture = N @ T                                               # sum_j G[i<-j] = sum_j T[i,j] N_j
    booked = dR - shortfall + recapture - stolen_from
    return {"booked": booked, "raw_steal": raw_by_thief, "realized_steal": realized_by_thief,
            "shortfall": shortfall, "recapture": recapture, "stolen_from": stolen_from}
