"""Shared test helpers (import as `from helpers import ...`)."""

from __future__ import annotations

from pathlib import Path

from artifactdiff.models import ComparisonResult


def kinds_paths(result: ComparisonResult) -> set[tuple[str, str]]:
    return {(c.kind.value, c.path) for c in result.changes}


def write(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    return path
