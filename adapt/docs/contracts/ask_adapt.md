# Contract: Ask ADAPT, the workspace agent (spec §11 Copilot)

Modules: `backend/adapt/api/agent.py` (tools, prompt, loop), `backend/adapt/agent/grounding.py` (figure check),
`backend/adapt/agent/groq_client.py` (`GroqClient.complete`, `agent_client`); config `copilot:` in
`config/narrative.yaml`; endpoint `POST /api/v1/copilot/chat`; UI `web/src/components/AskAdapt.tsx` on the Command
Center, above the metric band.

## What it is
A conversational agent you can ask anything about the workspace: performance, incidents and their probable drivers,
pending and past decisions with their why-nots, executions and the ledger, measured outcomes and calibration, data
health, policy, and the simulated world. It greets and chats like a normal assistant and answers general marketing
questions from its own knowledge. Workspace facts always come from tools.

## Tools (read-only; each calls the same view the matching screen uses)
| Tool | Returns | Cited link |
|---|---|---|
| `get_overview` | KPIs (7 d vs prior 7 d), brief, calibration factor, world day | Command Center `/` |
| `list_decisions` | every decision with status, class, expected ΔCAA, summary | `/decisions` |
| `get_decision(decision_id)` | legs, E/P10/P90, P(loss), policy checks, why-nots, evidence | `/decisions/{id}` |
| `list_anomalies` / `get_anomaly(anomaly_id)` | incidents, observed vs expected, ranked drivers | `/anomalies` |
| `list_executions` | sagas, legs, verification, the action ledger | `/executions` |
| `list_outcomes` | forecast vs measured, verdict, calibration eligibility | `/outcomes` |
| `get_learning` | calibration history, accuracy, feedback, models | `/learning` |
| `get_data_health` | sources, health, reconciliation, mapping coverage | `/data` |
| `get_policy` | channel policy, readiness, objective | `/executions?section=policy` |
| `get_opportunities` | ranked growth candidates, creative fatigue | `/decisions` |
| `get_recent_events(limit)` | activity log | `/` |
| `get_world_state` | world date/day, active scenario, pipeline job | `/scenarios` |

No tool approves, rejects, executes or edits anything. Results are compacted (empty values dropped, floats rounded,
lists capped at `list_items`) and cut at `tool_result_chars`. A failing tool returns `{"error": …}` to the model
instead of ending the conversation.

## Loop
1. The system prompt states the product, the world date and the rules: tools before facts; quote figures exactly
   (Indian format); hedge drivers ("evidence points to"); never claim it can act; plain text, short lists, **bold**.
2. Up to `max_tool_steps` rounds of tool calls (`status` events stream to the browser: "Checking decisions", …). The
   last round offers no tools, so the model must answer.
3. **Grounding** (`agent/grounding.py`): every quantity in the answer must match a value from a tool result, a list
   length, a quantity inside a result string, a date/time component, or a number the user typed. Matching reuses the
   guard's normalisation (₹1,25,000 / ₹1.25 L / 12.5 % ↔ 0.125 / 2.1×) and its ±0.5 % or displayed-rounding
   tolerance. Bare integers up to `small_int_allowance` are counts or numbering.
4. If any figure is unmatched, there is one corrective retry with the list fed back. If figures are still unmatched,
   the answer is shown with `verified: false` and the figures listed ("Not found in data: …"), never silently.
5. Conversation history: the last `history_turns` user/assistant turns are sent back with each question.

Model chain (`copilot.llm.models`): `openai/gpt-oss-120b` → `llama-3.3-70b-versatile` → `openai/gpt-oss-20b`,
filtered by `GET /models`. A 429 backs off twice, then the next model is tried.

## Wire format (SSE, `text/event-stream`)
Request: `{message: str (1–2000), history: [{role: user|assistant, content}] (≤ 40)}` with the usual write headers
(`X-Request-ID`, `Idempotency-Key`, CSRF when auth is on).
Events: `{type: "status", text}` × n → `{type: "answer", reply}` → `{type: "done"}`; or `{type: "error", message}`.
`reply = {text, evidence: [{label, href}], mode: LLM|TEMPLATE, model?, verified?, unverified?: [str], tools?: [str],
note?}`. Evidence links are app routes only (the frontend's `appHref` rejects anything else).

## Fallbacks
- No `GROQ_API_KEY`, or no model available: the deterministic template (`insight_views.copilot_answer`, with a
  greeting) and the note "AI offline: set GROQ_API_KEY in adapt/.env".
- Every model failing mid-conversation: the template with "AI unavailable right now (…)".
- Fixture frontend (no backend): a local template answer that says the agent needs the backend.

## TESTS
`backend/tests/test_grounding.py` (number formats, percent/fraction, list lengths, dates, rounding, user numbers).
`integration/test_agent.py`: every tool on a bootstrapped fixture workspace; a scripted Groq transport for the tool
loop (status events, grounded answer, cited link, retry that repairs, retry that still fails and lists the figure,
history passed, tool-step cap, offline greeting, failing model → template).
`web/tests/api-browser/insights.spec.ts` (stream, rich text, history, unverified badge, unsafe link, truncation,
error event, ask again), `web/tests/e2e/completion.spec.ts` and `insights.spec.ts` (fixture agent, links, mobile).
