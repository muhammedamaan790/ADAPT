"""FastAPI application factory for adapt-api.

Run (single process only; the workspace DuckDB file enforces one writer):
    uv run uvicorn adapt.api.main:app --app-dir backend --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from adapt import __version__
from adapt.api.schemas import HealthResponse
from adapt.config.settings import Settings, get_settings
from adapt.core.db import Database


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.db = db or Database(settings.workspace_db_path)
        try:
            yield
        finally:
            app.state.db.close()

    app = FastAPI(title="ADAPT API", version=__version__, lifespan=lifespan)

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health(request: Request) -> HealthResponse:
        s: Settings = request.app.state.settings
        try:
            request.app.state.db.query("SELECT 1")
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

    return app


app = create_app()
