"""Pydantic response schemas. These generate the OpenAPI contract the frontend builds against."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    workspace: str
    database: Literal["ok", "error"]
    execution_modes: dict[str, Literal["mock", "live"]]
    llm_mode: Literal["groq", "offline"]
