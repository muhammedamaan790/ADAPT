# Frontend API handoff

Specification: ADAPT v2.4.3 §§0.5, 12, 13, 22.4–22.5. Wire schemas: `adapt/web/src/api/contracts.ts`. Client: `adapt/web/src/api/client.ts`.

**Status:** provisional frontend contract, not generated OpenAPI. The current backend foundation exposes only `/api/v1/health`; C6 must implement/align the decision-loop endpoints before API mode is integrated. Do not change engine mathematics to match illustrative UI fixtures. The application root is `adapt/`, with sibling `web/`, `backend/` and `world/` directories, matching the existing backend layout.

## Endpoint contracts

All reads return JSON. List reads below return arrays (no pagination envelope in this first adapter). Monetary values are INR numbers; dates are ISO timestamps; absent-denominator values are `null` + reason, never NaN/Infinity.

| Method | Path under `/api/v1` | Response / request |
|---|---|---|
| GET | `/overview` | `overviewSchema`: metrics, source health, attention, series, loop, world day/scenario, factor and four verdict counts |
| GET | `/decisions` | `decisionSchema[]` |
| GET | `/decisions/{id}` | `decisionSchema` |
| GET | `/decisions/{id}/evidence` | `evidenceSchema`: chart, accounting decomposition and evidence drivers |
| POST | `/decisions/{id}/approve` | `{decision_hash, execute:true}` → updated `decisionSchema` |
| POST | `/decisions/{id}/reject` | `{decision_hash, reason}` → updated `decisionSchema` |
| GET | `/executions` | `executionSchema[]` |
| GET | `/outcomes` | `outcomeSchema[]` |
| GET | `/events` | `eventSchema[]`, **provisional polling endpoint** |
| POST | `/sim/scenario/{key}` | `{}` → `{ok:true}` |
| POST | `/sim/advance?days=1\|3\|7` | `{}` → `{ok:true}` |
| POST | `/sim/reset?seed=42` | `{}` → `{ok:true}` |
| GET | `/health` | Exact backend foundation `healthSchema` (also probed in fixture mode) |
| GET | `/anomalies` | `anomalySchema[]` |
| GET | `/anomalies/{id}` | `anomalySchema` |
| POST | `/anomalies/{id}/status` | `{status,reason}` → updated `anomalySchema` |
| GET | `/optimizer/context` | **Proposed read endpoint**, `optimizerContextSchema` |
| POST | `/optimizer/run` | `{objective:"PROFIT"}` → `decisionSchema` |
| POST | `/optimizer/whatif` | `allocationInputSchema` → `evaluationSchema` |
| POST | `/decisions/{id}/modify` | `allocationInputSchema` → a new `decisionSchema`, never in-place mutation |
| GET | `/ledger` | `ledgerSchema[]` |
| POST | `/executions/{id}/verify` | `{decision_hash,reason}` → `executionSchema` |
| POST | `/executions/{id}/retry` | `{decision_hash,reason}` → `executionSchema` |
| POST | `/executions/{id}/rollback` | `{decision_hash,reason}` → `executionSchema`; restore settings only |
| POST | `/executions/{id}/reconcile` | `{decision_hash,reason,final_resolution:"COMPENSATED"}` → `executionSchema` |
| POST | `/sim/fault/{UNKNOWN\|FAILED}` | `{}` → `{ok:true}`; no public API-mode UI control yet |

The workbench schemas live in `adapt/web/src/api/workbench-contracts.ts`. These routes remain provisional until C6 aligns them with its OpenAPI. The optimizer context route is a view-model proposal, not an endpoint already promised by the backend. Native paginated/domain responses should be adapted once in `client.ts` and validated there.

## Next frontend workbench boundaries

- Anomaly classification, gates and causal results are backend-owned. Fixture diagnoses show probable drivers and `NOT_ESTIMABLE`, not invented causal effects. S7 budget cuts remain visible but do not increase efficiency incident counts.
- The optimizer browser checks whole-rupee inputs, exact portfolio coverage, min/max budgets, daily movement bounds, inventory scale blocks, ceiling and reserve. These are input checks, not an authoritative risk or inventory policy evaluation.
- What-if valuation is an exact recorded example only in fixture mode. Arbitrary edits return `NOT_ESTIMABLE` with `estimate:null`. In API mode, the backend supplies the estimate and policy checks. The browser never computes model ΔCAA or bootstrap risk.
- A fixture modification creates a distinct `DRAFT` with `follows` and `valuation_status:"NOT_ESTIMABLE"`; the original becomes `SUPERSEDED`. Forecast, projected inventory and rejected alternatives are withheld for that draft. API mode must receive a separately valued decision from the backend before approval becomes possible.
- Execution legs optionally include `read_back_budget:number|null`. Requested settings, latest read-back, external request state and simulation mirror state are separate. Unknown read-back never displays a requested amount as verified.
- Retry is enabled only for a confirmed failure with no unknown, conflict, sent or unresolved mirror leg. Restore prior settings requires known state and a reason. Neither action undoes spend or exposure.
- Reconciliation currently requests **COMPENSATED only**. The fixture requires every read-back already equals its prior setting; reconciliation records the resolution and does not restore budgets. Accepted-partial/blocked manager workflows and real fresh-read requirements remain backend follow-up work.
- Recovery examples support UNKNOWN/FAILED injections after fixture approval and before outcome maturity. World advance/reset/scenario replacement stay blocked for unresolved executions. Restored fixture allocations produce no optimization feedback, since the proposed allocation was not maintained for its horizon.
- Outcome maturity uses days elapsed since execution, not since workspace initialization. This is UI fixture lifecycle behavior; measured values remain illustrative.
- Backend Connection probes seven GET endpoints. `READY` means a response matches the wire schema; a degraded health payload is shown as degraded separately. It does not validate auth sessions, mutations, live spend, or engine mathematics.

### Agreements still needed with C6

1. Spec names `/events/stream` SSE, but no event wire envelope yet. This first frontend uses `/events` polling through a single adapter. Replace `api.events` with an SSE store or provide a read-only event list. Never expose `gt_incidents` or hidden world parameters.
2. Confirm whether approval starts execution (`execute:true`) or only returns APPROVED. The UI supports APPROVED status but its complete flow expects an execution record. If a distinct execute endpoint is selected, implement it in the adapter, retaining hash-bound approval and backend policy checks.
3. Overview/evidence presentation fields (title, summary, time series, formula lineage) extend the canonical decision fields. Supply them as backend view models or adapt native endpoint responses in `client.ts`; do not recompute analytics in components.
4. Session authentication is backend-owned. `fetch` includes credentials. If the API requires CSRF or actor headers, wire the approved auth contract here. Never trust a frontend actor header alone.
5. Scenario replacement/reset semantics must be confirmed by the world owner. The UI asks for confirmation because its fixture adapter clears prior state. API control scope may differ.

## Mutation safety

Every mutation includes `X-Request-ID` and `Idempotency-Key`. Query reads may retry; mutations never automatically retry. Backend responses with 409 expire/conflict the operation visibly. Network uncertainty prompts refresh/read-back before retrying. The browser never writes canonical decision status in API mode. Policy checks in the UI are explanatory; the backend must revalidate dependency manifest, staleness classes, policy version, entity reservations and external state.

No API failure silently selects fixture data. A mode change needs an explicit `.env.local` edit and server restart. A manual new mutation produces a new key; C5 must enforce semantic idempotency per decision/hash and resolve uncertain requests. If request-ID reuse across explicit retry is required, add a persisted pending-intent store before enabling retry controls. There are no retry/reconcile/rollback buttons in this Stage 1 frontend.

## Stage-specific truth

- Stage 1 inventory kind must be `PROJECTED_SHORTFALL`. The UI rejects Stage 2 probabilities in that slot.
- Every decision has `provenance_inputs`; KPI popovers give formula, source and `available_at`.
- Evidence score = diagnostic strength, not causal confidence.
- `expected.prob_loss` is labelled **model P(loss)**.
- `unallocated` is all undeployed money, including the reserve; `reserve_floor` is not added again.
- Safety outcome is avoided loss and cannot update response curves.
- Outcome measurement is forecast-counterfactual, not causal proof. All four verdict totals appear.
- Execution uses distinct decision, saga, external and mirror states. UNKNOWN and mirror failures remain visible; world advance is blocked for unresolved states.
- No live Google or production-autonomous badges appear in fixtures.

## Browser acceptance path

`/` → top incident → `/decisions/{id}` → evidence, reserve, why-not and checks → approve displayed hash → verify every leg → advance 3 days → outcome → factor changed exactly once → next forecast → Scenario Lab reset. Repeat with S3 (remaining shortfall + review), S5 (blocked), S7 (no efficiency incident), rejection, API unavailable and 409 stale hash.
