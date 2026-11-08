"""Plain-text rendering (no colours) — used by ``plugin.export(result, 'text')`` and logs."""

from __future__ import annotations

from artifactdiff.models import Report
from artifactdiff.reporting import format_value


def render_text(report: Report, *, max_changes: int = 200) -> str:
    lines = [report.title, "=" * len(report.title)]
    s = report.summary
    lines.append(f"{s.files} file(s), {s.with_changes} with changes, {s.total_changes} change(s), {s.errors} error(s)")
    for e in report.entries:
        lines.append("")
        lines.append(f"[{e.file_status.value}] {e.old_path + ' -> ' if e.old_path else ''}{e.path}")
        if e.note:
            lines.append(f"  note: {e.note}")
        r = e.result
        if r is None:
            continue
        lines.append(f"  {r.status.value}: {r.summary}")
        if r.error:
            lines.append(f"  error: {r.error}")
        for c in r.changes[:max_changes]:
            lines.append(f"  {c.severity.value:<6} {c.kind.value:<12} {c.path}: {c.description}")
            if c.old_value is not None or c.new_value is not None:
                lines.append(f"         {format_value(c.old_value, 80)} -> {format_value(c.new_value, 80)}")
        for w in r.warnings:
            lines.append(f"  warning [{w.code}]: {w.message}")
    return "\n".join(lines) + "\n"
