# ADAPT

Stage 1 frontend for the user-supplied **ADAPT v2.4.3** plan, on branch `frontend`.

## Run

Requires Node 22.12+ (Node 24 also works). The app uses React + TypeScript + Vite, with self-hosted fonts.

```powershell
cd web
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. The three routes are `/`, `/decisions`, and `/scenarios`.

For this workstation, if Node is not on PATH, use the workspace's portable runtime before the commands above:

```powershell
$env:Path = 'C:\DataQuest\.runtime\node-v22.16.0-win-x64;' + $env:Path
```

## What works now

- Command Center: financial metrics with formula/source lineage, financial-impact attention queue, actual/baseline chart, source health and loop status.
- Decision Center: accounting decomposition, diagnostic evidence, campaign allocation, unallocated cash/reserve, rejected alternatives with rule IDs, policy checks, hash-bound approval dialog, rejection reason, execution legs and outcome feedback.
- Scenario Lab: Stage 1 examples S1–S5, S7 and DEMO_01; clock advance, reset, event feed and all four outcome verdict counts.
- Responsive desktop/mobile layout, dark/light theme, keyboard navigation, protected confirmation dialogs, loading/error/empty states, persisted fixture interactions.

**Default mode is `fixture`.** All bundled values are illustrative frontend examples. They are not seed-42 world output, detection/optimizer calculations, real read-back verification or evaluation evidence. The app sends no requests to ad platforms in this mode. Fixture scenarios validate UI states, not analytical correctness. Do not present them to judges as the integrated engine.

## Connect the backend

Copy `web/.env.example` to `web/.env.local`, set `VITE_DATA_MODE=api`, and restart Vite. The development proxy forwards `/api` to `http://127.0.0.1:8000`. Set `VITE_API_BASE_URL` for another deployment and configure its CORS/cookie policies. Keep all secrets on the backend; Vite variables are public.

The frontend **never switches to fixtures when API mode fails**. It validates responses with Zod and shows contract, network, permission and conflict errors. Authentication is owned by the backend team; the client includes session cookies, but this frontend does not implement a login/security system.

The empty repository did not contain C6's OpenAPI, so schemas are provisional, hand-authored in `web/src/api/contracts.ts`. The exact integration handoff, including the few unresolved endpoint contracts, is in [docs/frontend-api.md](docs/frontend-api.md). `npm run types:generate` generates OpenAPI types once FastAPI is running; align the adapter/schema rather than casting unvalidated JSON.

## Verification

```powershell
cd web
npm run build
npm test
npx playwright install chromium
npm run test:e2e
npm run test:e2e:api
```

These are frontend contract and interaction tests. Backend/world ground-truth and `test_e2e_stage1.py` remain the backend team's responsibility. Stage 2 features (live Google, sensitivity, autonomous modes, causal estimation, benchmark/evaluation panels, Copilot) are deliberately absent.

On a machine with Chrome already installed, `CHROMIUM_EXECUTABLE_PATH` can point to its executable instead of downloading a test browser. The creation-time local checks passed with Chrome: production build, 13 unit/contract checks, four fixture browser journeys and three API error browser checks. npm audit reported zero known vulnerabilities after the Vitest upgrade. CI is configured but has not run on GitHub yet.

## Team boundaries

Frontend owns `web/`, `docs/frontend-api.md`, `PRODUCT.md` and `DESIGN.md`. Backend and world teams can create `backend/`, `world/` and `evalharness/` independently. No backend implementation, deployment, commit or push is implied by this scaffold.
