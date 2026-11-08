# Contributing to ArtifactDiff

Thanks for helping! New format plugins, better comparisons of existing formats, bug reports with
reproducible (synthetic) files, and documentation fixes are all valuable.

## Setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 20+ (only for the web UI), git.

```bash
make setup        # uv sync --all-extras, npm ci in apps/web, pre-commit hooks
make test-fast    # Python tests without the slow memory tests
make test         # everything, including Playwright end-to-end tests
make lint         # ruff, mypy --strict, eslint, prettier
make dev          # API on :8000 + Vite on :5173 with hot reload
```

Without `make` (e.g. on Windows), run the underlying commands:

```bash
uv sync --all-extras
uv run pytest -m "not slow"
uv run ruff check . && uv run ruff format --check . && uv run mypy
cd apps/web && npm ci && npm run typecheck && npm run lint && npm run build
uv run python scripts/build_web.py --skip-npm   # bundle the UI into the API package
cd apps/web && npx playwright install chromium && npm run e2e
```

## Repository layout

See [docs/architecture.md](docs/architecture.md). In short: `packages/core` (engine and models),
`packages/plugins` (built-in plugins), `packages/sdk` (plugin-author API), `cli`, `apps/api`,
`apps/web`, `integrations/github-action`, `tests`, `examples`, `docs`.

## Ground rules for comparisons

These are what make ArtifactDiff trustworthy; reviews check them.

1. **Never invent a result.** Every `Change` needs `evidence` that says how it was detected.
2. **Don't guess silently.** Inferred matches (renames, auto-selected keys) must carry a
   `confidence` and/or a warning. When ambiguous, report removal + addition instead.
3. **Measurements are not probabilities.** Name the metric and its parameters (e.g. "SSIM, 7x7
   window"); never present a similarity score as a likelihood.
4. **Document limitations** in the plugin's `limitations` and in `docs/supported-formats.md`.
5. **Bound resources.** Stream or chunk large inputs, call `ctx.check()` in loops, honour size
   options, and add a test with a representative large input.
6. **Never modify inputs or reach the network.** Open databases read-only; resolve external
   references only next to the input file.
7. **Determinism.** Same inputs and options ⇒ same changes, order and fingerprint.

## Adding a built-in plugin

1. Implement `ArtifactPlugin` in `packages/plugins/src/artifactdiff_plugins/<name>.py`; import heavy
   dependencies inside methods and declare them in `requires` / `install_hint`.
2. Add it to `BUILTIN_PLUGINS` and, if needed, an extra in `pyproject.toml`.
3. Add a sample generator to `tests/samples.py`, register it in `tests/plugins/test_contract.py`
   (identical/modified/empty/garbage/truncated/missing/deterministic) and write format-specific tests.
4. Add an example (`scripts/generate_examples.py`) and documentation.

Third-party plugins don't need to live here: see [docs/plugin-development.md](docs/plugin-development.md).

## Pull requests

- Keep PRs focused; include tests; update `CHANGELOG.md` under *Unreleased*.
- Test files must be synthetic or clearly redistributable. Never commit confidential artifacts.
- CI must pass: lint, strict typing, tests on Linux/Windows/macOS, web build, E2E, Docker build.

By contributing you agree that your contributions are licensed under the Apache License 2.0.
