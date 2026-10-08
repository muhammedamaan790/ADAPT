# ADAPT

Decision workspace frontend for the user-supplied **ADAPT v2.4.3** plan, on branch `frontend`. Includes the Stage 1 screens and the next frontend workbenches; backend integration remains pending.

The application lives under `adapt/`: frontend in `adapt/web`, backend in `adapt/backend`, world service in `adapt/world`. Backend setup and run commands are in [adapt/README.md](adapt/README.md).

## Run

Requires Node 22.12+ (Node 24 also works). The app uses React + TypeScript + Vite, with self-hosted fonts.

```powershell
cd adapt/web
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. Routes: `/`, `/decisions`, `/inventory`, `/anomalies`, `/optimizer`, `/executions`, `/connection`, `/opportunities`, `/outcomes`, `/learning`, `/data`. Copilot opens from the top bar; mobile navigation exposes the new pages under **More pages**.

For this workstation, if Node is not on PATH, use the workspace's portable runtime before the commands above:

```powershell
$env:Path = 'C:\DataQuest\.runtime\node-v22.16.0-win-x64;' + $env:Path
```

## What works now

- Command Center: financial metrics with formula/source lineage, financial-impact attention queue, actual/baseline chart, source health and loop status.
- Decision Center: a three-part brief (what happened, what ADAPT recommends, expected result) with the approval bar, then tabs for budget changes, evidence, alternatives, execution and policy checks. Covers accounting decomposition, diagnostic evidence, campaign allocation, unallocated cash/reserve, rejected alternatives with rule IDs, policy checks, hash-bound approval dialog, rejection reason, execution legs and outcome feedback.
- Inventory: one row per SKU with available stock, sell rate (7-day vs 28-day), days of cover, inbound and attributed ad spend, plus the agent's rule-based recommendation (restock, hold ad spend, expedite, clear excess, scale, watch) and an alert feed. API: `GET /api/v1/data/inventory` from `marts.sku_daily`.
- Anomalies: searchable investigation queue, channel/status filters, source gates, causal estimability, linked evidence, acknowledgement and reasoned resolution. Expected budget movements stay outside efficiency incident counts.
- Optimizer: editable budgets with input bounds, recorded valuation inspection, explicit unavailable forecasts for arbitrary fixture edits, and separate superseding drafts. Six objective policies are named; only backend-supported objectives can be selected. Valuations and revisions must match the selected objective and proposal identity. Drafts cannot execute.
- Execution & Ledger: per-leg request/read-back/mirror states, unknown-result verification, confirmed-failure retry, prior-settings restoration, verified reconciliation and an action ledger. Recovery fault examples are fixture-only. **Policy & readiness** separates simulation/real qualification evidence, shows Google test-account limits and supports reviewed backend mode requests. Fixture policy is read-only and has no qualification evidence.
- Observe log: inspect/filter supplied unexecuted forecasts with proposal hashes and account context. No measured uplift or simulator truth is fabricated; forecast records never count toward real readiness.
- Backend Connection: 27 real read-only health/contract probes, including in fixture mode, with missing/auth/offline/contract-error states. It does not verify mutation safety or model correctness.
- Opportunity Map: ranked examples, feasibility evidence links, response point chart/table, creative fatigue and a scorer that leaves missing model output unavailable.
- Outcomes and Learning: class/verdict filters, forecast vs measured records, JSON export, once-applied calibration history, feedback eligibility and backend evaluation/model-registry views. No held-out results are fabricated.
- Data Hub: source health, mapping coverage, attribution reconciliation, formula lineage and CSV templates/upload/mapping/schema validation. Fixture staging stores metadata only; API ingest uses a proposed two-phase JSON contract.
- Copilot: read-only, cited fixture templates; API mode validates completed SSE answers, supports cancellation and rejects unsupported evidence URLs. SQL inspection renders bounded, query-matched backend results and explicit refusals; it has no fixture executor. This is not a connected LLM in fixture mode.
- Decision tools: recorded strategy comparisons, conservative/aggressive revision proposals, captured event timeline, snapshot export and explicit unavailable archived replay. Custom fixture revisions still require backend valuation.
- Responsive desktop/mobile layout, dark/light theme, keyboard navigation, protected confirmation dialogs, loading/error/empty states, persisted fixture interactions.
- Workspace management: create and switch isolated local demos, preserve separate decision/clock/calibration state, block switches during unresolved execution, and reload after a backend context acknowledgement in API mode.
- Model controls: inspect backend artifact identities, baseline/champion metrics and gates; request an allowed promotion or recorded rollback with review/revision checks. Missing artifacts stay unavailable.
- Head-to-Head: inspect/export precomputed backend or uploaded JSON reports, validate complete paired strategy/seed rows, filter seeds, and keep oracle results benchmark-only. No benchmark is generated in the browser.
- Replay archive: inspect supplied manifest identities, artifact links and processing steps independently of replay verification; missing engine artifacts remain explicit.
- Workspace settings: inspect/version-review the backend default objective and policy audit history under Execution & Ledger. Changes require supported capabilities, permission and a reason; the reviewed workspace/revision is bound to the request. Fixture settings remain read-only.
- Confidence-region evidence: inspect separate real/simulated, warmup/held-out counts and supplied 95% Wilson intervals. Empty samples stay unavailable, and bands do not authorize execution.
- Detailed Data Hub checks: freshness/completeness/consistency, hard failures and individual source checks; per-platform conversion/order and session/click ratios, reconciliation window and null-denominator reasons.
- Resilience: route view recovery, unknown-route navigation, consistent Asia/Kolkata timestamps and truthful missing-comparison labels.

**Default mode is `fixture`.** All bundled values are illustrative frontend examples. They are not seed-42 world output, detection/optimizer calculations, real read-back verification or evaluation evidence. The app sends no requests to ad platforms in this mode. Fixture scenarios validate UI states, not analytical correctness. Do not present them to judges as the integrated engine.

## Connect the backend

Copy `adapt/web/.env.example` to `adapt/web/.env.local`, set `VITE_DATA_MODE=api`, and restart Vite. The development proxy forwards `/api` to `http://127.0.0.1:8000`. Set `VITE_API_BASE_URL` for another deployment and configure its CORS/cookie policies. Keep all secrets on the backend; Vite variables are public.

The frontend **never switches to fixtures when API mode fails**. It validates responses with Zod and shows contract, network, permission and conflict errors. Authentication is owned by the backend team; the client includes session cookies, but this frontend does not implement a login/security system.

The backend currently exposes `/api/v1/health` and Data Hub source/health/mapping/reconciliation reads. Detection, diagnosis, evidence, response curves, portfolio economics, PROFIT optimization, outcome/calibration, pipeline cycles, decision snapshots/replay, policy and execution-saga modules are now present through backend commit `efcd2fb`. Decision-loop, management, policy and scenario-catalog HTTP endpoints remain pending. The Data Hub response shapes match the adapter by source inspection; live integration still needs validation against built canonical data.

Frontend schemas remain provisional, hand-authored in `adapt/web/src/api/contracts.ts`, `workbench-contracts.ts`, `insight-contracts.ts`, `management-contracts.ts`, `policy-contracts.ts` and `completion-contracts.ts`. Integration handoffs: [core APIs](docs/frontend-api.md), [insights](docs/frontend-insights-api.md), [management](docs/frontend-management-api.md), [objectives, policy and scenario capabilities](docs/frontend-policy-api.md), and [completion reads/settings/SQL](docs/frontend-completion-api.md). `npm run types:generate` generates OpenAPI types once FastAPI is running; align the adapter/schema rather than casting unvalidated JSON.

## Verification

```powershell
cd adapt/web
npm run build
npm test
npx playwright install chromium
npm run test:e2e
npm run test:e2e:api
```

These are frontend contract and interaction tests. Backend/world ground-truth and `test_e2e_stage1.py` remain the backend team's responsibility. Live ad APIs, fitted response/sensitivity models, autonomous modes, causal estimation, held-out evaluation execution, archived replay execution and LLM/SQL tools remain backend work. Workspace, model, report and archive interfaces now exist against provisional contracts; their backend behavior remains pending. These controls do not establish that the Stage 3 backend gate has passed.

On a machine with Chrome already installed, `CHROMIUM_EXECUTABLE_PATH` can point to its executable instead of downloading a test browser. The acceptance suites cover the original journeys plus CSV, insights, Copilot, management, objective selection and policy/scenario capabilities. Earlier records: [management verification](docs/frontend-management-verification.md) and [policy verification](docs/frontend-policy-verification.md). The latest record is [completion verification](docs/frontend-completion-verification.md). CI runs the production build and suites. Browser API tests use intercepted responses; they do not prove live backend integration.

## Team boundaries

Coverage and backend dependencies are listed in [the completion matrix](docs/frontend-completion-matrix.md). New wire shapes and existing Data Hub extensions are in [the completion API handoff](docs/frontend-completion-api.md). Production builds use `npm run build`; `npm run preview` serves the output locally for verification. A production host must provide SPA route fallback and an actual backend URL/API proxy; Vite's development proxy does not ship in `dist`.

Frontend owns `adapt/web/`, `docs/frontend-api.md`, `PRODUCT.md` and `DESIGN.md`. Backend/world/evaluation packages live in `adapt/backend/`, `adapt/world/` and `adapt/evalharness/`. This frontend change preserves the existing backend foundation and its CI workflow.
