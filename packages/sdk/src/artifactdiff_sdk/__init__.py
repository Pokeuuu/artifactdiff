"""ArtifactDiff plugin SDK — the stable surface for third-party plugin authors.

    from artifactdiff_sdk import ArtifactPlugin, ComparisonContext, PluginOptions, ChangeKind, ChangeCategory

    class MyPlugin(ArtifactPlugin):
        name = "my-format"
        version = "1.0.0"
        supported_extensions = (".example",)

        def inspect(self, file):
            return {"size": file.size}

        def compare(self, old, new, options, ctx):
            ...
            ctx.add_change(ChangeKind.MODIFIED, ChangeCategory.DATA, "$.title", "Title changed",
                           old="a", new="b", evidence="Compared the title fields")

Everything re-exported here follows the plugin API version in ``PLUGIN_API_VERSION``.
"""

from __future__ import annotations

from artifactdiff.errors import PluginInputError, ResourceLimitError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import (
    ArtifactFormat,
    Change,
    ChangeCategory,
    ChangeKind,
    ChangeSeverity,
    ComparisonResult,
    ComparisonStatus,
    ComparisonWarning,
    JSONValue,
    PluginMetadata,
)
from artifactdiff.plugin import (
    PLUGIN_API_VERSION,
    ArtifactPlugin,
    ComparisonContext,
    Detection,
    PluginOptions,
)

__all__ = [
    "PLUGIN_API_VERSION",
    "ArtifactFile",
    "ArtifactFormat",
    "ArtifactPlugin",
    "Change",
    "ChangeCategory",
    "ChangeKind",
    "ChangeSeverity",
    "ComparisonContext",
    "ComparisonResult",
    "ComparisonStatus",
    "ComparisonWarning",
    "Detection",
    "JSONValue",
    "PluginInputError",
    "PluginMetadata",
    "PluginOptions",
    "ResourceLimitError",
]
