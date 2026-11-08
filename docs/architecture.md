# Architecture

```
               ┌──────────┐   ┌──────────────┐   ┌──────────────────┐
  CLI ────────►│          │   │  Registry    │   │ Plugins          │
  API jobs ───►│  Engine  │──►│  select()    │──►│ supports/inspect │
  GH Action ──►│          │   │  (explicit)  │   │ compare(ctx)     │
               └────┬─────┘   └──────────────┘   └────────┬─────────┘
                    │  ComparisonResult                   │ ctx.add_change / warn /
                    ▼                                     │ metric / add_asset / check
               Reporting (JSON · Markdown · HTML · text) ◄┘
```

## Packages

| Path | Import name | Responsibility |
|---|---|---|
| `packages/core` | `artifactdiff` | Models, plugin contract, registry, engine, isolation, config, logging, directory comparison, reports |
| `packages/plugins` | `artifactdiff_plugins` | Built-in plugins; heavy libraries are imported inside methods |
| `packages/sdk` | `artifactdiff_sdk` | Stable surface for plugin authors, testing helpers, project scaffold |
| `cli` | `artifactdiff_cli` | Typer commands, Rich rendering, doctor, demo |
| `apps/api` | `artifactdiff_api` | FastAPI app, SQLAlchemy models, Alembic migrations, job manager, storage |
| `apps/web` | — | React/TypeScript UI, talks only to the REST API |
| `integrations/github-action` | — | Action runner; `action.yml` is at the repo root so `uses: owner/artifactdiff@v1` works |

All Python packages ship in one distribution (`artifactdiff`) with optional extras, so a single
`pip install` gives a consistent set; the separation is enforced by import direction
(core ← plugins/sdk ← cli/api).

## Core model

- `ArtifactFile` — read-only handle: display name (e.g. the repo path) vs. real path, size, lazy
  streaming SHA-256, signature sniffing.
- `ArtifactPlugin` — class attributes describe identity, detection (`supported_extensions`,
  `mime_types`, `signatures`/`sniff`), capabilities, limitations, `requires`, and a Pydantic
  `Options` model (unknown options are rejected). Methods: `supports`, `inspect`, `compare`,
  `summarize`, `export`.
- `ComparisonContext` — what plugins report into: `add_change` (assigns stable IDs, counts by
  kind/category/severity, caps the listing at `max_changes`), `count_omitted` (verified changes not
  listed individually, e.g. rows beyond a cap), `warn`, `limitation`, `metric`, `add_asset` (bounded
  by an asset budget), `check()` (cancellation/deadline) and `progress()`.
- `ComparisonResult` — status, plugin, both files' metadata, options, changes, warnings,
  limitations, statistics, assets, error, reproducibility (versions, platform, option/input hashes)
  and a `fingerprint` (SHA-256 over everything except timestamps and timings).

`Change` IDs are `sha256(plugin, kind, category, path)[:16]` (with a `-n` suffix for repeats), so
the same difference has the same ID across runs and machines.

## Engine flow

1. Validate both paths (regular files, size limit).
2. Select a plugin: specific plugins before the `text`/`binary` fallbacks, then by the weaker of the
   two detections (signature 3 > MIME 2 > extension 1 > content 0), then plugin priority, then name.
3. Validate options (config defaults merged with explicit options).
4. Inspect both files (failures become warnings).
5. If bytes are identical → `identical` without running the plugin. Otherwise run `compare` with a
   deadline. `PluginInputError`, resource limits, timeouts, cancellation and unexpected exceptions
   become `status: error` with a message — one bad file never aborts a batch.
6. Assemble statistics, summary, reproducibility and fingerprint.

### Isolation

`artifactdiff.isolation.run_isolated` runs the same engine in a `spawn`ed worker process connected by
a pipe (progress messages + result). The parent enforces a hard deadline (timeout + grace), can kill
the worker on cancellation, and survives native crashes. On POSIX the worker applies
`RLIMIT_AS = memory_limit_mb`. The API uses process isolation by default; the CLI offers `--isolate`.

## Registry and plugin loading

Built-ins are registered from the fixed list `artifactdiff_plugins.BUILTIN_PLUGINS`. Entry points
in the `artifactdiff.plugins` group are **enumerated** (no code runs) and only **imported** when
listed in `plugins.enabled`; `plugins.modules` / `--plugin-module` load explicit classes. Every
class is validated: name pattern, semantic version, plugin-API major version, `Options` type,
implemented abstract methods.

## Data flow in the API

`POST /compare` streams both uploads to `data_dir/comparisons/<id>/inputs/` (size enforced while
streaming and again at the ASGI layer), validates plugin and options synchronously, inserts a
`queued` row and submits a job to a bounded thread pool. The job runs the comparison (in a worker
process), moves generated assets into `assets/` (replacing inline base64 with URLs) and stores the
result JSON in the database. A retention thread deletes uploaded inputs after
`input_retention_hours` (results and visualizations are kept until deleted or
`result_retention_days`). On restart, interrupted jobs are marked failed.

## Reports

JSON is canonical (schemas in [`docs/schema/`](schema)). Markdown is designed for PR comments:
everything from the artifact (names, values) is escaped, `@` mentions are neutralised, and the
output can be truncated to GitHub's limit. HTML is a single file with inline CSS/JS, embedded images
and a restrictive CSP; it works offline.

## Design decisions

- **Plugins report into a context** rather than building result objects, so IDs, statistics,
  caps and fingerprints are consistent across plugins and minimal boilerplate is needed.
- **Direct glTF parsing** instead of a scene loader keeps names, hierarchy, materials and accessor
  data intact and lets us bound memory and refuse unsafe external URIs.
- **No librosa**: audio spectra use NumPy FFTs, avoiding numba/scipy for a few well-defined measures.
- **Format-neutral tensor core** (`_tensors.py`): safetensors implements a small `TensorStore`
  protocol; ONNX initializers or GGUF tensors can reuse every comparison.
- **Rows are never paired by guesswork**: without a key, tables are compared as row multisets.
