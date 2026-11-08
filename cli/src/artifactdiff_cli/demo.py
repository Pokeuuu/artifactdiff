"""``artifactdiff demo``: run the example pairs in ``examples/<format>/``.

Each example directory contains ``old.<ext>``, ``new.<ext>``, optional ``options.json`` and
``expected.json`` (status plus the expected (kind, category, path) of every change).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from artifactdiff.engine import Engine
from artifactdiff.errors import ArtifactDiffError
from artifactdiff.models import ComparisonResult
from artifactdiff.reporting import render_result


def find_examples_dir() -> Path | None:
    candidates = [Path.cwd() / "examples", Path(__file__).resolve().parents[2] / "examples"]
    for c in candidates:
        if c.is_dir() and any(c.glob("*/expected.json")):
            return c
    return None


def expected_summary(result: ComparisonResult) -> dict[str, Any]:
    return {
        "plugin": result.plugin.name if result.plugin else None,
        "status": result.status.value,
        "total_changes": result.statistics.total_changes,
        "changes": [[c.kind.value, c.category.value, c.path] for c in result.changes],
    }


def example_pairs(root: Path) -> list[tuple[str, Path, Path, dict[str, Any], Path]]:
    pairs = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        olds = sorted(d.glob("old.*"))
        news = sorted(d.glob("new.*"))
        if not olds or not news:
            continue
        opts_file = d / "options.json"
        opts = json.loads(opts_file.read_text(encoding="utf-8")) if opts_file.exists() else {}
        pairs.append((d.name, olds[0], news[0], opts, d / "expected.json"))
    return pairs


def run_demo(console: Console, examples_dir: Path | None, output_dir: Path | None, check: bool) -> int:
    root = examples_dir or find_examples_dir()
    if root is None or not root.is_dir():
        console.print("[red]error:[/] examples directory not found; pass --examples-dir")
        return 2
    engine = Engine()
    table = Table(header_style="dim")
    for col in ("example", "plugin", "status", "changes", "summary", "expected"):
        table.add_column(col, overflow="fold")
    failures = 0
    for name, old, new, opts, expected_file in example_pairs(root):
        try:
            result = engine.compare(old, new, plugin=opts.pop("_plugin", None), options=opts)
        except ArtifactDiffError as exc:
            table.add_row(name, "-", "[red]error[/]", "-", escape(str(exc)), "")
            failures += 1
            continue
        verdict = ""
        if expected_file.exists():
            expected = json.loads(expected_file.read_text(encoding="utf-8"))
            ok = expected_summary(result) == expected
            verdict = "[green]match[/]" if ok else "[red]MISMATCH[/]"
            failures += 0 if ok else 1
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            for fmt, ext in (("html", "html"), ("markdown", "md"), ("json", "json")):
                (output_dir / f"{name}.{ext}").write_text(render_result(result, fmt), encoding="utf-8")
        table.add_row(
            name,
            result.plugin.name if result.plugin else "-",
            result.status.value,
            f"{result.statistics.total_changes:,}",
            escape(result.summary),
            verdict,
        )
    console.print(table)
    if output_dir is not None:
        console.print(f"[dim]reports written to {escape(str(output_dir))}[/]")
    return 1 if (check and failures) else 0
