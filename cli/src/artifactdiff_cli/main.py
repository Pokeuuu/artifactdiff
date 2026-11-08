"""``artifactdiff`` command-line interface.

Exit codes (stable, suitable for CI):
  0  no differences (or differences below --fail-on, or --exit-zero)
  1  differences found
  2  usage error, missing/unreadable input, invalid options or configuration
  3  a comparison failed (corrupt input, plugin error, timeout) or no plugin supports the files
"""

from __future__ import annotations

import json
import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from artifactdiff import __version__
from artifactdiff.config import CONFIG_FILENAME, DEFAULT_CONFIG_TOML, ArtifactDiffConfig, load_config
from artifactdiff.directory import compare_directories
from artifactdiff.engine import Engine
from artifactdiff.errors import ArtifactDiffError, ComparisonCancelled, ComparisonTimeout
from artifactdiff.logs import configure_logging
from artifactdiff.models import ChangeSeverity, ComparisonResult, ComparisonStatus, FileEntryStatus, Report
from artifactdiff.reporting import load_document, render_report, render_result
from artifactdiff_cli.options import load_options_file, parse_option_pairs
from artifactdiff_cli.render import print_report, print_result

EXIT_OK, EXIT_DIFF, EXIT_USAGE, EXIT_FAILED = 0, 1, 2, 3
_USAGE_ERRORS = frozenset(
    {"InputFileError", "FileTooLargeError", "InvalidOptionsError", "PluginNotFoundError", "PluginUnavailableError"}
)


class OutputFormat(StrEnum):
    text = "text"
    json = "json"
    markdown = "markdown"
    html = "html"


class Severity(StrEnum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"


app = typer.Typer(
    name="artifactdiff",
    help="The universal diff for everything Git cannot meaningfully compare.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
plugins_app = typer.Typer(help="List, inspect, scaffold and validate plugins.", no_args_is_help=True)
config_app = typer.Typer(help="Create or show configuration.", no_args_is_help=True)
app.add_typer(plugins_app, name="plugins")
app.add_typer(config_app, name="config")


def _console(stderr: bool = False) -> Console:
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower()
        if enc and enc not in ("utf-8", "utf8") and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    return Console(stderr=stderr, highlight=False, soft_wrap=False)


err = _console(stderr=True)


def fail(message: str, code: int = EXIT_USAGE) -> NoReturn:
    err.print(f"[bold red]error:[/] {escape(message)}")
    raise typer.Exit(code)


@app.callback()
def _main(
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True, help="More output (-vv: debug logs).")] = 0,
    log_format: Annotated[str, typer.Option(help="Log format: text or json.")] = "text",
) -> None:
    level = "DEBUG" if verbose >= 2 else ("INFO" if verbose == 1 else "WARNING")
    configure_logging(level, log_format)


def _load_config(path: Path | None) -> ArtifactDiffConfig:
    try:
        return load_config(path)
    except ArtifactDiffError as exc:
        fail(str(exc))


def _engine(cfg: ArtifactDiffConfig, plugin_modules: list[str]) -> Engine:
    try:
        engine = Engine(config=cfg)
        for spec in plugin_modules:
            engine.registry.register_spec(spec)
        return engine
    except ArtifactDiffError as exc:
        fail(str(exc))
    except ImportError as exc:
        fail(f"Cannot import plugin module: {exc}")


def _write(text: str, output: Path | None, console: Console) -> None:
    if output is None:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        sys.stdout.flush()
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    console.print(f"[dim]report written to {escape(str(output))}[/]")


def _sev_ok(sev: ChangeSeverity | None, fail_on: ChangeSeverity | None) -> bool:
    """True if this severity should NOT fail the run."""
    if sev is None:
        return True
    return fail_on is not None and sev.rank < fail_on.rank


def exit_code_for_results(
    results: list[ComparisonResult], fail_on: ChangeSeverity | None, file_level_changes: bool = False
) -> int:
    if any(r.status in (ComparisonStatus.ERROR, ComparisonStatus.UNSUPPORTED) for r in results):
        return EXIT_FAILED
    if file_level_changes and fail_on is None:
        return EXIT_DIFF
    for r in results:
        if r.status == ComparisonStatus.DIFFERENT and not _sev_ok(r.statistics.max_severity, fail_on):
            return EXIT_DIFF
    return EXIT_OK


# ---------------------------------------------------------------------------------- diff
@app.command()
def diff(
    old: Annotated[Path, typer.Argument(help="Old file or directory.")],
    new: Annotated[Path, typer.Argument(help="New file or directory.")],
    plugin: Annotated[str | None, typer.Option("--plugin", "-p", help="Force a plugin (see `plugins list`).")] = None,
    option: Annotated[
        list[str] | None, typer.Option("--option", "-O", help="Plugin option key=value (repeatable).")
    ] = None,
    options_file: Annotated[Path | None, typer.Option(help="JSON file with plugin options.")] = None,
    fmt: Annotated[OutputFormat, typer.Option("--format", "-f", help="Output format.")] = OutputFormat.text,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write the report to a file.")] = None,
    recursive: Annotated[bool, typer.Option("--recursive", "-r", help="Compare two directories.")] = False,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", "-x", help="Glob to skip (repeatable).")] = None,
    fail_on: Annotated[Severity | None, typer.Option(help="Exit 1 only for changes at/above this severity.")] = None,
    exit_zero: Annotated[bool, typer.Option(help="Exit 0 even when differences are found.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="No terminal output; use the exit code.")] = False,
    verbose: Annotated[bool, typer.Option("--details", "-d", help="Show every change with evidence.")] = False,
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help=f"Config file (default: {CONFIG_FILENAME}).")
    ] = None,
    max_changes: Annotated[int | None, typer.Option(help="Changes kept in the result.")] = None,
    timeout: Annotated[float | None, typer.Option(help="Time limit per comparison in seconds.")] = None,
    max_file_size_mb: Annotated[float | None, typer.Option(help="Reject inputs larger than this.")] = None,
    isolate: Annotated[bool, typer.Option(help="Run in a worker process (hard timeout, crash isolation).")] = False,
    no_assets: Annotated[bool, typer.Option("--no-assets", help="Skip generating visualizations.")] = False,
    plugin_module: Annotated[list[str] | None, typer.Option(help="Load a trusted plugin 'pkg.mod:Class'.")] = None,
) -> None:
    """Compare two artifacts (or two directories with --recursive)."""
    cfg = _load_config(config)
    if max_changes is not None:
        cfg.limits.max_changes = max_changes
    if timeout is not None:
        cfg.limits.timeout_seconds = timeout
    if max_file_size_mb is not None:
        cfg.limits.max_file_size_mb = max_file_size_mb
    engine = _engine(cfg, plugin_module or [])
    severity = ChangeSeverity(fail_on.value) if fail_on else cfg.fail_on
    console = _console()

    try:
        opts: dict[str, Any] = load_options_file(options_file) if options_file else {}
        if option:
            model = engine.registry.get_class(plugin).Options if plugin else None
            if model is None:  # resolve the plugin that would be selected, to type list options
                model = _guess_options_model(engine, old, new, recursive)
            opts.update(parse_option_pairs(option, model))
    except ArtifactDiffError as exc:
        fail(str(exc))

    if recursive or (old.is_dir() and new.is_dir()):
        if not (old.is_dir() and new.is_dir()):
            fail("--recursive needs two directories")
        try:
            report = compare_directories(
                old,
                new,
                engine=engine,
                exclude=exclude or [],
                plugin=plugin,
                options=opts or None,
                generate_assets=not no_assets,
            )
        except ArtifactDiffError as exc:
            fail(str(exc))
        results = [e.result for e in report.entries if e.result is not None]
        file_level = any(
            e.file_status in (FileEntryStatus.ADDED, FileEntryStatus.REMOVED, FileEntryStatus.RENAMED)
            for e in report.entries
        )
        errors = any(e.result is None and e.note and e.file_status == FileEntryStatus.MODIFIED for e in report.entries)
        _emit_report(report, fmt, output, quiet, verbose, console)
        code = exit_code_for_results(results, severity, file_level)
        raise typer.Exit(EXIT_FAILED if errors else (EXIT_OK if exit_zero and code == EXIT_DIFF else code))

    for p in (old, new):
        if p.is_dir():
            fail(f"{p} is a directory; pass --recursive to compare directories")
    try:
        if isolate:
            from artifactdiff.isolation import IsolatedRequest, run_isolated

            result = run_isolated(
                IsolatedRequest(str(old), str(new), plugin, opts, old.name, new.name, not no_assets), cfg
            )
        else:
            result = engine.compare(old, new, plugin=plugin, options=opts, generate_assets=not no_assets)
    except (ComparisonTimeout, ComparisonCancelled) as exc:
        fail(str(exc), EXIT_FAILED)
    except ArtifactDiffError as exc:
        # In isolated mode errors arrive as WorkerError carrying the original exception name.
        kind = getattr(exc, "kind", None)
        usage = kind is None or kind in _USAGE_ERRORS
        fail(str(exc), EXIT_USAGE if usage else EXIT_FAILED)

    if not quiet or output is not None:
        if fmt == OutputFormat.text and output is None:
            print_result(console, result, verbose=verbose)
        else:
            _write(render_result(result, "text" if fmt == OutputFormat.text else fmt.value), output, err)
    code = exit_code_for_results([result], severity)
    raise typer.Exit(EXIT_OK if exit_zero and code == EXIT_DIFF else code)


def _guess_options_model(engine: Engine, old: Path, new: Path, recursive: bool) -> type[Any] | None:
    if recursive or not (old.is_file() and new.is_file()):
        return None
    from artifactdiff.files import ArtifactFile

    try:
        sel = engine.registry.select(ArtifactFile(old), ArtifactFile(new))
    except ArtifactDiffError:
        return None
    return type(sel.plugin).Options if sel else None


def _emit_report(
    report: Report, fmt: OutputFormat, output: Path | None, quiet: bool, verbose: bool, console: Console
) -> None:
    if quiet and output is None:
        return
    if fmt == OutputFormat.text and output is None:
        print_report(console, report, verbose=verbose)
    else:
        _write(render_report(report, fmt.value), output, err)


# ------------------------------------------------------------------------------- inspect
@app.command()
def inspect(
    file: Annotated[Path, typer.Argument(help="File to inspect.")],
    plugin: Annotated[str | None, typer.Option("--plugin", "-p")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Show what ArtifactDiff detects in a single file."""
    engine = _engine(_load_config(config), [])
    try:
        meta = engine.inspect(file, plugin=plugin)
    except ArtifactDiffError as exc:
        fail(str(exc))
    if as_json:
        _write(meta.model_dump_json(indent=2), None, err)
        return
    console = _console()
    table = Table(show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column(overflow="fold")
    table.add_row("file", escape(meta.name))
    table.add_row("size", f"{meta.size_bytes:,} bytes")
    table.add_row("sha256", meta.sha256)
    table.add_row("format", f"{meta.format.name if meta.format else '?'} (detected by {meta.detected_by})")
    for k, v in meta.properties.items():
        table.add_row(escape(str(k)), escape(json.dumps(v, default=str, ensure_ascii=False)[:300]))
    console.print(table)


# ------------------------------------------------------------------------------ plugins
@plugins_app.command("list")
def plugins_list(
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """List registered plugins and discovered (not loaded) third-party plugins."""
    cfg = _load_config(config)
    engine = _engine(cfg, [])
    metas = engine.registry.all_metadata()
    discovered = engine.registry.discover_entry_points(set(cfg.plugins.enabled))
    if as_json:
        payload = {
            "plugins": [m.model_dump(mode="json", exclude={"options_schema"}) for m in metas],
            "discovered": [d.__dict__ for d in discovered],
        }
        _write(json.dumps(payload, indent=2), None, err)
        return
    console = _console()
    table = Table(header_style="dim")
    for col in ("plugin", "version", "extensions", "status", "source"):
        table.add_column(col)
    for m in metas:
        exts = ", ".join(m.formats[0].extensions) if m.formats and m.formats[0].extensions else "(any)"
        status = (
            "[green]available[/]"
            if m.available
            else f"[red]unavailable[/] [dim]{escape(m.unavailable_reason or '')}[/]"
        )
        table.add_row(m.name, m.version, escape(exts), status, "built-in" if m.builtin else "external")
    console.print(table)
    for d in discovered:
        if d.name not in engine.registry.names():
            console.print(
                f"[yellow]discovered[/] {d.name} ({escape(d.target)}) from {d.distribution} — "
                f"not loaded; add it to plugins.enabled in {CONFIG_FILENAME} to use it"
            )


@plugins_app.command("info")
def plugins_info(
    name: Annotated[str, typer.Argument(help="Plugin name.")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show plugin details, including its options."""
    engine = _engine(_load_config(None), [])
    try:
        meta = engine.registry.metadata(name)
    except ArtifactDiffError as exc:
        fail(str(exc))
    if as_json:
        _write(meta.model_dump_json(indent=2), None, err)
        return
    console = _console()
    console.print(f"[bold]{meta.display_name}[/] [dim]({meta.name} {meta.version}, plugin API {meta.api_version})[/]")
    console.print(escape(meta.description))
    fmt = meta.formats[0]
    console.print(f"[dim]extensions:[/] {', '.join(fmt.extensions) or '(any)'}")
    console.print(f"[dim]capabilities:[/] {', '.join(meta.capabilities)}")
    console.print(f"[dim]status:[/] {'available' if meta.available else escape(meta.unavailable_reason or '')}")
    props = meta.options_schema.get("properties", {})
    if props:
        table = Table(title="options (-O key=value)", header_style="dim", title_justify="left")
        for col in ("option", "type", "default", "description"):
            table.add_column(col, overflow="fold")
        for key, spec in props.items():
            typ = (
                spec.get("type")
                or " | ".join(str(x.get("type", x.get("enum", "?"))) for x in spec.get("anyOf", []))
                or str(spec.get("enum", ""))
            )
            if "enum" in spec:
                typ = " | ".join(map(str, spec["enum"]))
            table.add_row(
                key, escape(str(typ)), escape(json.dumps(spec.get("default"))), escape(spec.get("description", ""))
            )
        console.print(table)
    for t in meta.limitations:
        console.print(f"[dim]limitation:[/] {escape(t)}")


@plugins_app.command("new")
def plugins_new(
    name: Annotated[str, typer.Argument(help="Plugin name, e.g. 'my-format'.")],
    extension: Annotated[list[str] | None, typer.Option("--extension", "-e", help="File extension, e.g. .myf")] = None,
    dest: Annotated[Path, typer.Option(help="Parent directory for the new project.")] = Path("."),
) -> None:
    """Generate a plugin project from the SDK template."""
    from artifactdiff_sdk.scaffold import create_plugin_project

    try:
        path = create_plugin_project(name, dest, extensions=extension or None)
    except (ArtifactDiffError, FileExistsError, ValueError) as exc:
        fail(str(exc))
    _console().print(
        f"[green]created[/] {escape(str(path))}\nnext: cd {escape(str(path))} && pip install -e . && pytest"
    )


@plugins_app.command("validate")
def plugins_validate(spec: Annotated[str, typer.Argument(help="'package.module:PluginClass' (imports it).")]) -> None:
    """Import a trusted plugin class and check it against the plugin contract."""
    from artifactdiff_sdk.testing import validate_plugin

    try:
        problems = validate_plugin(spec)
    except (ArtifactDiffError, ImportError, AttributeError) as exc:
        fail(str(exc))
    if problems:
        for p in problems:
            err.print(f"[red]✗[/] {escape(p)}")
        raise typer.Exit(EXIT_FAILED)
    _console().print(f"[green]✓[/] {escape(spec)} satisfies the plugin contract")


# ------------------------------------------------------------------------------- report
@app.command()
def report(
    file: Annotated[Path, typer.Argument(help="A JSON result or report produced by ArtifactDiff.")],
    fmt: Annotated[OutputFormat, typer.Option("--format", "-f")] = OutputFormat.text,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Re-render a saved JSON result as text, Markdown, HTML or JSON."""
    try:
        doc = load_document(file)
    except (ArtifactDiffError, OSError) as exc:
        fail(str(exc))
    _emit_report(doc, fmt, output, False, False, _console())


# ------------------------------------------------------------------------------- config
@config_app.command("init")
def config_init(
    path: Annotated[Path, typer.Option(help="Where to write the file.")] = Path(CONFIG_FILENAME),
    force: Annotated[bool, typer.Option(help="Overwrite an existing file.")] = False,
) -> None:
    """Write a commented default configuration file."""
    if path.exists() and not force:
        fail(f"{path} already exists (use --force to overwrite)")
    path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    _console().print(f"[green]wrote[/] {escape(str(path))}")


@config_app.command("show")
def config_show(config: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    """Print the effective configuration as JSON."""
    cfg = _load_config(config)
    data = cfg.model_dump(mode="json")
    data["_source"] = cfg.source or "(defaults)"
    _write(json.dumps(data, indent=2), None, err)


# ------------------------------------------------------------------------------ version
@app.command()
def version() -> None:
    """Print the ArtifactDiff version."""
    sys.stdout.write(f"artifactdiff {__version__}\n")


# ------------------------------------------------------------------------------- doctor
@app.command()
def doctor() -> None:
    """Check the installation: Python, optional dependencies, plugins and configuration."""
    from artifactdiff_cli.doctor import run_doctor

    raise typer.Exit(run_doctor(_console()))


# --------------------------------------------------------------------------------- demo
@app.command()
def demo(
    examples_dir: Annotated[Path | None, typer.Option(help="Directory containing example pairs.")] = None,
    output_dir: Annotated[
        Path | None, typer.Option(help="Write an HTML/Markdown/JSON report per example here.")
    ] = None,
    check: Annotated[bool, typer.Option(help="Fail if results differ from expected.json files.")] = False,
) -> None:
    """Run every bundled example comparison."""
    from artifactdiff_cli.demo import run_demo

    raise typer.Exit(run_demo(_console(), examples_dir, output_dir, check))


# -------------------------------------------------------------------------------- serve
@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address (default: local only).")] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
) -> None:
    """Start the local web UI and REST API (requires the 'api' extra)."""
    try:
        import uvicorn

        from artifactdiff_api.app import create_app
    except ImportError:
        fail('The web UI needs the API extra: pip install "artifactdiff[api]"')
    os.environ.setdefault("ARTIFACTDIFF_HOST", host)
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    app()
