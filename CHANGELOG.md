# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The report JSON schema and the plugin API are versioned
separately (`schema_version`, `PLUGIN_API_VERSION`).

## [Unreleased]

## [0.1.0] — 2026-11-08

First public release.

### Added
- Comparison engine: typed models, plugin registry with explicit registration and opt-in
  third-party plugins, signature/MIME/extension detection, per-plugin typed options, byte-identical
  short-circuit, cooperative timeouts and cancellation, optional process isolation with hard
  timeouts and (POSIX) memory limits, deterministic fingerprints and reproducibility metadata.
- Plugins: JSON, CSV/TSV, Parquet, SQLite, raster images, audio, PDF, glTF/GLB, safetensors, plus
  text and binary fallbacks.
- Reports: JSON (schema in `docs/schema/`), Markdown (PR-safe escaping), standalone offline HTML.
- CLI: `diff` (files and recursive directories), `inspect`, `plugins list|info|new|validate`,
  `report`, `config init|show`, `version`, `doctor`, `demo`, `serve`; CI exit codes.
- REST API (FastAPI, SQLAlchemy 2, Alembic): background jobs, progress, cancellation, retention
  sweeper, upload limits enforced while streaming, rate limiting, optional bearer token.
- Web UI (React, TypeScript, Vite, Tailwind, Monaco, Three.js, Recharts): dashboard, compare,
  results with format-specific viewers, history, plugins, settings, documentation.
- GitHub Action (composite) with single updatable PR comment, job summary and report artifact.
- Plugin SDK with testing helpers, project scaffold, and an example INI plugin.
- Examples for every format, benchmarks, Docker image and Compose file, CI and release workflows.
