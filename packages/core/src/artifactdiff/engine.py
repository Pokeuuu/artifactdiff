"""The comparison engine: plugin selection, option validation, limits and result assembly."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import sys
import threading
import time
from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from artifactdiff import __version__
from artifactdiff.config import ArtifactDiffConfig
from artifactdiff.errors import (
    ComparisonCancelled,
    ComparisonTimeout,
    InvalidOptionsError,
    PluginInputError,
    PluginNotFoundError,
    ResourceLimitError,
)
from artifactdiff.files import ArtifactFile, same_content
from artifactdiff.models import (
    ArtifactMetadata,
    ChangeSeverity,
    ComparisonResult,
    ComparisonStatistics,
    ComparisonStatus,
    ComparisonWarning,
    PluginRef,
    Reproducibility,
    max_severity,
)
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, PluginOptions, ProgressCallback
from artifactdiff.registry import PluginRegistry, Selection, default_registry, load_object

log = logging.getLogger(__name__)


def _library_versions(modules: tuple[str, ...]) -> dict[str, str]:
    if not modules:
        return {}
    dists = importlib_metadata.packages_distributions()
    out: dict[str, str] = {}
    for module in modules:
        for dist in dists.get(module, [module]):
            try:
                out[dist] = importlib_metadata.version(dist)
            except importlib_metadata.PackageNotFoundError:
                continue
    return dict(sorted(out.items()))


def _options_hash(options: dict[str, Any]) -> str:
    blob = json.dumps(options, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Engine:
    """Compares artifacts. Thread-safe as long as plugins are (built-in plugins are stateless)."""

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        config: ArtifactDiffConfig | None = None,
    ) -> None:
        self.config = config or ArtifactDiffConfig()
        self.registry = registry or default_registry()
        if self.config.plugins.enabled:
            self.registry.load_enabled_entry_points(set(self.config.plugins.enabled))
        for spec in self.config.plugins.modules:
            cls = load_object(spec)  # explicitly configured by the user => trusted
            if not (isinstance(cls, type) and issubclass(cls, ArtifactPlugin)):
                raise PluginNotFoundError(f"{spec} is not an ArtifactPlugin subclass")
            if getattr(cls, "name", None) not in self.registry.names():
                self.registry.register(cls)

    # ------------------------------------------------------------------- options
    def resolve_options(self, plugin: ArtifactPlugin, options: dict[str, Any] | None) -> PluginOptions:
        merged = {**self.config.plugins.options.get(plugin.name, {}), **(options or {})}
        try:
            return plugin.Options.model_validate(merged)
        except ValidationError as exc:
            raise InvalidOptionsError(f"Invalid options for plugin {plugin.name!r}:\n{exc}") from exc

    # ------------------------------------------------------------------- inspect
    def inspect(
        self, path: str | os.PathLike[str], *, plugin: str | None = None, display_name: str | None = None
    ) -> ArtifactMetadata:
        file = ArtifactFile(path, display_name)
        file.ensure_max_size(self.config.limits.max_file_size_bytes)
        detected_by: Literal["signature", "mime", "extension", "content", "explicit"]
        if plugin is not None:
            inst = self.registry.get(plugin)
            detected_by = "explicit"
        else:
            ranked = self.registry.available_for(file)
            if not ranked:
                raise PluginNotFoundError(f"No plugin supports {file.display_name}")
            inst = self.registry.get(ranked[0][0])
            detected_by = ranked[0][1].method
        return ArtifactMetadata(
            name=file.display_name,
            size_bytes=file.size,
            sha256=file.sha256,
            format=inst.artifact_format(),
            detected_by=detected_by,
            properties={"plugin": inst.name, **inst.inspect(file)},
        )

    # ------------------------------------------------------------------- compare
    def compare(
        self,
        old_path: str | os.PathLike[str],
        new_path: str | os.PathLike[str],
        *,
        plugin: str | None = None,
        options: dict[str, Any] | None = None,
        old_name: str | None = None,
        new_name: str | None = None,
        cancel_event: threading.Event | None = None,
        progress: ProgressCallback | None = None,
        generate_assets: bool = True,
    ) -> ComparisonResult:
        """Compare two files.

        Raises :class:`InputFileError`/:class:`FileTooLargeError` for unusable inputs and
        :class:`InvalidOptionsError`/:class:`PluginNotFoundError` for caller mistakes. Problems
        *inside* the comparison (corrupt data, plugin bugs, timeouts) are returned as a result
        with ``status == "error"`` so batch runs keep going.
        """
        started = time.monotonic()
        limits = self.config.limits
        old = ArtifactFile(old_path, old_name)
        new = ArtifactFile(new_path, new_name)
        old.ensure_max_size(limits.max_file_size_bytes)
        new.ensure_max_size(limits.max_file_size_bytes)

        selection = self.registry.select(old, new, plugin)
        if selection is None:  # only possible if the binary fallback is unregistered
            return self._unsupported(old, new, started)
        inst = selection.plugin
        opts = self.resolve_options(inst, options)
        opts_dict = opts.model_dump(mode="json")

        ctx = ComparisonContext(
            inst.name,
            max_changes=limits.max_changes,
            deadline=started + limits.timeout_seconds,
            cancel_event=cancel_event,
            progress=progress,
            max_asset_bytes=limits.max_asset_bytes,
            generate_assets=generate_assets,
        )
        if selection.explicit and (selection.old_detection is None or selection.new_detection is None):
            ctx.warn(
                "plugin-forced",
                f"Plugin {inst.name!r} was requested explicitly but does not recognise "
                + (
                    "both files"
                    if selection.old_detection is None and selection.new_detection is None
                    else (old.display_name if selection.old_detection is None else new.display_name)
                ),
            )
        if old.extension != new.extension:
            ctx.warn(
                "extension-mismatch",
                f"Files have different extensions ({old.extension or 'none'} vs {new.extension or 'none'})",
            )

        old_meta = self._metadata(inst, old, selection, "old", ctx)
        new_meta = self._metadata(inst, new, selection, "new", ctx)

        status: ComparisonStatus
        error: str | None = None
        log_extra = {"plugin": inst.name, "old_size": old.size, "new_size": new.size}
        if same_content(old, new):
            status = ComparisonStatus.IDENTICAL
        else:
            try:
                ctx.progress(0.0, "comparing")
                inst.compare(old, new, opts, ctx)
                status = ComparisonStatus.DIFFERENT if ctx.total_changes else ComparisonStatus.EQUIVALENT
            except PluginInputError as exc:
                status, error = ComparisonStatus.ERROR, str(exc)
                log.info("plugin rejected input", extra={**log_extra, "error": str(exc)})
            except ResourceLimitError as exc:
                status, error = ComparisonStatus.ERROR, f"Resource limit: {exc}"
                log.info("resource limit", extra={**log_extra, "error": str(exc)})
            except ComparisonTimeout:
                status = ComparisonStatus.ERROR
                error = f"Comparison exceeded the time limit of {limits.timeout_seconds:g}s"
                log.warning("comparison timed out", extra=log_extra)
            except ComparisonCancelled:
                status, error = ComparisonStatus.ERROR, "Comparison cancelled"
                log.info("comparison cancelled", extra=log_extra)
            except MemoryError:
                status, error = ComparisonStatus.ERROR, "Out of memory while comparing"
                log.error("out of memory", extra=log_extra)
            except Exception as exc:  # error isolation: a plugin bug must not crash batch runs
                status = ComparisonStatus.ERROR
                error = f"Plugin {inst.name!r} failed: {type(exc).__name__}: {exc}"
                log.exception("plugin crashed", extra=log_extra)

        if ctx.truncated:
            ctx.warn(
                "changes-truncated",
                f"{ctx.total_changes:,} changes found; {len(ctx.changes):,} are listed individually and all "
                "are counted (raise limits.max_changes or the plugin's max_* options to list more)",
            )
        for text in inst.limitations:
            ctx.limitation(text)

        result = self._assemble(inst, old_meta, new_meta, opts_dict, ctx, status, error, started)
        result.summary = inst.summarize(result)
        result.fingerprint = result.compute_fingerprint()
        log.info(
            "comparison finished",
            extra={
                **log_extra,
                "status": status.value,
                "changes": ctx.total_changes,
                "duration_ms": round(result.statistics.duration_ms, 1),
            },
        )
        return result

    # ------------------------------------------------------------------- helpers
    def _metadata(
        self, inst: ArtifactPlugin, file: ArtifactFile, sel: Selection, side: str, ctx: ComparisonContext
    ) -> ArtifactMetadata:
        det = sel.old_detection if side == "old" else sel.new_detection
        meta = ArtifactMetadata(
            name=file.display_name,
            size_bytes=file.size,
            sha256=file.sha256,
            format=inst.artifact_format(),
            detected_by="explicit" if sel.explicit else (det.method if det else None),
        )
        try:
            meta.properties = inst.inspect(file)
        except PluginInputError as exc:
            ctx.warn("inspect-failed", f"Could not inspect {side} file: {exc}")
        except Exception as exc:
            ctx.warn("inspect-failed", f"Could not inspect {side} file: {type(exc).__name__}: {exc}")
            log.exception("inspect crashed", extra={"plugin": inst.name, "side": side})
        return meta

    def _assemble(
        self,
        inst: ArtifactPlugin,
        old_meta: ArtifactMetadata,
        new_meta: ArtifactMetadata,
        opts: dict[str, Any],
        ctx: ComparisonContext,
        status: ComparisonStatus,
        error: str | None,
        started: float,
    ) -> ComparisonResult:
        severities = [ChangeSeverity(s) for s, n in ctx.severity_counts.items() if n]
        stats = ComparisonStatistics(
            total_changes=ctx.total_changes,
            reported_changes=len(ctx.changes),
            by_kind=dict(sorted(ctx.kind_counts.items())),
            by_category=dict(sorted(ctx.category_counts.items())),
            by_severity=dict(sorted(ctx.severity_counts.items())),
            max_severity=max_severity(severities),
            metrics=ctx.metrics,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        ref = PluginRef(name=inst.name, version=inst.version)
        return ComparisonResult(
            artifactdiff_version=__version__,
            created_at=datetime.now(UTC),
            status=status,
            plugin=ref,
            old=old_meta,
            new=new_meta,
            options=opts,
            changes=ctx.changes,
            warnings=ctx.warnings,
            limitations=ctx.limitations,
            statistics=stats,
            assets=ctx.assets,
            error=error,
            reproducibility=Reproducibility(
                artifactdiff_version=__version__,
                plugin=ref,
                python_version=platform.python_version(),
                platform=f"{sys.platform}-{platform.machine()}",
                libraries=_library_versions(inst.requires),
                options_sha256=_options_hash(opts),
                old_sha256=old_meta.sha256,
                new_sha256=new_meta.sha256,
            ),
        )

    def _unsupported(self, old: ArtifactFile, new: ArtifactFile, started: float) -> ComparisonResult:
        def meta(f: ArtifactFile) -> ArtifactMetadata:
            return ArtifactMetadata(name=f.display_name, size_bytes=f.size, sha256=f.sha256)

        result = ComparisonResult(
            artifactdiff_version=__version__,
            created_at=datetime.now(UTC),
            status=ComparisonStatus.UNSUPPORTED,
            old=meta(old),
            new=meta(new),
            summary="No installed plugin can compare these files",
            warnings=[ComparisonWarning(code="unsupported", message="No plugin supports this file pair")],
            statistics=ComparisonStatistics(duration_ms=(time.monotonic() - started) * 1000),
        )
        result.fingerprint = result.compute_fingerprint()
        return result


def compare_files(old: str | Path, new: str | Path, **kwargs: Any) -> ComparisonResult:
    """Convenience wrapper: ``compare_files('a.json', 'b.json')``."""
    return Engine().compare(old, new, **kwargs)
