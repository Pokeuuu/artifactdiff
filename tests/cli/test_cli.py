from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import write
from typer.testing import CliRunner

from artifactdiff import __version__
from artifactdiff_cli.main import app

runner = CliRunner()


def run(*args: str) -> tuple[int, str]:
    res = runner.invoke(app, list(args))
    return res.exit_code, res.output


def test_version() -> None:
    code, out = run("version")
    assert code == 0 and __version__ in out


def test_diff_exit_codes(tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", '{"a": 1}')
    b = write(tmp_path / "b.json", '{"a": 2}')
    same = write(tmp_path / "c.json", '{"a": 1}')
    assert run("diff", str(a), str(same))[0] == 0
    assert run("diff", str(a), str(b))[0] == 1
    assert run("diff", str(a), str(b), "--exit-zero")[0] == 0
    assert run("diff", str(a), str(b), "--fail-on", "high")[0] == 0  # a value change is "low"
    assert run("diff", str(a), str(tmp_path / "missing.json"))[0] == 2
    bad = write(tmp_path / "bad.json", "{")
    assert run("diff", str(a), str(bad))[0] == 3


def test_diff_json_output_is_valid_result(tmp_path: Path) -> None:
    from samples import json_pair

    a, b = json_pair(tmp_path)
    code, out = run("diff", str(a), str(b), "--format", "json")
    assert code == 1
    data = json.loads(out)
    assert data["status"] == "different" and data["plugin"]["name"] == "json"


def test_diff_html_and_markdown_files(tmp_path: Path) -> None:
    from samples import image_pair

    a, b = image_pair(tmp_path)
    html = tmp_path / "out" / "report.html"
    md = tmp_path / "out" / "report.md"
    assert run("diff", str(a), str(b), "-f", "html", "-o", str(html))[0] == 1
    assert run("diff", str(a), str(b), "-f", "markdown", "-o", str(md))[0] == 1
    text = html.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>") and "data:image/png;base64," in text
    assert "http://" not in text and "https://" not in text  # fully offline
    assert "artifactdiff-report" in md.read_text(encoding="utf-8")


def test_options_parsing(tmp_path: Path) -> None:
    a = write(tmp_path / "a.csv", "k,v\n1,a\n")
    b = write(tmp_path / "b.csv", "k,v\n1,b\n")
    code, out = run("diff", str(a), str(b), "-O", "primary_key=k", "-f", "json")
    data = json.loads(out)
    assert data["options"]["primary_key"] == ["k"]
    assert any(c["path"] == "row[k='1']" for c in data["changes"])
    code, out = run("diff", str(a), str(b), "-O", "nope=1")
    assert code == 2 and "Invalid options" in out


def test_quiet(tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", '{"a": 1}')
    b = write(tmp_path / "b.json", '{"a": 2}')
    code, out = run("diff", str(a), str(b), "-q")
    assert code == 1 and out.strip() == ""


def test_recursive(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    write(old / "same.json", '{"a":1}')
    write(new / "same.json", '{"a":1}')
    write(old / "mod.json", '{"a":1}')
    write(new / "mod.json", '{"a":2}')
    write(old / "gone.txt", "x")
    write(new / "fresh.txt", "y")
    write(old / "moved.bin", b"\x00\x01\x02")
    write(new / "sub" / "moved.bin", b"\x00\x01\x02")
    write(old / "skip.log", "a")
    write(new / "skip.log", "b")
    code, out = run("diff", str(old), str(new), "-r", "-x", "*.log", "-f", "json")
    assert code == 1
    rep = json.loads(out)
    status = {e["path"]: e["file_status"] for e in rep["entries"]}
    assert status == {"mod.json": "modified", "gone.txt": "removed", "fresh.txt": "added", "sub/moved.bin": "renamed"}


def test_report_command_rerenders(tmp_path: Path) -> None:
    from samples import json_pair

    a, b = json_pair(tmp_path)
    out_json = tmp_path / "r.json"
    run("diff", str(a), str(b), "-f", "json", "-o", str(out_json))
    code, out = run("report", str(out_json), "-f", "markdown")
    assert code == 0 and "$.model.name" in out
    code, out = run("report", str(write(tmp_path / "junk.json", "[1]")))
    assert code == 2


def test_inspect_and_plugins(tmp_path: Path) -> None:
    from samples import sqlite_pair

    a, _ = sqlite_pair(tmp_path)
    code, out = run("inspect", str(a), "--json")
    assert code == 0 and json.loads(out)["properties"]["tables"]["users"]["rows"] == 3
    code, out = run("plugins", "list", "--json")
    names = {p["name"] for p in json.loads(out)["plugins"]}
    assert {"json", "csv", "safetensors", "gltf"} <= names
    code, out = run("plugins", "info", "csv")
    assert code == 0 and "primary_key" in out
    assert run("plugins", "info", "nope")[0] == 2


def test_config_init_and_show(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert run("config", "init")[0] == 0
    assert run("config", "init")[0] == 2  # exists
    code, out = run("config", "show")
    assert code == 0 and json.loads(out)["limits"]["max_changes"] == 10000
    (tmp_path / "artifactdiff.toml").write_text("limits = 3\n")
    assert run("config", "show")[0] == 2


def test_plugins_new_and_validate(tmp_path: Path) -> None:
    code, out = run("plugins", "new", "ini-file", "-e", ".ini", "--dest", str(tmp_path))
    assert code == 0
    root = tmp_path / "artifactdiff-plugin-ini-file"
    assert (root / "src" / "artifactdiff_plugin_ini_file" / "plugin.py").exists()
    # the generated project's own tests pass
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(root / "tests")],
        cwd=root,
        env={**__import__("os").environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    sys.path.insert(0, str(root / "src"))
    try:
        code, out = run("plugins", "validate", "artifactdiff_plugin_ini_file.plugin:IniFilePlugin")
        assert code == 0, out
    finally:
        sys.path.remove(str(root / "src"))


def test_plugin_module_option(tmp_path: Path) -> None:
    pkg = tmp_path / "myplug.py"
    pkg.write_text(
        "from artifactdiff_sdk import *\n"
        "class P(ArtifactPlugin):\n"
        "    name='upper'\n    version='1.0.0'\n    supported_extensions=('.up',)\n"
        "    def inspect(self, f): return {}\n"
        "    def compare(self, a, b, o, ctx):\n"
        "        ctx.add_change(ChangeKind.MODIFIED, ChangeCategory.DATA, 'all', 'changed', evidence='test')\n"
    )
    sys.path.insert(0, str(tmp_path))
    try:
        a = write(tmp_path / "a.up", "a")
        b = write(tmp_path / "b.up", "b")
        _code, out = run("diff", str(a), str(b), "--plugin-module", "myplug:P", "-f", "json")
        assert json.loads(out)["plugin"]["name"] == "upper"
    finally:
        sys.path.remove(str(tmp_path))
        from artifactdiff.registry import default_registry

        default_registry().unregister("upper")


def test_isolated_mode(tmp_path: Path) -> None:
    a = write(tmp_path / "a.json", '{"a": 1}')
    b = write(tmp_path / "b.json", '{"a": 2}')
    code, out = run("diff", str(a), str(b), "--isolate", "-f", "json")
    assert code == 1 and json.loads(out)["statistics"]["total_changes"] == 1


def test_doctor_runs() -> None:
    code, out = run("doctor")
    assert code == 0 and "plugins" in out


def test_demo_check(tmp_path: Path) -> None:
    examples = Path(__file__).resolve().parents[2] / "examples"
    if not any(examples.glob("*/expected.json")):
        pytest.skip("examples not generated")
    code, out = run("demo", "--examples-dir", str(examples), "--check", "--output-dir", str(tmp_path))
    assert code == 0, out
    assert (tmp_path / "json.html").exists()


def test_module_entrypoint() -> None:
    res = subprocess.run(
        [sys.executable, "-m", "artifactdiff_cli", "version"], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0 and __version__ in res.stdout
