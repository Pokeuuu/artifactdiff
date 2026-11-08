# artifactdiff_plugins

Built-in format plugins. Heavy libraries are imported inside methods so the registry works without them.
Shared helpers: `_tabular.py` (CSV/Parquet row and statistics comparison), `_tensors.py` (format-neutral
tensor comparison), `_common.py`. Per-plugin behaviour: [docs/supported-formats.md](../../docs/supported-formats.md).
