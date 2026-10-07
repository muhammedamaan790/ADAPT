"""Optimizer (B7 + Stage 2 objectives, spec §8.2, §8.3, §22.3): greedy marginal allocation -> SLSQP polish ->
validator -> round -> repair -> revalidate, plus why-not, unallocated budget / cash reserve and the S3 safety candidate.

Objective modes (objectives.yaml; the selected one is a policy setting), over a fixed subset of the joint bootstrap
draws; reported values (E, P10, P90, P(loss) of dCAA, and dnet revenue) use all draws:
  PROFIT               max E[dCAA] - lambda (E - P10)                                   (Stage 1)
  GROWTH               max E[dnet revenue]  s.t.  E[abs CAA] >= CAA0 - max(5% |CAA0|, 2,000)   (OBJECTIVE_CAA_FLOOR)
  INVENTORY_CLEARANCE  max E[dCAA] + h x E[effective units sold on EXCESS-band SKUs] (cover > 45 days)
CAA0 = E[abs_CAA(s)] at the current allocation; the tolerance means "sacrifice at most this much contribution",
sign-safe for a negative CAA0. The feasible set is generated from config/guardrails.yaml, the same
object the policy engine (C4) re-validates:
  sum s' <= B - R | box [s0 (1 - d), s0 (1 + u)] | per-unit min/max | sum |s' - s0| <= cap | channel shares |
  no increase on MODEL_UNAVAILABLE / MIX_UNCERTAIN / frozen / data-dependency units | inventory gate
  (x_i > block -> no increase; allow <= x_i <= block -> an increase may not worsen any mapped SKU's projected
  shortfall). No global-optimality claim: the result reports the greedy-vs-SLSQP gap and a concavity check.

Fast evaluation: the mapped contribution of a draw is sum_k uc_k (min(req_k, max(ATP_k + rel_k, 0)) - rel_k), where
req/rel are the summed positive/negative unit requests on SKU k. With T = 0 a one-unit move updates two (n, K) arrays
instead of re-running the full model. With cannibalization (Stage 2, T != 0) the booked increments depend on every
unit, so the state keeps the (n, U) curve-increment matrix and re-books the flows (matrix products, O(n U^2)):
req = max(booked, 0) @ W / nrpu, rel = max(-booked, 0) @ W / nrpu. Tests assert both equal Portfolio.evaluate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

from adapt.economics.cannibalization import book
from adapt.economics.portfolio import Portfolio, PortfolioState
from adapt.economics.state import guardrails_config, objectives_config

FIXED_REASONS = ("EXECUTION_FREEZE", "SAFETY_COOLDOWN", "TRACKING_FREEZE", "DATA_DEPENDENCY")
NO_INCREASE_REASONS = ("MODEL_UNAVAILABLE", "MIX_UNCERTAIN", "COOLDOWN")


@dataclass
class Constraints:
    lo: np.ndarray
    hi: np.ndarray
    cap_total: float                 # B - R (or sum s0 when the baseline already breaks the reserve)
    B: float
    R: float
    moved_cap: float
    unit_reasons: dict[int, list[str]]   # no-increase / fixed reasons per unit index
    channel_max: dict[str, float] = field(default_factory=dict)
    channel_min: dict[str, float] = field(default_factory=dict)
    reserve_baseline_infeasible: bool = False


def build_constraints(state: PortfolioState, flags: dict[str, list[str]] | None = None,
                      guardrails: dict | None = None) -> Constraints:
    g = guardrails or guardrails_config()
    flags = flags or {}
    s0 = np.array([u.budget for u in state.units])
    total0 = float(s0.sum())
    B = float(g["budget"]["total_budget_inr"] or total0)
    R = max(float(g["budget"]["cash_reserve_rupees"] or 0), float(g["budget"]["cash_reserve_pct"] or 0) * B)
    ch = g["change"]
    lo = np.maximum(s0 * (1 - ch["max_daily_change_pct"]), np.minimum(ch["unit_min_budget_inr"], s0))
    hi = s0 * (1 + ch["max_daily_change_pct"])
    if ch.get("unit_max_budget_inr"):
        hi = np.minimum(hi, np.maximum(float(ch["unit_max_budget_inr"]), s0))
    mix = g["mix_uncertain_unmapped_share"]
    reasons: dict[int, list[str]] = {}
    for i, u in enumerate(state.units):
        r = list(flags.get(u.unit_id, []))
        if not u.model_available:
            r.append("MODEL_UNAVAILABLE")
        if u.unmapped_share > mix:
            r.append("MIX_UNCERTAIN")
        if r:
            reasons[i] = r
            hi[i] = s0[i]
            if any(x in FIXED_REASONS for x in r):
                lo[i] = s0[i]
    infeasible = total0 > B - R + 1e-6
    return Constraints(lo=lo, hi=hi, cap_total=total0 if infeasible else B - R, B=B, R=R,
                       moved_cap=float(ch["max_rupees_moved_pct"]) * total0, unit_reasons=reasons,
                       channel_max=dict(g["channel_share"].get("max") or {}),
                       channel_min=dict(g["channel_share"].get("min") or {}),
                       reserve_baseline_infeasible=infeasible)


class FastEval:
    """Exact Stage 1 objective pieces with O(n K) updates for a one-unit move."""

    def __init__(self, pf: Portfolio, gate: dict):
        self.pf = pf
        self.gate = gate
        self.n, self.U, self.K = len(pf.draws), len(pf.units), len(pf.sku_ids)
        self.kind = pf.kind
        self.base_risk, _ = pf.risk(pf.baseline)               # at the current allocation (no delta units)
        self.threshold = 0.0 if self.kind == "PROJECTED_SHORTFALL" else pf.p_unsafe
        self.base_shortfall = self.base_risk                   # Stage 1 name, kept for callers
        self.T = pf.T
        self._unm_cba, self._unm_net = pf.u * pf.ucr, pf.u.copy()

    # ---- the cannibalization path (T != 0): state = the (n, U) curve-increment matrix --------------------------------
    def _unit_dR(self, i: int, budget: float) -> np.ndarray:
        return (self.pf.unit_paths(i, budget) - self.pf.R0[:, i, :]).sum(-1)

    def _derive(self, s: np.ndarray, dR: np.ndarray) -> dict:
        booked = book(dR, self.pf.R0h + dR, self.T)["booked"]
        if self.K:
            req = np.clip(booked, 0, None) @ self.pf.W / self.pf.nrpu[None, :]
            rel = np.clip(-booked, 0, None) @ self.pf.W / self.pf.nrpu[None, :]
        else:
            req = rel = np.zeros((self.n, 0))
        return {"s": s, "dR": dR, "req": req, "rel": rel, "un_cba": booked @ self._unm_cba,
                "un_net": booked @ self._unm_net, "rows": {}}

    def unit_terms(self, i: int, budget: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(rows (n, K) of delta units on mapped SKUs, unmapped dCBA (n,), unmapped dNet (n,)) for unit i at budget."""
        pf = self.pf
        dRh = (pf.unit_paths(i, budget) - pf.R0[:, i, :]).sum(-1)
        rows = dRh[:, None] * pf.W[i][None, :] / pf.nrpu[None, :] if self.K else np.zeros((self.n, 0))
        return rows, dRh * pf.u[i] * pf.ucr[i], dRh * pf.u[i]

    def state_of(self, s: np.ndarray) -> dict:
        if self.T is not None:
            dR = np.zeros((self.n, self.U))
            for i in range(self.U):
                if abs(s[i] - self.pf.s0[i]) > 1e-9:
                    dR[:, i] = self._unit_dR(i, s[i])
            return self._derive(np.asarray(s, dtype=float).copy(), dR)
        req = np.zeros((self.n, self.K))
        rel = np.zeros((self.n, self.K))
        un_cba = np.zeros(self.n)
        un_net = np.zeros(self.n)
        rows = {}
        for i in range(self.U):
            if abs(s[i] - self.pf.s0[i]) > 1e-9:
                r, c, nn = self.unit_terms(i, s[i])
                rows[i] = (r, c, nn)
                req += np.clip(r, 0, None)
                rel += np.clip(-r, 0, None)
                un_cba += c
                un_net += nn
        return {"s": s.copy(), "req": req, "rel": rel, "un_cba": un_cba, "un_net": un_net, "rows": rows}

    def with_move(self, st: dict, i: int, budget: float) -> dict:
        if self.T is not None:
            dR = st["dR"].copy()
            dR[:, i] = self._unit_dR(i, budget) if abs(budget - self.pf.s0[i]) > 1e-9 else 0.0
            s = st["s"].copy()
            s[i] = budget
            return self._derive(s, dR)
        old = st["rows"].get(i)
        req, rel, un_cba, un_net = st["req"].copy(), st["rel"].copy(), st["un_cba"].copy(), st["un_net"].copy()
        if old is not None:
            req -= np.clip(old[0], 0, None)
            rel -= np.clip(-old[0], 0, None)
            un_cba -= old[1]
            un_net -= old[2]
        rows = dict(st["rows"])
        s = st["s"].copy()
        s[i] = budget
        if abs(budget - self.pf.s0[i]) > 1e-9:
            new = self.unit_terms(i, budget)
            rows[i] = new
            req += np.clip(new[0], 0, None)
            rel += np.clip(-new[0], 0, None)
            un_cba += new[1]
            un_net += new[2]
        else:
            rows.pop(i, None)
        return {"s": s, "req": req, "rel": rel, "un_cba": un_cba, "un_net": un_net, "rows": rows}

    def eff_units(self, st: dict) -> np.ndarray:
        """Effective delta units per draw and SKU after net-of-release rationing: (n, K)."""
        head = np.maximum(self.pf.atp[None, :] + st["rel"], 0.0)
        return np.minimum(st["req"], head) - st["rel"]

    def dcaa(self, st: dict) -> np.ndarray:
        pf = self.pf
        mapped = (self.eff_units(st) * pf.uc[None, :]).sum(1) if self.K else np.zeros(self.n)
        dspend = float(((st["s"] - pf.s0) * pf.pacing).sum()) * pf.H
        return mapped + st["un_cba"] - dspend

    def dnet(self, st: dict) -> np.ndarray:
        """Delta net revenue per draw: effective mapped units x nrpu + the unmapped booked revenue."""
        mapped = (self.eff_units(st) * self.pf.nrpu[None, :]).sum(1) if self.K else np.zeros(self.n)
        return mapped + st["un_net"]

    def set_excess(self, cover_days: float) -> None:
        """EXCESS band (spec §5): days of cover = available / baseline daily demand > cover_days; a zero forecast with
        stock on hand is UNBOUNDED cover, i.e. EXCESS."""
        pf = self.pf
        daily = pf.baseline / max(pf.H, 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            cover = np.where(daily > 0, pf.available / np.where(daily > 0, daily, 1.0), np.inf)
        self.excess_mask = (cover > cover_days) & (pf.available > 0)

    def excess_units_sold(self, st: dict) -> float:
        if not self.K or not getattr(self, "excess_mask", np.zeros(0, bool)).any():
            return 0.0
        return float(self.eff_units(st).mean(0)[self.excess_mask].sum())

    def risk(self, st: dict) -> tuple[np.ndarray, np.ndarray]:
        """(risk value, excess over the stage predicate) per SKU after the allocation in st."""
        return self.pf.risk(self.pf.baseline + self.eff_units(st).mean(0))

    def excess(self, st: dict) -> np.ndarray:
        return self.risk(st)[1]

    def shortfall(self, st: dict) -> np.ndarray:
        """Stage 1 projected shortfall (units) or Stage 2 P(stockout): the stage's risk value."""
        return self.risk(st)[0]

    def exposure(self, st: dict) -> np.ndarray:
        at_risk = (self.excess(st) > 1e-9).astype(float)
        return self.pf.W @ at_risk + self.pf.u if self.K else self.pf.u.copy()

    def gate_ok(self, st: dict) -> bool:
        """Inventory gate for every unit receiving an increase: BLOCK above the block share; in LIMIT an increase may
        not raise any mapped SKU's risk beyond max(its current risk, the predicate threshold)."""
        inc = st["s"] > self.pf.s0 + 1e-9
        if not inc.any():
            return True
        risk, excess = self.risk(st)
        x = (self.pf.W @ (excess > 1e-9).astype(float) + self.pf.u) if self.K else self.pf.u
        tol = 1e-6 if self.kind == "PROJECTED_SHORTFALL" else 1e-9
        for i in np.flatnonzero(inc):
            if x[i] > self.gate["block_above"]:
                return False
            if x[i] >= self.gate["allow_below"]:
                mapped = self.pf.W[i] > 0
                if (risk[mapped] > np.maximum(self.base_risk[mapped], self.threshold) + tol).any():
                    return False
        return True


def search_draws(state: PortfolioState, ec: dict | None = None) -> np.ndarray:
    """The fixed, evenly spaced subset of joint draws the search (and the inventory-gate predicate) evaluates."""
    ec = ec or objectives_config()["economics"]
    n_all = state.n_draws
    return np.linspace(0, n_all - 1, min(int(ec["search_draws"]), n_all)).round().astype(int)


def gate_label(x: float, gate: dict) -> str:
    return "BLOCK" if x > gate["block_above"] else ("LIMIT" if x >= gate["allow_below"] else "ALLOW")


class Optimizer:
    def __init__(self, state: PortfolioState, flags: dict[str, list[str]] | None = None,
                 guardrails: dict | None = None, objectives: dict | None = None, mode: str | None = None,
                 lam: float | None = None, portfolios: tuple[Portfolio, Portfolio] | None = None):
        self.state = state
        self.g = guardrails or guardrails_config()
        oc = objectives or objectives_config()
        self.mode = mode or oc.get("selected", "PROFIT")
        if self.mode not in ("PROFIT", "GROWTH", "INVENTORY_CLEARANCE"):
            raise ValueError(f"objective {self.mode} is not built (Stage 2: PROFIT, GROWTH, INVENTORY_CLEARANCE)")
        mc = oc.get(self.mode, {})
        self.lam = float(lam if lam is not None else mc.get("lambda", oc["PROFIT"]["lambda"]))
        self.ec = oc["economics"]
        self.step = float(self.ec["greedy_step_inr"])
        self.inc = float(self.ec["allocation_increment_inr"])
        self.c = build_constraints(state, flags, self.g)
        if portfolios is not None:                  # reuse (sensitivity scenarios share the revenue caches)
            self.pf, self.full = portfolios
        else:
            self.pf = Portfolio(state, search_draws(state, self.ec))  # search subset (fixed, deterministic)
            self.full = Portfolio(state)            # all draws, for reporting
        self.fe = FastEval(self.pf, self.g["inventory_gate"])
        self.caa0 = float(self.full.base_cba.mean())
        self.caa_tolerance = max(float(mc.get("caa_floor_pct", 0.05)) * abs(self.caa0),
                                 float(mc.get("min_tolerance_inr", 2000.0))) if self.mode == "GROWTH" else 0.0
        self.h = float(mc.get("holding_cost_inr_per_unit", 0.0)) if self.mode == "INVENTORY_CLEARANCE" else 0.0
        if self.mode == "INVENTORY_CLEARANCE":
            self.fe.set_excess(float(mc.get("excess_cover_days", 45.0)))
        self.s0 = self.pf.s0.copy()
        self.channels = [u.channel for u in state.units]

    # ---- feasibility ---------------------------------------------------------------------------------------------
    def linear_ok(self, s: np.ndarray, tol: float = 1e-6) -> bool:
        c = self.c
        if (s < c.lo - tol).any() or (s > c.hi + tol).any():
            return False
        if s.sum() > c.cap_total + tol or np.abs(s - self.s0).sum() > c.moved_cap + tol:
            return False
        tot = s.sum()
        for ch, mx in c.channel_max.items():
            if tot > 0 and s[[i for i, x in enumerate(self.channels) if x == ch]].sum() / tot > mx + tol:
                return False
        for ch, mn in c.channel_min.items():
            if tot > 0 and s[[i for i, x in enumerate(self.channels) if x == ch]].sum() / tot < mn - tol:
                return False
        return True

    def objective(self, d: np.ndarray) -> float:
        """The PROFIT risk-adjusted value of dCAA draws: E - lambda (E - P10)."""
        e = float(d.mean())
        return e - self.lam * (e - float(np.percentile(d, 10)))

    def value(self, st: dict) -> float:
        """The selected objective's value of an allocation state."""
        if self.mode == "GROWTH":
            return float(self.fe.dnet(st).mean())
        if self.mode == "INVENTORY_CLEARANCE":
            return float(self.fe.dcaa(st).mean()) + self.h * float(self.fe.excess_units_sold(st))
        return self.objective(self.fe.dcaa(st))

    def objective_ok(self, st: dict) -> bool:
        """Objective constraints (GROWTH: the CAA floor)."""
        if self.mode == "GROWTH":
            return float(self.fe.dcaa(st).mean()) >= -self.caa_tolerance - 1e-6
        return True

    # ---- 1. greedy marginal allocation ---------------------------------------------------------------------------
    def greedy(self, max_iter: int = 5000) -> tuple[np.ndarray, list[dict]]:
        st = self.fe.state_of(self.s0)
        cur = self.value(st)
        ladder = []
        for _ in range(max_iter):
            best = None
            for i in range(len(self.s0)):
                for sign in (1, -1):
                    nb = float(np.clip(st["s"][i] + sign * self.step, self.c.lo[i], self.c.hi[i]))
                    if abs(nb - st["s"][i]) < 1e-9:
                        continue
                    cand_s = st["s"].copy()
                    cand_s[i] = nb
                    if not self.linear_ok(cand_s):
                        continue
                    cand = self.fe.with_move(st, i, nb)
                    if sign > 0 and not self.fe.gate_ok(cand):
                        continue
                    if not self.objective_ok(cand):
                        continue
                    v = self.value(cand)
                    if v > cur + 1e-6 and (best is None or v > best[0]):
                        best = (v, i, nb, cand)
            if best is None:
                break
            v, i, nb, cand = best
            ladder.append({"unit_id": self.state.units[i].unit_id, "from": float(st["s"][i]), "to": nb,
                           "objective_gain": v - cur})
            st, cur = cand, v
        return st["s"], ladder

    # ---- 2. SLSQP polish -------------------------------------------------------------------------------------------
    def slsqp(self, start: np.ndarray) -> tuple[np.ndarray | None, str]:
        scale = np.maximum(self.s0, 1.0)

        def f(z):
            s = z * scale
            return -self.value(self.fe.state_of(s)) / 1000.0

        cons = [{"type": "ineq", "fun": lambda z: (self.c.cap_total - (z * scale).sum()) / 1000.0}]
        if self.mode == "GROWTH":
            cons.append({"type": "ineq", "fun": lambda z: (float(self.fe.dcaa(self.fe.state_of(z * scale)).mean())
                                                         + self.caa_tolerance) / 1000.0})
        try:
            res = minimize(f, start / scale, method="SLSQP", bounds=list(zip(self.c.lo / scale, self.c.hi / scale,
                                                                            strict=True)),
                           constraints=cons, options={"maxiter": 50, "ftol": 1e-6})
        except (ValueError, FloatingPointError) as exc:
            return None, f"SOLVER_FAILED: {exc}"
        return res.x * scale, "OK" if res.success else f"SOLVER_FAILED: {res.message}"

    def feasible(self, s: np.ndarray) -> bool:
        st = self.fe.state_of(s)
        return self.linear_ok(s) and self.fe.gate_ok(st) and self.objective_ok(st)

    # ---- 3. round -> repair -> revalidate ---------------------------------------------------------------------------
    def round_repair(self, s: np.ndarray) -> np.ndarray | None:
        out = s.copy()
        for i in range(len(out)):
            if abs(out[i] - self.s0[i]) < self.inc / 2:
                out[i] = self.s0[i]
            else:
                out[i] = np.round(out[i] / self.inc) * self.inc
        for _ in range(10 * len(out)):
            if self.feasible(out):
                return out
            resid = out - s
            moved = False
            for i in np.argsort(-np.abs(resid)):
                if out[i] > self.c.hi[i] + 1e-9 or (out.sum() > self.c.cap_total + 1e-9 and out[i] > self.s0[i]):
                    out[i] = max(out[i] - self.inc, self.s0[i] if out[i] > self.s0[i] else self.c.lo[i])
                    moved = True
                    break
                if out[i] < self.c.lo[i] - 1e-9:
                    out[i] = min(out[i] + self.inc, self.c.hi[i])
                    moved = True
                    break
                if np.abs(out - self.s0).sum() > self.c.moved_cap + 1e-9 and abs(out[i] - self.s0[i]) > 1e-9:
                    out[i] += -self.inc if out[i] > self.s0[i] else self.inc
                    moved = True
                    break
            if not moved:
                break
        return out if self.feasible(out) else None

    # ---- why not (at the final allocation) ---------------------------------------------------------------------------
    def up_probe(self, i: int, s_i: float) -> float:
        """The largest increase up to one step that lands on the allocation-increment grid inside the box (0 = none)."""
        top = np.floor(min(s_i + self.step, self.c.hi[i]) / self.inc + 1e-9) * self.inc
        return max(float(top - s_i), 0.0)

    def why_not(self, s: np.ndarray) -> list[dict]:
        st = self.fe.state_of(s)
        base_v = self.value(st)
        out = []
        for i, u in enumerate(self.state.units):
            if s[i] > self.s0[i] + 1e-9:
                continue
            entry = {"unit_id": u.unit_id, "channel": u.channel}
            reasons = self.c.unit_reasons.get(i, [])
            probe = self.up_probe(i, s[i])
            nb = s[i] + probe
            cand_s = s.copy()
            cand_s[i] = nb
            if reasons:
                entry.update(binding_constraint=reasons[0], all_constraints=reasons)
            elif probe <= 1e-9:
                entry["binding_constraint"] = "UNIT_MAX" if self.g["change"].get("unit_max_budget_inr") and \
                    self.c.hi[i] < self.s0[i] * (1 + self.g["change"]["max_daily_change_pct"]) - 1e-6 \
                    else "MAX_DAILY_CHANGE"
            elif not self.fe.gate_ok(self.fe.with_move(st, i, nb)):
                x = float(self.fe.exposure(self.fe.with_move(st, i, nb))[i])
                entry.update(binding_constraint="INVENTORY_GATE", exposure=x,
                             gate=gate_label(x, self.g["inventory_gate"]), risk_kind=self.fe.kind)
            elif np.abs(cand_s - self.s0).sum() > self.c.moved_cap + 1e-6:
                entry["binding_constraint"] = "DAILY_RUPEES_MOVED_CAP"
            elif cand_s.sum() > self.c.cap_total + 1e-6:
                entry["binding_constraint"] = "CASH_RESERVE" if self.c.R > 0 else "TOTAL_BUDGET"
                entry["transfer"] = self._best_transfer(s, st, i, base_v, probe)
            elif not self.linear_ok(cand_s):
                entry["binding_constraint"] = "CHANNEL_SHARE_MAX"
            elif not self.objective_ok(self.fe.with_move(st, i, nb)):
                entry["binding_constraint"] = "OBJECTIVE_CAA_FLOOR"
                entry["caa_floor"] = self.caa0 - self.caa_tolerance
            else:
                v = self.value(self.fe.with_move(st, i, nb))
                unit = {"GROWTH": " of expected net revenue"}.get(self.mode, "")
                entry.update(binding_constraint=None, reason="MARGINAL_VALUE_NONPOSITIVE",
                             marginal_value_per_step=v - base_v, step=probe,
                             text=f"Marginal {self.mode} value = {v - base_v:+,.0f} rupees{unit} per "
                                  f"{probe:,.0f} rupees")
            out.append(entry)
        return out

    def _best_transfer(self, s, st, r, base_v, amount: float) -> dict | None:
        """The best FEASIBLE transfer of `amount` from a unit holding budget to r (all constraints re-checked).
        A comparison, not a shadow price: no optimality or dual-price language."""
        best = None
        for d in range(len(s)):
            if d == r or s[d] - amount < self.c.lo[d] - 1e-9:
                continue
            cand_s = s.copy()
            cand_s[d] -= amount
            cand_s[r] += amount
            if not self.linear_ok(cand_s):
                continue
            cand = self.fe.with_move(self.fe.with_move(st, d, cand_s[d]), r, cand_s[r])
            if not self.fe.gate_ok(cand) or not self.objective_ok(cand):
                continue
            v = self.value(cand) - base_v
            if best is None or v > best["objective_change"]:
                best = {"from_unit": self.state.units[d].unit_id, "amount": amount, "objective_change": v,
                        "text": f"moving {amount:,.0f} rupees from {self.state.units[d].unit_id} to "
                                f"{self.state.units[r].unit_id} would change the {self.mode} objective by {v:+,.0f}"}
        return best

    # ---- diagnostics ------------------------------------------------------------------------------------------------
    def concavity(self, s: np.ndarray) -> dict:
        st = self.fe.state_of(s)
        f0 = self.value(st)
        bad, checked = [], 0
        for i in range(len(s)):
            if abs(s[i] - self.s0[i]) < 1e-9:
                continue
            checked += 1
            up = self.value(self.fe.with_move(st, i, s[i] + self.step))
            dn = self.value(self.fe.with_move(st, i, max(s[i] - self.step, 0.0)))
            if up + dn - 2 * f0 > 1e-6:
                bad.append(self.state.units[i].unit_id)
        return {"checked_units": checked, "non_concave_units": bad}

    # ---- full solve -------------------------------------------------------------------------------------------------
    def solve(self) -> dict:
        greedy_s, ladder = self.greedy()
        g_v = self.value(self.fe.state_of(greedy_s))
        sl_s, sl_status = self.slsqp(greedy_s)
        sl_v = None
        best_s, chosen = greedy_s, "greedy"
        if sl_s is not None and self.feasible(sl_s):
            sl_v = self.value(self.fe.state_of(sl_s))
            if sl_v > g_v + 1e-6:
                best_s, chosen = sl_s, "slsqp"
        final = self.round_repair(best_s)
        if final is None and chosen == "slsqp":
            final, chosen = self.round_repair(greedy_s), "greedy"
        if final is None:
            return {"status": "ROUNDING_INFEASIBLE", "greedy_objective": g_v}
        econ = self.full.evaluate(final)
        summ = econ.summary()
        units = self.state.units
        unallocated = self.c.B - float(final.sum())
        legs = [{"unit_id": u.unit_id, "platform": u.platform, "channel": u.channel, "campaign_ids": u.campaign_ids,
                 "budget_id": u.unit_id, "is_shared": u.is_shared, "before": float(self.s0[i]),
                 "after": float(final[i])}
                for i, u in enumerate(units) if abs(final[i] - self.s0[i]) > 1e-9]
        legs.sort(key=lambda leg: (leg["after"] > leg["before"], leg["unit_id"]))  # risk-reducing legs first
        st = self.fe.state_of(final)
        next_best = None
        for i in range(len(final)):
            nb = final[i] + self.up_probe(i, final[i])
            if i in self.c.unit_reasons or nb <= final[i] + 1e-9:
                continue
            cand_s = final.copy()
            cand_s[i] = nb
            cand = self.fe.with_move(st, i, nb)
            if not self.linear_ok(cand_s) or not self.fe.gate_ok(cand) or not self.objective_ok(cand):
                continue  # only feasible receivers explain why cash is left unallocated
            v = self.value(cand) - self.value(st)
            if next_best is None or v > next_best[1]:
                next_best = (units[i].unit_id, v)
        x = econ.exposure_by_unit
        return {
            "status": "OK", "objective": self.mode, "lambda": self.lam, "solver": chosen,
            "objective_value": self.value(self.fe.state_of(final)), "caa0": self.caa0,
            "caa_floor": (self.caa0 - self.caa_tolerance) if self.mode == "GROWTH" else None,
            "excess_units_sold": float(self.fe.excess_units_sold(self.fe.state_of(final)))
            if self.mode == "INVENTORY_CLEARANCE" else None,
            "allocation": {u.unit_id: float(final[i]) for i, u in enumerate(units)},
            "baseline": {u.unit_id: float(self.s0[i]) for i, u in enumerate(units)},
            "legs": legs,
            "expected": {**summ, "raw_pred": summ["E"], "search_draws": len(self.pf.draws),
                         "report_draws": len(self.full.draws)},
            "daily_delta_caa_p10_cum": np.percentile(np.cumsum(econ.daily_delta_caa, axis=1), 10, axis=0).tolist(),
            "inventory_risk_after": econ.inventory_risk_by_sku,
            "inventory_gate": {u: {"exposure": v, "gate": gate_label(v, self.g["inventory_gate"])}
                               for u, v in x.items()},
            "wasted_spend": econ.wasted_spend, "baseline_deficit_skus": econ.baseline_deficit_skus,
            "unallocated": unallocated, "reserve_floor": self.c.R, "total_budget": self.c.B,
            "unallocated_reason": (None if unallocated <= self.c.R + 1e-6 else
                                   {"next_best_unit": next_best[0] if next_best else None,
                                    "marginal_value_per_step": next_best[1] if next_best else None,
                                    "step": self.step}),
            "reserve_baseline_infeasible": self.c.reserve_baseline_infeasible,
            "why_not": self.why_not(final), "marginal_ladder": ladder,
            "greedy_vs_slsqp": {"greedy_objective": g_v, "slsqp_objective": sl_v, "slsqp_status": sl_status,
                                "gap": None if sl_v is None else sl_v - g_v},
            "concavity": self.concavity(final),
            "constraints": {"lo": dict(zip([u.unit_id for u in units], self.c.lo.tolist(), strict=True)),
                            "hi": dict(zip([u.unit_id for u in units], self.c.hi.tolist(), strict=True)),
                            "cap_total": self.c.cap_total, "moved_cap": self.c.moved_cap,
                            "unit_reasons": {units[i].unit_id: r for i, r in self.c.unit_reasons.items()}},
        }


def optimize(state: PortfolioState, flags: dict[str, list[str]] | None = None, **kw) -> dict:
    return Optimizer(state, flags, **kw).solve()
