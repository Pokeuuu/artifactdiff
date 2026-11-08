from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from helpers import write

from artifactdiff.config import ArtifactDiffConfig, Limits
from artifactdiff.engine import Engine
from artifactdiff.errors import (
    FileTooLargeError,
    InputFileError,
    InvalidOptionsError,
    PluginNotFoundError,
    PluginValidationError,
)
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ComparisonResult, ComparisonStatus
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, PluginOptions
from artifactdiff.registry import PluginRegistry, default_registry
from artifactdiff_sdk.testing import make_engine


class CrashPlugin(ArtifactPlugin):
    name = "crash"
    version = "1.0.0"
    supported_extensions = (".crash",)

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        return {}

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        raise ZeroDivisionError("boom")


class SlowPlugin(ArtifactPlugin):
    name = "slow"
    version = "1.0.0"
    supported_extensions = (".slow",)

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        return {}

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        while True:
            ctx.check()


class ManyPlugin(ArtifactPlugin):
    name = "many"
    version = "1.0.0"
    supported_extensions = (".many",)

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        return {}

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        for i in range(50):
            ctx.add_change(ChangeKind.ADDED, ChangeCategory.DATA, f"x[{i}]", "added", evidence="test")


def test_identical_files_short_circuit(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", '{"a": 1}')
    b = write(tmp_path / "b.json", '{"a": 1}')
    r = engine.compare(a, b)
    assert r.status == ComparisonStatus.IDENTICAL
    assert r.changes == []
    assert r.plugin is not None and r.plugin.name == "json"


def test_equivalent_when_only_formatting_differs(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", '{"a": 1, "b": [1,2]}')
    b = write(tmp_path / "b.json", '{\n  "b": [1, 2],\n  "a": 1\n}\n')
    r = engine.compare(a, b)
    assert r.status == ComparisonStatus.EQUIVALENT


def test_missing_file_raises(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", "{}")
    with pytest.raises(InputFileError):
        engine.compare(a, tmp_path / "nope.json")


def test_directory_is_not_a_file(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", "{}")
    with pytest.raises(InputFileError):
        engine.compare(a, tmp_path)


def test_size_limit(tmp_path: Path) -> None:
    eng = Engine(config=ArtifactDiffConfig(limits=Limits(max_file_size_mb=0.0001)))
    a = write(tmp_path / "a.txt", "x" * 1000)
    b = write(tmp_path / "b.txt", "y" * 1000)
    with pytest.raises(FileTooLargeError):
        eng.compare(a, b)


def test_invalid_options_rejected(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", '{"a":1}')
    b = write(tmp_path / "b.json", '{"a":2}')
    with pytest.raises(InvalidOptionsError):
        engine.compare(a, b, options={"no_such_option": 1})


def test_unknown_plugin(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", "{}")
    with pytest.raises(PluginNotFoundError):
        engine.compare(a, a, plugin="nope")


def test_plugin_crash_is_isolated(tmp_path: Path) -> None:
    eng = make_engine(CrashPlugin)
    r = eng.compare(write(tmp_path / "a.crash", "1"), write(tmp_path / "b.crash", "2"))
    assert r.status == ComparisonStatus.ERROR
    assert r.error is not None and "ZeroDivisionError" in r.error


def test_timeout_is_cooperative(tmp_path: Path) -> None:
    eng = make_engine(SlowPlugin, config=ArtifactDiffConfig(limits=Limits(timeout_seconds=0.2)))
    r = eng.compare(write(tmp_path / "a.slow", "1"), write(tmp_path / "b.slow", "2"))
    assert r.status == ComparisonStatus.ERROR
    assert "time limit" in (r.error or "")


def test_cancellation(tmp_path: Path) -> None:
    eng = make_engine(SlowPlugin)
    ev = threading.Event()
    ev.set()
    r = eng.compare(write(tmp_path / "a.slow", "1"), write(tmp_path / "b.slow", "2"), cancel_event=ev)
    assert r.status == ComparisonStatus.ERROR and r.error == "Comparison cancelled"


def test_max_changes_truncates_but_counts(tmp_path: Path) -> None:
    eng = make_engine(ManyPlugin, config=ArtifactDiffConfig(limits=Limits(max_changes=10)))
    r = eng.compare(write(tmp_path / "a.many", "1"), write(tmp_path / "b.many", "2"))
    assert len(r.changes) == 10
    assert r.statistics.total_changes == 50
    assert any(w.code == "changes-truncated" for w in r.warnings)


def test_change_ids_unique_and_stable(engine: Engine, tmp_path: Path) -> None:
    from samples import json_pair

    a, b = json_pair(tmp_path)
    r1, r2 = engine.compare(a, b), engine.compare(a, b)
    ids = [c.id for c in r1.changes]
    assert len(ids) == len(set(ids))
    assert ids == [c.id for c in r2.changes]
    assert r1.fingerprint == r2.fingerprint
    assert r1.created_at <= r2.created_at


def test_result_roundtrip_json(engine: Engine, tmp_path: Path) -> None:
    from samples import json_pair

    r = engine.compare(*json_pair(tmp_path))
    again = ComparisonResult.model_validate_json(r.model_dump_json())
    assert again == r
    assert again.compute_fingerprint() == r.fingerprint


def test_reproducibility_block(engine: Engine, tmp_path: Path) -> None:
    from samples import json_pair

    r = engine.compare(*json_pair(tmp_path))
    rep = r.reproducibility
    assert rep is not None and rep.old_sha256 == r.old.sha256 and len(rep.options_sha256) == 64


def test_config_plugin_options_are_defaults(tmp_path: Path) -> None:
    cfg = ArtifactDiffConfig.model_validate({"plugins": {"options": {"json": {"ignore_paths": ["$.b"]}}}})
    eng = Engine(config=cfg)
    r = eng.compare(write(tmp_path / "a.json", '{"a":1,"b":1}'), write(tmp_path / "b.json", '{"a":1,"b":2}'))
    assert r.status == ComparisonStatus.EQUIVALENT
    assert r.options["ignore_paths"] == ["$.b"]


def test_extension_mismatch_warns(engine: Engine, tmp_path: Path) -> None:
    r = engine.compare(write(tmp_path / "a.txt", "a\n"), write(tmp_path / "b.md", "b\n"))
    assert any(w.code == "extension-mismatch" for w in r.warnings)


def test_registry_rejects_invalid_plugin() -> None:
    class Bad(ArtifactPlugin):
        name = "Bad Name"
        version = "one"
        api_version = "2.0"
        supported_extensions = ("JSON",)

        def inspect(self, file: ArtifactFile) -> dict[str, Any]:
            return {}

        def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
            return None

    with pytest.raises(PluginValidationError) as exc:
        PluginRegistry().register(Bad)
    msg = str(exc.value)
    assert "name" in msg and "version" in msg and "api_version" in msg and "extension" in msg


def test_registry_rejects_duplicates() -> None:
    reg = PluginRegistry()
    reg.register(CrashPlugin)
    with pytest.raises(PluginValidationError):
        reg.register(CrashPlugin)


def test_selection_prefers_specific_over_fallback(tmp_path: Path) -> None:
    reg = default_registry()
    a = ArtifactFile(write(tmp_path / "a.json", "{}"))
    sel = reg.select(a, a)
    assert sel is not None and sel.plugin.name == "json"
    unknown = ArtifactFile(write(tmp_path / "x.unknownext", b"\x00\x01binary"))
    sel = reg.select(unknown, unknown)
    assert sel is not None and sel.plugin.name == "binary"
    texty = ArtifactFile(write(tmp_path / "x.weird", "plain text\n"))
    sel = reg.select(texty, texty)
    assert sel is not None and sel.plugin.name == "text"


def test_all_builtin_plugins_registered_and_valid() -> None:
    reg = default_registry()
    assert {
        "binary",
        "text",
        "json",
        "csv",
        "parquet",
        "sqlite",
        "image",
        "audio",
        "pdf",
        "gltf",
        "safetensors",
    } <= set(reg.names())
    for meta in reg.all_metadata():
        json.dumps(meta.model_dump(mode="json"))
        assert meta.builtin


def test_entry_points_not_loaded_unless_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []

    class FakeEP:
        name = "evil"
        value = "evil_pkg:Plugin"
        group = "artifactdiff.plugins"
        dist = None

        def load(self) -> type:
            loaded.append("evil")
            raise AssertionError("must not be loaded")

    import artifactdiff.registry as regmod

    monkeypatch.setattr(regmod, "entry_points", lambda group: [FakeEP()])
    reg = PluginRegistry()
    found = reg.discover_entry_points(set())
    assert [d.name for d in found] == ["evil"] and not found[0].enabled
    assert reg.load_enabled_entry_points(set()) == []
    assert loaded == []
