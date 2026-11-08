"""On-disk storage for uploads and generated assets.

Layout: ``<data_dir>/comparisons/<id>/inputs/{old,new}<ext>`` and ``.../assets/<asset-id>.png``.
IDs are server-generated hex strings and extensions are validated, so client-supplied names
never become path components (no traversal). Upload names are kept only as display labels.
"""

from __future__ import annotations

import base64
import re
import shutil
import unicodedata
from pathlib import Path
from typing import BinaryIO

from artifactdiff.models import ComparisonResult

_ID = re.compile(r"^[0-9a-f]{32}$")
_EXT = re.compile(r"^\.[a-z0-9]{1,12}$")
_ASSET = re.compile(r"^[a-z0-9-]{1,64}$")
_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\]")


class UploadTooLarge(Exception):
    pass


def display_name(raw: str | None, fallback: str) -> str:
    """A safe label: basename only, no control characters, bounded length."""
    if not raw:
        return fallback
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", _UNSAFE.sub("", name)).strip(" .")
    return name[:200] or fallback


def safe_extension(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return suffix if _EXT.match(suffix) else ""


class Storage:
    def __init__(self, root: Path) -> None:
        self.root = root / "comparisons"
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, cid: str) -> Path:
        if not _ID.match(cid):
            raise ValueError("invalid comparison id")
        return self.root / cid

    def input_path(self, cid: str, side: str, ext: str) -> Path:
        if side not in ("old", "new") or (ext and not _EXT.match(ext)):
            raise ValueError("invalid input reference")
        return self.dir(cid) / "inputs" / f"{side}{ext}"

    def find_input(self, cid: str, side: str) -> Path | None:
        if side not in ("old", "new"):
            return None
        d = self.dir(cid) / "inputs"
        if not d.is_dir():
            return None
        for p in d.iterdir():
            if p.stem == side and p.is_file():
                return p
        return None

    def save_upload(self, src: BinaryIO, dest: Path, limit: int) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with dest.open("wb") as out:
            while chunk := src.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise UploadTooLarge(f"File exceeds the upload limit of {limit:,} bytes")
                out.write(chunk)
        return written

    def extract_assets(self, cid: str, result: ComparisonResult) -> int:
        """Move inline asset data to files and replace it with API URLs. Returns bytes written."""
        total = 0
        d = self.dir(cid) / "assets"
        for asset in result.assets:
            if not asset.data_base64 or not _ASSET.match(asset.id):
                continue
            data = base64.b64decode(asset.data_base64)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{asset.id}.bin").write_bytes(data)
            total += len(data)
            asset.data_base64 = None
            asset.url = f"/api/v1/comparisons/{cid}/assets/{asset.id}"
        return total

    def asset_path(self, cid: str, asset_id: str) -> Path | None:
        if not _ASSET.match(asset_id):
            return None
        p = self.dir(cid) / "assets" / f"{asset_id}.bin"
        return p if p.is_file() else None

    def inline_assets(self, cid: str, result: ComparisonResult) -> ComparisonResult:
        """Copy of ``result`` with asset data embedded again (for standalone HTML/JSON exports)."""
        copy = result.model_copy(deep=True)
        for asset in copy.assets:
            p = self.asset_path(cid, asset.id)
            if p is not None:
                asset.data_base64 = base64.b64encode(p.read_bytes()).decode("ascii")
                asset.url = None
        return copy

    def delete_inputs(self, cid: str) -> None:
        shutil.rmtree(self.dir(cid) / "inputs", ignore_errors=True)

    def delete(self, cid: str) -> None:
        shutil.rmtree(self.dir(cid), ignore_errors=True)

    def usage_bytes(self) -> dict[str, int]:
        inputs = assets = 0
        for p in self.root.rglob("*"):
            if p.is_file():
                size = p.stat().st_size
                if p.parent.name == "inputs":
                    inputs += size
                else:
                    assets += size
        return {"inputs": inputs, "assets": assets, "total": inputs + assets}
