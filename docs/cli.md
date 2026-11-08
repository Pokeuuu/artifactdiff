# CLI reference

```
artifactdiff [--verbose/-v ...] [--log-format text|json] COMMAND
```

`-v` enables info logs, `-vv` debug logs (to stderr; logs never contain file contents).

## `diff OLD NEW`

Compare two files, or two directories with `--recursive` (two directories are detected automatically).

| Option | Meaning |
|---|---|
| `-p, --plugin NAME` | Force a plugin instead of auto-detection |
| `-O, --option key=value` | Plugin option, repeatable. Values are parsed as JSON when possible; list options accept `a,b` |
| `--options-file FILE` | JSON object of plugin options |
| `-f, --format text\|json\|markdown\|html` | Output format (default: rich terminal output) |
| `-o, --output FILE` | Write the report to a file |
| `-r, --recursive` | Compare directories |
| `-x, --exclude GLOB` | Skip paths (repeatable; `*` matches `/`; `dir/` matches a directory) |
| `--fail-on info\|low\|medium\|high` | Exit 1 only for changes at or above this severity |
| `--exit-zero` | Exit 0 even when differences exist |
| `-q, --quiet` | No output; rely on the exit code |
| `-d, --details` | Show every change with its evidence, metrics and limitations |
| `-c, --config FILE` | Configuration file |
| `--max-changes N` | Changes kept in the result (all are counted) |
| `--timeout SECONDS` | Per-comparison time limit |
| `--max-file-size-mb N` | Reject larger inputs |
| `--isolate` | Run in a worker process (hard timeout, crash isolation, POSIX memory limit) |
| `--no-assets` | Skip generating visualizations |
| `--plugin-module pkg.mod:Class` | Load a trusted plugin class for this run |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | No differences (identical or equivalent), or all below `--fail-on`, or `--exit-zero` |
| 1 | Differences found (directory mode: also added/removed/renamed files) |
| 2 | Usage error: missing/unreadable input, file too large, invalid options or configuration |
| 3 | A comparison failed (corrupt input, plugin error, timeout) or nothing could compare the files |

### Statuses

- **identical** — byte-for-byte equal (plugins are not run).
- **equivalent** — bytes differ but the plugin found no meaningful change (e.g. reformatted JSON, a
  re-encoded but pixel-identical PNG).
- **different** — at least one change.
- **error** — the comparison could not be completed; `error` explains why.
- **unsupported** — no plugin could handle the pair (only when the binary fallback is unavailable).

## `inspect FILE [--plugin NAME] [--json]`

Show the detected format and plugin-specific properties (row counts, tensors, pages, …).

## `plugins list [--json]` · `plugins info NAME [--json]`

List registered plugins (with availability and install hints) and third-party plugins that were
*discovered but not loaded*. `info` shows capabilities, limitations and every option with its type,
default and description.

## `plugins new NAME [-e .ext] [--dest DIR]` · `plugins validate pkg.mod:Class`

Scaffold a plugin project (working code + tests), and check a plugin class against the contract.
`validate` imports the module, so only run it on code you trust.

## `report FILE [-f FORMAT] [-o FILE]`

Re-render a saved JSON result or report.

## `config init [--path FILE] [--force]` · `config show`

Write a commented default `artifactdiff.toml`; print the effective configuration and its source.

## `doctor` · `version` · `demo [--check] [--output-dir DIR]` · `serve [--host] [--port]`

Diagnostics; version; run the bundled examples (with `--check`, compare against `expected.json`);
start the web UI and API (needs the `api` extra; binds to 127.0.0.1 by default).

## Configuration

Searched in this order: `$ARTIFACTDIFF_CONFIG`, then `artifactdiff.toml` or a `[tool.artifactdiff]`
table in `pyproject.toml`, walking up from the current directory to the repository root.
Command-line options override the file. Unknown keys are errors, not silently ignored.

```toml
exclude = ["*.log", "build/"]
fail_on = "medium"

[limits]
max_file_size_mb = 2048
timeout_seconds = 300
max_changes = 10000
max_asset_mb = 16
# memory_limit_mb = 4096   # POSIX, with --isolate

[plugins]
enabled = []        # third-party entry-point plugins allowed to load
modules = []        # or explicit "package.module:Class"

[plugins.options.csv]
primary_key = ["id"]

[plugins.options.image]
tolerance = 8
```
