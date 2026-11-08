# Tests

```bash
uv run pytest                 # everything (Python)
uv run pytest -m "not slow"   # skip memory/large-input tests
```

- `core/` engine, registry, config, reporting, isolation, docs freshness
- `plugins/` per-format tests and `test_contract.py` (identical/modified/empty/garbage/truncated/missing/
  deterministic for every plugin)
- `cli/`, `api/`, `action/` (real git repository + fake GitHub API)
- `samples.py` deterministic sample generators shared with examples and benchmarks

Web end-to-end tests live in `apps/web/e2e` (Playwright).
