from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import write

from artifactdiff.config import ArtifactDiffConfig, Limits, load_config
from artifactdiff.directory import is_excluded
from artifactdiff.engine import Engine
from artifactdiff.errors import ConfigError
from artifactdiff.isolation import IsolatedRequest, WorkerError, run_isolated
from artifactdiff.models import ComparisonStatus, Report
from artifactdiff.reporting import load_document, render_report, render_result, result_as_report
from artifactdiff.reporting.markdown import esc, render_markdown

SCHEMA = Path(__file__).resolve().parents[2] / "docs" / "schema" / "report.schema.json"


def test_markdown_escapes_untrusted_content(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", json.dumps({"k": "safe"}))
    b = write(tmp_path / "b.json", json.dumps({"k": "<img src=x onerror=alert(1)> @octocat | `x`"}))
    md = render_result(engine.compare(a, b, new_name="<script>.json"), "markdown")
    assert "<img" not in md and "<script>" not in md
    assert "@octocat" not in md  # mention neutralised
    assert esc("a|b") == "a\\|b"


def test_markdown_truncation(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", json.dumps({str(i): i for i in range(500)}))
    b = write(tmp_path / "b.json", json.dumps({str(i): -i for i in range(500)}))
    rep = result_as_report(engine.compare(a, b))
    md = render_markdown(rep, max_changes=1000, max_chars=5000)
    assert len(md) <= 5000 and "truncated" in md


def test_html_escapes_and_is_offline(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", json.dumps({"k": 1}))
    b = write(tmp_path / "b.json", json.dumps({"k": "</td><script>alert(1)</script>"}))
    html = render_result(engine.compare(a, b), "html")
    assert "<script>alert(1)" not in html
    assert "Content-Security-Policy" in html


def test_text_render(engine: Engine, tmp_path: Path) -> None:
    from samples import csv_pair

    out = render_result(engine.compare(*csv_pair(tmp_path)), "text")
    assert "row[id='3']" in out


def test_load_document_roundtrip(engine: Engine, tmp_path: Path) -> None:
    from samples import json_pair

    r = engine.compare(*json_pair(tmp_path))
    p = write(tmp_path / "r.json", render_result(r, "json"))
    rep = load_document(p)
    assert rep.entries[0].result == r
    p2 = write(tmp_path / "rep.json", render_report(rep, "json"))
    assert load_document(p2).entries[0].result == r


def test_report_schema_file_is_current() -> None:
    assert SCHEMA.exists(), "run scripts/export_schema.py"
    on_disk = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(Report.model_json_schema())), "run scripts/export_schema.py"


def test_config_file_and_pyproject(tmp_path: Path) -> None:
    (tmp_path / "artifactdiff.toml").write_text('exclude = ["*.log"]\nfail_on = "medium"\n[limits]\nmax_changes = 7\n')
    cfg = load_config(tmp_path / "artifactdiff.toml")
    assert cfg.exclude == ["*.log"] and cfg.limits.max_changes == 7 and cfg.fail_on is not None
    (tmp_path / "pyproject.toml").write_text('[tool.artifactdiff]\nexclude = ["x"]\n')
    assert load_config(tmp_path / "pyproject.toml").exclude == ["x"]
    (tmp_path / "bad.toml").write_text("[limits]\nmax_changes = -1\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "bad.toml")
    (tmp_path / "typo.toml").write_text("[limitz]\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "typo.toml")


def test_default_config_template_is_valid(tmp_path: Path) -> None:
    from artifactdiff.config import DEFAULT_CONFIG_TOML

    p = write(tmp_path / "artifactdiff.toml", DEFAULT_CONFIG_TOML)
    assert load_config(p) == load_config(p)


@pytest.mark.parametrize(
    ("path", "pattern", "hit"),
    [
        ("a/b/c.log", "*.log", True),
        ("c.log", "*.log", True),
        ("vendor/x.json", "vendor/", True),
        ("src/vendor/x.json", "vendor/", True),
        ("a/b.json", "**/b.json", True),
        ("a/b.json", "c.json", False),
    ],
)
def test_exclusions(path: str, pattern: str, hit: bool) -> None:
    assert is_excluded(path, [pattern]) is hit


def test_isolated_comparison(tmp_path: Path) -> None:
    from samples import json_pair

    a, b = json_pair(tmp_path)
    progress: list[float] = []
    r = run_isolated(IsolatedRequest(str(a), str(b)), progress=lambda f, m: progress.append(f))
    assert r.status == ComparisonStatus.DIFFERENT and progress
    assert r.fingerprint == Engine().compare(a, b).fingerprint


def test_isolated_errors_propagate(tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", "{}")
    with pytest.raises(WorkerError) as exc:
        run_isolated(
            IsolatedRequest(str(a), str(tmp_path / "missing.json")),
            ArtifactDiffConfig(limits=Limits(timeout_seconds=30)),
        )
    assert exc.value.kind == "InputFileError"
