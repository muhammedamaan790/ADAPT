"""Assemble the PortfolioState that portfolio_economics values (B6), from the canonical state as of a logical time.

Everything read here is in the canonical tables, which only contain rows with available_at <= as_of.
- units: one per budget (fit_curves.budget_units), s0 = current budget, pacing = delivered spend / budget over the
  trailing window (budget per day from core.budget_history)
- SKU mix: each unit's campaign_sku attribution weights (campaigns of a shared budget weighted by attributed revenue)
- nrpu / unit contribution: latest SCD2 price, COGS, ship cost, fee %, return rate and the trailing discount rate
- inventory: the last complete day's stock; available = on_hand - reserved + inbound_confidence x inbound arriving
  within H; baseline demand = seasonal-naive (last 7 days' mean)
A SKU with no usable price (nrpu <= 0) cannot be converted to units; its weight joins the unmapped share.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from adapt.economics.portfolio import PortfolioState, SkuState, UnitState
from adapt.metrics.formulas import nrpu as nrpu_fn
from adapt.metrics.formulas import unit_contribution
from adapt.predict.curves import CurveArtifact
from adapt.predict.fit_curves import budget_units, load_curves
from adapt.reconcile.core import UNMAPPED

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@lru_cache
def objectives_config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "objectives.yaml").read_text(encoding="utf-8"))


@lru_cache
def guardrails_config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "guardrails.yaml").read_text(encoding="utf-8"))


def _in(ids) -> str:
    return ", ".join("?" * len(ids))


def load_state(db, as_of: datetime, curves: dict[str, CurveArtifact] | None = None,
               horizon: int | None = None) -> PortfolioState:
    ec = objectives_config()["economics"]
    H = int(horizon or ec["horizon_days"])
    last = as_of.date() - timedelta(days=1)
    lo = last - timedelta(days=ec["attribution_window_days"] - 1)
    pace_lo = last - timedelta(days=ec["pacing_window_days"] - 1)
    curves = curves if curves is not None else load_curves(db)

    budgets = {b: (p, float(a or 0), bool(sh), st) for b, p, a, sh, st in
               db.query("SELECT budget_id, platform, current_amount_inr, is_shared, status FROM core.budgets")}
    weights_raw = db.query("SELECT campaign_id, sku, attribution_weight FROM core.campaign_sku")
    camp_w: dict[str, dict[str, float]] = {}
    for cid, sku, w in weights_raw:
        camp_w.setdefault(cid, {})[sku] = float(w)

    # ---- SKUs: unit economics + inventory ------------------------------------------------------------------------
    price_rows = db.query("""SELECT sku, price, cogs, ship_cost, fee_pct, return_rate FROM core.pricing_snapshots
                             QUALIFY row_number() OVER (PARTITION BY sku ORDER BY valid_from DESC) = 1""")
    disc = dict(db.query("""SELECT sku, sum(discount) / nullif(sum(gross), 0) FROM core.order_items
                            WHERE analysis_date BETWEEN ? AND ? GROUP BY 1""", [lo, last]))
    inv = {r[0]: r[1:] for r in db.query(
        """SELECT sku, on_hand, reserved, inbound_qty, expected_arrival, inbound_confidence, safety_stock
           FROM marts.sku_daily WHERE date = ?""", [last])}
    sold7 = dict(db.query("SELECT sku, sum(units) FROM marts.sku_daily WHERE date BETWEEN ? AND ? GROUP BY 1",
                          [last - timedelta(days=6), last]))
    skus: dict[str, SkuState] = {}
    for sku, price, cogs, ship, fee, rr in price_rows:
        if price is None or sku not in inv:
            continue
        d = min(max(float(disc.get(sku) or 0.0), 0.0), 0.95)
        n = nrpu_fn(float(price), d, min(max(float(rr or 0.0), 0.0), 0.95))
        if n <= 0:
            continue
        on_hand, reserved, inbound, arrival, conf, ss = inv[sku]
        inb = float(inbound or 0) if arrival is not None and arrival <= last + timedelta(days=H) else 0.0
        skus[sku] = SkuState(sku, n, unit_contribution(n, float(cogs or 0), float(ship or 0), float(fee or 0)),
                             float(on_hand or 0) - float(reserved or 0) + float(conf or 0) * inb, float(ss or 0),
                             float(sold7.get(sku) or 0) / 7.0, float(on_hand or 0))

    # ---- units ------------------------------------------------------------------------------------------------------
    units: list[UnitState] = []
    for bid, cids, channel, _ps in budget_units(db):
        platform, amount, shared, _status = budgets.get(bid, (None, 0.0, False, None))
        if amount <= 0:
            amount = float(db.query(f"SELECT coalesce(sum(current_budget_inr), 0) FROM core.campaigns "
                                    f"WHERE campaign_id IN ({_in(cids)})", cids)[0][0])
        spend, budget_days = db.query(f"""
            SELECT sum(cd.spend), sum(bh.value) FROM
              (SELECT date, sum(spend) AS spend FROM marts.campaign_daily WHERE campaign_id IN ({_in(cids)})
                 AND date BETWEEN ? AND ? GROUP BY 1) cd
            LEFT JOIN core.budget_history bh ON bh.entity_id = ? AND bh.effective_from <= cd.date
                 AND (bh.effective_to IS NULL OR cd.date < bh.effective_to)""", [*cids, pace_lo, last, bid])[0]
        pacing = float(spend) / float(budget_days) if spend and budget_days else 1.0
        pacing = min(max(pacing, 0.3), 1.2)
        rev = db.query(f"""SELECT campaign_id, sum(attributed_net_revenue), sum(spend) FROM marts.campaign_daily
                           WHERE campaign_id IN ({_in(cids)}) AND date BETWEEN ? AND ? GROUP BY 1""",
                       [*cids, lo, last])
        tot_rev = sum(float(r or 0) for _, r, _ in rev)
        tot_spend = sum(float(s or 0) for _, _, s in rev)
        share = {c: (float(r or 0) / tot_rev if tot_rev > 0 else 1 / len(cids)) for c, r, _ in rev} or \
            {c: 1 / len(cids) for c in cids}
        w: dict[str, float] = {}
        for c in cids:
            for sku, x in camp_w.get(c, {UNMAPPED: 1.0}).items():
                w[sku] = w.get(sku, 0.0) + share.get(c, 0.0) * x
        mapped = {k: v for k, v in w.items() if k != UNMAPPED and k in skus and v > 0}
        u = max(0.0, 1.0 - sum(mapped.values()))
        mapped_list = list(mapped)
        unm = db.query(f"""SELECT sum(cba), sum(net_revenue) FROM core.order_items
                           WHERE campaign_id IN ({_in(cids)}) AND analysis_date BETWEEN ? AND ?
                             {f"AND sku NOT IN ({_in(mapped_list)})" if mapped_list else ""}""",
                       [*cids, lo, last, *mapped_list])[0]
        if not unm[1]:
            unm = db.query(f"""SELECT sum(cba), sum(net_revenue) FROM core.order_items
                               WHERE campaign_id IN ({_in(cids)}) AND analysis_date BETWEEN ? AND ?""",
                           [*cids, lo, last])[0]
        ucr = float(unm[0]) / float(unm[1]) if unm[1] else 0.0
        units.append(UnitState(
            unit_id=bid, channel=channel, platform=platform or "", campaign_ids=list(cids), budget=amount,
            pacing=pacing, curve=curves.get(bid), observed_roas=tot_rev / tot_spend if tot_spend > 0 else 0.0,
            sku_weights=mapped, unmapped_share=u, unmapped_cr=ucr, is_shared=shared))

    # ---- non-modelled baseline (organic, direct, email, unpaid): constant across allocations ----------------------
    other = db.query("""SELECT sum(cba), sum(net_revenue) FROM core.order_items
                        WHERE campaign_id IS NULL AND analysis_date BETWEEN ? AND ?""", [lo, last])[0]
    days = (last - lo).days + 1
    return PortfolioState(as_of=as_of, horizon=H, units=units, skus=skus,
                          other_cba_daily=float(other[0] or 0) / days,
                          other_net_revenue_daily=float(other[1] or 0) / days,
                          meta={"last_complete_day": last.isoformat()})


def inputs_manifest(state: PortfolioState) -> dict:
    """EXACT-class inputs read by portfolio_economics (spec §2 staleness classes); hashed into economics_hash by C3.
    TOLERANCE (stock) and STATUS (incidents, health) classes are added by the decision layer."""
    return {
        "units": {u.unit_id: {"budget": u.budget, "status": None if u.curve is None else u.curve.status,
                              "sku_weights": u.sku_weights, "unmapped_share": u.unmapped_share,
                              "unmapped_cr": u.unmapped_cr} for u in state.units},
        "skus": {k: {"nrpu": s.nrpu, "unit_contribution": s.unit_contribution, "safety_stock": s.safety_stock}
                 for k, s in state.skus.items()},
        "horizon": state.horizon,
    }
