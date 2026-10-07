"""Runtime settings, loaded from environment variables (prefix ADAPT_) and an optional .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADAPT_", env_file=REPO_ROOT / ".env", extra="ignore")

    env: Literal["dev", "test", "demo"] = "dev"
    data_dir: Path = REPO_ROOT / "data"
    workspace: str = "demo"
    world_url: str = "http://127.0.0.1:8100"

    brand_timezone: str = "Asia/Kolkata"
    currency: str = "INR"

    # Execution mode is fixed at startup per platform; there is never an automatic fallback (principle 11).
    google_execution_mode: Literal["mock", "live"] = "mock"
    meta_execution_mode: Literal["mock"] = "mock"

    groq_api_key: str | None = Field(default=None, validation_alias="GROQ_API_KEY")

    # session login, roles and CSRF (spec §9.5; adapt/api/auth.py). Off only for in-process tests and fixture work.
    auth_enabled: bool = True
    session_secret: str | None = None  # random per process when unset: sessions end on restart
    seed_password: str | None = None  # first-boot password for seeded users; else random, written to data/auth
    secure_cookie: bool = False  # set true behind HTTPS

    # strict CORS: only the web app's origins (spec §9.5); the Vite dev proxy needs none
    web_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174",
                              "http://127.0.0.1:5174"]

    @property
    def workspace_db_path(self) -> Path:
        return self.data_dir / "workspaces" / f"{self.workspace}.duckdb"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts" / "sha256"


@lru_cache
def get_settings() -> Settings:
    return Settings()
