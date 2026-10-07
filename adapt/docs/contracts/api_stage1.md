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
   (set `ADAPT_SEED_PASSWORD` the first time, or read the generated `data/auth/initial_credentials.txt`)

The Vite dev server proxies `/api` to it.

## Login, roles and CSRF (spec §9.5; `backend/adapt/api/auth.py`)
- **Users**: `viewer` (viewer), `maria` (manager), `admin` (admin), with Argon2 hashes in `data/auth/users.json`.
  They live outside the workspace file, so `/sim/reset` keeps them. The first boot takes the password from
  `ADAPT_SEED_PASSWORD`; without it, random passwords are written once to `data/auth/initial_credentials.txt`.
- **Session**: `POST /auth/login {user_id, password}` sets the HttpOnly, SameSite=Lax `adapt_session` cookie (12 h,
  signed with `ADAPT_SESSION_SECRET`; random per process when unset, so a restart signs everyone out). It returns
  `{auth_enabled, user, csrf_token}`. `GET /auth/me` returns the same for the current cookie, or 401.
  `POST /auth/logout` clears the cookie.
- **CSRF**: every POST/PUT except login sends `X-CSRF-Token: <csrf_token>` (403 `CSRF_FAILED` otherwise). The token
  is kept in memory by the frontend and is never readable from a cookie.
- **Roles**: every GET needs viewer. Read-only computations sent as POST (simulate, what-if, creative score, Copilot)
  need viewer. Every state change needs manager. Policy, objective, model promote/rollback and new workspaces need
  admin. Denials are 403 `FORBIDDEN: this action needs the <role> role`.
- **Actor**: approvals, rejections, executions, rollbacks, anomaly status changes and world control calls record the
  signed-in user (no more `demo-manager`).
- **Audit**: every write (allowed or denied) is appended to `data/auth/audit.jsonl` with request id, actor, role,
  method, path and status.
- **Throttling**: 5 failed logins for one user name within 5 minutes lock that name (429) until the window passes.
- **Off switch**: `ADAPT_AUTH_ENABLED=false` (the in-process test suite) acts as the demo manager. `/health` is
  always public.
- **Frontend**: `web/src/components/AuthGate.tsx` shows the sign-in form on a 401 in API mode (fixture mode is not
  gated). The topbar shows the user's initials and a sign-out button.

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

**Screen endpoints** (insight, learning, model, policy, workspace and decision-detail screens;
`backend/adapt/api/{insight_views,routers/insights}.py`):

| Endpoint | Source / behaviour |
|---|---|
| GET `/opportunities` | spec §8.1 score: risk-adjusted ΔCAA of +min(₹1,000, the largest feasible increase) on one unit; FEASIBLE / BLOCKED (with the reason) / NOT_ESTIMABLE (no curve) |
| GET `/curves/{budget_id}` | model-estimated 7-day ΔCAA at 21 budgets (0.5–1.5× current); empty with the reason when MODEL_UNAVAILABLE |
| GET `/creatives/fatigue` | the fatigue evidence module on every campaign over the last 7 days (REVIEW when its gates pass) |
| GET `/learning/calibration`, `/learning/accuracy`, `/learning/feedback` | calibration log, MAE of calibrated forecasts vs measured outcomes, eligibility per outcome |
| GET `/models`, `/models/{name}/{version}` | registry champion (response_curve) + seasonal-naive demand; gates and metrics; no promote / rollback |
| GET `/policy`, `/policy/history`, `/objective` | Approve-mode channels with readiness counts (never eligible in Stage 1); policy versions with field diffs; PROFIT only |
| GET `/decisions/{id}/timeline`, `/snapshot`, `/archive`; POST `/decisions/{id}/simulate` | lifecycle + saga + outcome + calibration; snapshot manifest; replay environment and artifacts; the decision vs holding the current allocation |
| GET `/decisions/{id}/replay` | re-runs economics + optimizer + policy from the snapshot in a background thread (cached per decision); UNAVAILABLE while running, then VERIFIED / MISMATCH |
| GET `/workspaces`, POST `/workspaces/{id}/activate` | the one Stage 1 workspace |
| POST `/copilot/chat` | SSE `answer` + `done`, TEMPLATE mode (LLM offline), grounded in stored state with app-route citations |
| Later stage → contract's NOT_AVAILABLE / NOT_ESTIMABLE | `/learning/uplift`, `/learning/shadow`, `/learning/qualification`, `/eval/report`, POST `/creatives/score` |
| Later stage → 422 NOT_BUILT | PUT `/policy`, PUT `/objective`, POST `/models/{name}/promote` and `rollback`, POST `/workspaces`, `/ingest/upload`, `/ingest/mapping/confirm`, `/copilot/sql` |

**Decision ids** use only `[a-zA-Z0-9_-]`, as the archive contract requires (`opt-<hash>-R`, `-S<k>`, `-mod-<hash>-M`).

**Verified**:
- The real responses of all 23 screen endpoints pass the frontend's own zod schemas.
- The live sweep (`web/tests/live-api/pages.spec.ts`) loads all 11 app pages from the real backend with no alert,
  no contract mismatch and no fixture data.

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

**Outcome calibration note**: each outcome carries `calibration_note`, the reason in words for why the optimism
correction factor did or did not move (applied once; a safety or operational outcome; an INCONCLUSIVE verdict;
contaminated by a safety action; a non-positive or immaterial forecast). The outcome panel shows it.

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

## Stage 2 endpoints (wired to the Stage 2 engine; `integration/test_api_stage2.py`)
| Endpoint | Engine | Failure states |
|---|---|---|
| `GET /objective`, `PUT /objective {workspace_id, revision, objective, reason}` | `decide.alternatives.selected_objective / set_objective` (PROFIT, GROWTH, INVENTORY_CLEARANCE) | 403 not admin; 409 stale revision; 422 an objective that is not built |
| `/optimizer/context`, `/optimizer/run`, `/optimizer/whatif`, `/decisions/{id}/modify` | run under the workspace objective; what-if values GROWTH as Δnet revenue and CLEARANCE with the optimizer's own objective | a request for another objective is 409 (run, modify) or NOT_ESTIMABLE (what-if), never silently swapped |
| `POST /decisions/{id}/simulate` → `alternatives[]` | the decision's Conservative / Aggressive sensitivity scenarios with their policy result | empty for non-PROFIT objectives |
| `POST /decisions/{id}/alternatives/{name}/choose {decision_hash}` | `decide.alternatives.choose_alternative`: a new decision (own snapshot + hash) superseding the pending one | 409 stale hash or not PENDING_APPROVAL; 404 unknown scenario |
| `GET /decisions/{id}/narrative`, `GET /anomalies/{id}/narrative` | `agent.narrator`: the guarded narrative the pipeline's `narrate` step stored (generated once on demand for older items) | template + `fallback_reason` when the LLM is offline or the guard rejects it twice |
| `GET /overview` → `brief` | `agent.brief.daily_brief` from the same run | deterministic Stage 1 brief when none is stored |
| `GET /anomalies[/{id}]` → `causal` | `intel.causal_estimates` (gated synthetic control) | `NOT_ESTIMABLE` with the failed gate as the reason |
| `GET /platforms/health` | `execute.adapters.platform_health` + `execute.mirror.divergent` | a failing live check is `ok: false` with the reason; `sim_out_of_sync` lists unmirrored verified live legs |
| `POST /sim/advance` | `execute.mirror.advance_world` (the only advance path; one mirror retry first) | 409 `SIM OUT OF SYNC` while a leg is MIRROR_PENDING / MIRROR_FAILED (T41) |
| `POST /executions/{id}/reconcile?target=sim` | `execute.mirror.resolve_manually` after a fresh live read-back | 409 in mock mode or without a MIRROR_FAILED leg |
| `POST /models/demand/rollback {version, registry_revision, artifact_hash, reason}` | `learn.governance.rollback`; the next forecast loads the restored champion | 409 stale champion or registry revision; 422 for families without a rollback target (response curves) |
| `POST /models/{name}/promote` | none | 422 always: promotion follows the layered rule (§10.2) and is never manual |
| `GET /eval/report`, `GET /learning/uplift` | `evidence/eval.json` from `scripts/run_eval.py` (`ADAPT_EVAL_REPORT_PATH` overrides) | NOT_AVAILABLE until the report exists |
| `GET /sim/scenarios`, `POST /sim/scenario/{key}` | every world scenario: S1–S8, S10–S12, DEMO_01 | S9 (a ranking property, not an injected scenario) is NOT_BUILT |

TikTok and Amazon legs, executions and anomalies are served as `TikTok` / `Amazon`. `inventory_risk_after.kind` is
whatever the stage predicate produced (`PROJECTED_SHORTFALL` units, or `STOCKOUT_PROBABILITY` in [0, 1]).
The runtime builds the Google adapter with the workspace and the `.env` credentials, so
`ADAPT_GOOGLE_EXECUTION_MODE=live` executes through the live adapter (never a mock fallback).

