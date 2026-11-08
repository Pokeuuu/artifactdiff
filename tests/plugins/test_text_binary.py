from __future__ import annotations

import os
from pathlib import Path

from helpers import write

from artifactdiff.engine import Engine
from artifactdiff.models import ComparisonStatus


def test_text_hunks(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.yaml", "a: 1\nb: 2\nc: 3\n")
    b = write(tmp_path / "b.yaml", "a: 1\nb: 20\nc: 3\nd: 4\n")
    r = engine.compare(a, b)
    assert r.plugin is not None and r.plugin.name == "text"
    kinds = sorted(c.kind.value for c in r.changes)
    assert kinds == ["added", "modified"]
    mod = next(c for c in r.changes if c.kind.value == "modified")
    assert mod.old_value == "b: 2" and mod.new_value == "b: 20" and mod.details["old_start"] == 2


def test_text_line_endings_and_whitespace(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.txt", b"x\ny\n")
    b = write(tmp_path / "b.txt", b"x\r\n  y\r\n")
    r = engine.compare(a, b)
    assert any(c.path == "line_endings" for c in r.changes)
    r2 = engine.compare(a, b, options={"ignore_whitespace": True})
    assert [c.path for c in r2.changes] == ["line_endings"]


def test_text_non_utf8_warns(engine: Engine, tmp_path: Path) -> None:
    r = engine.compare(write(tmp_path / "a.txt", "caf\xe9\n".encode("latin-1")), write(tmp_path / "b.txt", "cafe\n"))
    assert any(w.code == "not-utf8" for w in r.warnings)


def test_text_empty_files(engine: Engine, tmp_path: Path) -> None:
    r = engine.compare(write(tmp_path / "a.txt", ""), write(tmp_path / "b.txt", "hello\n"))
    assert r.status == ComparisonStatus.DIFFERENT


def test_binary_ranges(engine: Engine, tmp_path: Path) -> None:
    base = bytes(range(256)) * 64
    changed = bytearray(base)
    changed[100] ^= 0xFF
    changed[5000:5010] = b"\x00" * 10
    a = write(tmp_path / "a.bin", base)
    b = write(tmp_path / "b.bin", bytes(changed) + b"tail")
    r = engine.compare(a, b)
    assert r.plugin is not None and r.plugin.name == "binary"
    paths = [c.path for c in r.changes]
    assert "bytes[0x64:0x65]" in paths
    assert any(p.startswith("bytes[0x1388") for p in paths)
    assert r.changes[-1].kind.value == "added"
    assert any("only raw bytes" in t for t in r.limitations)


def test_binary_streams_large_file(engine: Engine, tmp_path: Path) -> None:
    size = 8 * 1024 * 1024
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    blob = os.urandom(size)
    a.write_bytes(blob)
    b.write_bytes(blob[:-1] + bytes([blob[-1] ^ 1]))
    r = engine.compare(a, b, options={"chunk_size_kb": 256})
    assert r.statistics.metrics["differing_bytes_in_common_prefix"] == 1
    assert r.changes[0].details["start"] == size - 1


def test_binary_truncation(engine: Engine, tmp_path: Path) -> None:
    r = engine.compare(write(tmp_path / "a.bin", b"\x01" * 100), write(tmp_path / "b.bin", b"\x01" * 60))
    assert [c.kind.value for c in r.changes] == ["modified", "removed"]
