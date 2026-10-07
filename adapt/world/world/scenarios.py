"""Scenario injection (spec §14) with ground-truth incidents (spec §16 labelling contract).

A scenario activation is a committed world operation (`activate_scenario`) that stores resolved targets and its GT
incidents in sim_state. world.step then applies, every day:
  - continuous effects: CPM multiplier per platform, CTR multiplier per creative, audience multiplier per campaign,
    GA4 recording multiplier per channel, demand multiplier per category
  - one-shot events on their day: set a SKU's stock + block its purchase orders, change a category's prices
    (and restore them after the window), a human budget edit
GT incidents live in sim_state.gt_incidents (sim_truth stays write-once). They are never served on the public
listener and ADAPT has no path to the file; only the eval truth listener reads them.

Stage 1 scenarios: S1 auction, S2 creative fatigue, S3 stockout, S4 price, S5 tracking, S7 human budget cut,
DEMO_01 (fatigue + low hero-SKU cover on a Meta category, demand surge + deep stock on a Google category).
Stage 2 scenarios: S6 category demand surge, S8 retargeting audience saturation (frequency 2 -> 5, reach flat),
S10 holiday demand spike (seasonal_expected), S11 excess inventory (CLEARANCE), S12 fatigue + demand surge on the
same campaign (params.without = "fatigue" | "demand" removes one driver: the eval's GT effect-order forks).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

import duckdb

from world.state import WorldContext, WorldStateConflict
from world.truth import Truth, _solve_audience, frequency, impressions_at

SCENARIO_SCHEMA = """
CREATE TABLE IF NOT EXISTS scenario_activation (
    activation_id VARCHAR PRIMARY KEY, scenario_key VARCHAR NOT NULL, start_day INTEGER NOT NULL,
    end_day INTEGER NOT NULL, params JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS gt_incidents (
    incident_id VARCHAR PRIMARY KEY, activation_id VARCHAR NOT NULL, scenario VARCHAR NOT NULL,
    driver VARCHAR NOT NULL, entity_ids JSON NOT NULL, metric_set JSON NOT NULL, direction JSON NOT NULL,
    injection_start_day INTEGER NOT NULL, onset_day INTEGER NOT NULL, injection_end_day INTEGER NOT NULL,
    magnitude DOUBLE NOT NULL
);
CREATE TABLE IF NOT EXISTS po_block (sku VARCHAR PRIMARY KEY, until_day INTEGER NOT NULL);
"""

SCENARIOS = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S10", "S11", "S12", "DEMO_01")
ROAS_FAMILY = ["ROAS", "POAS", "CPA"]


@dataclass
class DayEffects:
    cpm_mult: dict[str, float] = field(default_factory=dict)  # platform -> x
    creative_ctr_mult: dict[str, float] = field(default_factory=dict)  # creative_id -> x
    audience_mult: dict[str, float] = field(default_factory=dict)  # campaign_id -> x
    ga_mult: dict[str, float] = field(default_factory=dict)  # channel -> x (paid channels, email, organic)
    demand_mult: dict[str, float] = field(default_factory=dict)  # category_code -> x


def _ramp(day: int, start: int, ramp_days: int) -> float:
    """0 before start, then linear to 1 over ramp_days (day start counts as the first ramp day)."""
    if day < start:
        return 0.0
    return min(1.0, (day - start + 1) / ramp_days)


def _onset(start: int, ramp_days: int) -> int:
    """First day the cumulative effect reaches 50% of full magnitude (spec §16)."""
    return start + math.ceil(ramp_days / 2) - 1


# ---- target resolution (deterministic from the catalog and current state) ---------------------------------------
def _campaign(truth: Truth, channel: str, category_code: str):
    return next(c for c in truth.catalog.campaigns if c.channel == channel and c.category_code == category_code)


def _hero_sku(truth: Truth, category_code: str):
    return max(truth.catalog.skus_of(category_code), key=lambda s: (s.mix_weight, s.sku))


def _top_creative(cur: duckdb.DuckDBPyConnection, truth: Truth, campaign_id: str, day: int) -> str:
    """The live creative with the most delivery so far (the one a fatigue story is about)."""
    live = [cr.creative_id for cr in truth.catalog.creatives if cr.campaign_id == campaign_id and cr.launch_day <= day]
    cum = dict(cur.execute("SELECT creative_id, cum_impressions FROM creative_state").fetchall())
    return max(live, key=lambda c: (cum.get(c, 0), c))


def _expected_sku_daily(truth: Truth, sku_id: str, day: int) -> float:
    from world.step import expected_sku_demand  # local import: step imports this module

    return expected_sku_demand(truth, day)[sku_id]


def _categories(truth: Truth):
    return list(truth.catalog.categories)


def resolve(key: str, truth: Truth, cur: duckdb.DuckDBPyConnection, start: int,
            params: dict[str, Any]) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
    """Returns (resolved params, end_day, GT incident rows)."""
    cats = _categories(truth)
    ranks = {c.rank: c.code for c in cats}
    by_rank = {n: ranks.get(n, ranks[max(ranks)]) for n in range(1, 5)}  # small catalogs fall back to the last
    gts: list[dict[str, Any]] = []

    def gt(scenario: str, driver: str, entities: list[str], metrics: list[str], direction: dict[str, str],
           ramp: int, end: int, magnitude: float) -> None:
        gts.append({"scenario": scenario, "driver": driver, "entity_ids": entities, "metric_set": metrics,
                    "direction": direction, "injection_start_day": start, "onset_day": _onset(start, ramp),
                    "injection_end_day": end, "magnitude": magnitude})

    if key == "S1":  # Meta CPM +45% platform-wide for 6 days
        mag, days = float(params.get("magnitude", 0.45)), int(params.get("days", 6))
        end = start + days - 1
        ids = [c.campaign_id for c in truth.catalog.campaigns if c.platform == "meta"]
        gt("S1", "auction", ids, ["CPM", "CPC", *ROAS_FAMILY],
           {"CPM": "UP", "CPC": "UP", "ROAS": "DOWN", "POAS": "DOWN", "CPA": "UP"}, 1, end, mag)
        return {"platform": "meta", "magnitude": mag}, end, gts

    if key == "S2":  # one creative's CTR -40% over 10 days (+ its audience recycling: frequency up)
        cat = params.get("category_code", by_rank[1])
        camp = params.get("campaign_id") or _campaign(truth, "meta_prospecting", cat).campaign_id
        creative = params.get("creative_id") or _top_creative(cur, truth, camp, start)
        mag, ramp, hold = float(params.get("magnitude", 0.40)), 10, int(params.get("hold_days", 4))
        end = start + ramp + hold - 1
        gt("S2", "creative_fatigue", [camp, creative], ["CTR", *ROAS_FAMILY],
           {"CTR": "DOWN", "ROAS": "DOWN", "POAS": "DOWN", "CPA": "UP"}, ramp, end, mag)
        return {"campaign_id": camp, "creative_id": creative, "magnitude": mag, "ramp_days": ramp,
                "audience_floor": 0.75}, end, gts

    if key == "S3":  # hero SKU stockout while the Google Search campaign keeps spending
        cat = params.get("category_code", by_rank[2])
        sku = params.get("sku") or _hero_sku(truth, cat).sku
        days = int(params.get("days", 7))
        end = start + days - 1
        on_hand = int(params.get("on_hand", 0))
        ids = [c.campaign_id for c in truth.catalog.campaigns if c.category_code == cat]
        gt("S3", "inventory", ids + [sku], ["CVR", *ROAS_FAMILY, "SKU_UNITS"],
           {"CVR": "DOWN", "ROAS": "DOWN", "POAS": "DOWN", "CPA": "UP", "SKU_UNITS": "DOWN"}, 1, end, 1.0)
        return {"category_code": cat, "sku": sku, "on_hand": on_hand, "po_block_days": days}, end, gts

    if key == "S4":  # +18% price on a category for 14 days
        cat = params.get("category_code", by_rank[3])
        pct, days = float(params.get("pct", 0.18)), int(params.get("days", 14))
        end = start + days - 1
        ids = [c.campaign_id for c in truth.catalog.campaigns if c.category_code == cat]
        gt("S4", "price", ids, ["CVR", "AOV", *ROAS_FAMILY], {"CVR": "DOWN", "AOV": "UP"}, 1, end, pct)
        return {"category_code": cat, "pct": pct}, end, gts

    if key == "S5":  # GA4 stops recording 60% of Google sessions; clicks and real orders unchanged
        channels = params.get("channels", ["google_search", "google_video"])
        drop, days = float(params.get("drop", 0.60)), int(params.get("days", 7))
        end = start + days - 1
        ids = [c.campaign_id for c in truth.catalog.campaigns if c.channel in channels]
        gt("S5", "tracking", ids, ["SESSION_CLICK"], {"SESSION_CLICK": "DOWN"}, 1, end, drop)
        return {"channels": channels, "drop": drop}, end, gts

    if key == "S7":  # human cuts a Google Search budget by 40% (must be classified budget_change, not raised)
        cat = params.get("category_code", by_rank[4])
        camp = _campaign(truth, "google_search", cat)
        cut = float(params.get("cut", 0.40))
        row = cur.execute("SELECT amount FROM budgets_state WHERE platform = 'google' AND budget_id = ?",
                          [camp.budget_id]).fetchone()
        if row is None:
            raise WorldStateConflict(f"budget {camp.budget_id} not set")
        gt("S7", "budget_change", [camp.campaign_id], ["SPEND"], {"SPEND": "DOWN"}, 1, start, cut)
        return {"budget_id": camp.budget_id, "amount": round(row[0] * (1 - cut) / 100.0) * 100.0, "cut": cut}, \
            start, gts

    if key == "S6":  # category demand surge (+40% over a 3-day ramp, 10 days): a positive anomaly, driver demand
        cat = params.get("category_code", by_rank[1])
        mag, ramp, days = float(params.get("magnitude", 0.40)), int(params.get("ramp_days", 3)), \
            int(params.get("days", 10))
        end = start + days - 1
        ids = [c.campaign_id for c in truth.catalog.campaigns if c.category_code == cat]
        gt("S6", "demand", ids, ["ROAS", "POAS", "CVR", "SKU_UNITS"],
           {"ROAS": "UP", "POAS": "UP", "CVR": "UP", "SKU_UNITS": "UP"}, ramp, end, mag)
        return {"category_code": cat, "magnitude": mag, "ramp_days": ramp}, end, gts

    if key == "S8":  # retargeting audience saturation: daily frequency ~2 -> 5 with reach flat (audience shrinks)
        camp = params.get("campaign_id") or max(
            (t for t in truth.campaigns.values() if t.channel == "meta_retargeting"),
            key=lambda t: (t.ctr_freq_gamma, t.campaign_id)).campaign_id  # the clearest CTR response to frequency
        t = truth.campaigns[camp]
        target_freq, ramp = float(params.get("target_frequency", 5.0)), int(params.get("ramp_days", 7))
        days = int(params.get("days", 14))
        end = start + days - 1
        c = truth.catalog.campaign(camp)
        row = cur.execute("SELECT amount FROM budgets_state WHERE platform = ? AND budget_id = ?",
                          [c.platform, c.budget_id]).fetchone()
        spend = (row[0] if row else t.s_ref) * t.budget_share * 0.975
        imps = impressions_at(spend, t)
        freq0 = frequency(imps, t.audience_size)
        floor = min(1.0, _solve_audience(imps, max(target_freq, freq0)) / t.audience_size)
        gt("S8", "audience_saturation", [camp], ["CTR", *ROAS_FAMILY],
           {"CTR": "DOWN", "ROAS": "DOWN", "POAS": "DOWN", "CPA": "UP"}, ramp, end, target_freq / freq0 - 1)
        return {"campaign_id": camp, "audience_floor": floor, "ramp_days": ramp, "frequency_before": freq0,
                "target_frequency": target_freq}, end, gts

    if key == "S10":  # a festival: every category's demand x1.6 for 2 days (must be labelled seasonal_expected)
        mag, days = float(params.get("magnitude", 0.60)), int(params.get("days", 2))
        end = start + days - 1
        ids = [c.campaign_id for c in truth.catalog.campaigns]
        gt("S10", "seasonal_expected", ids, ["ROAS", "POAS", "CVR", "SKU_UNITS"],
           {"ROAS": "UP", "POAS": "UP", "CVR": "UP", "SKU_UNITS": "UP"}, 1, end, mag)
        return {"magnitude": mag, "categories": [c.code for c in cats]}, end, gts

    if key == "S11":  # excess inventory: a category's stock set to ~120 days of cover (the CLEARANCE objective case)
        cat = params.get("category_code", by_rank[2])
        cover = float(params.get("cover_days", 120.0))
        stock = {s.sku: math.ceil(cover * _expected_sku_daily(truth, s.sku, start)) for s in truth.catalog.skus_of(cat)}
        ids = [c.campaign_id for c in truth.catalog.campaigns if c.category_code == cat]
        gt("S11", "excess_inventory", ids + sorted(stock), ["SKU_UNITS"], {"SKU_UNITS": "UP"}, 1, start, cover)
        return {"category_code": cat, "cover_days": cover, "on_hand": stock}, start, gts

    if key == "S12":  # creative fatigue + category demand surge on the same Meta campaign
        cat = params.get("category_code", by_rank[1])
        camp = params.get("campaign_id") or _campaign(truth, "meta_prospecting", cat).campaign_id
        creative = params.get("creative_id") or _top_creative(cur, truth, camp, start)
        fatigue, surge, ramp = float(params.get("magnitude", 0.40)), float(params.get("demand_surge", 0.30)), 10
        without = params.get("without")  # eval-only counterfactual fork: remove one driver
        if without not in (None, "fatigue", "demand"):
            raise ValueError("S12 params.without must be 'fatigue' or 'demand'")
        end = start + ramp + int(params.get("hold_days", 4)) - 1
        if without != "fatigue":
            gt("S12", "creative_fatigue", [camp, creative], ["CTR", *ROAS_FAMILY],
               {"CTR": "DOWN", "ROAS": "DOWN", "POAS": "DOWN", "CPA": "UP"}, ramp, end, fatigue)
        if without != "demand":
            gt("S12", "demand", [camp], ["CVR", *ROAS_FAMILY], {"CVR": "UP"}, ramp, end, surge)
        return {"campaign_id": camp, "creative_id": creative, "category_code": cat,
                "magnitude": 0.0 if without == "fatigue" else fatigue,
                "demand_surge": 0.0 if without == "demand" else surge, "ramp_days": ramp,
                "audience_floor": 1.0 if without == "fatigue" else 0.75,
                "without": without}, end, gts

    if key == "DEMO_01":
        women = [c for c in cats if c.department == "Women"]
        men = [c for c in cats if c.department == "Men"]
        meta_cat = params.get("meta_category_code") or max(
            women or cats, key=lambda c: truth.category_rates.set_index("category_code").loc[c.code, "units_mean"]).code
        google_cat = params.get("google_category_code") or min(men or cats, key=lambda c: c.rank).code
        camp = _campaign(truth, "meta_prospecting", meta_cat).campaign_id
        creative = _top_creative(cur, truth, camp, start)
        hero = _hero_sku(truth, meta_cat).sku
        ramp, days = 10, int(params.get("days", 21))
        end = start + days - 1
        hero_cover_days = float(params.get("hero_cover_days", 13.0))  # falls to ~3 days cover by D (= start+10)
        surge = float(params.get("demand_surge", 0.25))
        fatigue = float(params.get("magnitude", 0.50))  # stronger than S2 so the campaign-level ROAS move is material
        gt("DEMO_01", "creative_fatigue", [camp, creative], ["CTR", *ROAS_FAMILY],
           {"CTR": "DOWN", "ROAS": "DOWN", "POAS": "DOWN", "CPA": "UP"}, ramp, end, fatigue)
        gt("DEMO_01", "demand", [c.campaign_id for c in truth.catalog.campaigns if c.category_code == google_cat],
           ["ROAS", "POAS", "CVR"], {"ROAS": "UP", "POAS": "UP", "CVR": "UP"}, ramp, end, surge)
        return {"meta_campaign_id": camp, "creative_id": creative, "meta_category_code": meta_cat,
                "hero_sku": hero, "hero_on_hand": math.ceil(hero_cover_days * _expected_sku_daily(truth, hero, start)),
                "google_category_code": google_cat, "demand_surge": surge, "ramp_days": ramp,
                "deep_stock_days": 60, "magnitude": fatigue, "audience_floor": 0.60}, end, gts

    raise ValueError(f"unknown scenario {key}; known: {', '.join(SCENARIOS)}")


def op_activate_scenario(cur: duckdb.DuckDBPyConnection, payload: dict[str, Any], ctx: WorldContext) -> dict[str, Any]:
    truth: Truth = ctx.truth
    if truth is None:
        raise WorldStateConflict("scenarios need a seeded world")
    clock = cur.execute("SELECT day FROM world_clock WHERE id = 1").fetchone()
    if clock is None:
        raise WorldStateConflict("world not seeded: call reset first")
    key = str(payload["key"])
    start = int(payload.get("start_day", clock[0]))
    if start < clock[0]:
        raise WorldStateConflict(f"cannot start a scenario in the past (start {start} < clock {clock[0]})")
    n = cur.execute("SELECT count(*) FROM scenario_activation").fetchone()[0]
    activation_id = f"{key}-{n + 1:03d}"
    params, end, gts = resolve(key, truth, cur, start, dict(payload.get("params", {})))
    cur.execute("INSERT INTO scenario_activation VALUES (?, ?, ?, ?, ?)",
                [activation_id, key, start, end, json.dumps(params, sort_keys=True)])
    for i, g in enumerate(gts, start=1):
        cur.execute("INSERT INTO gt_incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [f"{activation_id}#{i}", activation_id, g["scenario"], g["driver"], json.dumps(g["entity_ids"]),
                     json.dumps(g["metric_set"]), json.dumps(g["direction"]), g["injection_start_day"],
                     g["onset_day"], g["injection_end_day"], g["magnitude"]])
    return {"activation_id": activation_id, "key": key, "start_day": start, "end_day": end}


# ---- applied by world.step --------------------------------------------------------------------------------------
def _active(cur: duckdb.DuckDBPyConnection, day: int) -> list[tuple[str, str, int, int, dict]]:
    rows = cur.execute("SELECT activation_id, scenario_key, start_day, end_day, params FROM scenario_activation "
                       "WHERE start_day <= ? ORDER BY activation_id", [day]).fetchall()
    return [(a, k, s, e, json.loads(p)) for a, k, s, e, p in rows]


def apply_day_events(cur: duckdb.DuckDBPyConnection, truth: Truth, day: int) -> None:
    """One-shot state changes that happen at the start of `day` (before anything is simulated)."""
    for _, key, start, end, p in _active(cur, day):
        if key == "S3" and day == start:
            cur.execute("UPDATE inventory_state SET on_hand = ?, inbound_qty = 0, inbound_day = NULL WHERE sku = ?",
                        [p["on_hand"], p["sku"]])
            cur.execute("INSERT OR REPLACE INTO po_block VALUES (?, ?)", [p["sku"], start + p["po_block_days"] - 1])
        elif key == "S4" and day in (start, end + 1):
            factor = 1 + p["pct"] if day == start else None
            for s in truth.catalog.skus_of(p["category_code"]):
                price = s.unit_price_inr * factor if factor else s.unit_price_inr
                cur.execute("UPDATE price_state SET price_inr = ? WHERE sku = ?", [round(price, 2), s.sku])
                cur.execute("INSERT OR REPLACE INTO price_history VALUES (?, ?, ?)", [s.sku, round(price, 2), day])
        elif key == "S7" and day == start:
            cur.execute("UPDATE budgets_state SET amount = ? WHERE platform = 'google' AND budget_id = ?",
                        [p["amount"], p["budget_id"]])
        elif key == "S11" and day == start:
            for sku, on_hand in sorted(p["on_hand"].items()):
                cur.execute("UPDATE inventory_state SET on_hand = greatest(on_hand, ?) WHERE sku = ?", [on_hand, sku])
        elif key == "DEMO_01" and day == start:
            cur.execute("UPDATE inventory_state SET on_hand = ?, inbound_qty = 0, inbound_day = NULL WHERE sku = ?",
                        [p["hero_on_hand"], p["hero_sku"]])
            cur.execute("INSERT OR REPLACE INTO po_block VALUES (?, ?)", [p["hero_sku"], end])
            from world.step import expected_sku_demand

            demand = expected_sku_demand(truth, day)
            for s in truth.catalog.skus_of(p["google_category_code"]):
                deep = math.ceil(p["deep_stock_days"] * demand[s.sku] * (1 + p["demand_surge"]))
                cur.execute("UPDATE inventory_state SET on_hand = greatest(on_hand, ?) WHERE sku = ?", [deep, s.sku])


def effects_for_day(cur: duckdb.DuckDBPyConnection, truth: Truth, day: int) -> DayEffects:
    eff = DayEffects()

    def mul(d: dict[str, float], k: str, x: float) -> None:
        d[k] = d.get(k, 1.0) * x

    for _, key, start, end, p in _active(cur, day):
        if not start <= day <= end:
            continue
        if key == "S1":
            mul(eff.cpm_mult, p["platform"], 1 + p["magnitude"])
        elif key in ("S2", "DEMO_01"):
            camp = p["campaign_id"] if key == "S2" else p["meta_campaign_id"]
            r = _ramp(day, start, p["ramp_days"])
            mul(eff.creative_ctr_mult, p["creative_id"], 1 - p["magnitude"] * r)
            mul(eff.audience_mult, camp, 1 - (1 - p["audience_floor"]) * r)
            if key == "DEMO_01":
                mul(eff.demand_mult, p["google_category_code"], 1 + p["demand_surge"] * r)
        elif key == "S5":
            for ch in p["channels"]:
                mul(eff.ga_mult, ch, 1 - p["drop"])
        elif key == "S6":
            mul(eff.demand_mult, p["category_code"], 1 + p["magnitude"] * _ramp(day, start, p["ramp_days"]))
        elif key == "S8":
            mul(eff.audience_mult, p["campaign_id"], 1 - (1 - p["audience_floor"]) * _ramp(day, start, p["ramp_days"]))
        elif key == "S10":
            for code in p["categories"]:
                mul(eff.demand_mult, code, 1 + p["magnitude"])
        elif key == "S12":
            r = _ramp(day, start, p["ramp_days"])
            mul(eff.creative_ctr_mult, p["creative_id"], 1 - p["magnitude"] * r)
            mul(eff.audience_mult, p["campaign_id"], 1 - (1 - p["audience_floor"]) * r)
            mul(eff.demand_mult, p["category_code"], 1 + p["demand_surge"] * r)
    return eff


def po_blocked(cur: duckdb.DuckDBPyConnection, day: int) -> set[str]:
    return {r[0] for r in cur.execute("SELECT sku FROM po_block WHERE until_day >= ?", [day]).fetchall()}
