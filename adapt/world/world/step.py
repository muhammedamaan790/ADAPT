"""world.step: one simulated day (spec §10). Runs inside the world-state transaction of an `advance` request.

Order of events in a day (all randomness keyed by (seed, day, entity, purpose), so call order never matters):
  1. inbound purchase orders arrive (ERP receipts)
  2. paid: per campaign, budget -> spend -> CPM -> impressions -> per-creative CTR -> conditional funnel
  3. unpaid: Email / Organic orders per category, plus the unmapped warehouse demand
  4. stock rationing: per SKU, purchase attempts are filled in a fixed random order until on-hand runs out;
     the rest are lost (the shopper met an out-of-stock page). Warehouse items are never rationed.
  5. orders, returns, platform-claimed conversions (with view-through), GA4 recording, ERP (s, Q) reordering,
     creative fatigue.
Facts are the world's "platform databases"; reporting endpoints render them in native formats.
fact_lost_demand is world-internal (eval only): no real platform reports demand lost to stockouts.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from world.funnel import FunnelInputs, n_max, prospect_funnel
from world.priors import benchmarks
from world.rng import generator, uniforms
from world.scenarios import SCENARIO_SCHEMA, apply_day_events, effects_for_day, op_activate_scenario, po_blocked
from world.state import WorldContext, WorldStateConflict, WorldStore
from world.truth import Truth, cpm_at, frequency, spend_for_purchases

WAREHOUSE = "__warehouse__"
SOURCE_MEDIUM = {
    "google_search": ("google", "cpc"),
    "google_video": ("youtube", "cpc"),
    "meta_prospecting": ("facebook", "paid_social"),
    "meta_retargeting": ("facebook", "paid_social"),
    "email": ("email", "email"),
    "organic": ("google", "organic"),
    "tiktok": ("tiktok", "paid_social"),            # Stage 2 SIMULATED channels
    "amazon_sp": ("amazon", "sponsored_products"),  # marketplace orders: never in the web store or GA4
    "amazon_organic": ("amazon", "organic"),
}
MARKETPLACE = ("amazon_sp", "amazon_organic")
SOURCE_RANK = {k: i for i, k in enumerate(SOURCE_MEDIUM)}

STEP_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign_status (campaign_id VARCHAR PRIMARY KEY, status VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS creative_state (creative_id VARCHAR PRIMARY KEY, cum_impressions BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS price_state (
    sku VARCHAR PRIMARY KEY, price_inr DOUBLE NOT NULL, base_price_inr DOUBLE NOT NULL
);
CREATE TABLE IF NOT EXISTS price_history (
    sku VARCHAR NOT NULL, price_inr DOUBLE NOT NULL, effective_from_day INTEGER NOT NULL,
    PRIMARY KEY (sku, effective_from_day)
);
CREATE TABLE IF NOT EXISTS inventory_state (
    sku VARCHAR PRIMARY KEY, on_hand BIGINT NOT NULL, inbound_qty BIGINT NOT NULL, inbound_day INTEGER,
    reorder_point BIGINT NOT NULL, order_qty BIGINT NOT NULL, lead_time_days INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS fact_ad_campaign_daily (
    day INTEGER, platform VARCHAR, campaign_id VARCHAR, impressions BIGINT, reach BIGINT, clicks BIGINT,
    sessions BIGINT, purchases BIGINT, orders BIGINT, spend_inr DOUBLE, cpm_inr DOUBLE, PRIMARY KEY (day, campaign_id)
);
CREATE TABLE IF NOT EXISTS fact_ad_creative_daily (
    day INTEGER, platform VARCHAR, campaign_id VARCHAR, adset_id VARCHAR, creative_id VARCHAR, impressions BIGINT,
    clicks BIGINT, spend_inr DOUBLE, conversions BIGINT, conversion_value_inr DOUBLE, PRIMARY KEY (day, creative_id)
);
CREATE TABLE IF NOT EXISTS fact_orders (
    order_id BIGINT PRIMARY KEY, day INTEGER, minute INTEGER, customer_id BIGINT, is_new_customer BOOLEAN,
    source VARCHAR, medium VARCHAR, channel VARCHAR, campaign_id VARCHAR, adset_id VARCHAR, creative_id VARCHAR,
    sku VARCHAR, category_code VARCHAR, qty INTEGER, unit_price_inr DOUBLE, discount_inr DOUBLE, cogs_inr DOUBLE,
    ship_cost_inr DOUBLE, payment_fee_inr DOUBLE, returned BOOLEAN, return_day INTEGER
);
CREATE TABLE IF NOT EXISTS fact_ga_daily (
    day INTEGER, source VARCHAR, medium VARCHAR, campaign_id VARCHAR, sessions BIGINT, purchases BIGINT,
    revenue_inr DOUBLE
);
CREATE TABLE IF NOT EXISTS fact_erp_daily (
    day INTEGER, sku VARCHAR, on_hand BIGINT, reserved BIGINT, inbound_qty BIGINT, inbound_day INTEGER,
    receipts BIGINT, units_sold BIGINT, PRIMARY KEY (day, sku)
);
CREATE TABLE IF NOT EXISTS fact_lost_demand (
    day INTEGER, sku VARCHAR, channel VARCHAR, campaign_id VARCHAR, units BIGINT
);
"""


def creative_quality(truth: Truth, creative_id: str, cum_impressions: int) -> float:
    """CTR multiplier of a creative: its base multiplier x wear-out (halves every half-life of delivery)."""
    ct = truth.creatives[creative_id]
    return ct.ctr_mult * 0.5 ** (cum_impressions / ct.fatigue_half_life_impressions)


def manager_budgets(truth: Truth, cum: dict[str, int], from_day: int, to_day: int) -> dict[str, float]:
    """The history 'human manager': budgets that should deliver the month's expected paid purchases.

    Uses what the manager can observe: current creative wear (delivery-weighted quality of live creatives) and
    the average effect of pacing, weekly CPM and daily noise. Rounded to Rs 100. world-internal (history only).
    """
    bench = benchmarks()
    sig = bench["daily_log_sigma"]
    lo, hi = bench["step"]["pacing"]
    pacing = 0.5 * (lo + hi)
    cpm_mult = float(np.mean(bench["weekly_cpm_profile"])) * math.exp(sig["cpm"] ** 2 / 2)
    noise_mult = math.exp(sig["ctr"] ** 2 / 2 + sig["cvr"] ** 2 / 2)
    rates = truth.category_rates.set_index("category_code")
    days = range(from_day, to_day + 1)
    out: dict[str, float] = {}
    for budget_id, members in truth.catalog.budgets().items():
        total = 0.0
        for cid in members:
            t = truth.campaigns[cid]
            live = [cr for cr in truth.catalog.creatives if cr.campaign_id == cid and cr.launch_day <= from_day]
            q = np.array([creative_quality(truth, cr.creative_id, cum.get(cr.creative_id, 0)) for cr in live])
            kappa = bench["step"]["delivery_optimization"]  # delivery ~ q^kappa, so served quality = E_w[q]
            served_quality = float((q ** (1 + kappa)).sum() / (q ** kappa).sum()) if q.size else 1.0
            demand = float(np.mean([truth.demand_index(t.category_code, d) for d in days]))
            target = rates.loc[t.category_code, f"paid_{t.channel}"] * demand
            spend = spend_for_purchases(target, t, demand, ctr_mult=served_quality * noise_mult, cpm_mult=cpm_mult)
            total += spend / pacing
        out[budget_id] = float(round(total / 100.0) * 100.0)
    return out


def _normal(seed: int, day: int, entity: str, purpose: str) -> float:
    return float(generator(seed, day, entity, purpose).standard_normal())


def _insert(cur: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    cur.register("_frame", df)
    try:
        cur.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _frame")
    finally:
        cur.unregister("_frame")


def expected_sku_demand(truth: Truth, day: int) -> dict[str, float]:
    """Expected daily purchase attempts per promoted SKU (all sources), used to initialise the ERP."""
    rates = truth.category_rates.set_index("category_code")
    cols = [c for c in rates.columns if c.startswith(("paid_", "unpaid_"))]
    out = {}
    for cat in truth.catalog.categories:
        total = float(rates.loc[cat.code, cols].sum()) * truth.demand_index(cat.code, day)
        for s in truth.catalog.skus_of(cat.code):
            out[s.sku] = total * s.mix_weight
    return out


# ---- operations registered on the store ---------------------------------------------------------------------
def op_init_world(cur: duckdb.DuckDBPyConnection, payload: dict[str, Any], ctx: WorldContext) -> dict[str, Any]:
    """Initial prices, creative wear and stock (initial_cover_days of expected demand) at the clock day."""
    truth: Truth = ctx.truth
    inv_cfg = benchmarks()["inventory"]
    row = cur.execute("SELECT day FROM world_clock WHERE id = 1").fetchone()
    if row is None:
        raise WorldStateConflict("world not seeded: call reset first")
    day = row[0]
    demand = expected_sku_demand(truth, day)
    for s in truth.catalog.skus:
        cur.execute("INSERT OR REPLACE INTO price_state VALUES (?, ?, ?)", [s.sku, s.unit_price_inr, s.unit_price_inr])
        cur.execute("INSERT OR REPLACE INTO price_history VALUES (?, ?, ?)", [s.sku, s.unit_price_inr, day])
        lead = truth.sku_lead_time[s.sku]
        d = demand[s.sku]
        cur.execute(
            "INSERT OR REPLACE INTO inventory_state VALUES (?, ?, 0, NULL, ?, ?, ?)",
            [s.sku, math.ceil(d * inv_cfg["initial_cover_days"]), math.ceil(d * (lead + inv_cfg["safety_days"])),
             math.ceil(d * inv_cfg["order_cover_days"]), lead],
        )
    for cr in truth.catalog.creatives:
        cur.execute("INSERT OR REPLACE INTO creative_state VALUES (?, 0)", [cr.creative_id])
    return {"day": day, "skus": len(truth.catalog.skus), "creatives": len(truth.catalog.creatives)}


def op_set_inventory(cur: duckdb.DuckDBPyConnection, payload: dict[str, Any], ctx: WorldContext) -> dict[str, Any]:
    """Control plane: absolute on-hand for a SKU, optionally cancelling its open purchase order (scenario S3)."""
    sku, on_hand = payload["sku"], int(payload["on_hand"])
    if on_hand < 0:
        raise ValueError("on_hand must be >= 0")
    if cur.execute("SELECT 1 FROM inventory_state WHERE sku = ?", [sku]).fetchone() is None:
        raise ValueError(f"unknown sku {sku}")
    cur.execute("UPDATE inventory_state SET on_hand = ? WHERE sku = ?", [on_hand, sku])
    if payload.get("cancel_inbound"):
        cur.execute("UPDATE inventory_state SET inbound_qty = 0, inbound_day = NULL WHERE sku = ?", [sku])
    return {"sku": sku, "on_hand": on_hand, "cancel_inbound": bool(payload.get("cancel_inbound"))}


def make_store(path, truth: Truth) -> WorldStore:
    ctx = WorldContext(truth=truth, simulate_day=lambda cur, seed, day: simulate_day(cur, truth, seed, day))
    return WorldStore(path, ctx=ctx, extra_schema=STEP_SCHEMA + SCENARIO_SCHEMA,
                      extra_ops={"init_world": op_init_world, "set_inventory": op_set_inventory,
                                 "activate_scenario": op_activate_scenario})


# ---- the day ------------------------------------------------------------------------------------------------------
def simulate_day(cur: duckdb.DuckDBPyConnection, truth: Truth, seed: int, day: int) -> dict[str, Any]:
    bench = benchmarks()
    st, inv_cfg, sigma = bench["step"], bench["inventory"], bench["daily_log_sigma"]
    cat = truth.catalog
    weekday = truth.config.world_date(day).weekday()
    weekly = bench["weekly_cpm_profile"]
    rho = bench["demand_on_conversion"]

    apply_day_events(cur, truth, day)  # scenario one-shots (stock, prices, human budget edits) happen first
    eff = effects_for_day(cur, truth, day)

    def demand(category_code: str) -> float:
        return truth.demand_index(category_code, day) * eff.demand_mult.get(category_code, 1.0)

    inventory = {r[0]: list(r[1:]) for r in cur.execute(
        "SELECT sku, on_hand, inbound_qty, inbound_day, reorder_point, order_qty, lead_time_days FROM inventory_state"
    ).fetchall()}
    if not inventory:
        raise WorldStateConflict("world not initialised: run init_world first")
    budgets = {(p, b): (amt, status) for p, b, amt, status in cur.execute(
        "SELECT platform, budget_id, amount, status FROM budgets_state").fetchall()}
    paused = {r[0] for r in cur.execute("SELECT campaign_id FROM campaign_status WHERE status = 'PAUSED'").fetchall()}
    cum = dict(cur.execute("SELECT creative_id, cum_impressions FROM creative_state").fetchall())
    prices = {r[0]: (r[1], r[2]) for r in
              cur.execute("SELECT sku, price_inr, base_price_inr FROM price_state").fetchall()}
    sku_by_id = {s.sku: s for s in cat.skus}

    # 1. receipts
    receipts: dict[str, int] = {}
    for sku, row in inventory.items():
        on_hand, inbound_qty, inbound_day = row[0], row[1], row[2]
        if inbound_qty > 0 and inbound_day is not None and inbound_day <= day:
            row[0] = on_hand + inbound_qty
            receipts[sku] = inbound_qty
            row[1], row[2] = 0, None

    price_factor = {
        c.code: sum(s.mix_weight * (prices[s.sku][0] / prices[s.sku][1]) ** truth.elasticity[c.code]
                    for s in cat.skus_of(c.code))
        for c in cat.categories
    }
    wh = truth.warehouse
    wh_price = float(wh["price_inr"])

    cand: dict[str, list] = defaultdict(list)  # purchase attempts (paid + unpaid), parallel columns
    campaign_rows, creative_rows = [], []
    creatives_of: dict[str, list] = defaultdict(list)
    for cr in cat.creatives:
        creatives_of[cr.campaign_id].append(cr)

    def add_candidates(n: int, *, channel: str, campaign_id, adset_ids, creative_ids, skus, returned, entity: str,
                       source_len: int, picked: np.ndarray | None) -> None:
        if n == 0:
            return

        def u(purpose: str) -> np.ndarray:
            arr = uniforms(seed, day, entity, purpose, source_len)
            return arr[picked] if picked is not None else arr

        cand["channel"] += [channel] * n
        cand["campaign_id"] += [campaign_id] * n
        cand["adset_id"] += list(adset_ids)
        cand["creative_id"] += list(creative_ids)
        cand["sku"] += list(skus)
        cand["returned"] += list(returned)
        cand["u_alloc"] += list(u("alloc"))
        cand["u_minute"] += list(u("minute"))
        cand["u_new"] += list(u("new_customer"))
        cand["u_cust"] += list(u("customer"))
        cand["u_lag"] += list(u("return_lag"))

    # 2. paid
    for c in cat.campaigns:
        t = truth.campaigns[c.campaign_id]
        amount, status = budgets.get((c.platform, c.budget_id), (0.0, "PAUSED"))
        live = sorted((cr for cr in creatives_of[c.campaign_id] if cr.launch_day <= day), key=lambda x: x.creative_id)
        target = 0.0
        if status == "ENABLED" and c.campaign_id not in paused and amount > 0 and live:
            lo, hi = st["pacing"]
            pace = lo + (hi - lo) * float(uniforms(seed, day, c.campaign_id, "pacing", 1)[0])
            target = amount * t.budget_share * pace
        if target <= 0:
            campaign_rows.append((day, c.platform, c.campaign_id, 0, 0, 0, 0, 0, 0, 0.0, None))
            continue
        cpm_noise = math.exp(sigma["cpm"] * _normal(seed, day, c.campaign_id, "noise:cpm"))
        cpm = cpm_at(target, t) * cpm_noise * weekly[weekday] * eff.cpm_mult.get(c.platform, 1.0)
        imps = math.floor(1000.0 * target / cpm)
        spend = imps * cpm / 1000.0
        audience = t.audience_size * eff.audience_mult.get(c.campaign_id, 1.0)
        sat = frequency(imps, audience) ** -t.ctr_freq_gamma
        ctr_noise = math.exp(sigma["ctr"] * _normal(seed, day, c.campaign_id, "noise:ctr"))
        cvr_noise = math.exp(sigma["cvr"] * _normal(seed, day, c.campaign_id, "noise:cvr"))
        quality = np.array([creative_quality(truth, cr.creative_id, cum.get(cr.creative_id, 0)) *
                            eff.creative_ctr_mult.get(cr.creative_id, 1.0) for cr in live])
        delivery = quality ** st["delivery_optimization"]
        weights = delivery / delivery.sum()
        creative_ctr = np.minimum(1.0, t.base_ctr * quality * sat * ctr_noise)
        p_buy = t.p_buy * demand(c.category_code) ** rho * cvr_noise * price_factor[c.category_code]
        skus = cat.skus_of(c.category_code)
        sku_ids = tuple(s.sku for s in skus) + (WAREHOUSE,)
        sku_w = tuple((1 - t.cross_sell_share) * s.mix_weight for s in skus) + (t.cross_sell_share,)
        sku_w = tuple(w / sum(sku_w) for w in sku_w)
        rr = tuple(s.return_rate for s in skus) + (float(wh["return_rate"]),)
        s_max = st["prospect_pool_spend_multiple"] * max(amount * t.budget_share, t.s_ref)
        min_cpm = t.base_cpm_inr * min(weekly) * math.exp(-5 * sigma["cpm"])
        inputs = FunnelInputs(
            spend=target, cpm=cpm, ctr=0.0, click_session_rate=t.click_session_rate, cvr=p_buy,
            audience_size=max(1, int(audience)), sku_ids=sku_ids, sku_weights=sku_w, return_rates=rr,
            creative_weights=tuple(float(w) for w in weights), creative_ctr=tuple(float(x) for x in creative_ctr),
        )
        a = prospect_funnel(seed, day, c.campaign_id, inputs, n_max(s_max, min_cpm))
        purchased = np.flatnonzero(a.purchased)
        add_candidates(
            purchased.size, channel=c.channel, campaign_id=c.campaign_id,
            adset_ids=[live[i].adset_id for i in a.creative_index[purchased]],
            creative_ids=[live[i].creative_id for i in a.creative_index[purchased]],
            skus=[sku_ids[i] for i in a.sku_index[purchased]], returned=a.returned[purchased],
            entity=c.campaign_id, source_len=imps, picked=purchased,
        )
        per_creative_imps = np.bincount(a.creative_index, minlength=len(live))
        per_creative_clicks = np.bincount(a.creative_index[a.clicked], minlength=len(live))
        for i, cr in enumerate(live):
            creative_rows.append([day, c.platform, c.campaign_id, cr.adset_id, cr.creative_id,
                                  int(per_creative_imps[i]), int(per_creative_clicks[i]),
                                  spend * per_creative_imps[i] / imps if imps else 0.0])
            cum[cr.creative_id] = cum.get(cr.creative_id, 0) + int(per_creative_imps[i])
        campaign_rows.append((day, c.platform, c.campaign_id, imps, int(np.unique(a.user_id).size),
                              int(a.clicked.sum()), int(a.session.sum()), int(purchased.size), 0, spend, cpm))

    # 3. unpaid demand
    rates = truth.category_rates.set_index("category_code")
    for c in cat.categories:
        idx = demand(c.code) * price_factor[c.code]
        skus = cat.skus_of(c.code)
        cdf = np.cumsum([s.mix_weight for s in skus])
        cdf[-1] = 1.0
        unpaid_sources = ("email", "organic") + (("amazon_organic",) if "unpaid_amazon_organic" in rates.columns
                                                  else ())
        for src in unpaid_sources:
            entity = f"unpaid:{c.code}:{src}"
            n = int(generator(seed, day, entity, "count").poisson(rates.loc[c.code, f"unpaid_{src}"] * idx))
            k = np.minimum(np.searchsorted(cdf, uniforms(seed, day, entity, "sku", n), side="right"), len(skus) - 1)
            chosen = [skus[i] for i in k]
            returned = uniforms(seed, day, entity, "return", n) < np.array([s.return_rate for s in chosen])
            add_candidates(n, channel=src, campaign_id=None, adset_ids=[None] * n, creative_ids=[None] * n,
                           skus=[s.sku for s in chosen], returned=returned, entity=entity, source_len=n, picked=None)
    store_index = float(np.mean([truth.demand_index(c.code, day) for c in cat.categories]))
    shares = truth.source_shares
    email_share = shares.get("Email", 0.0) / max(shares.get("Email", 0.0) + shares.get("Organic", 0.0), 1e-12)
    entity = "unpaid:__warehouse__"
    n = int(generator(seed, day, entity, "count").poisson(float(wh["units_mean"]) * store_index))
    is_email = uniforms(seed, day, entity, "source", n) < email_share
    for src, mask in (("email", is_email), ("organic", ~is_email)):
        m = int(mask.sum())
        picked = np.flatnonzero(mask)
        returned = uniforms(seed, day, entity, "return", n)[picked] < float(wh["return_rate"])
        add_candidates(m, channel=src, campaign_id=None, adset_ids=[None] * m, creative_ids=[None] * m,
                       skus=[WAREHOUSE] * m, returned=returned, entity=entity, source_len=n, picked=picked)

    # 4. stock rationing
    df = pd.DataFrame(cand) if cand else pd.DataFrame(columns=["channel", "sku", "u_alloc"])
    df["filled"] = True
    lost_rows = []
    units_sold: dict[str, int] = defaultdict(int)
    if not df.empty:
        for sku, g in df[df.sku != WAREHOUSE].groupby("sku", sort=True):
            available = max(int(inventory[sku][0]), 0)
            order = g.sort_values(["u_alloc"], kind="mergesort").index
            lost_idx = order[available:]
            df.loc[lost_idx, "filled"] = False
            sold = min(available, len(order))
            units_sold[sku] = sold
            inventory[sku][0] -= sold
            if len(lost_idx):
                lost = df.loc[lost_idx].groupby(["channel", "campaign_id"], dropna=False).size()
                for (channel, campaign_id), units in lost.items():
                    lost_rows.append((day, sku, channel, None if pd.isna(campaign_id) else campaign_id, int(units)))

    # 5. orders
    orders = df[df.filled].copy() if not df.empty else df
    if not orders.empty:
        orders["minute"] = (orders.u_minute * 1440).astype(int).clip(0, 1439)
        orders["source_rank"] = orders.channel.map(SOURCE_RANK)
        orders = orders.sort_values(["minute", "source_rank", "campaign_id", "creative_id", "u_alloc"],
                                    na_position="first", kind="mergesort").reset_index(drop=True)
        orders["order_id"] = (day + 100_000) * 1_000_000 + orders.index + 1
        new_rate = orders.channel.map(lambda ch: st["new_customer_rate"][ch])
        orders["is_new_customer"] = orders.u_new < new_rate
        orders["customer_id"] = np.where(orders.is_new_customer, orders.order_id,
                                         1 + (orders.u_cust * 2_000_000).astype(np.int64))
        orders["source"] = orders.channel.map(lambda ch: SOURCE_MEDIUM[ch][0])
        orders["medium"] = orders.channel.map(lambda ch: SOURCE_MEDIUM[ch][1])
        is_wh = orders.sku == WAREHOUSE
        orders["category_code"] = orders.sku.map(lambda s: None if s == WAREHOUSE else sku_by_id[s].category_code)
        orders["unit_price_inr"] = orders.sku.map(lambda s: wh_price if s == WAREHOUSE else prices[s][0])
        orders["cogs_inr"] = orders.sku.map(
            lambda s: float(wh["cogs_inr"]) if s == WAREHOUSE else sku_by_id[s].unit_cogs_inr)
        costs = bench["unit_costs"]
        orders["ship_cost_inr"] = orders.unit_price_inr.map(
            lambda p: round(max(costs["ship_cost_min_inr"], costs["ship_cost_pct"] * p), 2))
        orders["payment_fee_inr"] = (orders.unit_price_inr * costs["payment_fee_pct"]).round(2)
        lo, hi = st["return_lag_days"]
        lag = day + lo + (orders.u_lag * (hi - lo + 1)).astype(int)
        orders["return_day"] = pd.array(np.where(orders.returned, lag, 0), dtype="Int64")
        orders.loc[~orders.returned.astype(bool), "return_day"] = pd.NA
        orders.loc[is_wh, "sku"] = None
        orders["qty"] = 1
        orders["discount_inr"] = 0.0
        orders["day"] = day
        orders["returned"] = orders.returned.astype(bool)
        out = orders[["order_id", "day", "minute", "customer_id", "is_new_customer", "source", "medium", "channel",
                      "campaign_id", "adset_id", "creative_id", "sku", "category_code", "qty", "unit_price_inr",
                      "discount_inr", "cogs_inr", "ship_cost_inr", "payment_fee_inr", "returned", "return_day"]]
        _insert(cur, "fact_orders", out)

    # platform-claimed conversions per creative (click-through + view-through), campaign order counts, GA4
    paid_orders = orders[orders.campaign_id.notna()] if not orders.empty else orders
    by_creative = paid_orders.groupby("creative_id").agg(n=("order_id", "size"), value=("unit_price_inr", "sum")) \
        if not paid_orders.empty else pd.DataFrame(columns=["n", "value"])
    by_campaign = paid_orders.groupby("campaign_id").agg(n=("order_id", "size"), value=("unit_price_inr", "sum")) \
        if not paid_orders.empty else pd.DataFrame(columns=["n", "value"])
    creative_out = []
    for d_, platform, cid, asid, crid, imps_c, clicks_c, spend_c in creative_rows:
        n_ct = int(by_creative.n.get(crid, 0)) if not by_creative.empty else 0
        value_ct = float(by_creative.value.get(crid, 0.0)) if not by_creative.empty else 0.0
        vt = int(generator(seed, day, crid, "view_through").poisson(truth.campaigns[cid].view_through_rate * n_ct))
        avg_price = value_ct / n_ct if n_ct else 0.0
        creative_out.append((d_, platform, cid, asid, crid, imps_c, clicks_c, spend_c, n_ct + vt,
                             value_ct + vt * avg_price))
    _insert(cur, "fact_ad_creative_daily", pd.DataFrame(creative_out, columns=[
        "day", "platform", "campaign_id", "adset_id", "creative_id", "impressions", "clicks", "spend_inr",
        "conversions", "conversion_value_inr"]))
    campaign_df = pd.DataFrame(campaign_rows, columns=[
        "day", "platform", "campaign_id", "impressions", "reach", "clicks", "sessions", "purchases", "orders",
        "spend_inr", "cpm_inr"])
    if not by_campaign.empty:
        campaign_df["orders"] = campaign_df.campaign_id.map(by_campaign.n).fillna(0).astype(int)
    _insert(cur, "fact_ad_campaign_daily", campaign_df)

    ga_rate = st["ga_record_rate"]
    ga_rows = []
    for row in campaign_df.itertuples(index=False):
        if row.impressions == 0:
            continue
        ch = cat.campaign(row.campaign_id).channel
        if ch in MARKETPLACE:
            continue  # Amazon traffic never reaches the brand's GA4 property
        rate = ga_rate * eff.ga_mult.get(ch, 1.0)
        g = generator(seed, day, row.campaign_id, "ga")
        n_orders = int(row.orders)
        value = float(by_campaign.value.get(row.campaign_id, 0.0)) if not by_campaign.empty else 0.0
        purchases = int(g.binomial(n_orders, rate))
        ga_rows.append((day, *SOURCE_MEDIUM[ch], row.campaign_id, int(g.binomial(int(row.sessions), rate)),
                        purchases, value * purchases / n_orders if n_orders else 0.0))
    unpaid = orders[orders.campaign_id.isna()] if not orders.empty else orders
    for src in ("email", "organic"):
        sub = unpaid[unpaid.channel == src] if not unpaid.empty else unpaid
        n_orders = len(sub)
        g = generator(seed, day, f"ga:{src}", "ga")
        rate = ga_rate * eff.ga_mult.get(src, 1.0)
        sessions = int(g.poisson(n_orders / st["unpaid_session_cvr"])) if n_orders else 0
        purchases = int(g.binomial(n_orders, rate))
        value = float(sub.unit_price_inr.sum()) if n_orders else 0.0
        ga_rows.append((day, *SOURCE_MEDIUM[src], None, int(g.binomial(max(sessions, n_orders), rate)),
                        purchases, value * purchases / n_orders if n_orders else 0.0))
    _insert(cur, "fact_ga_daily", pd.DataFrame(ga_rows, columns=[
        "day", "source", "medium", "campaign_id", "sessions", "purchases", "revenue_inr"]))
    _insert(cur, "fact_lost_demand", pd.DataFrame(lost_rows, columns=["day", "sku", "channel", "campaign_id", "units"]))

    # ERP: (s, Q) policy on trailing observed sales, then the daily snapshot
    window, fast = inv_cfg["planning_window_days"], inv_cfg["fast_window_days"]
    trailing = {r[0]: (r[1], r[2], r[3], r[4]) for r in cur.execute(
        "SELECT sku, sum(units_sold), count(*), sum(units_sold) FILTER (WHERE day > ?), "
        "count(*) FILTER (WHERE day > ?) FROM fact_erp_daily WHERE day > ? GROUP BY sku",
        [day - fast, day - fast, day - window]
    ).fetchall()}
    erp_rows = []
    blocked = po_blocked(cur, day)  # supplier delay (scenario S3 / DEMO_01): no new purchase orders
    for sku in sorted(inventory):
        on_hand, inbound_qty, inbound_day, _, _, lead = inventory[sku]
        sold_hist, n_hist, sold_fast, n_fast = trailing.get(sku, (0, 0, 0, 0))
        today_units = units_sold.get(sku, 0)
        daily = max((sold_hist + today_units) / (n_hist + 1), ((sold_fast or 0) + today_units) / ((n_fast or 0) + 1))
        s_point = math.ceil(daily * (lead + inv_cfg["safety_days"]))
        q = max(1, math.ceil(daily * inv_cfg["order_cover_days"]))
        if inbound_qty == 0 and on_hand <= s_point and sku not in blocked:
            inbound_qty, inbound_day = q, day + lead
        cur.execute("UPDATE inventory_state SET on_hand = ?, inbound_qty = ?, inbound_day = ?, reorder_point = ?, "
                    "order_qty = ? WHERE sku = ?", [on_hand, inbound_qty, inbound_day, s_point, q, sku])
        erp_rows.append((day, sku, on_hand, 0, inbound_qty, inbound_day, receipts.get(sku, 0),
                         units_sold.get(sku, 0)))
    _insert(cur, "fact_erp_daily", pd.DataFrame(erp_rows, columns=[
        "day", "sku", "on_hand", "reserved", "inbound_qty", "inbound_day", "receipts", "units_sold"]))

    if creative_rows:
        cur.executemany("UPDATE creative_state SET cum_impressions = ? WHERE creative_id = ?",
                        [(cum[r[4]], r[4]) for r in creative_rows])

    return {
        "day": day,
        "spend_inr": round(float(campaign_df.spend_inr.sum()), 2),
        "orders": int(len(orders)),
        "paid_orders": int(len(paid_orders)),
        "lost_units": int(sum(r[4] for r in lost_rows)),
    }
