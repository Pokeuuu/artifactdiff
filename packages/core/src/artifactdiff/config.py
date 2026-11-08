"""Configuration loaded from ``artifactdiff.toml`` or ``[tool.artifactdiff]`` in pyproject.toml."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from artifactdiff.errors import ConfigError
from artifactdiff.models import ChangeSeverity

CONFIG_FILENAME = "artifactdiff.toml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(_Strict):
    max_file_size_mb: float = Field(2048, gt=0, description="Inputs above this size are rejected.")
    timeout_seconds: float = Field(300, gt=0, description="Per-comparison time limit.")
    max_changes: int = Field(10_000, gt=0, description="Changes kept in a result (all are counted).")
    max_asset_mb: float = Field(16, ge=0, description="Budget for generated visualizations per result.")
    memory_limit_mb: int | None = Field(
        None, gt=0, description="Address-space limit for isolated workers (POSIX only)."
    )

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)

    @property
    def max_asset_bytes(self) -> int:
        return int(self.max_asset_mb * 1024 * 1024)


class PluginsConfig(_Strict):
    enabled: list[str] = Field(
        default_factory=list,
        description="Third-party entry-point plugins allowed to load. Nothing loads implicitly.",
    )
    modules: list[str] = Field(
        default_factory=list, description="Extra plugin classes to load, as 'package.module:Class'."
    )
    options: dict[str, dict[str, Any]] = Field(default_factory=dict, description="Default options per plugin name.")


class ArtifactDiffConfig(_Strict):
    limits: Limits = Field(default_factory=Limits)
    exclude: list[str] = Field(default_factory=list, description="Glob patterns to skip.")
    fail_on: ChangeSeverity | None = Field(
        None, description="Minimum severity that makes `diff` exit with 1 (default: any change)."
    )
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    source: str | None = Field(None, exclude=True)


def find_config_file(start: Path | None = None) -> Path | None:
    env = os.environ.get("ARTIFACTDIFF_CONFIG")
    if env:
        return Path(env)
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, OSError):
                continue
            if "artifactdiff" in data.get("tool", {}):
                return pyproject
        if (directory / ".git").exists():
            break
    return None


def load_config(path: Path | None = None, *, search: bool = True) -> ArtifactDiffConfig:
    file = path if path is not None else (find_config_file() if search else None)
    if file is None:
        return ArtifactDiffConfig()
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {file}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {file}: {exc}") from exc
    if file.name == "pyproject.toml":
        data = data.get("tool", {}).get("artifactdiff", {})
    try:
        cfg = ArtifactDiffConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {file}:\n{exc}") from exc
    cfg.source = str(file)
    return cfg


DEFAULT_CONFIG_TOML = """\
# ArtifactDiff configuration. All keys are optional; values shown are the defaults.
# Docs: docs/cli.md#configuration

# Glob patterns (matched against relative paths) that are never compared.
exclude = []

# Minimum severity that makes `artifactdiff diff` exit with code 1.
# One of "info", "low", "medium", "high". Unset = any change fails.
# fail_on = "medium"

[limits]
max_file_size_mb = 2048   # inputs above this are rejected
timeout_seconds = 300     # per comparison
max_changes = 10000       # changes kept in a result (all are still counted)
max_asset_mb = 16         # budget for generated visualizations per result
# memory_limit_mb = 4096  # POSIX only, applies to isolated workers (--isolate)

[plugins]
# Third-party plugins are NEVER loaded automatically. List entry-point names to allow:
enabled = []
# Or load plugin classes directly:
modules = []

# Default options per plugin (see `artifactdiff plugins info <name>`):
# [plugins.options.csv]
# primary_key = ["id"]
#
# [plugins.options.image]
# tolerance = 8
"""
