"""Report rendering: JSON (canonical), Markdown (PR comments), HTML (standalone), text."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from artifactdiff import __version__
from artifactdiff.errors import ArtifactDiffError
from artifactdiff.models import ComparisonResult, FileEntryStatus, Report, ReportEntry, ReportSummary

FORMATS = ("json", "markdown", "html", "text")


def format_value(value: Any, limit: int = 120) -> str:
    """Compact, single-line representation of an old/new value."""
    if value is None:
        return "—"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def result_as_report(result: ComparisonResult) -> Report:
    entry = ReportEntry(
        path=result.new.name,
        old_path=result.old.name if result.old.name != result.new.name else None,
        file_status=FileEntryStatus.MODIFIED,
        result=result,
    )
    from artifactdiff.directory import build_report

    return build_report([entry], title="ArtifactDiff report")


def render_result(result: ComparisonResult, fmt: str, **kwargs: Any) -> str:
    if fmt == "json":
        return result.model_dump_json(indent=2)
    return render_report(result_as_report(result), fmt, **kwargs)


def render_report(report: Report, fmt: str, **kwargs: Any) -> str:
    if fmt == "json":
        return report.model_dump_json(indent=2)
    if fmt in ("markdown", "md"):
        from artifactdiff.reporting.markdown import render_markdown

        return render_markdown(report, **kwargs)
    if fmt == "html":
        from artifactdiff.reporting.html import render_html

        return render_html(report, **kwargs)
    if fmt == "text":
        from artifactdiff.reporting.text import render_text

        return render_text(report, **kwargs)
    raise ArtifactDiffError(f"Unknown report format {fmt!r}; expected one of {', '.join(FORMATS)}")


def load_document(path: str | Path) -> Report:
    """Load a JSON result *or* report written by ArtifactDiff and normalise it to a Report."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArtifactDiffError(f"{path} is not valid JSON: {exc}") from exc
    try:
        if isinstance(data, dict) and "entries" in data:
            return Report.model_validate(data)
        return result_as_report(ComparisonResult.model_validate(data))
    except ValidationError as exc:
        raise ArtifactDiffError(f"{path} is not an ArtifactDiff result or report:\n{exc}") from exc


def empty_report(title: str = "ArtifactDiff report") -> Report:
    return Report(
        artifactdiff_version=__version__, generated_at=datetime.now(UTC), title=title, summary=ReportSummary()
    )
