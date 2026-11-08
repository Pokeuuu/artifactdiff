"""``artifactdiff doctor``: environment diagnostics."""

from __future__ import annotations

import importlib.util
import platform
import sys
import tempfile

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from artifactdiff import __version__
from artifactdiff.config import find_config_file, load_config
from artifactdiff.errors import ArtifactDiffError
from artifactdiff.registry import PluginRegistry, default_registry


def run_doctor(console: Console) -> int:
    problems = 0
    console.print(
        f"[bold]artifactdiff {__version__}[/] on Python {platform.python_version()} "
        f"({sys.platform}, {platform.machine()})"
    )

    cfg_path = find_config_file()
    try:
        cfg = load_config(cfg_path) if cfg_path else load_config(search=False)
        console.print(f"[green]✓[/] configuration: {escape(str(cfg_path)) if cfg_path else 'defaults (no file found)'}")
    except ArtifactDiffError as exc:
        console.print(f"[red]✗ configuration: {escape(str(exc))}[/]")
        problems += 1
        cfg = None

    try:
        with tempfile.TemporaryFile() as fh:
            fh.write(b"ok")
        console.print("[green]✓[/] temporary directory is writable")
    except OSError as exc:
        console.print(f"[red]✗ temporary directory not writable: {escape(str(exc))}[/]")
        problems += 1

    reg = default_registry()
    table = Table(header_style="dim", title="plugins", title_justify="left")
    table.add_column("plugin")
    table.add_column("status")
    for meta in reg.all_metadata():
        status = (
            "[green]ready[/]" if meta.available else f"[yellow]missing deps[/] {escape(meta.unavailable_reason or '')}"
        )
        table.add_row(meta.name, status)
    console.print(table)

    if importlib.util.find_spec("soundfile") is not None:
        try:
            import soundfile as sf

            fmts = sf.available_formats()
            mp3 = "MP3" in fmts
            console.print(
                f"[green]✓[/] libsndfile {sf.__libsndfile_version__}"
                f" (MP3 decoding {'available' if mp3 else '[yellow]not available[/]'})"
            )
        except (OSError, ImportError) as exc:
            console.print(f"[yellow]! soundfile installed but libsndfile failed to load: {escape(str(exc))}[/]")

    api_ok = all(importlib.util.find_spec(m) is not None for m in ("fastapi", "sqlalchemy", "uvicorn", "alembic"))
    console.print(
        "[green]✓[/] web UI / API dependencies installed"
        if api_ok
        else '[dim]- web UI / API not installed (pip install "artifactdiff\\[api]")[/]'
    )

    enabled = set(cfg.plugins.enabled) if cfg else set()
    for d in PluginRegistry.discover_entry_points(enabled):
        state = "enabled" if d.enabled else "discovered, not enabled"
        console.print(f"[cyan]•[/] third-party plugin {d.name} from {d.distribution}: {state}")

    console.print("[green]All checks passed.[/]" if not problems else f"[red]{problems} problem(s) found.[/]")
    return 0 if not problems else 1
