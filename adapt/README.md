# ADAPT backend

Autonomous Decision & Allocation Platform for D2C (DataQuest 3.0). Spec: ADAPT v2.4.3.

## Layout
- `backend/adapt/`: the product API and decision engine (package `adapt`)
- `world/world/`: the simulated outside world, a separate process (package `world`)
- `evalharness/evalharness/`: offline evaluation, the only code allowed to see world truth

`adapt` must never import `world` or `evalharness` (checked by `lint-imports`).

## Setup (Windows, PowerShell)
```powershell
cd adapt
uv sync                      # Python 3.12 + locked deps
uv run pytest                # tests
uv run ruff check .          # lint
$env:PYTHONPATH="backend;world;evalharness"; uv run lint-imports
```

## Build the world's data backbone (once, after the Kaggle download)
```powershell
$env:PYTHONPATH="backend"; uv run python -m adapt.ingest.profile --raw-dir data/raw --out data/profile_report.json
$env:PYTHONPATH="world"; uv run python -m world.backbone --raw-dir data/raw --out data/world/backbone
```
Produces 60 price-band SKUs in 12 categories over the last 365 days, with a checksum manifest. Design decisions are in `docs/contracts/world_backbone.md`.

## Seed a world (truth + 365 simulated history days, ~2 minutes)
```powershell
$env:PYTHONPATH="world"; uv run python -m world.seed --seed 42 --overwrite --demo
```
`--demo` schedules the golden-demo scenario DEMO_01 ten days before day 0. Other scenarios (S1–S5, S7) are activated at runtime with `POST /control/scenario` (see `docs/contracts/world_scenarios.md`).
Writes `data/world/seed42/sim_truth.duckdb` (hidden truth, read-only afterwards) and `sim_state.duckdb` (the world after its history, clock at day 0). See `docs/contracts/world_truth.md` and `world_step.md`.

## Run the world service (simulated outside world + mock Google/Meta/store/GA4/ERP APIs)
```powershell
$env:WORLD_DIR="data/world/seed42"; uv run uvicorn world.main:app --app-dir world --port 8100
```
Control routes: `POST /control/reset|advance|fault|budget` (each needs an `X-Request-ID` header; a repeated ID is applied once), `GET /control/log`. Reset restores the post-history baseline. Reporting and mutation endpoints are listed in `docs/contracts/world_reporting.md`. Without `WORLD_DIR` the service runs a bare control-plane store (no reporting).

## Sync the sources into the ADAPT workspace (A2 connectors)
With the world service running on `ADAPT_WORLD_URL` (default `http://127.0.0.1:8100`):
```powershell
$env:PYTHONPATH="backend"; uv run python -m adapt.ingest.sync
```
First run backfills 365 days (~2 minutes for seed 42), later runs re-pull a 3-day window. Rules in `docs/contracts/connectors.md`.

## Build the canonical state, marts and data health (A3)
```powershell
$env:PYTHONPATH="backend"; uv run python -m adapt.reconcile.build
```
Rebuilds `core.*`, `marts.*` and `ops.data_health` as of the world's today (12:00 logical time) in ~8 s. The API then serves `/api/v1/data/sources`, `/data/health`, `/data/mapping-coverage` and `/data/reconciliation`. Rules in `docs/contracts/reconcile.md`.

## Run the API
```powershell
uv run uvicorn adapt.api.main:app --app-dir backend --port 8000
```
Run exactly **one** process: the workspace DuckDB file allows a single writer, and a second process fails to open it.

- Health: `GET http://127.0.0.1:8000/api/v1/health`
- OpenAPI contract for the frontend: `GET http://127.0.0.1:8000/openapi.json` (interactive docs at `/docs`)

## Stage 2 (see `docs/STAGE2.md`)
- Seed a world with the simulated TikTok + Amazon channels: `python -m world.seed --seed 42 --overwrite --demo --channels tiktok,amazon_sp`
- Narratives use Groq when `GROQ_API_KEY` is set (strict JSON, guarded), deterministic templates otherwise.
- Google Ads test account: `uv run python scripts/google_oauth_token.py`, sync once, `uv run python scripts/google_ads_setup.py`, then `ADAPT_GOOGLE_EXECUTION_MODE=live`.
- Evaluation: `uv run python scripts/run_eval.py --bench`, then `uv run python scripts/run_eval.py --seeds eval --days 60 -j 8` (overnight) → `evidence/eval.json`.
- Replay a decision in its archived code + lock environment: `uv run python scripts/replay.py <decision_id>`.
- macOS only: LightGBM needs OpenMP (`brew install libomp`); without it the demand champion stays seasonal-naive.
