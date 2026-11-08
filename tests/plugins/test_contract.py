"""Behaviour every built-in plugin must satisfy: identical, modified, empty, invalid, missing,
unsupported, deterministic, and never modifying its inputs."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest
import samples

from artifactdiff.engine import Engine
from artifactdiff.errors import InputFileError
from artifactdiff.models import ComparisonStatus
from artifactdiff.registry import default_registry
from artifactdiff_sdk.testing import validate_plugin

PAIRS: dict[str, tuple[Callable[[Path], tuple[Path, Path]], str]] = {
    "json": (samples.json_pair, ".json"),
    "csv": (samples.csv_pair, ".csv"),
    "parquet": (samples.parquet_pair, ".parquet"),
    "sqlite": (samples.sqlite_pair, ".sqlite"),
    "image": (samples.image_pair, ".png"),
    "audio": (samples.audio_pair, ".wav"),
    "pdf": (samples.pdf_pair, ".pdf"),
    "gltf": (samples.gltf_pair, ".glb"),
    "safetensors": (samples.safetensors_pair, ".safetensors"),
}

# Plugins for which an empty file is a valid (empty) artifact rather than invalid input.
EMPTY_IS_VALID = {"csv", "sqlite"}


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(params=sorted(PAIRS))
def plugin_case(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[str, Path, Path, str]:
    name = request.param
    gen, ext = PAIRS[name]
    a, b = gen(tmp_path)
    return name, a, b, ext


def test_every_builtin_is_valid() -> None:
    reg = default_registry()
    for name in reg.names():
        assert validate_plugin(reg.get_class(name)) == [], name


def test_detected_and_modified(engine: Engine, plugin_case: tuple[str, Path, Path, str]) -> None:
    name, a, b, _ = plugin_case
    before = (digest(a), digest(b))
    r = engine.compare(a, b)
    assert r.plugin is not None and r.plugin.name == name
    assert r.status == ComparisonStatus.DIFFERENT, r.error
    for c in r.changes:
        assert c.evidence, f"{name}: change {c.path} has no evidence"
        assert c.description
    assert (digest(a), digest(b)) == before, "inputs must not be modified"


def test_identical(engine: Engine, plugin_case: tuple[str, Path, Path, str]) -> None:
    _, a, _, _ = plugin_case
    copy = a.with_name("copy" + a.suffix)
    copy.write_bytes(a.read_bytes())
    r = engine.compare(a, copy)
    assert r.status == ComparisonStatus.IDENTICAL and not r.changes


def test_deterministic(engine: Engine, plugin_case: tuple[str, Path, Path, str]) -> None:
    _, a, b, _ = plugin_case
    r1, r2 = engine.compare(a, b), engine.compare(a, b)
    assert r1.fingerprint == r2.fingerprint
    assert [x.data_base64 for x in r1.assets] == [x.data_base64 for x in r2.assets]


def test_empty_file(engine: Engine, plugin_case: tuple[str, Path, Path, str]) -> None:
    name, a, _, ext = plugin_case
    empty = a.with_name("empty" + ext)
    empty.write_bytes(b"")
    r = engine.compare(a, empty, plugin=name)
    if name in EMPTY_IS_VALID:
        assert r.status in (ComparisonStatus.DIFFERENT, ComparisonStatus.ERROR)
    else:
        assert r.status == ComparisonStatus.ERROR and r.error


def test_garbage_input(engine: Engine, plugin_case: tuple[str, Path, Path, str]) -> None:
    name, a, _, ext = plugin_case
    garbage = a.with_name("garbage" + ext)
    garbage.write_bytes(b"\x00\xff\x13garbage-not-a-real-file" * 10)
    r = engine.compare(a, garbage, plugin=name)
    assert r.status == ComparisonStatus.ERROR and r.error, f"{name} accepted garbage: {r.summary}"


def test_truncated_input(engine: Engine, plugin_case: tuple[str, Path, Path, str]) -> None:
    name, a, b, ext = plugin_case
    if name in ("json", "csv"):
        pytest.skip("truncated text formats can still be valid documents")
    data = b.read_bytes()
    cut = a.with_name("cut" + ext)
    cut.write_bytes(data[: max(1, len(data) // 3)])
    r = engine.compare(a, cut, plugin=name)
    assert r.status == ComparisonStatus.ERROR or r.has_differences


def test_missing_file(engine: Engine, plugin_case: tuple[str, Path, Path, str]) -> None:
    _, a, _, ext = plugin_case
    with pytest.raises(InputFileError):
        engine.compare(a, a.with_name("missing" + ext))


def test_inspect(engine: Engine, plugin_case: tuple[str, Path, Path, str]) -> None:
    name, a, _, _ = plugin_case
    meta = engine.inspect(a)
    assert meta.properties["plugin"] == name and meta.sha256 == digest(a)


def test_unsupported_pair_falls_back(engine: Engine, tmp_path: Path) -> None:
    a = tmp_path / "a.xyz"
    b = tmp_path / "b.xyz"
    a.write_bytes(b"\x00\x01\x02")
    b.write_bytes(b"\x00\x01\x03")
    r = engine.compare(a, b)
    assert r.plugin is not None and r.plugin.name == "binary"
    assert r.limitations
