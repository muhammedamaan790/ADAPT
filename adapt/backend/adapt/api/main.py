"""FastAPI application factory for adapt-api.

Run (single process only; the workspace DuckDB file enforces one writer):
    uv run uvicorn adapt.api.main:app --app-dir backend --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from adapt import __version__
from adapt.api.routers import data, loop
from adapt.api.runtime import Runtime
from adapt.api.schemas import HealthResponse
from adapt.config.settings import Settings, get_settings
from adapt.core.db import Database


def create_app(settings: Settings | None = None, db: Database | None = None, world_client=None,
               sync_jobs: bool = False) -> FastAPI:
    """sync_jobs runs pipeline jobs inside the request (tests); the server runs them in the background."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.runtime = Runtime(settings, db or Database(settings.workspace_db_path), world_client)
        app.state.db = app.state.runtime.db
        app.state.sync_jobs = sync_jobs
        try:
            yield
        finally:
            app.state.runtime.wait(5)
            app.state.runtime.db.close()

    app = FastAPI(title="ADAPT API", version=__version__, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.web_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT"],
                       allow_headers=["Content-Type", "Accept", "X-Request-ID", "Idempotency-Key"])

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health(request: Request) -> HealthResponse:
        s: Settings = request.app.state.settings
        try:
            request.app.state.runtime.db.query("SELECT 1")
            db_status = "ok"
        except Exception:
            db_status = "error"
        return HealthResponse(
            status="ok" if db_status == "ok" else "degraded",
            version=__version__,
            workspace=s.workspace,
            database=db_status,
            execution_modes={"google": s.google_execution_mode, "meta": s.meta_execution_mode},
            llm_mode="groq" if s.groq_api_key else "offline",
        )

    app.include_router(data.router)
    app.include_router(loop.router)
    return app


app = create_app()
