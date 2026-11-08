"""Fallback for any file: size, hash and exact differing byte ranges (streamed)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, Detection, PluginOptions

_NONZERO = re.compile(rb"[^\x00]+")


class BinaryOptions(PluginOptions):
    max_regions: int = Field(20, ge=1, le=10_000, description="Differing byte ranges listed individually.")
    merge_gap: int = Field(16, ge=0, description="Ranges closer than this many bytes are merged.")
    chunk_size_kb: int = Field(1024, ge=4, le=65536, description="Streaming chunk size.")


class BinaryPlugin(ArtifactPlugin):
    name = "binary"
    version = "1.0.0"
    display_name = "Binary (fallback)"
    description = "Byte-level comparison used when no format-specific plugin applies."
    format_id = "binary"
    format_name = "Binary data"
    capabilities = ("byte-ranges", "streaming")
    limitations = (
        "No format-specific plugin was used: only raw bytes were compared, so the meaning of changes "
        "is unknown and an insertion shifts every following byte.",
    )
    priority = -100
    Options = BinaryOptions

    def supports(self, file: ArtifactFile) -> Detection | None:
        return Detection("content", 0)

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        head = file.head(16)
        return {"size_bytes": file.size, "magic_hex": head.hex()}

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, BinaryOptions)
        chunk = options.chunk_size_kb * 1024
        regions: list[list[int]] = []  # [start, end) in old/new coordinates (overlapping prefix)
        differing = 0
        offset = 0
        overlap = min(old.size, new.size)
        with old.open() as fa, new.open() as fb:
            while offset < overlap:
                ctx.check()
                n = min(chunk, overlap - offset)
                a, b = fa.read(n), fb.read(n)
                if a != b:
                    x = (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(n, "big")
                    differing += n - x.count(0)
                    for m in _NONZERO.finditer(x):
                        s, e = offset + m.start(), offset + m.end()
                        if regions and s - regions[-1][1] <= options.merge_gap:
                            regions[-1][1] = e
                        else:
                            regions.append([s, e])
                offset += n
                if overlap:
                    ctx.progress(offset / overlap, "scanning bytes")
            tail_old = fa.read(min(64, old.size - overlap)) if old.size > overlap else b""
            tail_new = fb.read(min(64, new.size - overlap)) if new.size > overlap else b""

        if old.size != new.size:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.METADATA,
                "size",
                f"File size changed by {new.size - old.size:+,} bytes",
                old=old.size,
                new=new.size,
                severity=ChangeSeverity.LOW,
                evidence="Compared file sizes from the filesystem",
            )
        with old.open() as fa, new.open() as fb:
            for s, e in regions[: options.max_regions]:
                fa.seek(s)
                fb.seek(s)
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.BINARY,
                    f"bytes[{s:#x}:{e:#x}]",
                    f"{e - s:,} byte(s) differ at offset {s:#x}",
                    old=fa.read(min(16, e - s)).hex(),
                    new=fb.read(min(16, e - s)).hex(),
                    evidence="XOR of both files' bytes at identical offsets (first 16 bytes shown)",
                    details={"start": s, "end": e},
                )
        if len(regions) > options.max_regions:
            ctx.warn(
                "regions-truncated",
                f"{len(regions):,} differing ranges found; only the first {options.max_regions} are listed",
            )
        if new.size > overlap:
            ctx.add_change(
                ChangeKind.ADDED,
                ChangeCategory.BINARY,
                f"bytes[{overlap:#x}:{new.size:#x}]",
                f"{new.size - overlap:,} byte(s) appended",
                new=tail_new[:16].hex(),
                evidence="New file is longer than the old file",
            )
        elif old.size > overlap:
            ctx.add_change(
                ChangeKind.REMOVED,
                ChangeCategory.BINARY,
                f"bytes[{overlap:#x}:{old.size:#x}]",
                f"{old.size - overlap:,} byte(s) truncated",
                old=tail_old[:16].hex(),
                evidence="Old file is longer than the new file",
            )
        ctx.metric("differing_bytes_in_common_prefix", differing)
        ctx.metric("differing_ranges", len(regions))
        ctx.metric("common_length", overlap)
