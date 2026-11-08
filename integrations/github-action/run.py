"""ArtifactDiff GitHub Action runner.

Reads the files changed between two commits straight from git objects (``git cat-file``) — the
pull request's code is never executed — compares each supported artifact, writes JSON/Markdown/
HTML reports, adds a job summary, and creates or updates one pull-request comment.

Configuration comes from ``INPUT_*`` environment variables set by ``action.yml``. Every value is
passed to git as a separate argv element (no shell), and refs are validated before use.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from artifactdiff import __version__
from artifactdiff.config import ArtifactDiffConfig, Limits, load_config
from artifactdiff.directory import build_report, is_excluded
from artifactdiff.engine import Engine
from artifactdiff.errors import ArtifactDiffError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeSeverity, ComparisonStatus, FileEntryStatus, Report, ReportEntry
from artifactdiff.registry import FALLBACK_PLUGINS
from artifactdiff.reporting import render_report
from artifactdiff.reporting.markdown import MARKER

REF_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._/\-^~@{}]{0,250}$")
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"
COMMENT_LIMIT = 65_000  # GitHub's limit is 65,536 characters


def env(name: str, default: str = "") -> str:
    return os.environ.get(f"INPUT_{name.upper().replace('-', '_')}", default).strip()


def env_list(name: str) -> list[str]:
    raw = env(name)
    return [p.strip() for p in re.split(r"[\n,]", raw) if p.strip()]


def env_bool(name: str, default: bool) -> bool:
    raw = env(name, str(default)).lower()
    return raw in ("1", "true", "yes", "on")


def log(msg: str) -> None:
    print(msg, flush=True)


def annotate(level: str, msg: str) -> None:
    # Workflow commands: neutralise characters that would end or inject a command.
    safe = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{safe}", flush=True)


# ---------------------------------------------------------------------------------- git
class Git:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def run(self, *args: str, binary: bool = False) -> Any:
        res = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=self.cwd, capture_output=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {res.stderr.decode('utf-8', 'replace').strip()}")
        return res.stdout if binary else res.stdout.decode("utf-8", "surrogateescape")

    def resolve(self, ref: str) -> str:
        if not REF_RE.match(ref):
            raise ValueError(f"Invalid git ref: {ref!r}")
        return str(self.run("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")).strip()

    def changes(self, base: str, head: str) -> list[tuple[str, str | None, str]]:
        """(status, old_path, new_path) between merge-base(base, head) and head."""
        out: bytes = self.run(
            "diff", "--name-status", "-z", "--no-color", "-M", "--no-ext-diff", f"{base}...{head}", "--", binary=True
        )
        parts = out.decode("utf-8", "surrogateescape").split("\0")
        items: list[tuple[str, str | None, str]] = []
        i = 0
        while i < len(parts) and parts[i]:
            status = parts[i]
            if status[0] in "RC":
                items.append((status, parts[i + 1], parts[i + 2]))
                i += 3
            else:
                items.append((status, None, parts[i + 1]))
                i += 2
        return items

    def merge_base(self, base: str, head: str) -> str:
        return str(self.run("merge-base", base, head)).strip()

    def blob_size(self, rev: str, path: str) -> int:
        return int(str(self.run("cat-file", "-s", f"{rev}:{path}")).strip())

    def blob_to_file(self, rev: str, path: str, dest: Path) -> None:
        with dest.open("wb") as fh:
            res = subprocess.run(
                ["git", "cat-file", "blob", f"{rev}:{path}"],
                cwd=self.cwd,
                stdout=fh,
                stderr=subprocess.PIPE,
                check=False,
            )
        if res.returncode != 0:
            raise RuntimeError(f"git cat-file failed for {path}: {res.stderr.decode('utf-8', 'replace').strip()}")


# ------------------------------------------------------------------------------ settings
@dataclass
class Settings:
    base: str
    head: str
    include: list[str]
    exclude: list[str]
    comment: bool
    fail_on: ChangeSeverity | None
    fail_on_error: bool
    max_files: int
    max_file_size_mb: float
    include_text: bool
    output_dir: Path
    token: str
    repository: str
    api_url: str
    event: dict[str, Any]


def read_settings() -> Settings:
    event: dict[str, Any] = {}
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).is_file():
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pr = event.get("pull_request") or {}
    base = env("base") or pr.get("base", {}).get("sha", "") or event.get("before", "")
    head = env("head") or pr.get("head", {}).get("sha", "") or os.environ.get("GITHUB_SHA", "HEAD")
    if not base:
        raise ValueError("No base commit: set the 'base' input (e.g. ${{ github.event.pull_request.base.sha }})")
    fail_on = env("fail-on").lower()
    return Settings(
        base=base,
        head=head,
        include=env_list("paths"),
        exclude=env_list("exclude"),
        comment=env_bool("comment-on-pr", True),
        fail_on=ChangeSeverity(fail_on) if fail_on else None,
        fail_on_error=env_bool("fail-on-error", False),
        max_files=int(env("max-files", "50") or 50),
        max_file_size_mb=float(env("max-file-size-mb", "200") or 200),
        include_text=env_bool("include-text", False),
        output_dir=Path(env("output-dir", "artifactdiff-report") or "artifactdiff-report"),
        token=os.environ.get("GITHUB_TOKEN", env("github-token")),
        repository=os.environ.get("GITHUB_REPOSITORY", ""),
        api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
        event=event,
    )


# ---------------------------------------------------------------------------- compare
def is_supported(engine: Engine, path: Path, display: str, include_text: bool) -> bool:
    ranked = engine.registry.available_for(ArtifactFile(path, display))
    return any(include_text or name not in FALLBACK_PLUGINS for name, _ in ranked)


def wanted(path: str, s: Settings) -> bool:
    if s.include and not any(
        fnmatch.fnmatchcase(path, p) or fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], p) for p in s.include
    ):
        return False
    return not is_excluded(path, s.exclude)


def collect(s: Settings, git: Git, engine: Engine, work: Path) -> Report:
    base = git.resolve(s.base)
    head = git.resolve(s.head)
    mb = git.merge_base(base, head)
    entries: list[ReportEntry] = []
    limit = int(s.max_file_size_mb * 1024 * 1024)
    skipped_unsupported = 0
    considered = 0
    for idx, (status, old_path, new_path) in enumerate(git.changes(base, head)):
        kind = status[0]
        src_path = old_path or new_path
        if not wanted(new_path, s) and not wanted(src_path, s):
            continue
        ext = Path(new_path).suffix.lower()[:16]
        if not re.fullmatch(r"\.[a-z0-9]{0,15}", ext):
            ext = ""
        if kind == "T":
            continue  # type change (e.g. file <-> symlink): nothing meaningful to compare
        if considered >= s.max_files:
            entries.append(
                ReportEntry(
                    path=new_path,
                    file_status=FileEntryStatus.MODIFIED,
                    note=f"Skipped: max-files ({s.max_files}) reached",
                )
            )
            continue
        try:
            if kind in ("A", "D"):
                rev = head if kind == "A" else mb
                size = git.blob_size(rev, new_path)
                probe = work / f"{idx}-probe{ext}"
                if size <= limit:
                    git.blob_to_file(rev, new_path, probe)
                    if not is_supported(engine, probe, new_path, s.include_text):
                        skipped_unsupported += 1
                        continue
                considered += 1
                entries.append(
                    ReportEntry(
                        path=new_path, file_status=FileEntryStatus.ADDED if kind == "A" else FileEntryStatus.REMOVED
                    )
                )
                continue
            # modified, renamed or copied
            pair_old = src_path
            file_status = FileEntryStatus.RENAMED if kind == "R" else FileEntryStatus.MODIFIED
            old_size, new_size = git.blob_size(mb, pair_old), git.blob_size(head, new_path)
            if max(old_size, new_size) > limit:
                considered += 1
                entries.append(
                    ReportEntry(
                        path=new_path,
                        old_path=pair_old if pair_old != new_path else None,
                        file_status=file_status,
                        note=f"Skipped: larger than max-file-size-mb ({s.max_file_size_mb:g})",
                    )
                )
                continue
            a, b = work / f"{idx}-old{ext}", work / f"{idx}-new{ext}"
            git.blob_to_file(mb, pair_old, a)
            git.blob_to_file(head, new_path, b)
            if any(p.read_bytes()[: len(LFS_POINTER)] == LFS_POINTER for p in (a, b)):
                considered += 1
                entries.append(
                    ReportEntry(
                        path=new_path,
                        file_status=FileEntryStatus.MODIFIED,
                        note="Stored in Git LFS: only the pointer file is in git history. "
                        "Run actions/checkout with lfs: true and compare the working tree.",
                    )
                )
                continue
            if not is_supported(engine, b, new_path, s.include_text):
                skipped_unsupported += 1
                continue
            considered += 1
            result = engine.compare(a, b, old_name=pair_old, new_name=new_path)
            if result.status == ComparisonStatus.IDENTICAL and kind == "R":
                entries.append(
                    ReportEntry(
                        path=new_path,
                        old_path=pair_old,
                        file_status=file_status,
                        note="Renamed without content changes",
                    )
                )
                continue
            entries.append(
                ReportEntry(
                    path=new_path,
                    old_path=pair_old if pair_old != new_path else None,
                    file_status=file_status,
                    result=result,
                )
            )
        except (ArtifactDiffError, RuntimeError, OSError) as exc:
            considered += 1
            entries.append(ReportEntry(path=new_path, file_status=FileEntryStatus.MODIFIED, note=f"Error: {exc}"))
    title = f"ArtifactDiff: {len(entries)} artifact(s) changed"
    report = build_report(
        entries,
        title=title,
        context={
            "base": base,
            "head": head,
            "merge_base": mb,
            "repository": s.repository,
            "skipped_unsupported": skipped_unsupported,
            "artifactdiff_version": __version__,
        },
    )
    return report


# ----------------------------------------------------------------------------- GitHub
def github(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"artifactdiff-action/{__version__}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = resp.read()
    return json.loads(payload) if payload else None


def upsert_comment(s: Settings, body: str) -> str:
    pr = (s.event.get("pull_request") or {}).get("number") or (s.event.get("issue") or {}).get("number")
    if not pr:
        return "not a pull request event; comment skipped"
    if not (s.token and s.repository):
        return "no token or repository; comment skipped"
    base = f"{s.api_url}/repos/{s.repository}/issues"
    existing = None
    page = 1
    while page <= 10:
        comments = github("GET", f"{base}/{pr}/comments?per_page=100&page={page}", s.token) or []
        for c in comments:
            if MARKER in (c.get("body") or "") and (c.get("user") or {}).get("type") == "Bot":
                existing = c["id"]
        if len(comments) < 100:
            break
        page += 1
    if existing:
        github("PATCH", f"{base}/comments/{existing}", s.token, {"body": body})
        return f"updated comment {existing}"
    created = github("POST", f"{base}/{pr}/comments", s.token, {"body": body})
    return f"created comment {created.get('id') if created else '?'}"


def write_outputs(values: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for k, v in values.items():
            fh.write(f"{k}={v}\n")


# ------------------------------------------------------------------------------- main
def main() -> int:
    try:
        s = read_settings()
    except ValueError as exc:
        annotate("error", str(exc))
        return 2
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd()))
    cfg_path = workspace / "artifactdiff.toml"
    try:
        cfg = load_config(cfg_path) if cfg_path.is_file() else ArtifactDiffConfig()
    except ArtifactDiffError as exc:
        annotate("error", f"Invalid artifactdiff.toml: {exc}")
        return 2
    # Repository config may tune plugins, but third-party plugin loading is never honoured here:
    # it would execute code chosen by the pull request.
    cfg.plugins.enabled = []
    cfg.plugins.modules = []
    cfg.limits = Limits(**{**cfg.limits.model_dump(), "max_file_size_mb": s.max_file_size_mb})
    s.exclude = [*cfg.exclude, *s.exclude]
    engine = Engine(config=cfg)
    git = Git(workspace)
    with tempfile.TemporaryDirectory(prefix="artifactdiff-") as tmp:
        try:
            report = collect(s, git, engine, Path(tmp))
        except (RuntimeError, ValueError) as exc:
            annotate("error", str(exc))
            return 2
    s.output_dir.mkdir(parents=True, exist_ok=True)
    md = render_report(report, "markdown", max_chars=COMMENT_LIMIT)
    (s.output_dir / "report.md").write_text(md, encoding="utf-8")
    (s.output_dir / "report.html").write_text(render_report(report, "html"), encoding="utf-8")
    (s.output_dir / "report.json").write_text(render_report(report, "json"), encoding="utf-8")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    sm = report.summary
    log(f"ArtifactDiff: {sm.files} artifact(s), {sm.total_changes} change(s), {sm.errors} error(s)")
    if s.comment and report.entries:
        try:
            log(upsert_comment(s, md))
        except urllib.error.HTTPError as exc:
            hint = (
                " (pull requests from forks get a read-only token; see docs/github-action.md)"
                if exc.code in (401, 403, 404)
                else ""
            )
            annotate("warning", f"Could not post the pull-request comment: HTTP {exc.code}{hint}")
        except (urllib.error.URLError, TimeoutError) as exc:
            annotate("warning", f"Could not post the pull-request comment: {exc}")
    write_outputs(
        {
            "total-changes": str(sm.total_changes),
            "max-severity": sm.max_severity.value if sm.max_severity else "",
            "changed-artifacts": str(sm.files),
            "errors": str(sm.errors),
            "report-dir": str(s.output_dir),
        }
    )
    if s.fail_on_error and sm.errors:
        annotate("error", f"{sm.errors} artifact comparison(s) failed")
        return 1
    if s.fail_on and sm.max_severity and sm.max_severity.rank >= s.fail_on.rank:
        annotate("error", f"Changes with severity '{sm.max_severity.value}' found (fail-on: {s.fail_on.value})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
