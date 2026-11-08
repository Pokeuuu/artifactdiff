"""Raster image comparison: dimensions, color profile, metadata and pixels.

Pixel comparison (same dimensions only) runs in horizontal strips. A pixel counts as changed
when any channel differs by more than ``tolerance`` (0-255). Changed pixels are grouped into
``region_block``-sized blocks, and 8-connected blocks form the reported regions.

Measures reported (all documented in docs/supported-formats.md):
* changed pixel count / ratio, mean and max absolute channel difference;
* SSIM — Wang et al. 2004, grayscale, 7x7 uniform window, K1=0.01, K2=0.03, on images
  downscaled to at most ``ssim_max_side`` pixels (an indicator of structural similarity, not a
  probability);
* dHash distance — Hamming distance between 64-bit difference hashes (size-independent).
Visual differences (category ``visual``) are reported separately from metadata differences.
"""

from __future__ import annotations

import hashlib
import math
from collections import deque
from typing import TYPE_CHECKING, Any

from pydantic import Field

from artifactdiff.errors import PluginInputError, ResourceLimitError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, PluginOptions
from artifactdiff_plugins._common import json_safe, png_bytes

if TYPE_CHECKING:
    import numpy as np
    from PIL import Image


class ImageOptions(PluginOptions):
    tolerance: int = Field(0, ge=0, le=255, description="Per-channel difference ignored (8 suits JPEG noise).")
    ignore_alpha: bool = Field(False, description="Compare RGB only.")
    ignore_metadata: bool = Field(False, description="Skip EXIF, DPI and text-chunk comparison.")
    region_block: int = Field(8, ge=1, le=256, description="Block size (px) used to group changed pixels.")
    max_regions: int = Field(50, ge=0, description="Changed regions listed individually (largest first).")
    max_pixels: int = Field(100_000_000, gt=0, description="Images with more pixels are rejected.")
    ssim: bool = Field(True, description="Compute SSIM (costs time on large images).")
    ssim_max_side: int = Field(1024, ge=32, description="Images are downscaled to this size before SSIM.")
    preview_max_side: int = Field(512, ge=32, description="Size of generated previews and heatmaps.")


def _open(file: ArtifactFile, max_pixels: int) -> Image.Image:
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(file.path)
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise PluginInputError(f"{file.display_name} is not a readable image: {exc}") from exc
    if img.width * img.height > max_pixels:
        raise ResourceLimitError(f"{file.display_name} has {img.width * img.height:,} pixels (limit {max_pixels:,})")
    try:
        img.load()
    except (OSError, SyntaxError, ValueError) as exc:
        raise PluginInputError(f"{file.display_name} is truncated or corrupted: {exc}") from exc
    return img


def _metadata(img: Image.Image) -> dict[str, Any]:
    from PIL import ExifTags

    meta: dict[str, Any] = {}
    icc = img.info.get("icc_profile")
    meta["icc_profile_sha256"] = hashlib.sha256(icc).hexdigest()[:16] if icc else None
    if "dpi" in img.info:
        meta["dpi"] = [round(float(x), 2) for x in img.info["dpi"]]
    exif = {}
    try:
        for tag, value in img.getexif().items():
            name = ExifTags.TAGS.get(tag, f"tag_{tag}")
            exif[name] = json_safe(value)
    except (AttributeError, OSError, ValueError):
        pass
    meta["exif"] = exif
    text = getattr(img, "text", None)
    if isinstance(text, dict):
        meta["text"] = {str(k): str(v)[:500] for k, v in text.items()}
    meta["frames"] = int(getattr(img, "n_frames", 1))
    return meta


def _rgba(img: Image.Image, ignore_alpha: bool) -> Image.Image:
    mode = "RGB" if ignore_alpha else "RGBA"
    return img if img.mode == mode else img.convert(mode)


def dhash(img: Image.Image) -> int:
    import numpy as np
    from PIL import Image as PILImage

    g = np.asarray(img.convert("L").resize((9, 8), PILImage.Resampling.LANCZOS), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def _box_mean(x: np.ndarray, k: int) -> np.ndarray:
    import numpy as np

    c = np.cumsum(np.cumsum(np.pad(x, ((1, 0), (1, 0))), axis=0), axis=1)
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s / (k * k)


def ssim(a: Image.Image, b: Image.Image, max_side: int) -> float:
    import numpy as np
    from PIL import Image as PILImage

    if max(a.size) > max_side:
        scale = max_side / max(a.size)
        size = (max(1, round(a.width * scale)), max(1, round(a.height * scale)))
        a, b = a.resize(size, PILImage.Resampling.BILINEAR), b.resize(size, PILImage.Resampling.BILINEAR)
    x = np.asarray(a.convert("L"), dtype=np.float64)
    y = np.asarray(b.convert("L"), dtype=np.float64)
    k = 7
    if min(x.shape) < k:
        return 1.0 if np.array_equal(x, y) else float("nan")
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mx, my = _box_mean(x, k), _box_mean(y, k)
    sxx = _box_mean(x * x, k) - mx * mx
    syy = _box_mean(y * y, k) - my * my
    sxy = _box_mean(x * y, k) - mx * my
    n = k * k
    sxx, syy, sxy = sxx * n / (n - 1), syy * n / (n - 1), sxy * n / (n - 1)  # sample covariance
    s = ((2 * mx * my + c1) * (2 * sxy + c2)) / ((mx * mx + my * my + c1) * (sxx + syy + c2))
    return float(s.mean())


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """8-connected components of a boolean block grid -> (r0, c0, r1, c1, blocks)."""
    import numpy as np

    seen = np.zeros_like(mask, dtype=bool)
    out = []
    rows, cols = mask.shape
    for r, c in zip(*np.nonzero(mask), strict=True):
        if seen[r, c]:
            continue
        q = deque([(int(r), int(c))])
        seen[r, c] = True
        r0 = r1 = int(r)
        c0 = c1 = int(c)
        n = 0
        while q:
            y, x = q.popleft()
            n += 1
            r0, r1, c0, c1 = min(r0, y), max(r1, y), min(c0, x), max(c1, x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < rows and 0 <= xx < cols and mask[yy, xx] and not seen[yy, xx]:
                        seen[yy, xx] = True
                        q.append((yy, xx))
        out.append((r0, c0, r1, c1, n))
    return out


def _heat_colormap(v: np.ndarray) -> np.ndarray:
    """0..255 -> black, red, yellow, white (perceptually ordered, colour-blind tolerant)."""
    import numpy as np

    t = v.astype(np.float32) / 255.0
    r = np.clip(t * 3, 0, 1)
    g = np.clip(t * 3 - 1, 0, 1)
    b = np.clip(t * 3 - 2, 0, 1)
    return (np.stack([r, g, b], axis=-1) * 255).astype(np.uint8)


class ImagePlugin(ArtifactPlugin):
    name = "image"
    version = "1.0.0"
    display_name = "Raster images"
    description = "Pixel, structural (SSIM), perceptual-hash and metadata comparison of raster images."
    format_id = "image"
    format_name = "Raster image"
    supported_extensions = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif")
    mime_types = ("image/png", "image/jpeg", "image/webp", "image/tiff", "image/bmp", "image/gif")
    signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"BM", b"GIF87a", b"GIF89a")
    capabilities = ("pixel-diff", "ssim", "perceptual-hash", "regions", "heatmap", "overlay", "metadata")
    limitations = (
        "Pixels are compared after conversion to 8-bit RGB(A); higher bit depths are quantised.",
        "Only the first frame of multi-frame images (GIF, multi-page TIFF) is compared.",
        "Pixel-level comparison requires identical dimensions; otherwise only metadata and the "
        "perceptual hash are compared.",
    )
    requires = ("PIL", "numpy")
    install_hint = 'pip install "artifactdiff[image]"'
    Options = ImageOptions

    def sniff(self, head: bytes) -> bool:
        return super().sniff(head) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        img = _open(file, ImageOptions().max_pixels)
        meta = _metadata(img)
        return {
            "format": img.format,
            "mode": img.mode,
            "width": img.width,
            "height": img.height,
            "frames": meta["frames"],
            "icc_profile": meta["icc_profile_sha256"] is not None,
            "exif_tags": len(meta["exif"]),
            "text_chunks": sorted(meta.get("text", {})),
        }

    # --------------------------------------------------------------------- compare
    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, ImageOptions)
        a, b = _open(old, options.max_pixels), _open(new, options.max_pixels)
        self._compare_props(ctx, a, b, options)
        if a.size != b.size:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.STRUCTURE,
                "dimensions",
                f"Dimensions changed from {a.width}x{a.height} to {b.width}x{b.height}",
                old=[a.width, a.height],
                new=[b.width, b.height],
                severity=ChangeSeverity.HIGH,
                evidence="Image header",
            )
            ctx.warn("pixel-diff-skipped", "Dimensions differ, so pixels were not compared one-to-one")
        dist = bin(dhash(a) ^ dhash(b)).count("1")
        ctx.metric("dhash_distance", dist)
        if a.size != b.size and dist:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.VISUAL,
                "perceptual",
                f"Perceptual hash differs in {dist}/64 bits",
                old=None,
                new=dist,
                severity=ChangeSeverity.MEDIUM if dist > 10 else ChangeSeverity.LOW,
                evidence="Hamming distance of 64-bit dHash (9x8 grayscale gradient) values",
            )
        ra, rb = _rgba(a, options.ignore_alpha), _rgba(b, options.ignore_alpha)
        if ctx.generate_assets:
            for role, img, label in (("old-preview", ra, "Old image"), ("new-preview", rb, "New image")):
                data, w, h = png_bytes(img, options.preview_max_side)
                ctx.add_asset(role, label, "image/png", data, width=w, height=h)
        if a.size == b.size:
            self._pixels(ctx, ra, rb, options)

    def _compare_props(self, ctx: ComparisonContext, a: Image.Image, b: Image.Image, opts: ImageOptions) -> None:
        for attr in ("format", "mode"):
            va, vb = getattr(a, attr), getattr(b, attr)
            if va != vb:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.METADATA,
                    attr,
                    f"Image {attr} changed",
                    old=va,
                    new=vb,
                    severity=ChangeSeverity.LOW,
                    evidence="Pillow image header",
                )
        ma, mb = _metadata(a), _metadata(b)
        if ma["icc_profile_sha256"] != mb["icc_profile_sha256"]:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.METADATA,
                "icc_profile",
                "Color profile changed",
                old=ma["icc_profile_sha256"],
                new=mb["icc_profile_sha256"],
                severity=ChangeSeverity.MEDIUM,
                evidence="SHA-256 of embedded ICC profile bytes (None = no profile)",
            )
        if ma["frames"] != mb["frames"]:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.STRUCTURE,
                "frames",
                "Frame count changed",
                old=ma["frames"],
                new=mb["frames"],
                severity=ChangeSeverity.MEDIUM,
                evidence="n_frames",
            )
        if max(ma["frames"], mb["frames"]) > 1:
            ctx.warn("first-frame-only", "Multi-frame image: only the first frame was compared")
        if opts.ignore_metadata:
            return
        if ma.get("dpi") != mb.get("dpi"):
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.METADATA,
                "dpi",
                "Resolution (DPI) changed",
                old=ma.get("dpi"),
                new=mb.get("dpi"),
                severity=ChangeSeverity.INFO,
                evidence="Image info",
            )
        for group in ("exif", "text"):
            ga, gb = ma.get(group, {}), mb.get(group, {})
            for key in sorted(set(ga) | set(gb)):
                if ga.get(key) == gb.get(key):
                    continue
                kind = (
                    ChangeKind.ADDED if key not in ga else ChangeKind.REMOVED if key not in gb else ChangeKind.MODIFIED
                )
                ctx.add_change(
                    kind,
                    ChangeCategory.METADATA,
                    f"{group}:{key}",
                    f"{group.upper()} '{key}' {kind.value}",
                    old=ga.get(key),
                    new=gb.get(key),
                    severity=ChangeSeverity.INFO,
                    evidence="EXIF tags via Pillow getexif()" if group == "exif" else "PNG text chunks",
                )

    def _pixels(self, ctx: ComparisonContext, a: Image.Image, b: Image.Image, opts: ImageOptions) -> None:
        import numpy as np
        from PIL import Image as PILImage
        from PIL import ImageDraw

        w, h = a.size
        bs = opts.region_block
        grid_h, grid_w = math.ceil(h / bs), math.ceil(w / bs)
        block_counts = np.zeros((grid_h, grid_w), dtype=np.int64)
        cell = max(1, math.ceil(max(w, h) / opts.preview_max_side))
        heat = np.zeros((math.ceil(h / cell), math.ceil(w / cell)), dtype=np.uint8)
        changed = 0
        total_abs = 0
        max_diff = 0
        strip = max(bs, (4_000_000 // max(1, w)) // bs * bs)  # ~4M pixels per strip, block aligned
        for y0 in range(0, h, strip):
            ctx.progress(y0 / h, "comparing pixels")
            y1 = min(h, y0 + strip)
            pa = np.asarray(a.crop((0, y0, w, y1)), dtype=np.int16)
            pb = np.asarray(b.crop((0, y0, w, y1)), dtype=np.int16)
            d = np.abs(pa - pb).max(axis=-1).astype(np.uint8)
            total_abs += int(np.abs(pa - pb).sum())
            max_diff = max(max_diff, int(d.max()) if d.size else 0)
            m = d > opts.tolerance
            changed += int(m.sum())
            # per-block counts
            rows = y1 - y0
            pad_r, pad_c = (-rows) % bs, (-w) % bs
            mp = np.pad(m, ((0, pad_r), (0, pad_c)))
            block_counts[y0 // bs : y0 // bs + mp.shape[0] // bs] += mp.reshape(
                mp.shape[0] // bs, bs, mp.shape[1] // bs, bs
            ).sum(axis=(1, 3))
            # heatmap at preview resolution (max within each cell); strips are not cell aligned, so
            # accumulate row by row-cell
            dp = np.where(m, d, 0)
            for cy in range(y0 // cell, math.ceil(y1 / cell)):
                r0, r1 = max(cy * cell, y0) - y0, min((cy + 1) * cell, y1) - y0
                if r1 <= r0:
                    continue
                band = dp[r0:r1]
                pc = (-w) % cell
                band = np.pad(band, ((0, 0), (0, pc)))
                heat[cy] = np.maximum(heat[cy], band.reshape(r1 - r0, -1, cell).max(axis=(0, 2)))
        n_pixels = w * h
        ratio = changed / n_pixels if n_pixels else 0.0
        channels = 3 if opts.ignore_alpha else 4
        ctx.metric("changed_pixels", changed)
        ctx.metric("changed_ratio", round(ratio, 6))
        ctx.metric("max_channel_difference", max_diff)
        ctx.metric("mean_abs_difference", round(total_abs / (n_pixels * channels), 4) if n_pixels else 0)
        ctx.metric("tolerance", opts.tolerance)
        if opts.ssim:
            ctx.metric("ssim", round(ssim(a, b, opts.ssim_max_side), 6))
        if changed == 0:
            return
        sev = ChangeSeverity.LOW if ratio < 0.01 else ChangeSeverity.MEDIUM if ratio < 0.1 else ChangeSeverity.HIGH
        ctx.add_change(
            ChangeKind.MODIFIED,
            ChangeCategory.VISUAL,
            "pixels",
            f"{changed:,} pixel(s) changed ({ratio:.2%})",
            old=None,
            new={"changed_pixels": changed, "ratio": round(ratio, 6)},
            severity=sev,
            evidence=f"Per-pixel max channel difference > {opts.tolerance} over {n_pixels:,} pixels",
            details={"max_channel_difference": max_diff},
        )
        comps = sorted(_components(block_counts > 0), key=lambda c: (-c[4], c[0], c[1]))
        ctx.metric("changed_regions", len(comps))
        regions = []
        for r0, c0, r1, c1, _n in comps[: opts.max_regions]:
            x0, y0r = c0 * bs, r0 * bs
            x1, y1r = min(w, (c1 + 1) * bs), min(h, (r1 + 1) * bs)
            px = int(block_counts[r0 : r1 + 1, c0 : c1 + 1].sum())
            regions.append((x0, y0r, x1, y1r))
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.VISUAL,
                f"region[{x0},{y0r},{x1 - x0}x{y1r - y0r}]",
                f"Changed region of {x1 - x0}x{y1r - y0r} px ({px:,} changed pixels)",
                new={"x": x0, "y": y0r, "width": x1 - x0, "height": y1r - y0r},
                severity=ChangeSeverity.LOW,
                evidence=f"8-connected {bs}x{bs}-px blocks containing changed pixels",
                details={"bbox": [x0, y0r, x1, y1r], "changed_pixels": px},
            )
        if len(comps) > opts.max_regions:
            ctx.warn("regions-truncated", f"{len(comps)} regions found; the {opts.max_regions} largest are listed")
        if not ctx.generate_assets:
            return
        hm = PILImage.fromarray(_heat_colormap(heat), "RGB")
        data, hw, hh = png_bytes(hm.resize((max(1, w // cell), max(1, h // cell)), PILImage.Resampling.NEAREST))
        ctx.add_asset("heatmap", "Difference heatmap (max channel difference)", "image/png", data, width=hw, height=hh)
        scale = min(1.0, opts.preview_max_side / max(w, h))
        base = b.convert("L").convert("RGB")
        if scale < 1:
            base = base.resize((max(1, round(w * scale)), max(1, round(h * scale))), PILImage.Resampling.BILINEAR)
        mask_small = PILImage.fromarray((heat > 0).astype(np.uint8) * 255, "L").resize(
            base.size, PILImage.Resampling.NEAREST
        )
        red = PILImage.new("RGB", base.size, (255, 40, 40))
        overlay = PILImage.composite(PILImage.blend(base, red, 0.6), base, mask_small)
        draw = ImageDraw.Draw(overlay)
        for x0, y0r, x1, y1r in regions:
            draw.rectangle((x0 * scale, y0r * scale, x1 * scale - 1, y1r * scale - 1), outline=(0, 200, 255), width=1)
        data, ow, oh = png_bytes(overlay)
        ctx.add_asset(
            "overlay", "Changed pixels over the new image, with region boxes", "image/png", data, width=ow, height=oh
        )
