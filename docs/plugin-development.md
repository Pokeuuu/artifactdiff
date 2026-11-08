# Plugin development

A plugin teaches ArtifactDiff a new format. It is a Python class; packaging it as a distribution
with an entry point lets users install and enable it.

## Scaffold

```bash
artifactdiff plugins new my-format -e .myf --dest .
cd artifactdiff-plugin-my-format
pip install -e ".[test]"
pytest
```

The generated plugin compares `key = value` files and its tests pass immediately; replace the
parsing logic with your format. A fuller example is
[`examples/plugins/artifactdiff-plugin-ini`](../examples/plugins/artifactdiff-plugin-ini).

## Anatomy

```python
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


class MyOptions(PluginOptions):  # unknown options are rejected
    ignore_keys: list[str] = Field(default_factory=list, description="Keys to ignore.")


class MyPlugin(ArtifactPlugin):
    name = "my-format"  # ^[a-z][a-z0-9-]{1,40}$
    version = "1.0.0"  # semantic version
    supported_extensions = (".myf",)  # lower-case, leading dot
    signatures = (b"MYF1",)  # optional magic bytes (strongest detection)
    capabilities = ("structural-diff",)
    limitations = ("Comments are ignored.",)
    requires = ("numpy",)  # checked without importing; shown in `doctor`
    install_hint = 'pip install "artifactdiff-plugin-my-format[full]"'
    Options = MyOptions

    def inspect(self, file: ArtifactFile) -> dict:
        return {"records": len(self._parse(file))}

    def compare(self, old, new, options, ctx: ComparisonContext) -> None:
        assert isinstance(options, MyOptions)
        a, b = self._parse(old), self._parse(new)
        for key in sorted(set(a) | set(b)):
            ctx.check()  # cancellation + timeout
            if key in options.ignore_keys:
                continue
            if key not in b:
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.DATA,
                    f"$.{key}",
                    f"'{key}' removed",
                    old=a[key],
                    severity=ChangeSeverity.MEDIUM,
                    evidence="Key present only in the old file",
                )
            ...

    def _parse(self, file: ArtifactFile) -> dict:
        raw = file.read_bytes(16 * 1024 * 1024)  # always pass a size limit
        try:
            ...
        except ValueError as exc:
            raise PluginInputError(f"{file.display_name} is not valid: {exc}") from exc
```

You never build a `ComparisonResult` yourself: the engine assembles it from what you report, adds
stable change IDs, statistics, the summary, reproducibility information and the fingerprint.

### Context API

| Call | Use |
|---|---|
| `ctx.add_change(kind, category, path, description, *, evidence, severity, old, new, confidence, details)` | Report one verified change. `evidence` is required. Set `confidence` only for inferred changes |
| `ctx.count_omitted(n, kind, category, severity)` | Count verified changes you do not list individually |
| `ctx.warn(code, message, path=None)` | Anything the user should know (auto-selected keys, skipped parts) |
| `ctx.limitation(text)` | A limitation specific to this run (static ones go in `limitations`) |
| `ctx.metric(key, value)` | Measured quantities (JSON-serializable) |
| `ctx.add_asset(role, label, media_type, data, width=, height=)` | Visualizations (PNG); respects the asset budget and `--no-assets` |
| `ctx.check()` / `ctx.progress(fraction, message)` | Call in loops; raises on cancellation/timeout |

Raise `PluginInputError` for invalid/corrupt input and `ResourceLimitError` for exceeded limits —
both become a clean `status: error`. Other exceptions are caught too, but are logged as plugin bugs.

### Guidelines

- **Paths** should be stable and structural (`$.a.b[2]`, `table:users/row[id=4]`, `node:Wheel`).
- **Kinds**: `added`, `removed`, `modified`, `type_changed`, `renamed`, `moved`.
  **Categories**: `structure`, `schema`, `data`, `metadata`, `visual`, `geometry`, `material`,
  `audio`, `statistics`, `text`, `binary`.
- **Severity** is a heuristic you must document (e.g. "type change = high").
- Keep `compare` deterministic: iterate in a defined order.
- Import heavy dependencies inside methods so `plugins list` works without them.
- Detection: `signatures` (or override `sniff`) beat MIME types, which beat extensions. Set
  `priority` to win ties against other plugins (the text fallback has priority −10).

## Testing

```python
from artifactdiff_sdk.testing import assert_plugin_contract, make_engine, run_plugin, write_file


def test_contract():
    assert_plugin_contract(MyPlugin)  # metadata, versions, options, serialisation


def test_change(tmp_path):
    r = run_plugin(MyPlugin, write_file(tmp_path, "a.myf", ...), write_file(tmp_path, "b.myf", ...))
    assert r.status == "different"


def test_detection(tmp_path):
    r = make_engine(MyPlugin).compare(...)  # real selection between your plugin and fallbacks
```

Test identical, modified, empty, invalid, truncated and large inputs.

## Packaging and enabling

```toml
# pyproject.toml of your plugin
[project.entry-points."artifactdiff.plugins"]
my-format = "artifactdiff_plugin_my_format.plugin:MyPlugin"
```

Installed plugins show up in `artifactdiff plugins list` as *discovered, not loaded*. Users opt in:

```toml
# artifactdiff.toml
[plugins]
enabled = ["my-format"]
```

or load a class directly with `--plugin-module package.module:Class`. ArtifactDiff never imports
third-party plugin code without one of these explicit steps, and the GitHub Action ignores both
settings (a pull request must not be able to choose code to run).

## Compatibility

`PLUGIN_API_VERSION` is currently `1.0`. Plugins declare `api_version`; a different major version is
rejected at registration. Everything exported from `artifactdiff_sdk` is covered by this version;
other modules are internal. Validate with `artifactdiff plugins validate package.module:Class`.
