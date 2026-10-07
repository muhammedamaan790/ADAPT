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

Open **http://127.0.0.1:5173**. Routes: `/`, `/decisions`, `/scenarios`, `/anomalies`, `/optimizer`, `/executions`, `/connection`, `/opportunities`, `/outcomes`, `/learning`, `/data`. Copilot opens from the top bar; mobile navigation exposes the new pages under **More pages**.

For this workstation, if Node is not on PATH, use the workspace's portable runtime before the commands above:

```powershell
$env:Path = 'C:\DataQuest\.runtime\node-v22.16.0-win-x64;' + $env:Path
```

## What works now

- Command Center: financial metrics with formula/source lineage, financial-impact attention queue, actual/baseline chart, source health and loop status.
- Decision Center: accounting decomposition, diagnostic evidence, campaign allocation, unallocated cash/reserve, rejected alternatives with rule IDs, policy checks, hash-bound approval dialog, rejection reason, execution legs and outcome feedback.
- Scenario Lab: Stage 1 examples S1–S5, S7 and DEMO_01; clock advance, reset, event feed and all four outcome verdict counts.
- Anomalies: searchable investigation queue, channel/status filters, source gates, causal estimability, linked evidence, acknowledgement and reasoned resolution. Expected budget movements stay outside efficiency incident counts.
- Optimizer: editable budgets with input bounds, recorded valuation inspection, explicit unavailable forecasts for arbitrary fixture edits, and separate superseding drafts. Drafts cannot execute.
- Execution & Ledger: per-leg request/read-back/mirror states, unknown-result verification, confirmed-failure retry, prior-settings restoration, verified reconciliation and an action ledger. Recovery fault examples are fixture-only.
- Backend Connection: real read-only health/contract probes, including in fixture mode, with missing/auth/offline/contract-error states. It does not verify mutation safety or model correctness.
- Opportunity Map: ranked examples, feasibility evidence links, response point chart/table, creative fatigue and a scorer that leaves missing model output unavailable.
- Outcomes and Learning: class/verdict filters, forecast vs measured records, JSON export, once-applied calibration history, feedback eligibility and backend evaluation/model-registry views. No held-out results are fabricated.
- Data Hub: source health, mapping coverage, attribution reconciliation, formula lineage and CSV templates/upload/mapping/schema validation. Fixture staging stores metadata only; API ingest uses a proposed two-phase JSON contract.
- Copilot: read-only, cited fixture templates; API mode validates completed SSE answers, supports cancellation and rejects unsupported evidence URLs. This is not a connected LLM in fixture mode.
- Decision tools: recorded strategy comparisons, conservative/aggressive revision proposals, captured event timeline, snapshot export and explicit unavailable archived replay. Custom fixture revisions still require backend valuation.
- Responsive desktop/mobile layout, dark/light theme, keyboard navigation, protected confirmation dialogs, loading/error/empty states, persisted fixture interactions.

**Default mode is `fixture`.** All bundled values are illustrative frontend examples. They are not seed-42 world output, detection/optimizer calculations, real read-back verification or evaluation evidence. The app sends no requests to ad platforms in this mode. Fixture scenarios validate UI states, not analytical correctness. Do not present them to judges as the integrated engine.

## Connect the backend

Copy `adapt/web/.env.example` to `adapt/web/.env.local`, set `VITE_DATA_MODE=api`, and restart Vite. The development proxy forwards `/api` to `http://127.0.0.1:8000`. Set `VITE_API_BASE_URL` for another deployment and configure its CORS/cookie policies. Keep all secrets on the backend; Vite variables are public.

The frontend **never switches to fixtures when API mode fails**. It validates responses with Zod and shows contract, network, permission and conflict errors. Authentication is owned by the backend team; the client includes session cookies, but this frontend does not implement a login/security system.

The backend foundation currently exposes `/api/v1/health`; C6's decision-loop endpoints are not implemented yet. Frontend schemas remain provisional, hand-authored in `adapt/web/src/api/contracts.ts`, `workbench-contracts.ts` and `insight-contracts.ts`. The integration handoff is in [docs/frontend-api.md](docs/frontend-api.md) and [docs/frontend-insights-api.md](docs/frontend-insights-api.md). `npm run types:generate` generates OpenAPI types once FastAPI is running; align the adapter/schema rather than casting unvalidated JSON.

## Verification

```powershell
cd adapt/web
npm run build
npm test
npx playwright install chromium
npm run test:e2e
npm run test:e2e:api
```

These are frontend contract and interaction tests. Backend/world ground-truth and `test_e2e_stage1.py` remain the backend team's responsibility. Live ad APIs, fitted response/sensitivity models, autonomous modes, causal estimation, held-out evaluation execution, archived replay and LLM/SQL tools remain backend work. Workspace creation/switching, model promotion/rollback and richer head-to-head evaluation controls remain later frontend work after their contracts exist. The new pages expose read views and honest unavailable states; they do not establish the Stage 3 backend gate has passed.

On a machine with Chrome already installed, `CHROMIUM_EXECUTABLE_PATH` can point to its executable instead of downloading a test browser. The acceptance suites include the previous journeys plus CSV, insight and Copilot coverage; recorded results are in [docs/frontend-remaining-verification.md](docs/frontend-remaining-verification.md). CI runs the production build and these suites. Browser API tests use intercepted responses; they do not prove live backend integration.

## Team boundaries

Frontend owns `adapt/web/`, `docs/frontend-api.md`, `PRODUCT.md` and `DESIGN.md`. Backend/world/evaluation packages live in `adapt/backend/`, `adapt/world/` and `adapt/evalharness/`. This frontend change preserves the existing backend foundation and its CI workflow.
