"""FastAPI application factory for adapt-api.

Run (single process only; the workspace DuckDB file enforces one writer):
    uv run uvicorn adapt.api.main:app --app-dir backend --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from adapt import __version__
from adapt.api.auth import COOKIE, CSRF_HEADER, DEMO_USER, PUBLIC, Auth, required_role
from adapt.api.routers import auth as auth_routes
from adapt.api.routers import data, insights, loop
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
        app.state.auth = (Auth(settings.data_dir, settings.session_secret, settings.seed_password,
                               settings.secure_cookie) if settings.auth_enabled else None)
        app.state.runtime = Runtime(settings, db or Database(settings.workspace_db_path), world_client)
        app.state.db = app.state.runtime.db
        app.state.sync_jobs = sync_jobs
        try:
            yield
        finally:
            app.state.runtime.wait(5)
            app.state.runtime.db.close()

    app = FastAPI(title="ADAPT API", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        """Session, CSRF and role checks for /api/v1 (adapt/api/auth.py). Registered before CORS so that CORS
        wraps it and 401/403 answers still carry the CORS headers."""
        method, path = request.method, request.url.path
        if method == "OPTIONS" or not path.startswith("/api/v1"):
            return await call_next(request)
        auth: Auth | None = request.app.state.auth
        if auth is None:
            request.state.user = DEMO_USER
            return await call_next(request)
        if (method, path) in PUBLIC:
            return await call_next(request)
        sess = auth.session(request.cookies.get(COOKIE))
        if sess is None:
            return JSONResponse({"detail": "AUTH_REQUIRED: sign in to continue"}, status_code=401)
        user, sid = sess
        write = method not in ("GET", "HEAD")
        if write and not auth.csrf_ok(sid, request.headers.get(CSRF_HEADER)):
            resp = JSONResponse({"detail": "CSRF_FAILED: reload the page and try again"}, status_code=403)
        elif not user.can(need := required_role(method, path)):
            resp = JSONResponse({"detail": f"FORBIDDEN: this action needs the {need} role "
                                           f"(signed in as {user.role})"}, status_code=403)
        else:
            request.state.user = user
            resp = await call_next(request)
        if write:
            auth.audit(request.headers.get("X-Request-ID"), user, method, path, resp.status_code)
        return resp

    app.add_middleware(CORSMiddleware, allow_origins=settings.web_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT"],
                       allow_headers=["Content-Type", "Accept", "X-Request-ID", "Idempotency-Key", CSRF_HEADER])

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

    app.include_router(auth_routes.router)
    app.include_router(data.router)
    app.include_router(loop.router)
    app.include_router(insights.router)
    return app


app = create_app()
