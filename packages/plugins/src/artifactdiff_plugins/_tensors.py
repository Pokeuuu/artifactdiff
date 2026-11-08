"""Format-neutral tensor comparison over a :class:`TensorStore`.

A store lists tensors (name, dtype, shape) and yields their values in bounded chunks. The
safetensors plugin implements one; ONNX initializers or GGUF tensors can implement the same
interface and reuse every comparison below unchanged.

Statistics are accumulated in float64 across chunks (Chan et al. parallel variance), so memory
use is bounded by ``chunk_elements`` regardless of tensor size.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ComparisonContext

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True)
class TensorInfo:
    name: str
    dtype: str  # format-specific name, e.g. "F32", "BF16"
    shape: tuple[int, ...]
    nbytes: int
    numeric: bool  # False => values compared byte-wise only (e.g. FP8)

    @property
    def elements(self) -> int:
        return math.prod(self.shape) if self.shape else 1


class TensorStore(Protocol):
    def tensors(self) -> dict[str, TensorInfo]: ...

    def iter_raw(self, name: str, chunk_bytes: int) -> Iterator[bytes]: ...

    def iter_values(self, name: str, chunk_elements: int) -> Iterator[np.ndarray]: ...  # float64 chunks


@dataclass
class Stats:
    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    min: float = math.inf
    max: float = -math.inf
    nan: int = 0
    inf: int = 0

    def update(self, x: np.ndarray) -> None:
        import numpy as np

        finite = np.isfinite(x)
        self.nan += int(np.isnan(x).sum())
        self.inf += int(np.isinf(x).sum())
        x = x[finite]
        n = x.size
        if not n:
            return
        mean = float(x.mean())
        m2 = float(((x - mean) ** 2).sum())
        tot = self.count + n
        delta = mean - self.mean
        self.mean += delta * n / tot
        self.m2 += m2 + delta * delta * self.count * n / tot
        self.count = tot
        self.min = min(self.min, float(x.min()))
        self.max = max(self.max, float(x.max()))

    def as_dict(self) -> dict[str, Any]:
        def r(v: float) -> float | None:
            return None if not math.isfinite(v) else float(f"{v:.6g}")

        return {
            "min": r(self.min),
            "max": r(self.max),
            "mean": r(self.mean) if self.count else None,
            "std": r(math.sqrt(self.m2 / self.count)) if self.count else None,
            "nan": self.nan,
            "inf": self.inf,
        }


def tensor_stats(store: TensorStore, name: str, chunk: int, ctx: ComparisonContext) -> dict[str, Any]:
    s = Stats()
    for x in store.iter_values(name, chunk):
        ctx.check()
        s.update(x)
    return s.as_dict()


def tensor_hash(store: TensorStore, name: str, chunk_bytes: int) -> str:
    h = hashlib.sha256()
    for b in store.iter_raw(name, chunk_bytes):
        h.update(b)
    return h.hexdigest()


def raw_equal(a: TensorStore, b: TensorStore, name_a: str, name_b: str, chunk_bytes: int) -> bool:
    return all(x == y for x, y in zip(a.iter_raw(name_a, chunk_bytes), b.iter_raw(name_b, chunk_bytes), strict=True))


def pair_stats(
    a: TensorStore, b: TensorStore, name: str, chunk: int, atol: float, ctx: ComparisonContext
) -> dict[str, Any]:
    import numpy as np

    sa, sb = Stats(), Stats()
    sq_diff = sq_a = 0.0
    max_abs = 0.0
    differing = 0
    for x, y in zip(a.iter_values(name, chunk), b.iter_values(name, chunk), strict=True):
        ctx.check()
        sa.update(x)
        sb.update(y)
        d = y - x
        both_nan = np.isnan(x) & np.isnan(y)
        d = np.where(both_nan, 0.0, d)
        fd = np.where(np.isfinite(d), d, 0.0)
        sq_diff += float(np.square(fd).sum())
        sq_a += float(np.square(np.where(np.isfinite(x), x, 0.0)).sum())
        ad = np.abs(d)
        if ad.size:
            max_abs = max(max_abs, float(np.nanmax(ad)) if not np.all(np.isnan(ad)) else 0.0)
        differing += int((~(ad <= atol) & ~both_nan).sum())
    l2 = math.sqrt(sq_diff)
    norm_a = math.sqrt(sq_a)
    return {
        "old": sa.as_dict(),
        "new": sb.as_dict(),
        "l2_difference": float(f"{l2:.6g}"),
        "max_abs_difference": float(f"{max_abs:.6g}"),
        "relative_difference": float(f"{l2 / norm_a:.6g}") if norm_a > 0 else None,
        "differing_elements": differing,
    }


@dataclass
class TensorDiffOptions:
    compare_values: bool
    compute_stats: bool
    atol: float
    chunk_elements: int
    detect_renames: bool
    max_listed_unchanged: int = 0


def _severity(rel: float | None) -> ChangeSeverity:
    if rel is None:
        return ChangeSeverity.MEDIUM
    return ChangeSeverity.LOW if rel < 1e-3 else ChangeSeverity.MEDIUM if rel < 0.1 else ChangeSeverity.HIGH


def compare_stores(ctx: ComparisonContext, a: TensorStore, b: TensorStore, opts: TensorDiffOptions) -> None:
    ta, tb = a.tensors(), b.tensors()
    chunk_bytes = opts.chunk_elements * 4
    removed = [n for n in ta if n not in tb]
    added = [n for n in tb if n not in ta]
    renames: dict[str, str] = {}
    if opts.detect_renames and removed and added and opts.compare_values:
        by_sig: dict[tuple[str, tuple[int, ...]], list[str]] = {}
        for n in added:
            by_sig.setdefault((tb[n].dtype, tb[n].shape), []).append(n)
        hashes_b: dict[str, str] = {}
        candidates: dict[str, list[str]] = {}
        for n in removed:
            sig = (ta[n].dtype, ta[n].shape)
            if sig not in by_sig:
                continue
            ha = tensor_hash(a, n, chunk_bytes)
            for m in by_sig[sig]:
                if m not in hashes_b:
                    hashes_b[m] = tensor_hash(b, m, chunk_bytes)
                if hashes_b[m] == ha:
                    candidates.setdefault(n, []).append(m)
        claimed: dict[str, int] = {}
        for ms in candidates.values():
            for m in ms:
                claimed[m] = claimed.get(m, 0) + 1
        for n, ms in candidates.items():
            if len(ms) == 1 and claimed[ms[0]] == 1:
                renames[n] = ms[0]
            else:
                ctx.warn(
                    "ambiguous-rename",
                    f"Tensor '{n}' has byte-identical data under several new names "
                    f"({', '.join(ms[:3])}); not reported as a rename",
                    n,
                )

    for old_name, new_name in renames.items():
        t = tb[new_name]
        ctx.add_change(
            ChangeKind.RENAMED,
            ChangeCategory.STRUCTURE,
            f"tensor:{new_name}",
            f"Tensor '{old_name}' renamed to '{new_name}'",
            old=old_name,
            new=new_name,
            confidence=0.95,
            severity=ChangeSeverity.LOW,
            evidence="Only removed/added pair with equal dtype and shape and byte-identical data (SHA-256)",
            details={"dtype": t.dtype, "shape": list(t.shape)},
        )
    renamed_new = set(renames.values())

    def describe(store: TensorStore, t: TensorInfo) -> dict[str, Any]:
        d: dict[str, Any] = {"dtype": t.dtype, "shape": list(t.shape), "parameters": t.elements}
        if opts.compute_stats and opts.compare_values and t.numeric:
            d["stats"] = tensor_stats(store, t.name, opts.chunk_elements, ctx)
        return d

    for n in removed:
        if n not in renames:
            ctx.add_change(
                ChangeKind.REMOVED,
                ChangeCategory.STRUCTURE,
                f"tensor:{n}",
                f"Tensor '{n}' removed",
                old=describe(a, ta[n]),
                severity=ChangeSeverity.MEDIUM,
                evidence="Tensor name not in new header",
            )
    for n in added:
        if n not in renamed_new:
            ctx.add_change(
                ChangeKind.ADDED,
                ChangeCategory.STRUCTURE,
                f"tensor:{n}",
                f"Tensor '{n}' added",
                new=describe(b, tb[n]),
                severity=ChangeSeverity.LOW,
                evidence="Tensor name not in old header",
            )

    common = [n for n in ta if n in tb]
    unchanged = 0
    rel_by_tensor: dict[str, float | None] = {}
    for i, n in enumerate(common):
        ctx.progress(i / max(1, len(common)), f"tensor {n}")
        x, y = ta[n], tb[n]
        if x.dtype != y.dtype:
            ctx.add_change(
                ChangeKind.TYPE_CHANGED,
                ChangeCategory.SCHEMA,
                f"tensor:{n}/dtype",
                f"dtype of '{n}' changed from {x.dtype} to {y.dtype}",
                old=x.dtype,
                new=y.dtype,
                severity=ChangeSeverity.HIGH,
                evidence="Tensor header",
            )
        if x.shape != y.shape:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.SCHEMA,
                f"tensor:{n}/shape",
                f"Shape of '{n}' changed from {list(x.shape)} to {list(y.shape)}",
                old=list(x.shape),
                new=list(y.shape),
                severity=ChangeSeverity.HIGH,
                evidence="Tensor header",
                details={"old_parameters": x.elements, "new_parameters": y.elements},
            )
        if x.dtype != y.dtype or x.shape != y.shape or not opts.compare_values:
            continue
        if raw_equal(a, b, n, n, chunk_bytes):
            unchanged += 1
            continue
        if not x.numeric:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.DATA,
                f"tensor:{n}/values",
                f"Values of '{n}' changed (dtype {x.dtype} compared byte-wise)",
                severity=ChangeSeverity.MEDIUM,
                evidence="Raw tensor bytes differ",
            )
            continue
        st = pair_stats(a, b, n, opts.chunk_elements, opts.atol, ctx)
        rel = st["relative_difference"]
        rel_by_tensor[n] = rel
        if st["differing_elements"] == 0:
            unchanged += 1  # bytes differ only in ways within tolerance (e.g. NaN payloads, -0.0)
            continue
        ctx.add_change(
            ChangeKind.MODIFIED,
            ChangeCategory.DATA,
            f"tensor:{n}/values",
            f"{st['differing_elements']:,} of {x.elements:,} values of '{n}' changed"
            + (f" (relative L2 difference {rel:.3g})" if rel is not None else ""),
            old=st["old"],
            new=st["new"],
            severity=_severity(rel),
            evidence=f"Element-wise comparison in float64, chunks of {opts.chunk_elements:,} elements, "
            f"absolute tolerance {opts.atol:g}",
            details={
                k: st[k] for k in ("l2_difference", "max_abs_difference", "relative_difference", "differing_elements")
            },
        )
    pa = sum(t.elements for t in ta.values())
    pb = sum(t.elements for t in tb.values())
    if pa != pb:
        ctx.add_change(
            ChangeKind.MODIFIED,
            ChangeCategory.STRUCTURE,
            "parameters",
            f"Total parameter count changed by {pb - pa:+,}",
            old=pa,
            new=pb,
            severity=ChangeSeverity.MEDIUM,
            evidence="Sum of tensor element counts from the headers",
        )
    ctx.metric("old_tensors", len(ta))
    ctx.metric("new_tensors", len(tb))
    ctx.metric("old_parameters", pa)
    ctx.metric("new_parameters", pb)
    ctx.metric("unchanged_tensors", unchanged)
    ctx.metric("relative_difference_by_tensor", rel_by_tensor)
