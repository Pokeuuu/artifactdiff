"""safetensors comparison: header (tensors, dtypes, shapes, metadata) and chunked value statistics.

The file is opened with the official ``safetensors`` library (``safe_open``), which validates it
against the reference implementation; the header is additionally parsed and bounds-checked here.
Tensor data is then read in fixed-size chunks with ordinary file reads, so memory use does not
depend on tensor size. Weights are only ever read as numbers — never executed or deserialised
with pickle.
"""

from __future__ import annotations

import itertools
import json
import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import Field

from artifactdiff.errors import PluginInputError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, PluginOptions
from artifactdiff_plugins import _tensors
from artifactdiff_plugins._tensors import TensorInfo

if TYPE_CHECKING:
    import numpy as np

MAX_HEADER = 100 * 1024 * 1024  # same bound as the reference implementation
DTYPES: dict[str, tuple[str, int]] = {  # name -> (numpy dtype or special, itemsize)
    "F64": ("<f8", 8),
    "F32": ("<f4", 4),
    "F16": ("<f2", 2),
    "BF16": ("bf16", 2),
    "I64": ("<i8", 8),
    "I32": ("<i4", 4),
    "I16": ("<i2", 2),
    "I8": ("i1", 1),
    "U64": ("<u8", 8),
    "U32": ("<u4", 4),
    "U16": ("<u2", 2),
    "U8": ("u1", 1),
    "BOOL": ("?", 1),
    "F8_E4M3": ("raw", 1),
    "F8_E5M2": ("raw", 1),
}


class SafetensorsOptions(PluginOptions):
    compare_values: bool = Field(True, description="Read tensor data (disable for header-only comparison).")
    compute_stats: bool = Field(True, description="Report min/max/mean/std for changed, added and removed tensors.")
    atol: float = Field(0.0, ge=0, description="Absolute tolerance per element.")
    chunk_elements: int = Field(1 << 20, ge=1024, le=1 << 26, description="Elements read per chunk.")
    detect_renames: bool = Field(True, description="Report unique byte-identical removed/added tensors as renames.")


@dataclass
class _Entry:
    info: TensorInfo
    begin: int  # absolute file offset


class SafetensorsStore:
    def __init__(self, file: ArtifactFile) -> None:
        self.file = file
        self.metadata: dict[str, str] = {}
        self.entries: dict[str, _Entry] = {}
        self._parse()

    def _parse(self) -> None:
        f = self.file
        if f.size < 8:
            raise PluginInputError(f"{f.display_name} is too small to be a safetensors file")
        with f.open() as fh:
            (n,) = struct.unpack("<Q", fh.read(8))
            if n > MAX_HEADER or 8 + n > f.size:
                raise PluginInputError(f"{f.display_name}: invalid header length {n:,}")
            raw = fh.read(n)
        try:
            header = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginInputError(f"{f.display_name}: header is not valid JSON: {exc}") from exc
        if not isinstance(header, dict):
            raise PluginInputError(f"{f.display_name}: header must be a JSON object")
        # Validate with the official implementation as well.
        try:
            from safetensors import safe_open

            with safe_open(str(f.path), framework="numpy") as st:
                lib_keys = set(st.keys())
                self.metadata = dict(st.metadata() or {})
        except Exception as exc:  # the library raises SafetensorError / various I/O errors
            raise PluginInputError(f"{f.display_name} was rejected by the safetensors library: {exc}") from exc
        data_start = 8 + n
        data_len = f.size - data_start
        spans = []
        for name, spec in header.items():
            if name == "__metadata__":
                continue
            try:
                dtype, shape, (b0, b1) = spec["dtype"], tuple(int(s) for s in spec["shape"]), spec["data_offsets"]
            except (KeyError, TypeError, ValueError) as exc:
                raise PluginInputError(f"{f.display_name}: malformed entry for tensor '{name}'") from exc
            if dtype not in DTYPES:
                raise PluginInputError(f"{f.display_name}: tensor '{name}' has unsupported dtype {dtype}")
            nbytes = math.prod(shape) * DTYPES[dtype][1] if shape else DTYPES[dtype][1]
            if not (0 <= b0 <= b1 <= data_len) or b1 - b0 != nbytes:
                raise PluginInputError(f"{f.display_name}: tensor '{name}' has inconsistent data offsets")
            spans.append((b0, b1))
            self.entries[name] = _Entry(
                TensorInfo(name, dtype, shape, nbytes, DTYPES[dtype][0] != "raw"), data_start + b0
            )
        spans.sort()
        for (_, e0), (s1, _) in itertools.pairwise(spans):
            if s1 < e0:
                raise PluginInputError(f"{f.display_name}: tensor data regions overlap")
        if lib_keys != set(self.entries):
            raise PluginInputError(f"{f.display_name}: header disagrees with the safetensors library")

    def tensors(self) -> dict[str, TensorInfo]:
        return {k: v.info for k, v in sorted(self.entries.items())}

    def iter_raw(self, name: str, chunk_bytes: int) -> Iterator[bytes]:
        e = self.entries[name]
        remaining = e.info.nbytes
        with self.file.open() as fh:
            fh.seek(e.begin)
            while remaining > 0:
                b = fh.read(min(chunk_bytes, remaining))
                if not b:
                    raise PluginInputError(f"{self.file.display_name}: unexpected end of data in '{name}'")
                remaining -= len(b)
                yield b

    def iter_values(self, name: str, chunk_elements: int) -> Iterator[np.ndarray]:
        import numpy as np

        e = self.entries[name]
        kind, size = DTYPES[e.info.dtype]
        for raw in self.iter_raw(name, chunk_elements * size):
            if kind == "bf16":
                u = np.frombuffer(raw, dtype="<u2").astype(np.uint32) << 16
                yield u.view(np.float32).astype(np.float64)
            else:
                yield np.frombuffer(raw, dtype=kind).astype(np.float64)


class SafetensorsPlugin(ArtifactPlugin):
    name = "safetensors"
    version = "1.0.0"
    display_name = "safetensors (ML weights)"
    description = "Tensor-level comparison of .safetensors model weights with chunked numerical statistics."
    format_id = "safetensors"
    format_name = "safetensors"
    supported_extensions = (".safetensors",)
    capabilities = ("tensor-diff", "dtype", "shape", "statistics", "l2-difference", "rename-detection", "chunked")
    limitations = (
        "FP8 tensors are compared byte-wise; numerical statistics are not computed for them.",
        "Rename detection requires byte-identical data; renamed *and* modified tensors appear as removed + added.",
    )
    requires = ("safetensors", "numpy")
    install_hint = 'pip install "artifactdiff[safetensors]"'
    Options = SafetensorsOptions

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        store = SafetensorsStore(file)
        ts = store.tensors()
        dtypes: dict[str, int] = {}
        for t in ts.values():
            dtypes[t.dtype] = dtypes.get(t.dtype, 0) + 1
        return {
            "tensors": len(ts),
            "parameters": sum(t.elements for t in ts.values()),
            "dtypes": dtypes,
            "metadata": store.metadata,
        }

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, SafetensorsOptions)
        a, b = SafetensorsStore(old), SafetensorsStore(new)
        for key in sorted(set(a.metadata) | set(b.metadata)):
            va, vb = a.metadata.get(key), b.metadata.get(key)
            if va != vb:
                kind = ChangeKind.ADDED if va is None else ChangeKind.REMOVED if vb is None else ChangeKind.MODIFIED
                ctx.add_change(
                    kind,
                    ChangeCategory.METADATA,
                    f"metadata:{key}",
                    f"Metadata '{key}' {kind.value}",
                    old=va,
                    new=vb,
                    severity=ChangeSeverity.INFO,
                    evidence="__metadata__ in the header",
                )
        if old.size != new.size:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.METADATA,
                "file_size",
                f"File size changed by {new.size - old.size:+,} bytes",
                old=old.size,
                new=new.size,
                severity=ChangeSeverity.INFO,
                evidence="File sizes",
            )
        if not options.compare_values:
            ctx.limitation("Values were not compared (compare_values=false); only headers were compared.")
        _tensors.compare_stores(
            ctx,
            a,
            b,
            _tensors.TensorDiffOptions(
                compare_values=options.compare_values,
                compute_stats=options.compute_stats,
                atol=options.atol,
                chunk_elements=options.chunk_elements,
                detect_renames=options.detect_renames,
            ),
        )
