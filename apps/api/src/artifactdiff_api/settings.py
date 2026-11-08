"""API settings, read from ``ARTIFACTDIFF_*`` environment variables (see .env.example)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "artifactdiff"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARTIFACTDIFF_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=default_data_dir, description="Uploads, assets and the SQLite DB.")
    database_url: str | None = Field(None, description="SQLAlchemy URL; default: SQLite in data_dir.")
    max_upload_mb: float = Field(512, gt=0, description="Per-file upload limit.")
    input_retention_hours: float = Field(24, ge=0, description="Uploaded files are deleted after this.")
    result_retention_days: float = Field(0, ge=0, description="Delete comparisons older than this (0 = keep).")
    timeout_seconds: float = Field(300, gt=0)
    max_changes: int = Field(10_000, gt=0)
    isolation: Literal["process", "inline"] = Field("process", description="Run comparisons in worker processes.")
    max_concurrent_jobs: int = Field(2, ge=1, le=64)
    memory_limit_mb: int | None = Field(None, gt=0, description="Per-worker address-space limit (POSIX).")
    rate_limit_per_minute: int = Field(30, ge=0, description="POST /compare per client per minute (0 = off).")
    api_token: str | None = Field(None, description="If set, every API call needs 'Authorization: Bearer <token>'.")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    web_dir: Path | None = Field(None, description="Built web UI to serve (default: bundled copy if present).")
    sweep_interval_seconds: float = Field(600, gt=0)

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'artifactdiff.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
