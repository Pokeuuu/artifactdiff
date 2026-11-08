"""Rich terminal rendering of results and reports."""

from __future__ import annotations

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from artifactdiff.models import ComparisonResult, ComparisonStatus, FileEntryStatus, Report
from artifactdiff.reporting import format_value

SEV_STYLE = {"info": "dim", "low": "green", "medium": "yellow", "high": "bold red"}
KIND_STYLE = {
    "added": "green",
    "removed": "red",
    "modified": "yellow",
    "type_changed": "magenta",
    "renamed": "cyan",
    "moved": "blue",
}
STATUS_STYLE = {
    ComparisonStatus.IDENTICAL: "dim",
    ComparisonStatus.EQUIVALENT: "dim",
    ComparisonStatus.DIFFERENT: "yellow",
    ComparisonStatus.ERROR: "bold red",
    ComparisonStatus.UNSUPPORTED: "red",
}


def result_renderable(r: ComparisonResult, *, max_changes: int = 50, verbose: bool = False) -> Group:
    parts: list[object] = []
    plugin = f"{r.plugin.name} {r.plugin.version}" if r.plugin else "none"
    header = Text.assemble(
        (f"{r.status.value.upper()}", STATUS_STYLE.get(r.status, "")),
        "  ",
        (r.summary, "bold"),
        "\n",
        (f"{r.old.name}", "cyan"),
        " → ",
        (f"{r.new.name}", "cyan"),
        (f"   plugin {plugin} · {r.statistics.duration_ms:.0f} ms", "dim"),
    )
    parts.append(header)
    if r.error:
        parts.append(Text(f"error: {r.error}", style="bold red"))
    if r.changes:
        table = Table(show_edge=False, header_style="dim", expand=False, pad_edge=False)
        table.add_column("sev", no_wrap=True)
        table.add_column("kind", no_wrap=True)
        table.add_column("path", overflow="fold", max_width=48)
        table.add_column("change", overflow="fold")
        table.add_column("old → new", overflow="fold", max_width=60)
        shown = r.changes if verbose else r.changes[:max_changes]
        for c in shown:
            vals = ""
            if c.old_value is not None or c.new_value is not None:
                vals = f"{format_value(c.old_value, 40)} → {format_value(c.new_value, 40)}"
            desc = escape(c.description) + (f"\n[dim]{escape(c.evidence)}[/dim]" if verbose else "")
            table.add_row(
                f"[{SEV_STYLE[c.severity.value]}]{c.severity.value}[/]",
                f"[{KIND_STYLE.get(c.kind.value, '')}]{c.kind.value}[/]",
                escape(c.path),
                desc,
                escape(vals),
            )
        parts.append(table)
        hidden = r.statistics.total_changes - len(shown)
        if hidden > 0:
            parts.append(Text(f"… {hidden:,} more change(s) (use --verbose or --format json)", style="dim"))
    for w in r.warnings:
        parts.append(Text(f"⚠ {w.code}: {w.message}", style="yellow"))
    if verbose:
        for t in r.limitations:
            parts.append(Text(f"ⓘ {t}", style="dim"))
        for k, v in r.statistics.metrics.items():
            parts.append(Text(f"  {k} = {format_value(v, 100)}", style="dim"))
    return Group(*parts)  # type: ignore[arg-type]


def print_result(console: Console, r: ComparisonResult, **kw: object) -> None:
    console.print(Panel(result_renderable(r, **kw), border_style=STATUS_STYLE.get(r.status, ""), expand=True))  # type: ignore[arg-type]


def print_report(console: Console, report: Report, *, verbose: bool = False) -> None:
    for e in report.entries:
        if e.result is not None:
            print_result(console, e.result, verbose=verbose)
        else:
            style = {FileEntryStatus.ADDED: "green", FileEntryStatus.REMOVED: "red"}.get(e.file_status, "cyan")
            name = f"{e.old_path} → {e.path}" if e.old_path else e.path
            console.print(
                f"[{style}]{e.file_status.value:>9}[/] {escape(name)}"
                + (f" [dim]({escape(e.note)})[/]" if e.note else "")
            )
    s = report.summary
    console.print(
        f"\n[bold]{s.files}[/] file(s) · [bold]{s.with_changes}[/] with changes · "
        f"[bold]{s.total_changes:,}[/] change(s) · {s.added_files} added · {s.removed_files} removed"
        + (f" · [red]{s.errors} error(s)[/]" if s.errors else "")
    )
