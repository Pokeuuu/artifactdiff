"""Plugin project generator (``artifactdiff plugins new <name>``).

The generated plugin is small but *working*: it compares ``key = value`` files so the generated
tests pass immediately, and authors replace the parsing logic with their own format.
"""

from __future__ import annotations

import re
from pathlib import Path

from artifactdiff import __version__
from artifactdiff.plugin import PLUGIN_API_VERSION

_NAME = re.compile(r"^[a-z][a-z0-9-]{1,40}$")

_PYPROJECT = """\
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "artifactdiff-plugin-{name}"
version = "0.1.0"
description = "ArtifactDiff plugin for {ext_list} files"
requires-python = ">=3.12"
dependencies = ["artifactdiff>={core_version}"]

[project.optional-dependencies]
test = ["pytest>=8"]

# Users must still opt in: add "{name}" to [plugins].enabled in artifactdiff.toml.
[project.entry-points."artifactdiff.plugins"]
{name} = "{pkg}.plugin:{cls}"

[tool.hatch.build.targets.wheel]
packages = ["src/{pkg}"]
"""

_PLUGIN = '''\
"""ArtifactDiff plugin for {ext_list} files.

Replace ``parse`` with real parsing for your format. Report every difference through
``ctx.add_change`` with evidence explaining how it was detected.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from artifactdiff_sdk import (
    ArtifactFile,
    ArtifactPlugin,
    ChangeCategory,
    ChangeKind,
    ChangeSeverity,
    ComparisonContext,
    PluginInputError,
    PluginOptions,
)


class {cls}Options(PluginOptions):
    ignore_keys: list[str] = Field(default_factory=list, description="Keys to ignore.")
    max_size_mb: float = Field(16, gt=0, description="Files are parsed in memory up to this size.")


class {cls}(ArtifactPlugin):
    name = "{name}"
    version = "0.1.0"
    api_version = "{api}"
    display_name = "{title}"
    description = "Compares {ext_list} files entry by entry."
    format_id = "{name}"
    format_name = "{title}"
    supported_extensions = {exts!r}
    capabilities = ("structural-diff",)
    limitations = ("Lines that are not 'key = value' pairs are ignored.",)
    Options = {cls}Options

    def parse(self, file: ArtifactFile, opts: {cls}Options) -> dict[str, str]:
        raw = file.read_bytes(int(opts.max_size_mb * 1024 * 1024))
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PluginInputError(f"{{file.display_name}} is not UTF-8 text") from exc
        entries: dict[str, str] = {{}}
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not line.lstrip().startswith("#"):
                entries[key.strip()] = value.strip()
        return entries

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        return {{"entries": len(self.parse(file, {cls}Options()))}}

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions,
                ctx: ComparisonContext) -> None:
        assert isinstance(options, {cls}Options)
        a, b = self.parse(old, options), self.parse(new, options)
        for key in sorted(set(a) | set(b)):
            ctx.check()  # honour cancellation and timeouts
            if key in options.ignore_keys:
                continue
            if key not in b:
                ctx.add_change(ChangeKind.REMOVED, ChangeCategory.DATA, key, f"Entry '{{key}}' removed",
                               old=a[key], severity=ChangeSeverity.MEDIUM, evidence="Key only in the old file")
            elif key not in a:
                ctx.add_change(ChangeKind.ADDED, ChangeCategory.DATA, key, f"Entry '{{key}}' added",
                               new=b[key], evidence="Key only in the new file")
            elif a[key] != b[key]:
                ctx.add_change(ChangeKind.MODIFIED, ChangeCategory.DATA, key, f"Entry '{{key}}' changed",
                               old=a[key], new=b[key], evidence="Compared values as strings")
        ctx.metric("old_entries", len(a))
        ctx.metric("new_entries", len(b))
'''

_TEST = """\
from pathlib import Path

from artifactdiff_sdk.testing import assert_plugin_contract, run_plugin, write_file
from {pkg}.plugin import {cls}


def test_contract() -> None:
    assert_plugin_contract({cls})


def test_detects_changes(tmp_path: Path) -> None:
    old = write_file(tmp_path, "old{ext}", "a = 1\\nb = 2\\n")
    new = write_file(tmp_path, "new{ext}", "a = 1\\nb = 3\\nc = 4\\n")
    result = run_plugin({cls}, old, new)
    assert result.status == "different"
    assert {{(c.kind.value, c.path) for c in result.changes}} == {{("modified", "b"), ("added", "c")}}


def test_equivalent_when_only_formatting_differs(tmp_path: Path) -> None:
    old = write_file(tmp_path, "old{ext}", "a=1\\n")
    new = write_file(tmp_path, "new{ext}", "a = 1\\n# comment\\n")
    assert run_plugin({cls}, old, new).status == "equivalent"
"""

_README = """\
# artifactdiff-plugin-{name}

An [ArtifactDiff](https://github.com/Pokeuuu/artifactdiff) plugin for {ext_list} files.

```bash
pip install -e ".[test]"
pytest
```

ArtifactDiff never loads third-party plugins automatically. Enable this one explicitly:

```toml
# artifactdiff.toml
[plugins]
enabled = ["{name}"]
```

Then `artifactdiff plugins list` shows it and `artifactdiff diff old{ext} new{ext}` uses it.
"""


def create_plugin_project(name: str, dest: str | Path, *, extensions: list[str] | None = None) -> Path:
    if not _NAME.match(name):
        raise ValueError("Plugin name must match ^[a-z][a-z0-9-]{1,40}$ (e.g. 'my-format')")
    exts = tuple(e if e.startswith(".") else f".{e}" for e in (extensions or [f".{name.replace('-', '')}"]))
    exts = tuple(e.lower() for e in exts)
    pkg = "artifactdiff_plugin_" + name.replace("-", "_")
    cls = "".join(part.capitalize() for part in name.split("-")) + "Plugin"
    title = " ".join(part.capitalize() for part in name.split("-"))
    root = Path(dest) / f"artifactdiff-plugin-{name}"
    if root.exists():
        raise FileExistsError(f"{root} already exists")
    fields = {
        "name": name,
        "pkg": pkg,
        "cls": cls,
        "title": title,
        "exts": exts,
        "ext": exts[0],
        "ext_list": ", ".join(exts),
        "api": PLUGIN_API_VERSION,
        "core_version": __version__,
    }
    files = {
        "pyproject.toml": _PYPROJECT,
        f"src/{pkg}/__init__.py": '"""ArtifactDiff plugin package."""\n',
        f"src/{pkg}/plugin.py": _PLUGIN,
        "tests/test_plugin.py": _TEST,
        "README.md": _README,
    }
    for rel, template in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template.format(**fields), encoding="utf-8")
    return root
