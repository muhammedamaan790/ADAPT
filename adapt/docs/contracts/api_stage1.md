# Contract: Stage 1 API (C6) and template narratives (C7)

Modules: `backend/adapt/api/{models,views,runtime}.py`, `backend/adapt/api/routers/loop.py`,
`backend/adapt/decide/narrative.py`. OpenAPI: `docs/openapi.json` (`scripts/export_openapi.py`) or `/openapi.json`
on a running server.

Wire shapes mirror the frontend's zod schemas (`web/src/api/contracts.ts`, `workbench-contracts.ts`) field for
field, and FastAPI validates every response before sending it. Fields that zod marks `.optional()` (`follows`,
`valuation_status`, `Metric.reason`) are omitted when empty, never sent as `null`. **Verified**: the real responses
of a full fixture-world loop (overview, decisions, decision, evidence, executions, outcomes, events, anomalies,
health, ledger, optimizer context) all pass the frontend's own zod schemas under vitest.

## Run
1. Start the world service: `WORLD_DIR=data/world/seed42 uv run uvicorn world.main:app --app-dir world --port 8100`
2. Bootstrap the workspace once (sync + day-0 cycle + baseline): `uv run python -m adapt.api.runtime --bootstrap`
3. Start the API: `uv run uvicorn adapt.api.main:app --app-dir backend --port 8000`

The Vite dev server proxies `/api` to it.

## Endpoints (`/api/v1`)
| Area | Method + path | Notes |
|---|---|---|
| System | GET `/health` | unchanged |
| Command Center | GET `/overview`, `/events`, `/pipeline/status` | `/pipeline/status` shows background jobs |
| Decision Center | GET `/decisions`, `/decisions/{id}`, `/decisions/{id}/evidence` | |
| Decision Center | POST `/decisions/{id}/approve` `{decision_hash, execute}` | `execute:true` runs the saga in the request |
| Decision Center | POST `/decisions/{id}/reject` `{decision_hash, reason}`; POST `/decisions/{id}/modify` | modify creates a NEW decision with `follows`; the original is superseded |
| Optimizer | GET `/optimizer/context`; POST `/optimizer/run`, `/optimizer/whatif` | PROFIT only in Stage 1 |
| Anomalies | GET `/anomalies`, `/anomalies/{id}`; POST `/anomalies/{id}/status` | |
| Execution | GET `/executions`, `/ledger`, `/outcomes`; POST `/executions/{id}/verify`, `/rollback`, `/reconcile` | POST `/retry` → 422 NOT_BUILT |
| Scenario Lab | GET `/sim/scenarios` (catalog: Stage 1 AVAILABLE, the rest NOT_BUILT with missing modules); POST `/sim/scenario/{key}` (S1–S5, S7, DEMO_01), `/sim/advance?days=n`, `/sim/reset?seed=42`, `/sim/fault/FAILED` | `/sim/fault/UNKNOWN` → 422 NOT_BUILT |

**Rules for every mutation**:
- It requires `X-Request-ID` and `Idempotency-Key` (422 without them). Semantic idempotency is enforced by the
  engine: approval is bound to `decision_hash`, and a decision has at most one execution saga.
- Status codes:

  | Code | Meaning |
  |---|---|
  | 409 | stale hash, expired decision, conflict, pipeline busy, unresolved execution |
  | 403 | role |
  | 404 | unknown id |
  | 422 | Stage 2 feature |
  | 503 | world service unreachable |

## Agreements (answers to the open items in `docs/frontend-api.md`)
1. **Events**: `/events` polling (newest first, ≤ 100): decision lifecycle events, `action_executed`,
   `outcome_matured`, pipeline runs, execution states and the running job. SSE is not built.
2. **Approve executes**: `execute:true` (the UI default) approves, then runs the saga in the same request (seed 42:
   40 verified legs in about 2 s). `execute:false` returns APPROVED.
3. **View-model fields**:
   - title, summary and why-not reasons come from the deterministic templates (C7); brief and metrics come from the
     backend
   - `cost_of_inaction_7d` = the model-estimated contribution forgone by not acting (max(E[ΔCAA], 0) of the
     decision). The spec's pre-anomaly-path definition is Stage 2.
   - `by_sku` = projected shortfall units
   - leg `entity` = campaign name(s)
4. **Auth**: Stage 1 acts as one demo manager. Session auth with roles and CSRF is not built; until it is, the API
   must not be exposed beyond localhost.
5. **Advance and reset**:
   - Advance moves the world and returns `{ok:true}` at once. The pipeline then runs for each new day as a
     background job (about 40 s per day on seed 42), and mutations get 409 while it runs; poll
     `/pipeline/status` or `/events`.
   - Advance and scenario changes are refused while an execution is unresolved.
   - Reset restores the world AND the workspace to its day-0 baseline, clearing decisions, executions and outcomes
     since day 0.

## Measured (seed 42, live over HTTP)
| | Value |
|---|---|
| Bootstrap (full sync + day-0 cycle + baseline) | 322 s, once |
| GET endpoints | 0.1–0.6 s |
| Approve + execute | 1.3 s, 40 legs VERIFIED by read-back |
| Advance 1 day | acknowledged in 0.5 s; the background pipeline job completes in 31 s |
| Reset | 1.8 s, back to day 0 with no executions |

Port note: on the development machine a Docker container already publishes port 8000 with an older API build, so
run this build on another port (`--port 8010` and `VITE_API_BASE_URL`), or stop that container.

**Whole rupees**: the optimizer context returns whole-rupee targets. In what-if and modify, an entry within one rupee
of a fractional current budget (a Meta budget converted from USD cents) means "unchanged".

## Stage 1 browser journey (D5) against the real backend
`web/playwright.live.config.ts` with `web/tests/live-api/golden.spec.ts` (Firefox) **passes** on seed 42 in about
9 minutes:
1. Command Center in API MODE, with no contract mismatch; the lineage popover shows the formula.
2. The top decision shows its evidence, why-not reasons, policy checks and unallocated budget.
3. Approve the displayed hash and execute: the browser shows all 40 legs verified.
4. Advance 3 days (background job): the outcome panel shows INCONCLUSIVE, with +₹3.91 L measured against a
   +₹8.25 L calibrated decision-time forecast.
5. Load S3 and advance 2 days: a safety proposal ("reduce spend selling M_JEANS-P5 by ₹68,660/day") is rejected with
   a reason.
6. Reset: day 0, with no outcomes.

**Frontend follow-up (copy)**: when `calibration_applied` is false, the outcome panel always says "Safety outcomes are
excluded from response-curve calibration". For an INCONCLUSIVE OPTIMIZATION outcome the true reason is
"INCONCLUSIVE outcomes do not calibrate".

## C7: template narratives
`decide/narrative.py` builds every decision's title, summary and why-not sentence, and the overview brief, only from
the decision object's own numbers: legs, expected values, why-not and checks. There is no LLM and no invented value.
Wording follows the claim hierarchy: "model-estimated", "model P(loss)", "forecast-counterfactual", and no causal
verbs. Rupee amounts use lakh / crore. Every decision therefore has a rationale offline.

## TESTS
`integration/test_e2e_stage1.py`, the **Stage 1 gate** through the HTTP API on the fixture world:
1. Bootstrap, then overview.
2. A pending PROFIT decision with checks, why-not, inventory risk and budget ceiling; its evidence; anomalies;
   optimizer context.
3. Mutation headers are enforced.
4. What-if, then modify (a new decision that follows the original, which becomes SUPERSEDED).
5. A stale hash → 409; approve + execute → EXECUTED with every leg read-back VERIFIED; the ledger records it; a
   duplicate execution → 409.
6. Daily advances until the outcome matures (the verdict and the factor are shown). The next cycle refits after the
   matured outcome; the events record it.
7. S3 → a SAFETY candidate in review, which is rejected; an unsupported scenario → 422.
8. Reset → day 0 with a clean slate.

Setting `ADAPT_CONTRACT_SAMPLES=<dir>` dumps the real responses for the zod check. The browser half of the gate is
the frontend's Playwright journey (D5).
