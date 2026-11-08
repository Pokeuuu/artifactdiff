"""Helpers shared by built-in plugins."""

from __future__ import annotations

import io
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL import Image


def json_safe(value: Any) -> Any:
    """Convert NumPy scalars/arrays, bytes, NaN/inf and tuples into JSON-friendly values."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        b = bytes(value)
        return {"$bytes": len(b), "hex": b[:32].hex() + ("…" if len(b) > 32 else "")}
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        seq = sorted(value, key=str) if isinstance(value, (set, frozenset)) else value
        return [json_safe(v) for v in seq]
    item = getattr(value, "item", None)  # NumPy scalar
    if callable(item) and getattr(value, "shape", None) == ():
        return json_safe(item())
    tolist = getattr(value, "tolist", None)  # NumPy array
    if callable(tolist):
        return json_safe(tolist())
    return str(value)


def rel_change(old: float, new: float) -> float | None:
    if old == 0:
        return None if new == 0 else math.inf
    return (new - old) / abs(old)


def png_bytes(img: Image.Image, max_side: int | None = None) -> tuple[bytes, int, int]:
    """Encode a Pillow image as PNG (deterministic), optionally downscaled."""
    from PIL import Image as PILImage

    if max_side and max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))), PILImage.Resampling.LANCZOS
        )
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue(), img.width, img.height


def human_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def truncate(text: str, limit: int = 2000) -> str:
    return text if len(text) <= limit else text[:limit] + f"… ({len(text) - limit} more characters)"
