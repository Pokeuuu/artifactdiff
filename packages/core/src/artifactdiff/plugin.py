"""The plugin contract.

A plugin is a class deriving from :class:`ArtifactPlugin`. The engine instantiates it, asks it
whether it ``supports`` a file, ``inspect``s both inputs and calls ``compare`` with a
:class:`ComparisonContext`. Plugins *report* changes into the context instead of building
result objects themselves; the engine assembles the final :class:`ComparisonResult` (IDs,
statistics, fingerprints, reproducibility info) so every plugin produces consistent output.
"""

from __future__ import annotations

import base64
import importlib.util
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from artifactdiff.errors import ComparisonCancelled, ComparisonTimeout, PluginValidationError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import (
    ArtifactFormat,
    Asset,
    Change,
    ChangeCategory,
    ChangeKind,
    ChangeSeverity,
    ComparisonResult,
    ComparisonWarning,
    JSONValue,
    PluginMetadata,
)

#: Major version of the plugin API. Plugins declaring another major are rejected.
PLUGIN_API_VERSION = "1.0"

DetectionMethod = Literal["signature", "mime", "extension", "content"]
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")


class PluginOptions(BaseModel):
    """Base class for per-plugin options. Unknown options are rejected, not ignored."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Detection:
    method: DetectionMethod
    score: int  # signature=3, mime=2, extension=1, content=0 (weak guess)


ProgressCallback = Callable[[float, str], None]


class ComparisonContext:
    """Collects changes, warnings, metrics and assets during one comparison.

    Plugins should call :meth:`check` inside long loops so that cancellation and timeouts are
    honoured promptly.
    """

    def __init__(
        self,
        plugin_name: str,
        *,
        max_changes: int = 10_000,
        deadline: float | None = None,
        cancel_event: threading.Event | None = None,
        progress: ProgressCallback | None = None,
        max_asset_bytes: int = 16 * 1024 * 1024,
        generate_assets: bool = True,
    ) -> None:
        self.plugin_name = plugin_name
        self.max_changes = max_changes
        self.deadline = deadline
        self.cancel_event = cancel_event
        self._progress = progress
        self.max_asset_bytes = max_asset_bytes
        self.generate_assets = generate_assets
        self.changes: list[Change] = []
        self.total_changes = 0
        self.kind_counts: Counter[str] = Counter()
        self.category_counts: Counter[str] = Counter()
        self.severity_counts: Counter[str] = Counter()
        self.warnings: list[ComparisonWarning] = []
        self.limitations: list[str] = []
        self.metrics: dict[str, JSONValue] = {}
        self.assets: list[Asset] = []
        self._asset_bytes = 0
        self._id_counts: Counter[str] = Counter()

    # ------------------------------------------------------------------ changes
    def add_change(
        self,
        kind: ChangeKind,
        category: ChangeCategory,
        path: str,
        description: str,
        *,
        evidence: str,
        severity: ChangeSeverity = ChangeSeverity.LOW,
        old: JSONValue | None = None,
        new: JSONValue | None = None,
        confidence: float | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> Change | None:
        """Record a change. Returns ``None`` once ``max_changes`` is reached (still counted)."""
        self.total_changes += 1
        self.kind_counts[kind.value] += 1
        self.category_counts[category.value] += 1
        self.severity_counts[severity.value] += 1
        if len(self.changes) >= self.max_changes:
            return None
        base_id = Change.make_id(self.plugin_name, kind.value, category.value, path)
        n = self._id_counts[base_id]
        self._id_counts[base_id] += 1
        change = Change(
            id=base_id if n == 0 else f"{base_id}-{n}",
            kind=kind,
            category=category,
            severity=severity,
            path=path,
            description=description,
            old_value=old,
            new_value=new,
            confidence=confidence,
            evidence=evidence,
            details=details or {},
        )
        self.changes.append(change)
        return change

    def count_omitted(
        self, n: int, kind: ChangeKind, category: ChangeCategory, severity: ChangeSeverity = ChangeSeverity.LOW
    ) -> None:
        """Count ``n`` verified changes that are not listed individually (e.g. rows beyond a cap)."""
        if n <= 0:
            return
        self.total_changes += n
        self.kind_counts[kind.value] += n
        self.category_counts[category.value] += n
        self.severity_counts[severity.value] += n

    @property
    def truncated(self) -> bool:
        return self.total_changes > len(self.changes)

    # ----------------------------------------------------------------- warnings
    def warn(self, code: str, message: str, path: str | None = None) -> None:
        w = ComparisonWarning(code=code, message=message, path=path)
        if w not in self.warnings:
            self.warnings.append(w)

    def limitation(self, text: str) -> None:
        if text not in self.limitations:
            self.limitations.append(text)

    def metric(self, key: str, value: JSONValue) -> None:
        self.metrics[key] = value

    # ------------------------------------------------------------------- assets
    def add_asset(
        self,
        role: str,
        label: str,
        media_type: str,
        data: bytes,
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> Asset | None:
        if not self.generate_assets:
            return None
        if self._asset_bytes + len(data) > self.max_asset_bytes:
            self.warn(
                "asset-budget-exceeded",
                f"Visualization '{label}' was skipped: asset budget of {self.max_asset_bytes:,} bytes reached",
            )
            return None
        self._asset_bytes += len(data)
        asset = Asset(
            id=f"a{len(self.assets) + 1:03d}-{role}",
            label=label,
            role=role,
            media_type=media_type,
            width=width,
            height=height,
            data_base64=base64.b64encode(data).decode("ascii"),
            size_bytes=len(data),
        )
        self.assets.append(asset)
        return asset

    # ------------------------------------------------------- cancellation/progress
    def check(self) -> None:
        """Raise if the comparison has been cancelled or its deadline has passed."""
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ComparisonCancelled("Comparison cancelled")
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise ComparisonTimeout("Comparison exceeded its time limit")

    def progress(self, fraction: float, message: str = "") -> None:
        self.check()
        if self._progress is not None:
            self._progress(max(0.0, min(1.0, fraction)), message)


class ArtifactPlugin(ABC):
    """Base class for format plugins. See ``docs/plugin-development.md``."""

    # --- identity -----------------------------------------------------------------------
    name: ClassVar[str]
    version: ClassVar[str]
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    api_version: ClassVar[str] = PLUGIN_API_VERSION
    documentation_url: ClassVar[str | None] = None

    # --- detection ----------------------------------------------------------------------
    format_id: ClassVar[str] = ""
    format_name: ClassVar[str] = ""
    supported_extensions: ClassVar[tuple[str, ...]] = ()
    mime_types: ClassVar[tuple[str, ...]] = ()
    #: Byte prefixes identifying the format (checked at offset 0). Override ``sniff`` for more.
    signatures: ClassVar[tuple[bytes, ...]] = ()
    #: Used to break ties between plugins with the same detection score (higher wins).
    priority: ClassVar[int] = 0

    # --- capabilities -------------------------------------------------------------------
    capabilities: ClassVar[tuple[str, ...]] = ()
    limitations: ClassVar[tuple[str, ...]] = ()
    #: Importable top-level modules the plugin needs (checked without importing them).
    requires: ClassVar[tuple[str, ...]] = ()
    #: Shown when ``requires`` is not satisfied, e.g. 'pip install "artifactdiff[tabular]"'.
    install_hint: ClassVar[str | None] = None
    Options: ClassVar[type[PluginOptions]] = PluginOptions

    # ------------------------------------------------------------------- detection
    def sniff(self, head: bytes) -> bool:
        """Return True if the first bytes identify this format. Default: ``signatures``."""
        return any(head.startswith(sig) for sig in self.signatures)

    def supports(self, file: ArtifactFile) -> Detection | None:
        """Decide whether this plugin can handle ``file`` and how confident the detection is."""
        if self.signatures or type(self).sniff is not ArtifactPlugin.sniff:
            try:
                head = file.head(4096)
            except OSError:
                head = b""
            if head and self.sniff(head):
                return Detection("signature", 3)
        if file.mime_type and file.mime_type in self.mime_types:
            return Detection("mime", 2)
        if file.extension in self.supported_extensions:
            return Detection("extension", 1)
        return None

    # -------------------------------------------------------------------- behaviour
    @abstractmethod
    def inspect(self, file: ArtifactFile) -> dict[str, JSONValue]:
        """Return format-specific properties of a single file (JSON-serializable)."""

    @abstractmethod
    def compare(
        self,
        old: ArtifactFile,
        new: ArtifactFile,
        options: PluginOptions,
        ctx: ComparisonContext,
    ) -> None:
        """Compare two files and report every verified difference via ``ctx.add_change``."""

    def summarize(self, result: ComparisonResult) -> str:
        """One-line human summary. Plugins may override for format-specific phrasing."""
        stats = result.statistics
        if result.status.value in {"identical", "equivalent"}:
            return "No differences found" + (
                " (files are byte-identical)" if result.status.value == "identical" else ""
            )
        if result.status.value != "different":
            return result.error or result.status.value
        parts = [f"{n} {k.replace('_', ' ')}" for k, n in sorted(stats.by_kind.items())]
        return f"{stats.total_changes} change(s): " + ", ".join(parts)

    def export(self, result: ComparisonResult, fmt: str) -> str:
        """Render a result in ``fmt`` ('json', 'markdown', 'html', 'text')."""
        from artifactdiff.reporting import render_result

        return render_result(result, fmt)

    # -------------------------------------------------------------------- metadata
    @classmethod
    def artifact_format(cls) -> ArtifactFormat:
        return ArtifactFormat(
            id=cls.format_id or cls.name,
            name=cls.format_name or cls.display_name or cls.name,
            extensions=list(cls.supported_extensions),
            mime_types=list(cls.mime_types),
        )

    @classmethod
    def check_available(cls) -> tuple[bool, str | None]:
        missing = [m for m in cls.requires if importlib.util.find_spec(m) is None]
        if missing:
            hint = f" (install with: {cls.install_hint})" if cls.install_hint else ""
            return False, "Missing Python modules: " + ", ".join(missing) + hint
        return True, None

    @classmethod
    def metadata(cls, *, builtin: bool = True) -> PluginMetadata:
        available, reason = cls.check_available()
        return PluginMetadata(
            name=cls.name,
            version=cls.version,
            display_name=cls.display_name or cls.name,
            description=cls.description,
            api_version=cls.api_version,
            formats=[cls.artifact_format()],
            capabilities=list(cls.capabilities),
            limitations=list(cls.limitations),
            requires=list(cls.requires),
            available=available,
            unavailable_reason=reason,
            builtin=builtin,
            options_schema=cls.Options.model_json_schema(),
            documentation_url=cls.documentation_url,
        )

    @classmethod
    def validate_class(cls) -> None:
        """Check the static plugin contract. Raises :class:`PluginValidationError`."""
        problems: list[str] = []
        name = getattr(cls, "name", None)
        if not isinstance(name, str) or not _NAME_RE.match(name):
            problems.append("`name` must match ^[a-z][a-z0-9-]{1,40}$")
        version = getattr(cls, "version", None)
        if not isinstance(version, str) or not _SEMVER_RE.match(version):
            problems.append("`version` must be a semantic version like '1.0.0'")
        if (
            not isinstance(cls.api_version, str)
            or cls.api_version.split(".")[0] != (PLUGIN_API_VERSION.split(".", maxsplit=1)[0])
        ):
            problems.append(f"`api_version` {cls.api_version!r} is incompatible with core API {PLUGIN_API_VERSION}")
        for ext in cls.supported_extensions:
            if not (ext.startswith(".") and ext == ext.lower()):
                problems.append(f"extension {ext!r} must be lower-case and start with '.'")
        if not (isinstance(cls.Options, type) and issubclass(cls.Options, PluginOptions)):
            problems.append("`Options` must subclass artifactdiff.plugin.PluginOptions")
        if getattr(cls, "__abstractmethods__", None):
            problems.append("abstract methods not implemented: " + ", ".join(sorted(cls.__abstractmethods__)))
        if problems:
            raise PluginValidationError(f"Plugin {name or cls.__name__!r} is invalid: " + "; ".join(problems))


def options_dict(options: PluginOptions) -> dict[str, Any]:
    return options.model_dump(mode="json")
