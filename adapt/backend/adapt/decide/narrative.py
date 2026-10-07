"""Deterministic template narratives (C7, spec §11 "No API key -> full functionality", §17 Stage 1 brief).

Every decision gets a title, a one-paragraph summary and rationale lines built ONLY from the decision object's own
numbers (legs, expected values, why-not, checks); nothing is invented and no causal verb is used. Wording follows the
claim hierarchy: model outputs are "model-estimated", P(loss) is "model P(loss)", outcome measurements are
"forecast-counterfactual". The Groq narrator (Stage 2) may only rephrase these atoms.
"""

from __future__ import annotations

PLATFORM = {"meta": "Meta", "google": "Google"}


def inr(x: float | None, signed: bool = False) -> str:
    """Indian-format rupees: lakh / crore for large values."""
    if x is None:
        return "—"
    sign = ("+" if x > 0 else "−" if x < 0 else "") if signed else ("−" if x < 0 else "")
    v = abs(float(x))
    if v >= 1e7:
        body = f"₹{v / 1e7:.2f} Cr"
    elif v >= 1e5:
        body = f"₹{v / 1e5:.2f} L"
    else:
        s = f"{v:,.0f}"
        body = f"₹{s}"
    return sign + body


def _names(legs, names: dict[str, str], k: int = 2) -> str:
    labels = [names.get(leg["unit_id"], leg["unit_id"]) for leg in legs]
    return ", ".join(labels[:k]) + (f" and {len(labels) - k} more" if len(labels) > k else "")


def decision_title(d: dict, names: dict[str, str]) -> str:
    legs = d["legs"]
    up = [leg for leg in legs if leg["after"] > leg["before"]]
    down = [leg for leg in legs if leg["after"] < leg["before"]]
    moved_down = sum(leg["before"] - leg["after"] for leg in down)
    moved_up = sum(leg["after"] - leg["before"] for leg in up)
    if d["class"] == "SAFETY":
        sku = (d.get("trigger") or {}).get("sku")
        n = len(down)
        return f"Safety: reduce spend selling {sku} by {inr(moved_down)}/day ({n} budget{'s' * (n != 1)})"
    if up and down:
        if moved_down > 1.5 * moved_up:  # mostly a cut: say so, then the smaller shift
            return (f"Cut {inr(moved_down)}/day on {len(down)} budget{'s' * (len(down) != 1)}, "
                    f"add {inr(moved_up)}/day on {len(up)}")
        return f"Move {inr(min(moved_up, moved_down))}/day from {_names(down, names, 1)} to {_names(up, names, 1)}"
    if down:
        return f"Cut {inr(moved_down)}/day across {len(down)} budget{'s' * (len(down) != 1)}; leave it unallocated"
    return f"Increase {inr(moved_up)}/day on {_names(up, names)}"


def decision_summary(d: dict, names: dict[str, str]) -> str:
    e = d["expected"]
    parts = []
    if d["class"] == "SAFETY":
        rem = d.get("remaining_shortfall")
        parts.append(f"{(d.get('trigger') or {}).get('sku')} has a projected stock shortfall over the next 7 days.")
        parts.append("The cut is the smallest feasible one on the campaigns selling it"
                     + (f"; {rem:,.0f} units of shortfall remain even at the largest allowed cut."
                        if rem and rem > 1e-6 else " that removes the shortfall."))
        parts.append(f"Model-estimated contribution change over 7 days: {inr(e['E'], True)} "
                     f"(P10 {inr(e['p10'], True)}). Requires review: freed budget stays unallocated.")
        return " ".join(parts)
    parts.append(f"Model-estimated contribution after ads over 7 days: {inr(e['E'], True)} "
                 f"(P10 {inr(e['p10'], True)}, P90 {inr(e['p90'], True)}; model P(loss) {e['prob_loss']:.0%}). "
                 f"Shown with the optimism correction: {inr(e['calibrated_pred'], True)}.")
    if d.get("unallocated", 0) > 0:
        parts.append(f"{inr(d['unallocated'])}/day stays unallocated"
                     + (f" ({inr(d['reserve_floor'])} policy reserve)." if d.get("reserve_floor") else
                        ": the next rupee on any feasible budget is expected to lose contribution."))
    return " ".join(parts)


def why_not_text(w: dict, names: dict[str, str]) -> tuple[str, str, str]:
    """(rule_id, reason sentence, metric) for one why-not entry."""
    who = names.get(w["unit_id"], w["unit_id"])
    rule = w.get("binding_constraint") or w.get("reason") or "UNSPECIFIED"
    if rule == "MARGINAL_VALUE_NONPOSITIVE":
        v = w.get("marginal_value_per_step", 0.0)
        return rule, f"{who}: the next {inr(w.get('step', 500))}/day is model-estimated to change risk-adjusted " \
                     f"contribution by {inr(v, True)} over 7 days.", "marginal PROFIT value"
    if rule in ("TOTAL_BUDGET", "CASH_RESERVE"):
        t = w.get("transfer")
        if t:
            return rule, f"{who}: the budget ceiling is fully used; the best feasible transfer " \
                         f"({inr(t['amount'])}/day from {names.get(t['from_unit'], t['from_unit'])}) would change " \
                         f"risk-adjusted contribution by {inr(t['objective_change'], True)}.", "feasible transfer"
        return rule, f"{who}: the budget ceiling is fully used and no feasible transfer exists.", "budget ceiling"
    if rule == "INVENTORY_GATE":
        gate, share = w.get("gate", "BLOCK"), w.get("exposure", 0)
        return rule, f"{who}: {gate} — {share:.0%} of its revenue maps to SKUs with a " \
                     "projected stock shortfall.", "projected shortfall exposure"
    text = {
        "MODEL_UNAVAILABLE": "no usable response curve (own or pooled), so it cannot be scaled up; cuts stay allowed",
        "MIX_UNCERTAIN": "more than 20% of its revenue is on unmapped SKUs",
        "MAX_DAILY_CHANGE": "already at the ±20% daily change limit",
        "UNIT_MAX": "already at its maximum budget",
        "DAILY_RUPEES_MOVED_CAP": "the daily cap on rupees moved is reached",
        "CHANNEL_SHARE_MAX": "its channel is at the maximum share",
        "DATA_DEPENDENCY": "a required data source is not GREEN",
        "TRACKING_FREEZE": "its platform has an open tracking incident",
        "EXECUTION_FREEZE": "an earlier execution on it is unresolved",
        "SAFETY_COOLDOWN": "it is in a safety cooldown",
        "COOLDOWN": "ADAPT changed it within the last 3 days",
    }.get(rule, rule.replace("_", " ").lower())
    return rule, f"{who}: {text}.", "policy constraint"


CHECK_LABEL = {
    "KILL_SWITCH": "Kill switch inactive", "TOTAL_BUDGET": "Within budget ceiling minus reserve",
    "SAFETY_ONLY_REDUCES": "Safety action only reduces spend", "MAX_DAILY_CHANGE": "Within ±20% daily change",
    "UNIT_MIN_BUDGET": "Above per-budget minimum", "DAILY_RUPEES_MOVED_CAP": "Within daily rupees-moved cap",
    "CHANNEL_SHARE": "Channel shares within policy", "NO_INCREASE_MODEL_UNAVAILABLE": "No scale-up without a model",
    "NO_INCREASE_MIX_UNCERTAIN": "No scale-up on uncertain SKU mix",
    "FROZEN_OR_FIXED_UNITS": "No change to frozen budgets", "COOLDOWN": "Cooldown respected",
    "INVENTORY_GATE": "Inventory gate (projected shortfall)",
}


def brief(world_day: int, incidents: int, pending: list[dict], executing: int, outcomes: dict, factor: float) -> str:
    bits = [f"World day {world_day}."]
    bits.append(f"{incidents} open efficiency incident{'s' * (incidents != 1)}." if incidents else
                "No open efficiency incidents.")
    if pending:
        top = max(pending, key=lambda d: d["expected"]["E"])
        bits.append(f"{len(pending)} decision{'s' * (len(pending) != 1)} await approval; the largest is "
                    f"model-estimated at {inr(top['expected']['E'], True)} over 7 days.")
    if executing:
        bits.append(f"{executing} execution{'s' * (executing != 1)} in progress.")
    n = sum(outcomes.values())
    if n:
        bits.append(f"Measured outcomes: {outcomes.get('SUCCESS', 0)} success, {outcomes.get('NEUTRAL', 0)} neutral, "
                    f"{outcomes.get('FAILED', 0)} failed, {outcomes.get('INCONCLUSIVE', 0)} inconclusive "
                    f"(forecast-counterfactual).")
    bits.append(f"Optimism correction factor {factor:.2f}.")
    return " ".join(bits)
