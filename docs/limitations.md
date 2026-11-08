# Limitations

ArtifactDiff aims to report only what it verified. This page collects what it does **not** do, so
you can judge results correctly. Each result also lists the limitations of the plugin that produced it.

## General

- **Severity is a heuristic.** It helps triage; it does not know what matters in your domain.
- **Measures are not probabilities.** SSIM, perceptual-hash distance, relative L2 differences and
  similar numbers are reported with their definitions and must not be read as likelihoods.
- **Equivalent ≠ identical.** `equivalent` means the plugin found nothing it compares; bytes it does
  not inspect (comments, ordering, encodings it normalises) may still differ.
- **Change listings are capped** (`limits.max_changes`, per-plugin `max_*` options). All changes are
  counted; a warning says when the listing is partial.
- **Memory.** Streaming formats (SQLite, safetensors, audio, binary) use bounded memory. CSV/Parquet
  joins, JSON parsing, image decoding and PDF rendering hold data in memory; see
  [benchmarks.md](benchmarks.md) for measured peaks and use the size limits.
- **Timeouts** are cooperative inline; only process isolation (`--isolate`, server default) can stop
  a long call inside a native library. Memory limits are POSIX-only.

## Per format

| Plugin | Limitations |
|---|---|
| JSON | Parsed fully in memory (bounded by `max_size_mb`). JSON5/comments are not supported. In `lcs` mode, elements inside a replaced run are paired by position. |
| CSV | Types are inferred from a sample (`infer_rows`). Without a primary key, a modified row is a removal plus an addition. Rows with more fields than the header are rejected. Statistics are descriptive, not significance tests. |
| Parquet | Columns whose type changed are excluded from record matching. Nested list/struct values are compared as whole values. |
| SQLite | Views and virtual tables are compared by definition only. Keyless tables are row multisets (≈100 bytes of memory per distinct row) unless `match_by_rowid`. Databases with a `-wal` file are opened without `immutable` (SQLite may create a `-shm` file; the database itself is never written). |
| Images | Compared as 8-bit RGB(A); higher bit depths are quantised. Only the first frame of GIF/multi-page TIFF. Pixel comparison requires identical dimensions. SSIM runs on downscaled images above `ssim_max_side`. |
| Audio | Levels are RMS/peak dBFS, not LUFS. Waveform comparison requires equal sample rate and channels (no resampling or alignment, so a shifted signal differs everywhere after the shift). MP3/Opus depend on the libsndfile build. |
| PDF | Only the text layer is compared: no OCR, so scanned pages are flagged but not compared textually. Embedded images are compared by count and placement; visual differences come from rendering. Password-protected PDFs are unsupported. |
| glTF | glTF 2.0 only. Draco/meshopt-compressed and sparse accessors are not decoded. External buffers/textures must sit next to the model (not available for web uploads of `.gltf`). Animations, skins and cameras are compared by count. Rename detection needs identical geometry and transform. |
| safetensors | FP8 tensors are compared byte-wise only. A tensor that was renamed *and* modified appears as removed + added. |
| text | Line-based; no syntax awareness (YAML/TOML are not parsed). |
| binary | Byte offsets only: an insertion shifts everything after it. |

## Not supported (yet)

ONNX, GGUF, PyTorch pickles (never planned for unpickling — unsafe), Office documents, video, point
clouds, other 3D formats (FBX, OBJ, USD), archives. See [roadmap.md](roadmap.md).

## Environment

- The web UI is a single-user local tool by default; it has an optional shared token, not user
  accounts or per-user permissions.
- The GitHub Action cannot comment on pull requests from forks with the default token (reports are
  still produced as a job summary and artifact).
- The CI workflow builds and smoke-tests the Docker image. PostgreSQL support uses standard
  SQLAlchemy and Alembic, but the automated test suite runs against SQLite only.
