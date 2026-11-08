"""Recursive directory comparison and multi-file report assembly."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from artifactdiff import __version__
from artifactdiff.engine import Engine
from artifactdiff.errors import ArtifactDiffError
from artifactdiff.models import (
    ChangeSeverity,
    ComparisonStatus,
    FileEntryStatus,
    Report,
    ReportEntry,
    ReportSummary,
    max_severity,
)


def is_excluded(rel_path: str, patterns: list[str]) -> bool:
    """Match a POSIX relative path against glob patterns (``*`` also matches ``/``)."""
    name = rel_path.rsplit("/", 1)[-1]
    for pat in patterns:
        pat = pat.strip()
        if not pat:
            continue
        if pat.endswith("/"):  # directory pattern
            if rel_path.startswith(pat) or f"/{pat}" in f"/{rel_path}":
                return True
            continue
        if fnmatch.fnmatchcase(rel_path, pat) or fnmatch.fnmatchcase(name, pat):
            return True
        if pat.startswith("**/") and fnmatch.fnmatchcase(rel_path, pat[3:]):
            return True
    return False


def _walk(root: Path, exclude: list[str]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        dirnames[:] = [
            d for d in dirnames if not is_excluded((f"{rel_dir}/{d}" if rel_dir != "." else d) + "/", exclude)
        ]
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            if full.is_symlink() or not full.is_file():
                continue  # never follow symlinks out of the compared trees
            rel = full.relative_to(root).as_posix()
            if not is_excluded(rel, exclude):
                files[rel] = full
    return files


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def compare_directories(
    old_dir: str | os.PathLike[str],
    new_dir: str | os.PathLike[str],
    *,
    engine: Engine | None = None,
    exclude: list[str] | None = None,
    plugin: str | None = None,
    options: dict[str, Any] | None = None,
    include_unchanged: bool = False,
    generate_assets: bool = True,
) -> Report:
    engine = engine or Engine()
    patterns = [*(engine.config.exclude), *(exclude or [])]
    old_files = _walk(Path(old_dir), patterns)
    new_files = _walk(Path(new_dir), patterns)

    entries: list[ReportEntry] = []
    removed = sorted(set(old_files) - set(new_files))
    added = sorted(set(new_files) - set(old_files))

    # Exact renames: a removed and an added file with identical bytes, matched uniquely.
    removed_by_hash: dict[str, list[str]] = {}
    for rel in removed:
        removed_by_hash.setdefault(_sha(old_files[rel]), []).append(rel)
    renamed: dict[str, str] = {}
    for rel in added:
        candidates = removed_by_hash.get(_sha(new_files[rel]), [])
        if len(candidates) == 1:
            renamed[rel] = candidates.pop()
    for new_rel, old_rel in sorted(renamed.items()):
        entries.append(
            ReportEntry(
                path=new_rel,
                old_path=old_rel,
                file_status=FileEntryStatus.RENAMED,
                note="Byte-identical content; detected as a rename",
            )
        )
    gone = set(renamed.values())
    entries += [ReportEntry(path=r, file_status=FileEntryStatus.REMOVED) for r in removed if r not in gone]
    entries += [ReportEntry(path=r, file_status=FileEntryStatus.ADDED) for r in added if r not in renamed]

    for rel in sorted(set(old_files) & set(new_files)):
        try:
            result = engine.compare(
                old_files[rel],
                new_files[rel],
                plugin=plugin,
                options=options,
                old_name=rel,
                new_name=rel,
                generate_assets=generate_assets,
            )
        except ArtifactDiffError as exc:
            entries.append(ReportEntry(path=rel, file_status=FileEntryStatus.MODIFIED, note=str(exc)))
            continue
        if result.status == ComparisonStatus.IDENTICAL:
            if include_unchanged:
                entries.append(ReportEntry(path=rel, file_status=FileEntryStatus.UNCHANGED, result=result))
            continue
        entries.append(ReportEntry(path=rel, file_status=FileEntryStatus.MODIFIED, result=result))

    entries.sort(key=lambda e: e.path)
    return build_report(
        entries,
        title="ArtifactDiff directory report",
        context={"old": str(old_dir), "new": str(new_dir), "exclude": patterns},
    )


def build_report(entries: list[ReportEntry], *, title: str, context: dict[str, Any] | None = None) -> Report:
    summary = ReportSummary(files=len(entries))
    severities: list[ChangeSeverity] = []
    for e in entries:
        if e.file_status == FileEntryStatus.ADDED:
            summary.added_files += 1
        elif e.file_status == FileEntryStatus.REMOVED:
            summary.removed_files += 1
        r = e.result
        if r is None:
            if e.note and e.file_status == FileEntryStatus.MODIFIED:
                summary.errors += 1
            continue
        summary.compared += 1
        if r.status == ComparisonStatus.DIFFERENT:
            summary.with_changes += 1
        elif r.status == ComparisonStatus.ERROR:
            summary.errors += 1
        elif r.status == ComparisonStatus.UNSUPPORTED:
            summary.unsupported += 1
        summary.total_changes += r.statistics.total_changes
        if r.statistics.max_severity:
            severities.append(r.statistics.max_severity)
    summary.max_severity = max_severity(severities)
    return Report(
        artifactdiff_version=__version__,
        generated_at=datetime.now(UTC),
        title=title,
        context=context or {},
        entries=entries,
        summary=summary,
    )
