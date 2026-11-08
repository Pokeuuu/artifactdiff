"""Response models (OpenAPI is generated from these)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from artifactdiff.models import ComparisonResult, PluginMetadata

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    plugins_available: int
    plugins_total: int
    database: Literal["ok", "error"]


class ServerConfig(BaseModel):
    max_upload_mb: float
    input_retention_hours: float
    result_retention_days: float
    timeout_seconds: float
    isolation: str
    auth_required: bool
    version: str


class PluginList(BaseModel):
    plugins: list[PluginMetadata]


class ComparisonSummary(BaseModel):
    id: str
    created_at: datetime
    finished_at: datetime | None
    job_status: JobStatus
    result_status: str | None
    plugin: str | None
    plugin_version: str | None
    old_name: str
    new_name: str
    old_size: int
    new_size: int
    total_changes: int
    max_severity: str | None
    summary: str | None
    error: str | None
    duration_ms: float | None
    inputs_available: bool
    progress: float | None = None
    progress_message: str | None = None


class ComparisonDetail(ComparisonSummary):
    options: dict[str, Any] = Field(default_factory=dict)
    result: ComparisonResult | None = None


class ComparisonPage(BaseModel):
    items: list[ComparisonSummary]
    total: int
    limit: int
    offset: int


class Accepted(BaseModel):
    id: str
    job_status: JobStatus
    url: str


class ActivityPoint(BaseModel):
    date: str
    comparisons: int
    changes: int


class PluginUsage(BaseModel):
    plugin: str
    comparisons: int


class RecentChange(BaseModel):
    comparison_id: str
    file: str
    path: str
    kind: str
    severity: str
    description: str


class Stats(BaseModel):
    total_comparisons: int
    completed: int
    failed: int
    files_analyzed: int
    bytes_analyzed: int
    total_changes: int
    by_result_status: dict[str, int]
    plugin_usage: list[PluginUsage]
    activity: list[ActivityPoint]
    recent_changes: list[RecentChange]
    supported_formats: int
    supported_extensions: list[str]
    storage: dict[str, int]


class ErrorBody(BaseModel):
    detail: str
