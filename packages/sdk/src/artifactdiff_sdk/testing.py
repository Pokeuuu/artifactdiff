"""Testing helpers for plugin authors.

from artifactdiff_sdk.testing import run_plugin, assert_plugin_contract

def test_contract():
    assert_plugin_contract(MyPlugin)

def test_detects_change(tmp_path):
    result = run_plugin(MyPlugin, write(tmp_path, "a.example", ...), write(tmp_path, "b.example", ...))
    assert result.status == "different"
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from artifactdiff.config import ArtifactDiffConfig
from artifactdiff.engine import Engine
from artifactdiff.errors import PluginValidationError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ComparisonResult
from artifactdiff.plugin import ArtifactPlugin
from artifactdiff.registry import PluginRegistry, load_object


def validate_plugin(target: str | type[ArtifactPlugin]) -> list[str]:
    """Return a list of contract problems (empty when the plugin is valid)."""
    cls = load_object(target) if isinstance(target, str) else target
    if not (isinstance(cls, type) and issubclass(cls, ArtifactPlugin)):
        return [f"{target!r} is not an ArtifactPlugin subclass"]
    problems: list[str] = []
    try:
        cls.validate_class()
    except PluginValidationError as exc:
        problems.append(str(exc))
        return problems
    available, reason = cls.check_available()
    if not available:
        problems.append(f"dependencies missing: {reason}")
        return problems
    custom_detection = cls.supports is not ArtifactPlugin.supports or cls.sniff is not ArtifactPlugin.sniff
    if not (cls.supported_extensions or cls.signatures or cls.mime_types or custom_detection):
        problems.append("plugin declares no extensions, MIME types or signatures, so it can never be selected")
    try:
        cls.Options()
    except Exception as exc:
        problems.append(f"Options() must be constructible with defaults: {exc}")
    try:
        json.dumps(cls.metadata(builtin=False).model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        problems.append(f"metadata is not JSON-serializable: {exc}")
    return problems


def assert_plugin_contract(cls: type[ArtifactPlugin]) -> None:
    problems = validate_plugin(cls)
    assert not problems, "Plugin contract violated:\n- " + "\n- ".join(problems)


def make_engine(
    *plugins: type[ArtifactPlugin], config: ArtifactDiffConfig | None = None, include_fallbacks: bool = True
) -> Engine:
    """An engine whose registry contains only the given plugins (plus text/binary fallbacks)."""
    reg = PluginRegistry()
    if include_fallbacks:
        from artifactdiff_plugins.binary import BinaryPlugin
        from artifactdiff_plugins.text import TextPlugin

        reg.register(BinaryPlugin, builtin=True)
        reg.register(TextPlugin, builtin=True)
    for p in plugins:
        reg.register(p)
    return Engine(registry=reg, config=config or ArtifactDiffConfig())


def run_plugin(
    plugin: type[ArtifactPlugin],
    old: str | os.PathLike[str],
    new: str | os.PathLike[str],
    options: dict[str, Any] | None = None,
    *,
    force: bool = True,
) -> ComparisonResult:
    """Run one comparison through the real engine using ``plugin``."""
    engine = make_engine(plugin)
    return engine.compare(old, new, plugin=plugin.name if force else None, options=options)


def write_file(directory: str | os.PathLike[str], name: str, content: bytes | str) -> Path:
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    return path


def temp_artifact(content: bytes | str, suffix: str) -> ArtifactFile:
    fd, name = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(content.encode() if isinstance(content, str) else content)
    return ArtifactFile(name)
