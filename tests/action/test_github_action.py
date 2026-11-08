"""Runs the GitHub Action script against a real git repository and a fake GitHub API."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "integrations" / "github-action" / "run.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("artifactdiff_action_run", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # required by @dataclass in the loaded module
    spec.loader.exec_module(mod)
    return mod


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str, str]:
    import samples

    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "ci@example.invalid")
    git(r, "config", "user.name", "CI")
    git(r, "config", "commit.gpgsign", "false")
    src = tmp_path / "src"
    src.mkdir()
    a_json, b_json = samples.json_pair(src)
    a_csv, b_csv = samples.csv_pair(src)
    a_db, b_db = samples.sqlite_pair(src)
    (r / "config").mkdir()
    shutil.copy(a_json, r / "config" / "model.json")
    shutil.copy(a_csv, r / "data.csv")
    shutil.copy(a_db, r / "app.sqlite")
    shutil.copy(b_db, r / "rename-me.sqlite")  # distinct bytes from app.sqlite
    (r / "README.md").write_text("hello\n")
    (r / "skip.log").write_text("a\n")
    (r / "big.json").write_text(json.dumps({"x": "y" * 40000}))
    (r / "lfs.bin").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:aa\nsize 1\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    base = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "-b", "feature")
    shutil.copy(b_json, r / "config" / "model.json")
    shutil.copy(b_csv, r / "data.csv")
    (r / "app.sqlite").unlink()
    git(r, "mv", "rename-me.sqlite", "renamed.sqlite")
    (r / "README.md").write_text("hello world\n")
    (r / "skip.log").write_text("b\n")
    (r / "big.json").write_text(json.dumps({"x": "z" * 40000}))
    samples.parquet_pair(src)
    shutil.copy(src / "new.parquet", r / "new.parquet")
    (r / "lfs.bin").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:bb\nsize 2\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "change")
    head = git(r, "rev-parse", "HEAD")
    return r, base, head


class FakeGitHub(BaseHTTPRequestHandler):
    calls: list[tuple[str, str, Any]] = []
    existing: list[dict[str, Any]] = []
    status = 200

    def _reply(self, code: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        type(self).calls.append(("GET", self.path, None))
        self._reply(type(self).status, type(self).existing if type(self).status == 200 else {"message": "no"})

    def _write(self, method: str) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["Authorization"] == "Bearer test-token"
        type(self).calls.append((method, self.path, body))
        self._reply(201 if method == "POST" else 200, {"id": 99})

    def do_POST(self) -> None:
        self._write("POST")

    def do_PATCH(self) -> None:
        self._write("PATCH")

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def fake_api() -> Any:
    FakeGitHub.calls = []
    FakeGitHub.existing = []
    FakeGitHub.status = 200
    server = HTTPServer(("127.0.0.1", 0), FakeGitHub)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def setup_env(
    monkeypatch: pytest.MonkeyPatch, repo: Path, base_sha: str, head_sha: str, api: str, tmp_path: Path, **inputs: str
) -> Path:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "base": {"sha": base_sha}, "head": {"sha": head_sha}}}))
    out = tmp_path / "out"
    for k, v in {
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_WORKSPACE": str(repo),
        "GITHUB_TOKEN": "test-token",
        "GITHUB_REPOSITORY": "acme/models",
        "GITHUB_API_URL": api,
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
        "INPUT_OUTPUT_DIR": str(out),
        "INPUT_EXCLUDE": "*.log",
        "INPUT_MAX_FILE_SIZE_MB": "0.03",
    }.items():
        monkeypatch.setenv(k, v)
    for k, v in inputs.items():
        monkeypatch.setenv("INPUT_" + k.upper().replace("-", "_"), v)
    return out


def test_action_end_to_end(
    repo: tuple[Path, str, str], fake_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r, base, head = repo
    out = setup_env(monkeypatch, r, base, head, fake_api, tmp_path)
    runner = load_runner()
    assert runner.main() == 0
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    status = {e["path"]: e for e in rep["entries"]}
    assert status["config/model.json"]["result"]["status"] == "different"
    assert status["data.csv"]["result"]["plugin"]["name"] == "csv"
    assert status["app.sqlite"]["file_status"] == "removed"
    assert status["renamed.sqlite"]["file_status"] == "renamed"
    assert status["renamed.sqlite"]["old_path"] == "rename-me.sqlite"
    assert status["new.parquet"]["file_status"] == "added"
    assert "larger than max-file-size-mb" in status["big.json"]["note"]
    assert "Git LFS" in status["lfs.bin"]["note"]
    assert "README.md" not in status, "plain text is left to GitHub's own diff by default"
    assert "skip.log" not in status
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "artifactdiff-report" in md and "config/model.json" in md
    assert (out / "report.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert "ArtifactDiff" in (tmp_path / "summary.md").read_text(encoding="utf-8")
    outputs = (tmp_path / "output.txt").read_text()
    assert "total-changes=" in outputs and "report-dir=" in outputs
    posts = [c for c in FakeGitHub.calls if c[0] == "POST"]
    assert len(posts) == 1 and posts[0][1] == "/repos/acme/models/issues/7/comments"
    assert len(posts[0][2]["body"]) <= runner.COMMENT_LIMIT


def test_action_updates_existing_comment(
    repo: tuple[Path, str, str], fake_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r, base, head = repo
    FakeGitHub.existing = [
        {"id": 1, "body": "unrelated", "user": {"type": "User"}},
        {"id": 42, "body": "<!-- artifactdiff-report -->\nold", "user": {"type": "Bot"}},
    ]
    setup_env(monkeypatch, r, base, head, fake_api, tmp_path)
    assert load_runner().main() == 0
    patches = [c for c in FakeGitHub.calls if c[0] == "PATCH"]
    assert [p[1] for p in patches] == ["/repos/acme/models/issues/comments/42"]
    assert not [c for c in FakeGitHub.calls if c[0] == "POST"]


def test_fork_token_failure_is_a_warning(
    repo: tuple[Path, str, str],
    fake_api: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    r, base, head = repo
    FakeGitHub.status = 403
    setup_env(monkeypatch, r, base, head, fake_api, tmp_path)
    assert load_runner().main() == 0
    assert "::warning::Could not post the pull-request comment: HTTP 403" in capsys.readouterr().out


def test_fail_on_and_paths(
    repo: tuple[Path, str, str], fake_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r, base, head = repo
    out = setup_env(
        monkeypatch,
        r,
        base,
        head,
        fake_api,
        tmp_path,
        **{"fail-on": "high", "paths": "config/*", "comment-on-pr": "false"},
    )
    assert load_runner().main() == 1  # JSON type change is "high"
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert [e["path"] for e in rep["entries"]] == ["config/model.json"]
    assert not FakeGitHub.calls


def test_invalid_ref_rejected(
    repo: tuple[Path, str, str], fake_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r, _base, head = repo
    setup_env(monkeypatch, r, "--upload-pack=touch /tmp/pwned", head, fake_api, tmp_path, base="--output=/tmp/x")
    assert load_runner().main() == 2


def test_action_yml_is_consistent() -> None:
    text = (ROOT / "action.yml").read_text(encoding="utf-8")
    assert "pull_request_target" not in text.split("runs:")[1]
    for name in ("base", "head", "comment-on-pr", "fail-on", "exclude", "max-files"):
        assert f"  {name}:" in text
        assert f"INPUT_{name.upper().replace('-', '_')}" in text
    # inputs reach the script through env vars, never by interpolation into shell scripts
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("run:"):
            indent = len(line) - len(line.lstrip())
            block = [line]
            for nxt in lines[i + 1 :]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                block.append(nxt)
            assert not any("${{" in b for b in block), block


def test_regression_oversized_rename_keeps_renamed_status(
    repo: tuple[Path, str, str], fake_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A renamed file above max-file-size-mb used to be reported as "modified".
    r, base, head = repo
    out = setup_env(monkeypatch, r, base, head, fake_api, tmp_path, **{"comment-on-pr": "false"})
    monkeypatch.setenv("INPUT_MAX_FILE_SIZE_MB", "0.001")
    assert load_runner().main() == 0
    entries = {e["path"]: e for e in json.loads((out / "report.json").read_text(encoding="utf-8"))["entries"]}
    assert entries["renamed.sqlite"]["file_status"] == "renamed"
    assert entries["renamed.sqlite"]["old_path"] == "rename-me.sqlite"


def test_regression_yaml_files_parse() -> None:
    # Unquoted "key: value" text inside descriptions once made action.yml unparseable on GitHub.
    yaml = pytest.importorskip("yaml")
    files = [
        ROOT / "action.yml",
        ROOT / "docker-compose.yml",
        ROOT / "examples" / "workflows" / "artifactdiff.yml",
        *sorted((ROOT / ".github" / "workflows").glob("*.yml")),
    ]
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    for name, spec in action["inputs"].items():
        assert isinstance(spec["description"], str) and spec["description"], name
        env_name = "INPUT_" + name.upper().replace("-", "_")
        uses_input = any(env_name in (step.get("env") or {}) for step in action["runs"]["steps"])
        assert uses_input or name in ("extras", "python-version", "upload-artifact", "artifact-name", "github-token"), (
            f"input {name} is never passed to the runner"
        )


def test_actionlint_workflows() -> None:
    exe = shutil.which("actionlint")
    if exe is None:
        pytest.skip("actionlint not installed")
    res = subprocess.run(
        [exe, *map(str, sorted((ROOT / ".github" / "workflows").glob("*.yml")))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
