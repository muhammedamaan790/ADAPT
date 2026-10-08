"""Ask ADAPT: the workspace agent behind POST /copilot/chat (spec §11 Copilot, built on the Stage 2 Groq client).

A Groq tool-calling chat over the same read-only views the screens use, so it can answer anything about the
workspace: performance, incidents and their drivers, decisions and why-nots, executions and the ledger, outcomes and
calibration, data health, policy, the simulated world. Rules:

- Read-only. No tool approves, executes or edits anything; the agent points to the Decision Center instead.
- Grounded. Every figure in an answer must appear in a tool result or the question (agent/grounding.py). One
  corrective retry is allowed; an answer that still carries unmatched figures is shown with them listed, never
  silently.
- At most `max_tool_steps` tool rounds. Progress is streamed as `status` events; the answer arrives whole, after its
  check.
- No GROQ_API_KEY, or no model answering: the deterministic template answer (insight_views.copilot_answer).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from fastapi import Request

from adapt.agent import grounding
from adapt.agent.atoms import narrative_config
from adapt.agent.groq_client import GroqClient, LLMUnavailable, agent_client
from adapt.api import insight_views as iv
from adapt.api import views as v
from adapt.api.routers import data as data_routes
from adapt.api.routers.loop import _scenario, _state_cache, rt
from adapt.policy.engine import current_policy

GREETING_RE = re.compile(r"^\s*(hi|hey|hello|hiya|yo|good (morning|afternoon|evening)|namaste|thanks|thank you)\b",
                         re.IGNORECASE)


def _cfg() -> dict:
    return narrative_config()["copilot"]


# ---- tools ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    status: str                       # progress line shown while the tool runs
    run: Callable[[Request, dict], object]
    href: Callable[[dict], tuple[str, str]] | None = None   # (label, app route) cited when the tool was used
    params: dict | None = None


def _dump(x):
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    if isinstance(x, list):
        return [_dump(i) for i in x]
    return x


def _overview(req, _a):
    r = rt(req)
    return v.overview_view(r.db, r.settings.workspace, r.world(), _scenario(r.db))


def _decision(req, a):
    db = rt(req).db
    out = v.decision_view(db, a["decision_id"])
    if out is None:
        return {"error": f"no decision {a['decision_id']}; call list_decisions for valid ids"}
    try:
        out["evidence"] = v.evidence_view(db, a["decision_id"])
    except Exception:  # an operational decision may have no evidence package
        out["evidence"] = None
    return out


def _anomaly(req, a):
    out = v.anomaly_view(rt(req).db, a["anomaly_id"])
    return out if out is not None else {"error": f"no anomaly {a['anomaly_id']}; call list_anomalies for valid ids"}


def _learning(req, _a):
    db = rt(req).db
    return {"calibration": iv.calibration(db), "accuracy": iv.accuracy(db), "feedback": iv.feedback(db),
            "models": iv.models(db)}


def _data_health(req, _a):
    return {"sources": _dump(data_routes.sources(req)), "health": _dump(data_routes.health(req)),
            "reconciliation": _dump(data_routes.reconciliation(req)),
            "mapping_coverage": _dump(data_routes.mapping_coverage(req))}


def _policy(req, _a):
    r = rt(req)
    try:
        world = int(r.world().get("seed"))
    except Exception:  # noqa: BLE001 - the readiness view works without the world's id
        world = -1
    return {"policy": iv.policy(r.db, current_policy(r.db), r.adapters, world),
            "objective": iv.objective(r.db, r.settings.workspace)}


def _opportunities(req, _a):
    r = rt(req)
    state, flags, _opt, _as_of = _state_cache(r)
    return {"opportunities": iv.opportunities(r.db, state, flags), "creative_fatigue": iv.creative_fatigue(r.db)}


def _world(req, _a):
    r = rt(req)
    return {"world": r.world(), "active_scenario": _scenario(r.db),
            "pipeline": {**r.job, "busy": r.busy, "unresolved_executions": r.unresolved_executions()}}


_ID = {"type": "object", "additionalProperties": False}
TOOLS: list[Tool] = [
    Tool("get_overview", "Command Center: headline KPIs for the last 7 days vs the prior 7 (net revenue, ad spend, "
         "contribution after ads, MER, POAS, at-risk SKUs), the morning brief, calibration factor, world day.",
         "Reading the Command Center", lambda q, a: _overview(q, a), lambda a: ("Command Center", "/")),
    Tool("list_decisions", "All budget decisions with status (pending approval, executed, rejected...), class, "
         "expected contribution change and summary.", "Checking decisions",
         lambda q, a: v.decision_list(rt(q).db), lambda a: ("Decision Center", "/decisions")),
    Tool("get_decision", "One decision in full: allocation legs, expected ΔCAA with P10/P90, P(loss), policy checks, "
         "why-not alternatives, inventory and evidence.", "Opening the decision",
         _decision, lambda a: (f"Decision {a.get('decision_id', '')}", f"/decisions/{a.get('decision_id', '')}"),
         {**_ID, "properties": {"decision_id": {"type": "string"}}, "required": ["decision_id"]}),
    Tool("list_anomalies", "Detected incidents and signals with metric, change vs baseline, probable driver and "
         "status.", "Scanning anomalies", lambda q, a: v.anomaly_list(rt(q).db), lambda a: ("Anomalies", "/anomalies")),
    Tool("get_anomaly", "One incident in detail: observed vs expected, drivers ranked with evidence, decomposition.",
         "Investigating the incident", _anomaly, lambda a: ("Anomalies", "/anomalies"),
         {**_ID, "properties": {"anomaly_id": {"type": "string"}}, "required": ["anomaly_id"]}),
    Tool("list_executions", "Executions of approved decisions: saga state, legs, verification, plus the action "
         "ledger.", "Reviewing executions",
         lambda q, a: {"executions": v.execution_list(rt(q).db), "ledger": v.ledger_list(rt(q).db)},
         lambda a: ("Execution & Ledger", "/executions")),
    Tool("list_outcomes", "Measured outcomes of executed decisions: forecast vs measured, verdict, calibration "
         "eligibility.", "Reading outcomes", lambda q, a: v.outcome_list(rt(q).db),
         lambda a: ("Outcomes", "/outcomes")),
    Tool("get_learning", "Learning loop: calibration factor history, forecast accuracy, feedback eligibility, models "
         "(champion/challenger).", "Checking what the system learned", _learning,
         lambda a: ("Learning", "/learning")),
    Tool("get_data_health", "Data sources and their health scores, freshness, attribution reconciliation and "
         "mapping coverage.", "Checking data health", _data_health, lambda a: ("Data Hub", "/data")),
    Tool("get_policy", "Execution policy per channel (approve mode, guardrails, readiness) and the optimisation "
         "objective.", "Reading policy", _policy,
         lambda a: ("Channel policy", "/executions?section=policy")),
    Tool("get_opportunities", "Ranked growth candidates (marginal contribution, feasibility, blockers) and creative "
         "fatigue signals.", "Ranking opportunities", _opportunities, lambda a: ("Decision Center", "/decisions")),
    Tool("get_recent_events", "The activity log: pipeline runs, detections, approvals, executions, outcomes, newest "
         "first.", "Reading recent activity",
         lambda q, a: v.event_list(rt(q).db, limit=int(a.get("limit") or 25)), lambda a: ("Command Center", "/"),
         {**_ID, "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 60}}}),
    Tool("get_world_state", "The simulated world: today's date and day number, the active scenario, pipeline job "
         "status.", "Checking the simulation", _world, lambda a: ("Scenario Lab", "/scenarios")),
]
BY_NAME = {t.name: t for t in TOOLS}


def tool_schemas() -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.params or {**_ID, "properties": {}}}} for t in TOOLS]


def compact(obj, items: int):
    """Drop empty values, round floats and cap list lengths so a tool result fits the model's context."""
    if isinstance(obj, dict):
        out = {k: compact(x, items) for k, x in obj.items() if x not in (None, "", [], {})}
        return {k: x for k, x in out.items() if x not in (None, "", [], {})}
    if isinstance(obj, list):
        kept = [compact(x, items) for x in obj[:items]]
        return kept + ([f"... {len(obj) - items} more"] if len(obj) > items else [])
    if isinstance(obj, float):
        return round(obj, 4) if abs(obj) < 100 else round(obj, 2)
    return obj


def run_tool(req: Request, name: str, args: dict) -> tuple[object, str]:
    """(raw result, compact JSON for the model). Errors become a result the model can read and recover from."""
    tool = BY_NAME.get(name)
    if tool is None:
        return None, json.dumps({"error": f"unknown tool {name}"})
    cfg = _cfg()
    try:
        raw = _dump(tool.run(req, args))
    except Exception as exc:  # a missing table or a 503 must not end the conversation
        detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
        return None, json.dumps({"error": f"{name} unavailable: {detail}"})
    text = json.dumps(compact(raw, int(cfg["list_items"])), default=str, ensure_ascii=False)
    limit = int(cfg["tool_result_chars"])
    return raw, text if len(text) <= limit else text[:limit] + ' ... (truncated)"'


# ---- prompt --------------------------------------------------------------------------------------------------------
def system_prompt(req: Request) -> str:
    r = rt(req)
    try:
        w = r.world()
        today = f"Simulated world date {w.get('date')} (day {w.get('day')})."
    except Exception:
        today = "The simulated world clock is unavailable right now."
    return f"""You are ADAPT, the AI assistant inside ADAPT (Autonomous Decision & Allocation Platform), an \
advertising decision workspace for the D2C brand workspace "{r.settings.workspace}". Currency is INR. {today}

What the product does, as a closed loop: it ingests ads (Meta, Google, TikTok, Amazon), store, GA4 and inventory data \
and reconciles them; detects anomalies and diagnoses probable drivers (creative fatigue, auction pressure, \
saturation, demand, price, inventory, tracking); forecasts response curves and stock risk; an optimiser proposes \
budget reallocations that maximise contribution after ads under guardrails; a human approves in the Decision Center; \
execution runs as a verified saga; outcomes are measured against the forecast and calibrate future forecasts.

How to answer:
- Be warm, natural and concise, like a sharp analyst colleague. Greet back when greeted. General marketing or \
analytics questions can be answered from your own knowledge.
- For anything about this workspace, call the tools first; never guess. Use as few tools as the question needs.
- Quote figures exactly as the tools return them, formatted for India (₹1,25,000 or ₹1.25 L). Do not compute new \
totals, differences or percentages that the tools did not return.
- Say "probable driver" or "evidence points to", never "caused". Keep forecasts, measured results and simulated data \
clearly labelled.
- You cannot approve, reject, execute or change anything. For actions, tell the user where to do it (Decision \
Center, Execution & Ledger, Scenario Lab).
- Format: plain sentences, short bullet lists with "- " when listing, **bold** for the key figure. No tables, no \
headings. Usually 2 to 6 sentences."""


# ---- the agent loop ------------------------------------------------------------------------------------------------
def _assistant_message(msg: dict) -> dict:
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


def _history(history: list[dict], turns: int) -> list[dict]:
    keep = [h for h in history if h.get("role") in ("user", "assistant") and str(h.get("content", "")).strip()]
    return [{"role": h["role"], "content": str(h["content"])[:2000]} for h in keep[-2 * turns:]]


def _template(req: Request, message: str, note: str) -> dict:
    if GREETING_RE.match(message) and len(message.split()) <= 6:
        text = ("Hi! I'm ADAPT's assistant. Ask me about performance, incidents, pending decisions, executions, "
                "outcomes or data health, and I'll answer from the workspace.")
        reply = {"text": text, "evidence": [{"label": "Command Center", "href": "/"}]}
    else:
        reply = iv.copilot_answer(rt(req).db, message)
    return {**reply, "mode": "TEMPLATE", "verified": True, "unverified": [], "tools": [], "note": note}


def answer(req: Request, message: str, history: list[dict] | None = None,
           client: GroqClient | None = None) -> Iterator[dict]:
    """Events: {type: status, text} while tools run, then {type: answer, reply}, then {type: done}."""
    cfg = _cfg()
    if client is None:
        r = rt(req)
        if getattr(r, "_agent_client", None) is None:
            r._agent_client = agent_client(r.settings)  # the model list is checked once per process
        client = r._agent_client
    if client.offline:
        yield {"type": "answer", "reply": _template(req, message, "AI offline: set GROQ_API_KEY in adapt/.env "
                                                                  "for full answers.")}
        yield {"type": "done"}
        return
    messages = [{"role": "system", "content": system_prompt(req)},
                *_history(history or [], int(cfg["history_turns"])), {"role": "user", "content": message}]
    known = grounding.values_in(message)
    for h in history or []:
        if h.get("role") == "user":
            grounding.values_in(str(h.get("content", "")), known)
    used: list[tuple[str, str]] = []
    tools_run: list[str] = []
    retried = False
    model = ""
    try:
        steps = 0
        while True:
            offer_tools = steps < int(cfg["max_tool_steps"])
            model, msg = client.complete(messages, tool_schemas() if offer_tools else [])
            calls = msg.get("tool_calls") or []
            if calls and offer_tools:
                steps += 1
                messages.append(_assistant_message(msg))
                for call in calls:
                    fn = call.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}") or {}
                    except ValueError:
                        args = {}
                    tool = BY_NAME.get(name)
                    if tool is not None:
                        yield {"type": "status", "text": tool.status}
                    raw, text = run_tool(req, name, args)
                    if raw is not None:
                        grounding.values_in(raw, known)
                        tools_run.append(name)
                        if tool is not None and tool.href is not None:
                            link = tool.href(args)
                            if link not in used:
                                used.append(link)
                    messages.append({"role": "tool", "tool_call_id": call.get("id", name), "content": text})
                continue
            text = (msg.get("content") or "").strip()
            if not text:
                raise LLMUnavailable(f"{model}: empty answer")
            check = grounding.check(text, known, small_int=int(cfg["small_int_allowance"]))
            if not check.ok and not retried:
                retried = True
                yield {"type": "status", "text": "Double-checking the figures"}
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": (
                    "Before I show that: these figures do not appear in any tool result: "
                    + ", ".join(check.unverified) + ". Rewrite the answer quoting only figures exactly as the "
                    "tools returned them (call a tool if you need one). Do not mention this check.")})
                continue
            reply = {"text": text, "evidence": [{"label": lbl, "href": href} for lbl, href in used[:4]],
                     "mode": "LLM", "model": model, "verified": check.ok, "unverified": check.unverified,
                     "tools": tools_run}
            yield {"type": "answer", "reply": reply}
            yield {"type": "done"}
            return
    except LLMUnavailable as exc:
        yield {"type": "answer", "reply": _template(req, message, f"AI unavailable right now ({exc}); showing the "
                                                                  "built-in answer.")}
        yield {"type": "done"}
