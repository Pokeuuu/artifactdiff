"""Read-only handle on an input file with lazy, streaming hashing and signature sniffing."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import stat
from collections.abc import Iterator
from functools import cached_property
from pathlib import Path
from typing import BinaryIO

from artifactdiff.errors import FileTooLargeError, InputFileError

CHUNK_SIZE = 1024 * 1024


class ArtifactFile:
    """An input artifact. Plugins receive this instead of a bare path.

    ``display_name`` is what reports show (e.g. the repository path in CI), while ``path`` is
    where the bytes actually live (often a temporary file).
    """

    def __init__(self, path: str | os.PathLike[str], display_name: str | None = None) -> None:
        self.path = Path(path)
        self.display_name = display_name or self.path.name
        try:
            st = self.path.stat()
        except FileNotFoundError as exc:
            raise InputFileError(f"File not found: {self.display_name}") from exc
        except OSError as exc:
            raise InputFileError(f"Cannot access {self.display_name}: {exc.strerror}") from exc
        if not stat.S_ISREG(st.st_mode):
            raise InputFileError(f"Not a regular file: {self.display_name}")
        self.size = st.st_size

    @property
    def extension(self) -> str:
        """Lower-cased extension of the *display* name (falls back to the real path)."""
        ext = Path(self.display_name).suffix or self.path.suffix
        return ext.lower()

    @property
    def mime_type(self) -> str | None:
        return mimetypes.guess_type(self.display_name)[0]

    def open(self) -> BinaryIO:
        return self.path.open("rb")

    def head(self, n: int = 512) -> bytes:
        with self.open() as fh:
            return fh.read(n)

    def iter_chunks(self, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        with self.open() as fh:
            while chunk := fh.read(chunk_size):
                yield chunk

    @cached_property
    def sha256(self) -> str:
        h = hashlib.sha256()
        for chunk in self.iter_chunks():
            h.update(chunk)
        return h.hexdigest()

    def ensure_max_size(self, max_bytes: int | None) -> None:
        if max_bytes is not None and self.size > max_bytes:
            raise FileTooLargeError(
                f"{self.display_name} is {self.size:,} bytes, above the limit of {max_bytes:,} bytes"
            )

    def read_bytes(self, max_bytes: int | None = None) -> bytes:
        """Read the whole file. Plugins must pass a limit; the engine has already enforced one."""
        self.ensure_max_size(max_bytes)
        return self.path.read_bytes()

    def __repr__(self) -> str:
        return f"ArtifactFile({self.display_name!r}, size={self.size})"


def same_content(a: ArtifactFile, b: ArtifactFile) -> bool:
    """Byte-for-byte equality using size first, then streaming hashes."""
    return a.size == b.size and a.sha256 == b.sha256
