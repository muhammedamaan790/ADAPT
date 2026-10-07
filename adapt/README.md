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
$env:PYTHONPATH="world"; uv run python -m world.seed --seed 42 --overwrite
```
Writes `data/world/seed42/sim_truth.duckdb` (hidden truth, read-only afterwards) and `sim_state.duckdb` (the world after its history, clock at day 0). See `docs/contracts/world_truth.md` and `world_step.md`.

## Run the world service (simulated outside world + mock Google/Meta APIs)
```powershell
uv run uvicorn world.main:app --app-dir world --port 8100
```
State lives in `data/world/sim_state.duckdb`. Control routes: `POST /control/reset|advance|fault|budget` (each needs an `X-Request-ID` header; a repeated ID is applied once), `GET /control/log`. Mock platforms: `/google/v25/customers/{cid}/campaignBudgets:mutate`, `/google/v25/customers/{cid}/googleAds:search`, `/meta/v25.0/{campaign_id}`.

## Run the API
```powershell
uv run uvicorn adapt.api.main:app --app-dir backend --port 8000
```
Run exactly **one** process: the workspace DuckDB file allows a single writer, and a second process fails to open it.

- Health: `GET http://127.0.0.1:8000/api/v1/health`
- OpenAPI contract for the frontend: `GET http://127.0.0.1:8000/openapi.json` (interactive docs at `/docs`)
