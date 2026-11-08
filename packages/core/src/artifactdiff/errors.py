"""Exception hierarchy. Plugins raise these; the engine turns them into result statuses."""

from __future__ import annotations


class ArtifactDiffError(Exception):
    """Base class for all ArtifactDiff errors."""


class InputFileError(ArtifactDiffError):
    """An input path is missing, not a regular file, or unreadable."""


class FileTooLargeError(ArtifactDiffError):
    """An input exceeds the configured size limit."""


class PluginInputError(ArtifactDiffError):
    """The plugin could not parse an input (invalid/corrupted/unsupported variant)."""


class PluginUnavailableError(ArtifactDiffError):
    """The plugin's optional dependencies are not installed."""


class PluginNotFoundError(ArtifactDiffError):
    """No plugin with the requested name, or no plugin supports the inputs."""


class PluginValidationError(ArtifactDiffError):
    """A plugin class does not satisfy the plugin contract (metadata / API version)."""


class InvalidOptionsError(ArtifactDiffError):
    """Comparison options failed validation against the plugin's options model."""


class ComparisonCancelled(ArtifactDiffError):
    """The comparison was cancelled by the caller."""


class ComparisonTimeout(ArtifactDiffError):
    """The comparison exceeded its time limit."""


class ResourceLimitError(ArtifactDiffError):
    """A configured resource limit (pixels, rows, tensor size, ...) was exceeded."""


class ConfigError(ArtifactDiffError):
    """The configuration file is invalid."""
