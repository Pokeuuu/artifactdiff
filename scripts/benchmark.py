"""Benchmarks for representative inputs: wall time and peak resident memory per comparison.

Each case generates synthetic inputs, then runs ONE comparison in a fresh subprocess so the
reported peak RSS belongs to that comparison alone (tracemalloc cannot see memory allocated by
Rust/C libraries such as Polars, Arrow or Pillow).

Usage: python scripts/benchmark.py [--scale 1.0] [--only csv,parquet] [--markdown]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

CHILD = r"""
import json, sys, time
from artifactdiff.engine import Engine
from artifactdiff.config import ArtifactDiffConfig, Limits

def peak_rss():
    if sys.platform == "win32":
        import ctypes, ctypes.wintypes as w
        class PMC(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = w.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.POINTER(PMC), w.DWORD]
        psapi.GetProcessMemoryInfo.restype = w.BOOL
        pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return pmc.PeakWorkingSetSize
    import resource
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r if sys.platform == "darwin" else r * 1024

a, b, opts = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
engine = Engine(config=ArtifactDiffConfig(limits=Limits(timeout_seconds=3600, max_file_size_mb=100000)))
baseline = peak_rss()
t = time.perf_counter()
r = engine.compare(a, b, options=opts, generate_assets=True)
print(json.dumps({"seconds": time.perf_counter() - t, "peak_rss": peak_rss(), "baseline_rss": baseline,
                  "status": r.status.value, "changes": r.statistics.total_changes, "plugin": r.plugin.name}))
"""


def gen_csv(d: Path, s: float) -> tuple[Path, Path, dict[str, object], str]:
    n = int(500_000 * s)
    a, b = d / "a.csv", d / "b.csv"
    with a.open("w") as fa, b.open("w") as fb:
        fa.write("id,name,value,score\n")
        fb.write("id,name,value,score\n")
        for i in range(n):
            fa.write(f"{i},user{i},{i * 3},{i % 97}.5\n")
            fb.write(f"{i},user{i},{i * 3 + (1 if i % 100 == 0 else 0)},{i % 97}.5\n")
    return a, b, {"primary_key": ["id"]}, f"{n:,} rows x 4 cols, 1% rows modified"


def gen_parquet(d: Path, s: float) -> tuple[Path, Path, dict[str, object], str]:
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    n = int(2_000_000 * s)
    rng = np.random.default_rng(1)
    vals = rng.standard_normal(n)
    pq.write_table(
        pa.table({"id": np.arange(n), "v": vals, "k": np.arange(n) % 1000}), d / "a.parquet", row_group_size=250_000
    )
    vals[::200] += 1
    pq.write_table(
        pa.table({"id": np.arange(n), "v": vals, "k": np.arange(n) % 1000}), d / "b.parquet", row_group_size=250_000
    )
    return d / "a.parquet", d / "b.parquet", {"primary_key": ["id"]}, f"{n:,} rows x 3 cols, 0.5% modified"


def gen_sqlite(d: Path, s: float) -> tuple[Path, Path, dict[str, object], str]:
    import sqlite3

    n = int(1_000_000 * s)
    for name, mod in (("a.db", 0), ("b.db", 1)):
        conn = sqlite3.connect(d / name)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, a TEXT, b REAL)")
        conn.execute(
            f"WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<{n}) "
            f"INSERT INTO t SELECT x, 'row' || x, x * 0.5 + (CASE WHEN x % 1000 = 0 THEN {mod} ELSE 0 END) "
            "FROM c"
        )
        conn.commit()
        conn.close()
    return d / "a.db", d / "b.db", {}, f"{n:,} rows (keyed), 0.1% modified"


def gen_safetensors(d: Path, s: float) -> tuple[Path, Path, dict[str, object], str]:
    import numpy as np
    from safetensors.numpy import save_file

    n = int(64 * 1024 * 1024 * s)  # float32 elements per file (256 MiB at scale 1)
    x = np.zeros(n, np.float32)
    save_file({"w": x.reshape(-1, 1024)}, str(d / "a.safetensors"))
    x[::997] = 0.5
    save_file({"w": x.reshape(-1, 1024)}, str(d / "b.safetensors"))
    return d / "a.safetensors", d / "b.safetensors", {}, f"{n * 4 / 2**20:.0f} MiB tensor per file"


def gen_image(d: Path, s: float) -> tuple[Path, Path, dict[str, object], str]:
    from PIL import Image, ImageDraw

    w, h = int(6000 * s**0.5), int(4000 * s**0.5)
    img = Image.radial_gradient("L").resize((w, h)).convert("RGB")
    img.save(d / "a.png", compress_level=1)
    ImageDraw.Draw(img).rectangle((w // 3, h // 3, w // 2, h // 2), fill=(255, 0, 0))
    img.save(d / "b.png", compress_level=1)
    return d / "a.png", d / "b.png", {}, f"{w}x{h} px ({w * h / 1e6:.0f} MP)"


def gen_audio(d: Path, s: float) -> tuple[Path, Path, dict[str, object], str]:
    import numpy as np
    import soundfile as sf

    sr, secs = 44_100, int(600 * s)
    t = np.arange(sr * secs, dtype=np.float32) / sr
    x = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(d / "a.wav", np.stack([x, x], 1), sr)
    x[sr * 60 : sr * 61] *= 0.5
    sf.write(d / "b.wav", np.stack([x, x], 1), sr)
    return d / "a.wav", d / "b.wav", {}, f"{secs // 60} min stereo 44.1 kHz WAV"


def gen_binary(d: Path, s: float) -> tuple[Path, Path, dict[str, object], str]:
    n = int(512 * 1024 * 1024 * s)
    chunk = os.urandom(1 << 20)
    with (d / "a.bin").open("wb") as fa, (d / "b.bin").open("wb") as fb:
        for i in range(n >> 20):
            fa.write(chunk)
            fb.write(chunk if i % 64 else bytes([chunk[0] ^ 1]) + chunk[1:])
    return d / "a.bin", d / "b.bin", {}, f"{n / 2**20:.0f} MiB, one byte changed per 64 MiB"


CASES: dict[str, Callable[[Path, float], tuple[Path, Path, dict[str, object], str]]] = {
    "csv": gen_csv,
    "parquet": gen_parquet,
    "sqlite": gen_sqlite,
    "safetensors": gen_safetensors,
    "image": gen_image,
    "audio": gen_audio,
    "binary": gen_binary,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--only", default="")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()
    names = [n for n in CASES if not args.only or n in args.only.split(",")]
    rows = []
    for name in names:
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.perf_counter()
            a, b, opts, desc = CASES[name](Path(tmp), args.scale)
            gen = time.perf_counter() - t0
            size = a.stat().st_size + b.stat().st_size
            out = subprocess.run(
                [sys.executable, "-c", CHILD, str(a), str(b), json.dumps(opts)],
                capture_output=True,
                text=True,
                check=False,
            )
            if out.returncode != 0:
                print(f"{name}: FAILED\n{out.stderr}", file=sys.stderr)
                continue
            res = json.loads(out.stdout.strip().splitlines()[-1])
            rows.append((name, desc, size, res))
            print(
                f"{name:12} {desc:42} inputs {size / 2**20:8.1f} MiB  {res['seconds']:7.2f} s  "
                f"peak RSS {res['peak_rss'] / 2**20:7.0f} MiB  ({res['changes']:,} changes; generated in {gen:.0f}s)",
                flush=True,
            )
    if args.markdown:
        print(f"\nMachine: {platform.platform()}, Python {platform.python_version()}, {os.cpu_count()} CPUs\n")
        print("| Format | Input | Total input size | Time | Peak RSS | Changes |")
        print("|---|---|---:|---:|---:|---:|")
        for name, desc, size, r in rows:
            print(
                f"| {name} | {desc} | {size / 2**20:,.0f} MiB | {r['seconds']:.1f} s | "
                f"{r['peak_rss'] / 2**20:,.0f} MiB | {r['changes']:,} |"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
