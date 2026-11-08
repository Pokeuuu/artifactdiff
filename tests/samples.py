"""Deterministic, synthetic sample artifacts for tests, examples and benchmarks.

Everything here is generated from code (no third-party data), so it is freely distributable.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import zlib
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------- JSON / text


def json_pair(d: Path) -> tuple[Path, Path]:
    old = {
        "model": {
            "name": "resnet",
            "layers": [{"type": "conv", "units": 64}, {"type": "relu"}, {"type": "dense", "units": 10}],
        },
        "training": {"epochs": 10, "lr": 0.001},
        "tags": ["vision", "baseline"],
    }
    new = {
        "model": {
            "name": "resnet-v2",
            "layers": [
                {"type": "conv", "units": 128},
                {"type": "relu"},
                {"type": "dropout", "rate": 0.5},
                {"type": "dense", "units": 10},
            ],
        },
        "training": {"epochs": "20", "lr": 0.001, "seed": 42},
        "tags": ["vision"],
    }
    a, b = d / "old.json", d / "new.json"
    a.write_text(json.dumps(old, indent=2), encoding="utf-8")
    b.write_text(json.dumps(new, indent=2), encoding="utf-8")
    return a, b


# ------------------------------------------------------------------------------------ CSV


def csv_text(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join("" if r.get(c) is None else str(r[c]) for c in cols))
    return "\n".join(lines) + "\n"


def csv_pair(d: Path) -> tuple[Path, Path]:
    cols = ["id", "name", "age", "score"]
    old = [{"id": i, "name": f"user{i}", "age": 20 + i, "score": round(50 + i * 1.5, 1)} for i in range(1, 11)]
    new = [dict(r) for r in old if r["id"] != 3]
    new[0]["score"] = 99.5
    new[4]["name"] = "renamed"
    new[5]["age"] = None
    new.append({"id": 11, "name": "user11", "age": 31, "score": 66.0, "country": "NZ"})
    for r in new:
        r.setdefault("country", "US")
    a, b = d / "old.csv", d / "new.csv"
    a.write_text(csv_text(old, cols), encoding="utf-8")
    b.write_text(csv_text(new, [*cols, "country"]), encoding="utf-8")
    return a, b


# -------------------------------------------------------------------------------- Parquet


def parquet_pair(d: Path) -> tuple[Path, Path]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    old = pa.table(
        {
            "id": pa.array([1, 2, 3, 4], pa.int64()),
            "price": pa.array([1.0, 2.0, 3.0, 4.0]),
            "sku": pa.array(["a", "b", "c", "d"]),
            "qty": pa.array([1, 2, 3, 4], pa.int32()),
        }
    )
    schema = pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("price", pa.float64()),
            pa.field("sku", pa.string()),
            pa.field("qty", pa.int64()),
            pa.field("note", pa.string()),
        ]
    )
    new = pa.table(
        {
            "id": [1, 2, 4, 5],
            "price": [1.0, 2.5, 4.0, 9.0],
            "sku": ["a", "b", "d", "e"],
            "qty": [1, 2, 4, 5],
            "note": [None, "promo", None, "new"],
        },
        schema=schema,
    )
    a, b = d / "old.parquet", d / "new.parquet"
    pq.write_table(old, a, compression="snappy")
    pq.write_table(new, b, compression="zstd")
    return a, b


# --------------------------------------------------------------------------------- SQLite


def sqlite_db(path: Path, statements: list[str]) -> Path:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        for s in statements:
            conn.execute(s)
        conn.commit()
    finally:
        conn.close()
    return path


def sqlite_pair(d: Path) -> tuple[Path, Path]:
    a = sqlite_db(
        d / "old.sqlite",
        [
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL, name TEXT)",
            "CREATE TABLE logs (msg TEXT, level INTEGER)",
            "CREATE TABLE legacy (x INTEGER)",
            "CREATE INDEX idx_users_email ON users(email)",
            "CREATE TRIGGER trg AFTER INSERT ON users BEGIN SELECT 1; END",
            "INSERT INTO users VALUES (1,'a@x.io','Ann'),(2,'b@x.io','Bob'),(3,'c@x.io','Cy')",
            "INSERT INTO logs VALUES ('start',1),('run',2)",
        ],
    )
    b = sqlite_db(
        d / "new.sqlite",
        [
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL, name TEXT, active INTEGER DEFAULT 1)",
            "CREATE TABLE logs (msg TEXT, level INTEGER)",
            "CREATE TABLE audit (id INTEGER PRIMARY KEY, what TEXT)",
            "CREATE UNIQUE INDEX idx_users_email ON users(email)",
            "INSERT INTO users VALUES (1,'a@x.io','Ann',1),(2,'b@x.io','Robert',1),(4,'d@x.io','Dee',0)",
            "INSERT INTO logs VALUES ('start',1),('run',3)",
        ],
    )
    return a, b


# ---------------------------------------------------------------------------------- Images


def image_pair(d: Path, size: tuple[int, int] = (96, 64)) -> tuple[Path, Path]:
    from PIL import Image, ImageDraw

    w, h = size
    old = Image.new("RGB", size, (30, 60, 90))
    dr = ImageDraw.Draw(old)
    dr.rectangle((10, 10, 40, 40), fill=(200, 180, 40))
    new = old.copy()
    dr2 = ImageDraw.Draw(new)
    dr2.rectangle((w - 30, h - 25, w - 10, h - 8), fill=(220, 30, 30))
    a, b = d / "old.png", d / "new.png"
    old.save(a)
    meta = __import__("PIL.PngImagePlugin", fromlist=["PngInfo"]).PngInfo()
    meta.add_text("Software", "ArtifactDiff sample generator")
    new.save(b, pnginfo=meta)
    return a, b


# ----------------------------------------------------------------------------------- Audio


def tone(
    path: Path,
    *,
    freq: float = 440.0,
    seconds: float = 1.0,
    sr: int = 16_000,
    amp: float = 0.5,
    silence_tail: float = 0.0,
    channels: int = 1,
    subtype: str = "PCM_16",
) -> Path:
    import numpy as np
    import soundfile as sf

    t = np.arange(int(seconds * sr)) / sr
    x = amp * np.sin(2 * np.pi * freq * t)
    if silence_tail:
        x = np.concatenate([x, np.zeros(int(silence_tail * sr))])
    data = np.stack([x] * channels, axis=1) if channels > 1 else x
    sf.write(path, data, sr, subtype=subtype)
    return path


def audio_pair(d: Path) -> tuple[Path, Path]:
    a = tone(d / "old.wav", freq=440, seconds=1.0, amp=0.5)
    b = tone(d / "new.wav", freq=660, seconds=1.0, amp=0.25, silence_tail=0.6)
    return a, b


# ------------------------------------------------------------------------------------- PDF


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(
    path: Path,
    pages: list[list[str]],
    *,
    size: tuple[int, int] = (300, 200),
    title: str | None = None,
    sizes: list[tuple[int, int]] | None = None,
) -> Path:
    """Write a minimal, valid PDF with one text line per entry using the standard Helvetica font."""
    objs: list[bytes] = []
    n_pages = len(pages)
    page_ids = [3 + 2 * i for i in range(n_pages)]
    font_id = 3 + 2 * n_pages
    info_id = font_id + 1
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {n_pages} >>".encode())
    for i, lines in enumerate(pages):
        w, h = sizes[i] if sizes else size
        ops = ["BT", "/F1 12 Tf", "14 TL", f"20 {h - 30} Td"]
        for line in lines:
            ops.append(f"({_pdf_escape(line)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] /Contents {page_ids[i] + 1} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(f"<< /Title ({_pdf_escape(title or '')}) /Producer (ArtifactDiff samples) >>".encode())
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {info_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


def pdf_pair(d: Path) -> tuple[Path, Path]:
    a = make_pdf(d / "old.pdf", [["Quarterly report", "Revenue: 100"], ["Appendix A"]], title="Report v1")
    b = make_pdf(
        d / "new.pdf", [["Quarterly report", "Revenue: 120"], ["Appendix A"], ["Appendix B"]], title="Report v2"
    )
    return a, b


# ------------------------------------------------------------------------------------ glTF


def _pad4(b: bytes, fill: bytes = b"\x00") -> bytes:
    return b + fill * ((4 - len(b) % 4) % 4)


def make_glb(path: Path, meshes: list[dict[str, Any]], *, materials: list[dict[str, Any]] | None = None) -> Path:
    """Write a GLB.

    Each mesh is ``{name, positions: [(x,y,z)...], indices: [...], node: {name, translation?}, material?}``.
    """
    import numpy as np

    bin_parts: list[bytes] = []
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    gl_meshes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    offset = 0
    for m in meshes:
        pos = np.asarray(m["positions"], dtype=np.float32)
        idx = np.asarray(m["indices"], dtype=np.uint16)
        for arr, target in ((pos, 34962), (idx, 34963)):
            raw = _pad4(arr.tobytes())
            buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": arr.nbytes, "target": target})
            bin_parts.append(raw)
            offset += len(raw)
        accessors.append(
            {
                "bufferView": len(buffer_views) - 2,
                "componentType": 5126,
                "count": len(pos),
                "type": "VEC3",
                "min": pos.min(axis=0).tolist(),
                "max": pos.max(axis=0).tolist(),
            }
        )
        accessors.append(
            {"bufferView": len(buffer_views) - 1, "componentType": 5123, "count": len(idx), "type": "SCALAR"}
        )
        prim: dict[str, Any] = {"attributes": {"POSITION": len(accessors) - 2}, "indices": len(accessors) - 1}
        if "material" in m:
            prim["material"] = m["material"]
        gl_meshes.append({"name": m["name"], "primitives": [prim]})
        node = {"name": m["node"]["name"], "mesh": len(gl_meshes) - 1}
        for k in ("translation", "rotation", "scale"):
            if k in m["node"]:
                node[k] = m["node"][k]
        nodes.append(node)
    gltf = {
        "asset": {"version": "2.0", "generator": "ArtifactDiff samples"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": gl_meshes,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": offset}],
    }
    if materials:
        gltf["materials"] = materials
    js = _pad4(json.dumps(gltf, separators=(",", ":")).encode(), b" ")
    binc = b"".join(bin_parts)
    total = 12 + 8 + len(js) + 8 + len(binc)
    out = struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
    out += struct.pack("<I4s", len(binc), b"BIN\x00") + binc
    path.write_bytes(out)
    return path


CUBE_POS = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
CUBE_IDX = [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 2, 3, 7, 2, 7, 6, 1, 2, 6, 1, 6, 5, 0, 3, 7, 0, 7, 4]
TRI_POS = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]


def gltf_pair(d: Path) -> tuple[Path, Path]:
    mats_a = [{"name": "Red", "pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1], "roughnessFactor": 0.5}}]
    mats_b = [{"name": "Red", "pbrMetallicRoughness": {"baseColorFactor": [0.8, 0, 0, 1], "roughnessFactor": 0.5}}]
    a = make_glb(
        d / "old.glb",
        [
            {"name": "CubeMesh", "positions": CUBE_POS, "indices": CUBE_IDX, "node": {"name": "Cube"}, "material": 0},
            {
                "name": "TriMesh",
                "positions": TRI_POS,
                "indices": [0, 1, 2],
                "node": {"name": "Marker", "translation": [3, 0, 0]},
            },
        ],
        materials=mats_a,
    )
    moved = [(x, y, z * 1.5) for x, y, z in CUBE_POS]  # same counts, different geometry
    b = make_glb(
        d / "new.glb",
        [
            {"name": "CubeMesh", "positions": moved, "indices": CUBE_IDX, "node": {"name": "Cube"}, "material": 0},
            {
                "name": "TriMesh",
                "positions": TRI_POS,
                "indices": [0, 1, 2],
                "node": {"name": "Marker", "translation": [3, 2, 0]},
            },
            {"name": "TriMesh2", "positions": TRI_POS, "indices": [0, 1, 2], "node": {"name": "Extra"}},
        ],
        materials=mats_b,
    )
    return a, b


# ----------------------------------------------------------------------------- safetensors


def safetensors_pair(d: Path) -> tuple[Path, Path]:
    import numpy as np
    from safetensors.numpy import save_file

    rng = np.random.default_rng(7)
    w1 = rng.standard_normal((16, 8)).astype(np.float32)
    b1 = np.zeros(8, dtype=np.float32)
    emb = rng.standard_normal((32, 4)).astype(np.float16)
    head = rng.standard_normal((8, 2)).astype(np.float32)
    old = {"layer1.weight": w1, "layer1.bias": b1, "embed.weight": emb, "head.weight": head}
    w1b = w1.copy()
    w1b[0, :] += 0.01
    new = {
        "layer1.weight": w1b,
        "layer1.bias": b1.copy(),
        "embeddings.weight": emb.copy(),
        "head.weight": rng.standard_normal((8, 3)).astype(np.float32),
        "layer2.weight": np.ones((4, 4), np.float32),
    }
    a, b = d / "old.safetensors", d / "new.safetensors"
    save_file(old, str(a), metadata={"format": "np", "version": "1"})
    save_file(new, str(b), metadata={"format": "np", "version": "2"})
    return a, b


def crc_png_chunk(kind: bytes, data: bytes) -> bytes:  # used by corrupted-file tests
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
