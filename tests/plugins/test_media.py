from __future__ import annotations

import base64
from pathlib import Path

import pytest
from helpers import kinds_paths, write

from artifactdiff.engine import Engine
from artifactdiff.models import ComparisonStatus

np = pytest.importorskip("numpy")


# ---------------------------------------------------------------------------------- image
def test_image_regions_and_metadata(engine: Engine, tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from samples import image_pair

    r = engine.compare(*image_pair(tmp_path))
    kp = kinds_paths(r)
    assert ("modified", "pixels") in kp
    assert ("added", "text:Software") in kp
    regions = [c for c in r.changes if c.path.startswith("region[")]
    assert len(regions) == 1
    x0, y0, x1, y1 = regions[0].details["bbox"]
    assert x0 <= 66 and y0 <= 39 and x1 >= 86 and y1 >= 56  # the red rectangle drawn at (66,39)-(86,56)
    assert {a.role for a in r.assets} >= {"old-preview", "new-preview", "heatmap", "overlay"}
    assert 0 < r.statistics.metrics["ssim"] < 1
    visual = {c.category.value for c in r.changes if c.path in ("pixels",) or c.path.startswith("region")}
    meta = {c.category.value for c in r.changes if c.path.startswith("text:")}
    assert visual == {"visual"} and meta == {"metadata"}


def test_image_tolerance(engine: Engine, tmp_path: Path) -> None:
    from PIL import Image

    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (40, 40), (100, 100, 100)).save(a)
    Image.new("RGB", (40, 40), (103, 100, 100)).save(b)
    assert engine.compare(a, b).status == ComparisonStatus.DIFFERENT
    r = engine.compare(a, b, options={"tolerance": 5})
    assert r.status == ComparisonStatus.EQUIVALENT
    assert r.statistics.metrics["max_channel_difference"] == 3


def test_image_dimension_change(engine: Engine, tmp_path: Path) -> None:
    from PIL import Image

    a, b = tmp_path / "a.png", tmp_path / "b.jpg"
    Image.new("RGB", (40, 30), (10, 20, 30)).save(a)
    Image.new("RGB", (80, 60), (10, 20, 30)).save(b, quality=95)
    r = engine.compare(a, b)
    kp = kinds_paths(r)
    assert ("modified", "dimensions") in kp and ("modified", "format") in kp
    assert any(w.code == "pixel-diff-skipped" for w in r.warnings)


def test_image_formats_webp_tiff(engine: Engine, tmp_path: Path) -> None:
    from PIL import Image

    for ext in ("webp", "tiff"):
        a, b = tmp_path / f"a.{ext}", tmp_path / f"b.{ext}"
        Image.new("RGB", (32, 32), (0, 0, 0)).save(a, lossless=True) if ext == "webp" else Image.new(
            "RGB", (32, 32), (0, 0, 0)
        ).save(a)
        im = Image.new("RGB", (32, 32), (0, 0, 0))
        im.putpixel((5, 5), (255, 255, 255))
        im.save(b, lossless=True) if ext == "webp" else im.save(b)
        r = engine.compare(a, b)
        assert r.plugin is not None and r.plugin.name == "image"
        assert r.statistics.metrics["changed_pixels"] == 1


def test_image_truncated_is_error(engine: Engine, tmp_path: Path) -> None:
    from samples import image_pair

    a, b = image_pair(tmp_path)
    bad = write(tmp_path / "bad.png", b.read_bytes()[:60])
    assert engine.compare(a, bad).status == ComparisonStatus.ERROR


def test_image_pixel_limit(engine: Engine, tmp_path: Path) -> None:
    from samples import image_pair

    r = engine.compare(*image_pair(tmp_path), options={"max_pixels": 100})
    assert r.status == ComparisonStatus.ERROR and "Resource limit" in (r.error or "")


def test_image_large_streams_in_strips(engine: Engine, tmp_path: Path) -> None:
    from PIL import Image

    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (3000, 2000), (5, 5, 5)).save(a)
    im = Image.new("RGB", (3000, 2000), (5, 5, 5))
    for x in range(2990, 3000):
        im.putpixel((x, 1999), (250, 0, 0))
    im.save(b)
    r = engine.compare(a, b, options={"ssim": False})
    assert r.statistics.metrics["changed_pixels"] == 10
    region = next(c for c in r.changes if c.path.startswith("region["))
    assert region.details["bbox"][2] == 3000 and region.details["bbox"][3] == 2000


# ---------------------------------------------------------------------------------- audio
def test_audio_sample(engine: Engine, tmp_path: Path) -> None:
    pytest.importorskip("soundfile")
    from samples import audio_pair

    r = engine.compare(*audio_pair(tmp_path))
    kp = {p for _, p in kinds_paths(r)}
    assert {"duration", "level:rms_dbfs", "level:peak_dbfs", "silence:trailing"} <= kp
    assert any(p.startswith("spectrum:") for p in kp)
    assert any(p.startswith("time[") for p in kp)
    assert {a.role for a in r.assets} == {"waveform", "spectrogram"}
    assert r.statistics.metrics["old_peak_dbfs"] == pytest.approx(-6.02, abs=0.1)


def test_audio_sample_rate_and_channels(engine: Engine, tmp_path: Path) -> None:
    from samples import tone

    a = tone(tmp_path / "a.wav", sr=16000)
    b = tone(tmp_path / "b.flac", sr=22050, channels=2)
    r = engine.compare(a, b)
    kp = {p for _, p in kinds_paths(r)}
    assert {"samplerate", "channels", "format"} <= kp
    assert any(w.code == "waveform-skipped" for w in r.warnings)


def test_audio_identical_signal_different_container(engine: Engine, tmp_path: Path) -> None:
    from samples import tone

    a = tone(tmp_path / "a.wav", sr=16000)
    b = tone(tmp_path / "b.flac", sr=16000)
    r = engine.compare(a, b)
    assert {c.path for c in r.changes} == {"format"}
    # libsndfile may round float->PCM16 differently per container: at most 1 LSB
    assert r.statistics.metrics["max_abs_sample_difference"] <= 2 / 32768


def test_audio_ogg(engine: Engine, tmp_path: Path) -> None:
    from samples import tone

    a = tone(tmp_path / "a.ogg", sr=16000, subtype="VORBIS")
    b = tone(tmp_path / "b.ogg", sr=16000, seconds=1.5, subtype="VORBIS")
    r = engine.compare(a, b)
    assert ("modified", "duration") in kinds_paths(r)


def test_audio_corrupt(engine: Engine, tmp_path: Path) -> None:
    from samples import tone

    a = tone(tmp_path / "a.wav")
    bad = write(tmp_path / "bad.wav", b"RIFF\x10\x00\x00\x00WAVEjunkjunk")
    assert engine.compare(a, bad).status == ComparisonStatus.ERROR


# ------------------------------------------------------------------------------------ PDF
def test_pdf_sample(engine: Engine, tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")
    from samples import pdf_pair

    r = engine.compare(*pdf_pair(tmp_path))
    kp = kinds_paths(r)
    assert ("added", "page[3]") in kp
    assert ("modified", "metadata:Title") in kp
    text = next(c for c in r.changes if c.category.value == "text")
    assert text.old_value == "Revenue: 100" and text.new_value == "Revenue: 120"
    assert any(c.path == "page[1]/render" for c in r.changes)
    assert any(a.role.startswith("overlay-page") for a in r.assets)


def test_pdf_inserted_page_does_not_shift(engine: Engine, tmp_path: Path) -> None:
    from samples import make_pdf

    a = make_pdf(tmp_path / "a.pdf", [["one"], ["two"], ["three"]])
    b = make_pdf(tmp_path / "b.pdf", [["one"], ["inserted"], ["two"], ["three"]])
    r = engine.compare(a, b)
    assert kinds_paths(r) == {("added", "page[2]")}


def test_pdf_page_size(engine: Engine, tmp_path: Path) -> None:
    from samples import make_pdf

    a = make_pdf(tmp_path / "a.pdf", [["x"]], size=(300, 200))
    b = make_pdf(tmp_path / "b.pdf", [["x"]], size=(612, 792))
    assert ("modified", "page[1]/size") in kinds_paths(engine.compare(a, b))


def test_pdf_corrupt(engine: Engine, tmp_path: Path) -> None:
    from samples import make_pdf

    a = make_pdf(tmp_path / "a.pdf", [["x"]])
    bad = write(tmp_path / "bad.pdf", b"%PDF-1.4\ngarbage")
    assert engine.compare(a, bad).status == ComparisonStatus.ERROR


# ----------------------------------------------------------------------------------- glTF
def test_gltf_sample(engine: Engine, tmp_path: Path) -> None:
    from samples import gltf_pair

    r = engine.compare(*gltf_pair(tmp_path))
    kp = kinds_paths(r)
    geo = next(c for c in r.changes if c.path == "mesh:CubeMesh/primitive[0]/geometry")
    assert "counts unchanged" in geo.description and geo.details["max_vertex_displacement"] > 0
    assert ("modified", "node:Marker/transform") in kp
    assert ("added", "node:Extra") in kp and ("added", "mesh:TriMesh2") in kp
    assert ("modified", "material:Red/baseColorFactor") in kp
    assert ("modified", "scene/bounding_box") in kp
    assert any(a.role == "wireframe" for a in r.assets)


def test_gltf_rename_detection(engine: Engine, tmp_path: Path) -> None:
    from samples import CUBE_IDX, CUBE_POS, make_glb

    a = make_glb(
        tmp_path / "a.glb", [{"name": "M", "positions": CUBE_POS, "indices": CUBE_IDX, "node": {"name": "OldName"}}]
    )
    b = make_glb(
        tmp_path / "b.glb", [{"name": "M", "positions": CUBE_POS, "indices": CUBE_IDX, "node": {"name": "NewName"}}]
    )
    r = engine.compare(a, b)
    ren = [c for c in r.changes if c.kind.value == "renamed"]
    assert len(ren) == 1 and ren[0].confidence == 0.9


def test_gltf_json_with_data_uri_and_external_rejected(engine: Engine, tmp_path: Path) -> None:
    import json

    import numpy as np

    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32).tobytes()

    def doc(uri: str) -> str:
        return json.dumps(
            {
                "asset": {"version": "2.0"},
                "buffers": [{"byteLength": len(pos), "uri": uri}],
                "bufferViews": [{"buffer": 0, "byteLength": len(pos)}],
                "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
                "meshes": [{"name": "T", "primitives": [{"attributes": {"POSITION": 0}}]}],
                "nodes": [{"name": "N", "mesh": 0}],
                "scenes": [{"nodes": [0]}],
            }
        )

    a = write(tmp_path / "a.gltf", doc("data:application/octet-stream;base64," + base64.b64encode(pos).decode()))
    b = write(tmp_path / "b.gltf", doc("../../etc/passwd"))
    r = engine.compare(a, b)
    assert any("not allowed" in w.message or "not available" in w.message for w in r.warnings)
    assert any(w.code == "geometry-unavailable" for w in r.warnings)


def test_gltf_corrupt(engine: Engine, tmp_path: Path) -> None:
    from samples import gltf_pair

    a, b = gltf_pair(tmp_path)
    bad = write(tmp_path / "bad.glb", b.read_bytes()[:40])
    assert engine.compare(a, bad).status == ComparisonStatus.ERROR


# ---------------------------------------------------------------------------- safetensors
def test_safetensors_sample(engine: Engine, tmp_path: Path) -> None:
    pytest.importorskip("safetensors")
    from samples import safetensors_pair

    r = engine.compare(*safetensors_pair(tmp_path))
    kp = kinds_paths(r)
    assert ("renamed", "tensor:embeddings.weight") in kp
    assert ("modified", "tensor:head.weight/shape") in kp
    assert ("added", "tensor:layer2.weight") in kp
    assert ("modified", "metadata:version") in kp
    assert ("modified", "parameters") in kp
    vals = next(c for c in r.changes if c.path == "tensor:layer1.weight/values")
    assert vals.details["differing_elements"] == 8
    assert vals.details["max_abs_difference"] == pytest.approx(0.01, rel=1e-3)
    assert set(vals.old_value) >= {"min", "max", "mean", "std"}
    assert r.statistics.metrics["unchanged_tensors"] == 1  # layer1.bias


def test_safetensors_bf16_and_dtype_change(engine: Engine, tmp_path: Path) -> None:
    # BF16 is not a NumPy dtype: write raw safetensors by hand.
    import json
    import struct

    from safetensors.numpy import save_file

    def bf16_file(path: Path, values: list[float]) -> Path:
        f32 = np.array(values, np.float32)
        raw = (f32.view(np.uint32) >> 16).astype("<u2").tobytes()
        header = json.dumps({"w": {"dtype": "BF16", "shape": [len(values)], "data_offsets": [0, len(raw)]}}).encode()
        header += b" " * ((8 - len(header) % 8) % 8)
        path.write_bytes(struct.pack("<Q", len(header)) + header + raw)
        return path

    a = bf16_file(tmp_path / "a.safetensors", [1.0, 2.0, 3.0])
    b = bf16_file(tmp_path / "b.safetensors", [1.0, 2.0, 4.0])
    r = engine.compare(a, b)
    vals = next(c for c in r.changes if c.path == "tensor:w/values")
    assert vals.details["max_abs_difference"] == 1.0 and vals.new_value["max"] == 4.0
    c = tmp_path / "c.safetensors"
    save_file({"w": np.array([1.0, 2.0, 4.0], np.float32)}, str(c))
    assert ("type_changed", "tensor:w/dtype") in kinds_paths(engine.compare(a, c))


def test_safetensors_malformed(engine: Engine, tmp_path: Path) -> None:
    import struct

    from samples import safetensors_pair

    a, _ = safetensors_pair(tmp_path)
    huge = write(tmp_path / "huge.safetensors", struct.pack("<Q", 1 << 40) + b"{}")
    assert engine.compare(a, huge).status == ComparisonStatus.ERROR
    bad_offsets = b'{"w":{"dtype":"F32","shape":[4],"data_offsets":[0,8]}}'
    bad = write(tmp_path / "bad.safetensors", struct.pack("<Q", len(bad_offsets)) + bad_offsets + b"\x00" * 8)
    r = engine.compare(a, bad)
    assert r.status == ComparisonStatus.ERROR


@pytest.mark.slow
def test_safetensors_memory_is_bounded(engine: Engine, tmp_path: Path) -> None:
    import tracemalloc

    from safetensors.numpy import save_file

    n = 8 * 1024 * 1024  # 32 MiB float32 per file
    x = np.zeros(n, np.float32)
    save_file({"big": x}, str(tmp_path / "a.safetensors"))
    x[::1000] = 1.0
    save_file({"big": x}, str(tmp_path / "b.safetensors"))
    del x
    tracemalloc.start()
    r = engine.compare(tmp_path / "a.safetensors", tmp_path / "b.safetensors", options={"chunk_elements": 1 << 18})
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert r.status == ComparisonStatus.DIFFERENT
    assert peak < 24 * 1024 * 1024, f"peak traced memory {peak / 1e6:.1f} MB for 2x32 MiB inputs"
