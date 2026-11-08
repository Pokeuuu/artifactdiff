"""Typed data model shared by the engine, plugins, reports, CLI and API.

Everything here is plain data (Pydantic v2 models) so results can be serialized to the
documented JSON report schema (``docs/schema/report.schema.json``) and loaded back.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Version of the JSON result/report schema. Bump the major on breaking changes.
SCHEMA_VERSION = "1.0"

JSONValue = Any  # JSON-serializable value (old/new values, evidence details, ...)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False, use_enum_values=False)


class ChangeKind(StrEnum):
    """What happened to the element at ``Change.path``."""

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    TYPE_CHANGED = "type_changed"
    RENAMED = "renamed"
    MOVED = "moved"


class ChangeCategory(StrEnum):
    """Which aspect of the artifact the change concerns."""

    STRUCTURE = "structure"  # keys, tables, nodes, pages, tensors (existence / layout)
    SCHEMA = "schema"  # columns, types, nullability, indexes, triggers
    DATA = "data"  # values, rows, records, tensor values
    METADATA = "metadata"  # EXIF, PDF info, file-level properties
    VISUAL = "visual"  # rendered pixels
    GEOMETRY = "geometry"  # meshes, vertices, transforms, bounds
    MATERIAL = "material"  # 3D materials / textures
    AUDIO = "audio"  # waveform / timing / loudness
    STATISTICS = "statistics"  # descriptive statistics / distributions
    TEXT = "text"  # textual content
    BINARY = "binary"  # raw byte-level differences


class ChangeSeverity(StrEnum):
    """Heuristic impact level assigned by the plugin. Documented per plugin."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    ChangeSeverity.INFO: 0,
    ChangeSeverity.LOW: 1,
    ChangeSeverity.MEDIUM: 2,
    ChangeSeverity.HIGH: 3,
}


class ComparisonStatus(StrEnum):
    IDENTICAL = "identical"  # byte-for-byte identical inputs
    EQUIVALENT = "equivalent"  # bytes differ but the plugin found no meaningful change
    DIFFERENT = "different"  # at least one change reported
    ERROR = "error"  # comparison failed (invalid input, plugin error, timeout, ...)
    UNSUPPORTED = "unsupported"  # no plugin can compare this pair


class ArtifactFormat(_Model):
    id: str = Field(description="Stable format identifier, e.g. 'json' or 'safetensors'.")
    name: str
    extensions: list[str] = Field(default_factory=list)
    mime_types: list[str] = Field(default_factory=list)


class ArtifactMetadata(_Model):
    """Facts about one input file. ``properties`` comes from the plugin's ``inspect``."""

    name: str = Field(description="Display name (file name or repository path).")
    size_bytes: int
    sha256: str
    format: ArtifactFormat | None = None
    detected_by: Literal["signature", "mime", "extension", "content", "explicit"] | None = None
    properties: dict[str, JSONValue] = Field(default_factory=dict)


class Change(_Model):
    """A single verified difference between the old and the new artifact."""

    id: str = Field(description="Stable identifier derived from plugin, kind, category and path.")
    kind: ChangeKind
    category: ChangeCategory
    severity: ChangeSeverity
    path: str = Field(description="Structural location, e.g. '$.model.layers[2]' or 'table:users/row[id=4]'.")
    description: str
    old_value: JSONValue | None = None
    new_value: JSONValue | None = None
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Only set for inferred changes (e.g. renames). Heuristic, not a probability.",
    )
    evidence: str = Field(description="How the change was detected, so it can be verified.")
    details: dict[str, JSONValue] = Field(default_factory=dict)

    @staticmethod
    def make_id(plugin: str, kind: str, category: str, path: str) -> str:
        raw = "\x1f".join((plugin, kind, category, path)).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]


class ComparisonWarning(_Model):
    code: str = Field(description="Machine-readable code, e.g. 'pk-auto-selected'.")
    message: str
    path: str | None = None


class Asset(_Model):
    """A generated visualization (heatmap, waveform, preview...). Never the raw input file."""

    id: str
    label: str
    role: str = Field(description="e.g. 'old-preview', 'new-preview', 'heatmap', 'overlay', 'waveform'.")
    media_type: str
    width: int | None = None
    height: int | None = None
    data_base64: str | None = Field(
        default=None, description="Inline data. Stripped by the API, which serves assets separately."
    )
    url: str | None = None
    size_bytes: int = 0


class ComparisonStatistics(_Model):
    total_changes: int = 0
    reported_changes: int = Field(0, description="Changes included in the result (may be capped).")
    by_kind: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)
    max_severity: ChangeSeverity | None = None
    metrics: dict[str, JSONValue] = Field(
        default_factory=dict, description="Plugin-specific measured quantities (documented per plugin)."
    )
    duration_ms: float = 0.0


class PluginRef(_Model):
    name: str
    version: str


class PluginMetadata(_Model):
    name: str
    version: str
    display_name: str
    description: str
    api_version: str
    formats: list[ArtifactFormat]
    capabilities: list[str]
    limitations: list[str]
    requires: list[str] = Field(default_factory=list, description="Python modules needed at runtime.")
    available: bool = True
    unavailable_reason: str | None = None
    builtin: bool = True
    options_schema: dict[str, JSONValue] = Field(default_factory=dict)
    documentation_url: str | None = None


class Reproducibility(_Model):
    artifactdiff_version: str
    plugin: PluginRef | None
    python_version: str
    platform: str
    libraries: dict[str, str] = Field(default_factory=dict)
    options_sha256: str
    old_sha256: str
    new_sha256: str


class ComparisonResult(_Model):
    schema_version: str = SCHEMA_VERSION
    artifactdiff_version: str
    created_at: datetime
    status: ComparisonStatus
    plugin: PluginRef | None = None
    old: ArtifactMetadata
    new: ArtifactMetadata
    options: dict[str, JSONValue] = Field(default_factory=dict)
    summary: str = ""
    changes: list[Change] = Field(default_factory=list)
    warnings: list[ComparisonWarning] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    statistics: ComparisonStatistics = Field(default_factory=ComparisonStatistics)
    assets: list[Asset] = Field(default_factory=list)
    error: str | None = None
    reproducibility: Reproducibility | None = None
    fingerprint: str = Field(
        "", description="sha256 over inputs, plugin, options, changes and warnings; stable across runs."
    )

    def compute_fingerprint(self) -> str:
        payload = {
            "old": self.old.sha256,
            "new": self.new.sha256,
            "plugin": self.plugin.model_dump() if self.plugin else None,
            "options": self.options,
            "status": str(self.status),
            "changes": [c.model_dump(mode="json") for c in self.changes],
            "warnings": [w.model_dump(mode="json") for w in self.warnings],
            "metrics": self.statistics.metrics,
            "total": self.statistics.total_changes,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def has_differences(self) -> bool:
        return self.status == ComparisonStatus.DIFFERENT


class FileEntryStatus(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    RENAMED = "renamed"
    UNCHANGED = "unchanged"


class ReportEntry(_Model):
    """One file in a multi-file report (directory comparison / pull request)."""

    path: str
    old_path: str | None = None
    file_status: FileEntryStatus
    result: ComparisonResult | None = None
    note: str | None = None


class ReportSummary(_Model):
    files: int = 0
    compared: int = 0
    with_changes: int = 0
    errors: int = 0
    unsupported: int = 0
    added_files: int = 0
    removed_files: int = 0
    total_changes: int = 0
    max_severity: ChangeSeverity | None = None


class Report(_Model):
    """Top-level document written by ``--format json`` for directory / CI comparisons."""

    schema_version: str = SCHEMA_VERSION
    artifactdiff_version: str
    generated_at: datetime
    title: str = "ArtifactDiff report"
    context: dict[str, JSONValue] = Field(default_factory=dict)
    entries: list[ReportEntry] = Field(default_factory=list)
    summary: ReportSummary = Field(default_factory=ReportSummary)


def max_severity(severities: list[ChangeSeverity]) -> ChangeSeverity | None:
    return max(severities, key=lambda s: s.rank) if severities else None
