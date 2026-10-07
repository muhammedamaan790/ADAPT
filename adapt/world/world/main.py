"""world-service public listener (spec §2). Serves only what real platforms would, plus /control/*.

Ground truth is never routed here; it gets a separate loopback, token-gated listener in eval mode only.
Run a seeded world (after `python -m world.seed --seed 42`):
  $env:WORLD_DIR="data/world/seed42"; uv run uvicorn world.main:app --app-dir world --port 8100
Without WORLD_DIR the service runs a bare control-plane store (budgets, faults, clock; no reporting).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from world.mutations import google, meta
from world.reporting import ga4_erp
from world.reporting import google as google_reporting
from world.reporting import meta as meta_reporting
from world.reporting import store as store_reporting
from world.seed import BASELINE_NAME
from world.state import CommitResult, WorldStateConflict, WorldStore
from world.step import make_store
from world.truth import load_truth

DEFAULT_STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "world" / "sim_state.duckdb"


class ResetRequest(BaseModel):
    seed: int = Field(ge=0)


class AdvanceRequest(BaseModel):
    days: int = Field(default=1, ge=1, le=365)


class FaultRequest(BaseModel):
    platform: Literal["google", "meta"]
    fault: Literal["rate_limit", "unavailable", "bad_request", "timeout_after_success"]
    count: int = Field(default=1, ge=1, le=100)


class BudgetRequest(BaseModel):
    platform: Literal["google", "meta"]
    budget_id: str = Field(min_length=1)
    amount: float = Field(ge=0)
    status: Literal["ENABLED", "PAUSED"] = "ENABLED"


class ClockResponse(BaseModel):
    seeded: bool
    seed: int | None = None
    day: int | None = None


class CommitResponse(BaseModel):
    seq: int
    replayed: bool
    result: dict


def _commit_response(c: CommitResult) -> CommitResponse:
    return CommitResponse(seq=c.seq, replayed=c.replayed, result=c.result)


def create_app(state_path: str | Path | None = None, world_dir: str | Path | None = None) -> FastAPI:
    wdir = Path(world_dir) if world_dir else (Path(os.environ["WORLD_DIR"]) if os.environ.get("WORLD_DIR") else None)
    path = Path(state_path or os.environ.get("WORLD_STATE_PATH", DEFAULT_STATE_PATH))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if wdir is not None:
            if not (wdir / "sim_truth.duckdb").exists():
                raise RuntimeError(f"{wdir} is not a seeded world (run: python -m world.seed --seed N)")
            truth = load_truth(wdir / "sim_truth.duckdb")
            app.state.store = make_store(wdir / "sim_state.duckdb", truth)
            app.state.baseline = wdir / BASELINE_NAME
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            app.state.store = WorldStore(path)
            app.state.baseline = None
        try:
            yield
        finally:
            app.state.store.close()

    app = FastAPI(title="ADAPT world service", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(WorldStateConflict)
    async def _conflict(_: Request, exc: WorldStateConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _invalid(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    def store(request: Request) -> WorldStore:
        return request.app.state.store

    @app.get("/health", response_model=ClockResponse)
    def health(request: Request) -> ClockResponse:
        clock = store(request).clock()
        if clock is None:
            return ClockResponse(seeded=False)
        return ClockResponse(seeded=True, seed=clock[0], day=clock[1])

    @app.post("/control/reset", response_model=CommitResponse)
    def reset(
        body: ResetRequest,
        request: Request,
        x_request_id: str = Header(...),
        x_actor_id: str = Header(default="scenario-lab"),
    ) -> CommitResponse:
        """Seeded world: restore the post-history baseline (fast). Bare store: clear state and set the clock."""
        s = store(request)
        if s.ctx.truth is None:
            return _commit_response(s.commit("reset", x_request_id, x_actor_id, body.model_dump()))
        seeded = s.ctx.truth.config.seed
        if body.seed != seeded:
            raise WorldStateConflict(f"this world service runs seed {seeded}; seed {body.seed} needs "
                                     f"`python -m world.seed --seed {body.seed}` and WORLD_DIR pointing at it")
        baseline = request.app.state.baseline
        if baseline is None or not Path(baseline).exists():
            raise WorldStateConflict("no baseline snapshot for this world; reseed it")
        s.restore_from(baseline)
        clock = s.clock()
        last = s.log()[-1]["seq"]
        return CommitResponse(seq=last, replayed=False, result={"seed": clock[0], "day": clock[1],
                                                                "restored": "baseline"})

    @app.post("/control/advance", response_model=CommitResponse)
    def advance(
        body: AdvanceRequest,
        request: Request,
        x_request_id: str = Header(...),
        x_actor_id: str = Header(default="scenario-lab"),
    ) -> CommitResponse:
        return _commit_response(store(request).commit("advance", x_request_id, x_actor_id, body.model_dump()))

    @app.post("/control/fault", response_model=CommitResponse)
    def fault(
        body: FaultRequest,
        request: Request,
        x_request_id: str = Header(...),
        x_actor_id: str = Header(default="scenario-lab"),
    ) -> CommitResponse:
        """Arm a fault on a mock platform's next `count` mutations (Scenario Lab / failure tests)."""
        return _commit_response(store(request).commit("set_fault", x_request_id, x_actor_id, body.model_dump()))

    @app.post("/control/budget", response_model=CommitResponse)
    def control_budget(
        body: BudgetRequest,
        request: Request,
        x_request_id: str = Header(...),
        x_actor_id: str = Header(default="scenario-lab"),
    ) -> CommitResponse:
        """Seed a budget or apply a scripted external 'human' edit (S7, T13/T14/T26). Not an ad-platform route."""
        return _commit_response(store(request).commit("set_budget", x_request_id, x_actor_id, body.model_dump()))

    app.include_router(google.router)
    app.include_router(meta.router)
    app.include_router(google_reporting.router)
    app.include_router(meta_reporting.router)
    app.include_router(store_reporting.router)
    app.include_router(ga4_erp.router)

    @app.get("/control/log")
    def log(request: Request) -> list[dict]:
        return store(request).log()

    return app


app = create_app()  # the DB file is opened in the lifespan, not at import
