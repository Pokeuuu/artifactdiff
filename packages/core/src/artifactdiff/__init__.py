"""ArtifactDiff core: the universal diff engine for artifacts Git cannot meaningfully compare."""

from __future__ import annotations

__version__ = "0.1.0"

from artifactdiff.config import ArtifactDiffConfig, load_config
from artifactdiff.engine import Engine, compare_files
from artifactdiff.files import ArtifactFile
from artifactdiff.models import (
    ArtifactFormat,
    ArtifactMetadata,
    Change,
    ChangeCategory,
    ChangeKind,
    ChangeSeverity,
    ComparisonResult,
    ComparisonStatistics,
    ComparisonStatus,
    ComparisonWarning,
    PluginMetadata,
    Report,
)
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, PluginOptions

__all__ = [
    "ArtifactDiffConfig",
    "ArtifactFile",
    "ArtifactFormat",
    "ArtifactMetadata",
    "ArtifactPlugin",
    "Change",
    "ChangeCategory",
    "ChangeKind",
    "ChangeSeverity",
    "ComparisonContext",
    "ComparisonResult",
    "ComparisonStatistics",
    "ComparisonStatus",
    "ComparisonWarning",
    "Engine",
    "PluginMetadata",
    "PluginOptions",
    "Report",
    "__version__",
    "compare_files",
    "load_config",
]
