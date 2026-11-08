# Benchmarks

Measured with `python scripts/benchmark.py --markdown`, which generates synthetic inputs and runs each
comparison in a fresh process. **Peak RSS** is the operating system's peak resident memory of that
process (including Python itself, ~35 MiB, and native libraries such as Polars, Arrow and Pillow,
which `tracemalloc` cannot see). Visualizations were generated (default settings).

Machine: Windows 11, Python 3.13.7, 16 logical CPUs, NVMe SSD. ArtifactDiff 0.1.0.

| Format | Input | Total input size | Time | Peak RSS | Changes |
|---|---|---:|---:|---:|---:|
| csv | 500,000 rows × 4 cols, 1% rows modified, `primary_key=id` | 29 MiB | 1.0 s | 471 MiB | 5,001 |
| parquet | 2,000,000 rows × 3 cols, 0.5% modified, `primary_key=id` | 55 MiB | 1.2 s | 595 MiB | 10,002 |
| sqlite | 1,000,000 rows (keyed), 0.1% modified | 47 MiB | 3.1 s | 40 MiB | 1,000 |
| safetensors | 256 MiB tensor per file | 512 MiB | 4.1 s | 116 MiB | 1 |
| image | 6000×4000 px (24 MP) PNG | 2 MiB | 3.0 s | 550 MiB | 2 |
| audio | 10 min stereo 44.1 kHz WAV | 202 MiB | 2.8 s | 64 MiB | 1 |
| binary | 512 MiB, one byte changed per 64 MiB | 1,024 MiB | 2.2 s | 39 MiB | 8 |

(*Changes* counts every verified change; e.g. CSV's 5,001 = 5,000 modified rows + one statistics change.)

## Reading the numbers

- **Streaming plugins** — SQLite (merge-join), safetensors (chunked reads), audio (block decoding)
  and binary (chunked XOR) — keep memory roughly constant: 1 GiB of binary input used 39 MiB.
- **Tabular plugins** use Polars' streaming engine, but the key join and hash aggregation still
  materialise key columns and hashes; memory grows with row count (≈1 KiB per row in these runs).
  Use `max_file_size_mb` to bound it; out-of-core keyed comparison is on the [roadmap](roadmap.md).
- **Images** are decoded fully by Pillow (24 MP RGB ≈ 70 MiB per image) plus strip buffers, SSIM on
  a downscaled copy, and previews.

Reproduce or scale up/down with `--scale` (e.g. `--scale 4` for 4× larger inputs) and select
formats with `--only csv,sqlite`.
