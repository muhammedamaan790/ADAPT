"""Copilot (Stage 3, spec §11): a Groq tool-calling agent over READ-ONLY analytical tools, never the decision engine.

- Tools return deterministic results, each with a tool-result id (T1, T2, ...) and the app route that shows it.
  get_kpis, list_anomalies, explain_anomaly, get_decisions, get_inventory, get_reconciliation, get_response_curve,
  simulate_allocation (portfolio_economics valuation, not executable), run_sql (agent/sql.py guard), and the one bounded
  write select_proposal(proposal_id, rationale): it can only name an EXISTING decision id; it records the selection
  and routes it to the channel's mode machine (Approve / Review). It never creates actions, amounts or ids, and never
  executes. Free-form amounts or unknown ids are rejected by the tool schema / lookup.
- At most 6 tool steps. The final answer must cite the tool-result ids it relies on as [T1]; the guard rejects any
  number that matches no value in the CITED results, any long entity id absent from them, and causal verbs. One retry
  with the violations, then the deterministic template answer (mode TEMPLATE). Requests to Groq are non-streaming
  (tool calling, no strict schema: a separate configuration from the narrator's); the answer reaches the browser over
  the agreed SSE contract (answer + done).
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from adapt.agent import numbers
from adapt.agent.guard import CAUSAL, ID_RE

MAX_STEPS = 6
REL_TOL = 0.005
CITE_RE = re.compile(r"\[(T\d+)\]")

SYSTEM = """You are ADAPT's analyst copilot for a D2C growth manager. Answer ONLY from tool results: call tools to get
facts, then answer in at most 5 short plain-English sentences. After every sentence that states a fact or number, cite
the tool result it came from as [T1] (several: [T1][T3]). Copy numbers exactly as the tools give them (you may round
for display). Never invent numbers, ids, budgets or actions; never say anything "caused" something: say "associated
with" or "evidence points to". You cannot approve, execute or change budgets; to recommend an existing proposal, call
select_proposal with its decision id. All money is INR."""


def _fn(name: str, desc: str, props: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}}}


TOOLS = [
    _fn("get_kpis", "Brand KPIs for the last 7 days vs the previous 7: net revenue, spend, CAA, MER, POAS."),
    _fn("list_anomalies", "Open incidents with metric, change, rupee impact and probable driver."),
    _fn("explain_anomaly", "The evidence-checked explanation and causal estimate of one incident.",
        {"anomaly_id": {"type": "string"}}, ["anomaly_id"]),
    _fn("get_decisions", "Decisions with status, legs (budget before/after), expected contribution and P(loss).",
        {"status": {"type": "string", "enum": ["PENDING_APPROVAL", "EXECUTED", "ANY"]}}),
    _fn("get_inventory", "SKU stock: on hand, days of cover and the inventory risk after the pending decision."),
    _fn("get_reconciliation", "Platform-claimed vs store-attributed revenue and over-attribution per platform."),
    _fn("get_response_curve", "A budget unit's response curve: current budget and model-estimated marginal ROAS.",
        {"budget_id": {"type": "string"}}, ["budget_id"]),
    _fn("simulate_allocation", "Value an edited allocation with portfolio economics (an estimate, not executable).",
        {"legs": {"type": "array", "items": {"type": "object", "properties": {
            "budget_id": {"type": "string"}, "after": {"type": "number"}}, "required": ["budget_id", "after"],
            "additionalProperties": False}}}, ["legs"]),
    _fn("run_sql", "One read-only SELECT over the marts (brand_daily, campaign_daily, adset_daily, creative_daily, "
                   "channel_daily, sku_daily, geo_daily, recon_daily); at most 500 rows.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("select_proposal", "Recommend an EXISTING proposal (decision id) to the manager with a rationale. Never "
                           "executes; the decision still needs approval under its channel's mode.",
        {"proposal_id": {"type": "string"}, "rationale": {"type": "string"}}, ["proposal_id", "rationale"]),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.copilot_selections (
    decision_id VARCHAR NOT NULL, selected_at TIMESTAMP NOT NULL, actor VARCHAR, rationale VARCHAR, routed_to VARCHAR
);
"""


class Tools:
    """Deterministic, read-only tool implementations over the stored engine state."""

    def __init__(self, db, runtime=None, actor: str = "copilot"):
        self.db, self.rt, self.actor = db, runtime, actor
        self.results: dict[str, dict] = {}

    def call(self, name: str, args: dict) -> dict:
        if name not in TOOL_NAMES:
            out = {"error": f"unknown tool {name}"}
            href, label = "/data", name
        else:
            try:
                out, href, label = getattr(self, name)(**args)
            except TypeError as exc:
                out, href, label = {"error": f"invalid arguments: {exc}"}, "/data", name
            except Exception as exc:  # noqa: BLE001 - a tool failure is reported to the model, never invented around
                out, href, label = {"error": str(exc)[:300]}, "/data", name
        rid = f"T{len(self.results) + 1}"
        self.results[rid] = {"id": rid, "tool": name, "href": href, "label": label, "data": out}
        return {"result_id": rid, **out} if isinstance(out, dict) else {"result_id": rid, "data": out}

    # ---- tools -------------------------------------------------------------------------------------------------------
    def get_kpis(self):
        from adapt.api import views as v

        world = self.rt.world() if self.rt else {"day": 0}
        ov = v.overview_view(self.db, "workspace", world, "")
        return ({"metrics": [{"key": m["key"], "label": m["label"], "value": m["value"], "change_vs_prior_7d":
                              m["change"]} for m in ov["metrics"]]}, "/data", "Brand KPIs")

    def list_anomalies(self):
        from adapt.api import views as v

        rows = [a for a in v.anomaly_list(self.db) if a["status"] != "RESOLVED"][:8]
        return ({"incidents": [{"anomaly_id": a["anomaly_id"], "title": a["title"], "metric": a["metric"],
                                "change": a["change"], "impact_inr": a["impact"], "probable_driver": a["driver"],
                                "decision_id": a["decision_id"]} for a in rows]}, "/anomalies", "Open incidents")

    def explain_anomaly(self, anomaly_id: str):
        from adapt.api import views as v

        a = v.anomaly_view(self.db, anomaly_id)
        if a is None:
            return {"error": f"no incident {anomaly_id}"}, "/anomalies", "Incident"
        nar = v.latest_narrative(self.db, "incident", anomaly_id)
        return ({"title": a["title"], "probable_driver": a["driver"], "causal": a["causal"],
                 "checked_explanation": [s["text"] for s in (nar or {}).get("sentences", [])]},
                "/anomalies", f"Incident {anomaly_id}")

    def get_decisions(self, status: str = "PENDING_APPROVAL"):
        from adapt.api import views as v

        ds = [d for d in v.decision_list(self.db) if status == "ANY" or d["status"] == status][:5]
        out = [{"decision_id": d["decision_id"], "title": d["title"], "status": d["status"],
                "expected_caa_p50_inr": d["expected"]["p50"], "p10_inr": d["expected"]["p10"],
                "p90_inr": d["expected"]["p90"], "model_prob_loss": d["expected"]["prob_loss"],
                "unallocated_inr": d["unallocated"],
                "legs": [{"entity": leg["entity"], "budget_id": leg["budget_id"], "before_inr": leg["before"],
                          "after_inr": leg["after"]} for leg in d["legs"]],
                "why_not": [w["reason"] for w in d["why_not"]][:3]} for d in ds]
        href = f"/decisions/{ds[0]['decision_id']}" if len(ds) == 1 else "/decisions"
        return {"decisions": out}, href, "Decisions"

    def get_inventory(self):
        rows = self.db.query("""SELECT sku, on_hand, mean_daily_units FROM marts.sku_daily
                                WHERE date = (SELECT max(date) FROM marts.sku_daily) ORDER BY sku""")
        out = []
        for sku, on_hand, mdu in rows:
            cover = None if not mdu else round(float(on_hand) / float(mdu), 1)
            out.append({"sku": sku, "on_hand": on_hand, "days_of_cover": cover})
        out.sort(key=lambda r: (r["days_of_cover"] is None, r["days_of_cover"] or 0))
        return {"lowest_cover_skus": out[:10]}, "/opportunities", "Inventory"

    def get_reconciliation(self):
        from adapt.api.routers.data import reconciliation_view

        return reconciliation_view(self.db).model_dump(), "/data", "Reconciliation"

    def get_response_curve(self, budget_id: str):
        from adapt.api import views as v
        from adapt.economics.state import load_state

        run = self.db.query("SELECT max(as_of) FROM ops.pipeline_runs WHERE status = 'COMPLETED'")[0][0]
        u = next((x for x in load_state(self.db, run).units if x.unit_id == budget_id), None)
        if u is None:
            return {"error": f"no budget unit {budget_id}"}, "/opportunities", "Response curve"
        out = {"budget_id": budget_id, "name": v.unit_names(self.db).get(budget_id, budget_id),
               "current_budget_inr": u.budget, "model_available": u.model_available}
        if u.model_available:
            out["model_estimated_marginal_roas"] = float(u.curve.marginal_roas(u.pacing * u.budget))
            out["curve_status"] = u.curve.status
        return out, "/opportunities", f"Response curve {budget_id}"

    def simulate_allocation(self, legs: list[dict]):
        from adapt.economics.portfolio import Portfolio
        from adapt.economics.state import load_state

        run = self.db.query("SELECT max(as_of) FROM ops.pipeline_runs WHERE status = 'COMPLETED'")[0][0]
        state = load_state(self.db, run)
        alloc = {u.unit_id: u.budget for u in state.units}
        unknown = [leg["budget_id"] for leg in legs if leg["budget_id"] not in alloc]
        if unknown:
            return {"error": f"unknown budget ids {unknown}"}, "/optimizer", "What-if"
        for leg in legs:
            alloc[leg["budget_id"]] = float(leg["after"])
        s = Portfolio(state).evaluate(alloc).summary()
        return ({"expected_caa_change_inr": s["E"], "p10_inr": s["P10"], "p90_inr": s["P90"],
                 "model_prob_loss": s["prob_loss"], "net_revenue_change_inr": s["delta_net_revenue"],
                 "note": "model estimate over 7 days; not executable"}, "/optimizer", "What-if valuation")

    def run_sql(self, query: str):
        from adapt.agent import sql

        r = sql.run(self.db.path, query, limit=50)
        return {"columns": r["columns"], "rows": r["rows"], "truncated": r["truncated"]}, "/data", "SQL result"

    def select_proposal(self, proposal_id: str, rationale: str):
        from adapt.decide.decisions import DecisionError, get_decision

        try:
            d = get_decision(self.db, proposal_id)
        except DecisionError:
            return {"error": f"no proposal {proposal_id}: the copilot can only select an existing decision id"}, \
                "/decisions", "Proposal"
        routed = ("APPROVE: awaiting a manager's hash-bound approval" if d["status"] == "PENDING_APPROVAL"
                  else f"not actionable (status {d['status']})")

        def work(cur):
            cur.execute(DDL)
            cur.execute("INSERT INTO ops.copilot_selections VALUES (?, ?, ?, ?, ?)",
                        [proposal_id, datetime.now(), self.actor, rationale[:500], routed])

        self.db.write(work)
        return ({"proposal_id": proposal_id, "status": d["status"], "routed_to": routed},
                f"/decisions/{proposal_id}", f"Decision {proposal_id}")


# ---- the guard -------------------------------------------------------------------------------------------------------
def _leaves(x) -> tuple[list[float], set[str]]:
    nums, text = [], set()
    if isinstance(x, bool):
        return nums, text
    if isinstance(x, (int, float)):
        nums.append(float(x))
    elif isinstance(x, str):
        text.update(ID_RE.findall(x))
        for q in numbers.parse(x):
            nums.append(q.value)
    elif isinstance(x, dict):
        for k, v in x.items():
            n, t = _leaves(v)
            nums += n
            text |= t
            text.update(ID_RE.findall(str(k)))
    elif isinstance(x, (list, tuple)):
        for v in x:
            n, t = _leaves(v)
            nums += n
            text |= t
    return nums, text


def check(text: str, results: dict[str, dict]) -> tuple[bool, list[str], list[str]]:
    """(ok, violations, cited ids). Every number must match a value of a CITED result."""
    v = []
    cited = list(dict.fromkeys(CITE_RE.findall(text)))
    unknown = [c for c in cited if c not in results]
    if unknown:
        v.append(f"cited unknown result ids {unknown}")
    pool, ids = [], set()
    for c in cited:
        if c in results:
            n, t = _leaves(results[c]["data"])
            pool += n
            ids |= t
    plain = CITE_RE.sub("", text)
    qs = numbers.parse(plain)
    if qs and not cited:
        v.append("numbers without a cited tool result")
    for q in qs:
        if not any(numbers.matches(q, val, unit, REL_TOL) for val in pool
                   for unit in ("INR", "PCT", "FRACTION", "PLAIN", "DAYS", "X")):
            v.append(f"'{q.raw}' matches no value in the cited results")
    for eid in ID_RE.findall(plain):
        if eid not in ids:
            v.append(f"id {eid} is not in the cited results")
    if CAUSAL.search(plain.lower()):
        v.append("causal wording is not allowed")
    return not v, v, cited


def answer(db, message: str, client, runtime=None, actor: str = "copilot", template=None) -> dict:
    """{text, evidence[{label, href}], mode, ...}: the guarded LLM answer, or the template answer."""
    from adapt.agent.groq_client import LLMUnavailable

    def fallback(reason: str) -> dict:
        out = template(db, message) if template else {"text": "LLM offline.", "evidence": [], "mode": "TEMPLATE"}
        return {**out, "fallback_reason": reason}

    if client is None or client.offline:
        return fallback("LLM offline")
    tools = Tools(db, runtime, actor)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": message[:2000]}]
    retried = False
    for _step in range(MAX_STEPS + 2):
        try:
            model, msg = client.chat_tools(messages, TOOLS)
        except LLMUnavailable as exc:
            return fallback(f"LLM unavailable: {exc}")
        calls = msg.get("tool_calls") or []
        if calls and sum(1 for m in messages if m["role"] == "tool") < MAX_STEPS:
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                res = tools.call(fn.get("name", ""), args if isinstance(args, dict) else {})
                messages.append({"role": "tool", "tool_call_id": call.get("id"),
                                 "content": json.dumps(res, default=float)[:6000]})
            continue
        text = (msg.get("content") or "").strip()
        ok, violations, cited = check(text, tools.results)
        if ok and text:
            evidence, seen = [], set()
            for c in cited:
                r = tools.results[c]
                if r["href"] not in seen:
                    seen.add(r["href"])
                    evidence.append({"label": r["label"], "href": r["href"]})
            return {"text": CITE_RE.sub("", text).replace("  ", " ").strip(), "evidence": evidence or
                    [{"label": "Decisions", "href": "/decisions"}], "mode": "LLM", "model": model,
                    "tool_calls": [r["tool"] for r in tools.results.values()]}
        if retried:
            return fallback("the guard rejected the answer twice: " + "; ".join(violations[:3]))
        retried = True
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": "Your answer failed the evidence check: " + "; ".join(
            violations[:6]) + ". Rewrite it citing [T#] for every fact and using only numbers from those results."})
    return fallback("tool-step limit reached")
