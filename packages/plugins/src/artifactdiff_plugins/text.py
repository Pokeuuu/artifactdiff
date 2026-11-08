"""Line-based diff for text files (configs, YAML, Markdown, source...). Fallback for text-like data."""

from __future__ import annotations

import difflib
from typing import Any

from pydantic import Field

from artifactdiff.errors import PluginInputError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, Detection, PluginOptions
from artifactdiff_plugins._common import truncate

TEXT_EXTENSIONS = (
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".svg",
    ".properties",
    ".env",
    ".log",
    ".tex",
    ".graphql",
    ".gql",
    ".proto",
    ".sql",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".tf",
    ".hcl",
    ".jsonl",
    ".ndjson",
)


def decode_text(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig",):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


_TEXT_CONTROLS = {8, 9, 10, 12, 13, 27}


def looks_like_text(head: bytes) -> bool:
    if not head or b"\x00" in head:
        return False
    controls = sum(1 for byte in head if byte < 0x20 and byte not in _TEXT_CONTROLS)
    if controls > len(head) * 0.05:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc.start >= len(head) - 3  # a multi-byte character cut by the sample boundary
    return True


class TextOptions(PluginOptions):
    ignore_whitespace: bool = Field(False, description="Ignore leading/trailing whitespace on each line.")
    ignore_case: bool = Field(False, description="Compare lines case-insensitively.")
    max_size_mb: float = Field(64, gt=0, description="Text files above this size are rejected.")
    max_lines_exact: int = Field(
        50_000, gt=0, description="Above this many lines difflib's faster 'autojunk' heuristic is used."
    )


class TextPlugin(ArtifactPlugin):
    name = "text"
    version = "1.0.0"
    display_name = "Text (line diff)"
    description = "Line-based comparison for text formats without a dedicated plugin."
    format_id = "text"
    format_name = "Text"
    supported_extensions = TEXT_EXTENSIONS
    capabilities = ("line-diff",)
    limitations = ("Text is compared line by line without understanding its syntax.",)
    priority = -10
    Options = TextOptions

    def supports(self, file: ArtifactFile) -> Detection | None:
        if file.extension in self.supported_extensions:
            return Detection("extension", 1)
        try:
            head = file.head(8192)
        except OSError:
            return None
        if file.size == 0 or looks_like_text(head):
            return Detection("content", 0)
        return None

    def _lines(self, file: ArtifactFile, opts: TextOptions, ctx: ComparisonContext) -> tuple[list[str], str, str]:
        raw = file.read_bytes(int(opts.max_size_mb * 1024 * 1024))
        if b"\x00" in raw[:8192] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise PluginInputError(f"{file.display_name} contains NUL bytes and does not look like text")
        text, enc = decode_text(raw)
        if enc == "latin-1":
            ctx.warn("not-utf8", f"{file.display_name} is not valid UTF-8; decoded as Latin-1")
        eol = "crlf" if "\r\n" in text else ("lf" if "\n" in text else "none")
        return text.splitlines(), enc, eol

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        raw = file.read_bytes(64 * 1024 * 1024)
        text, enc = decode_text(raw)
        return {
            "encoding": enc,
            "lines": len(text.splitlines()),
            "line_endings": "crlf" if "\r\n" in text else "lf",
            "trailing_newline": text.endswith("\n"),
        }

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, TextOptions)
        a, enc_a, eol_a = self._lines(old, options, ctx)
        b, enc_b, eol_b = self._lines(new, options, ctx)
        if enc_a != enc_b:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.METADATA,
                "encoding",
                "Text encoding changed",
                old=enc_a,
                new=enc_b,
                severity=ChangeSeverity.INFO,
                evidence="Decoding attempt order: UTF-8 (BOM-aware), UTF-16 (BOM), Latin-1",
            )
        if eol_a != eol_b and "none" not in (eol_a, eol_b):
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.METADATA,
                "line_endings",
                "Line endings changed",
                old=eol_a,
                new=eol_b,
                severity=ChangeSeverity.INFO,
                evidence="Searched both files for CRLF sequences",
            )

        def norm(line: str) -> str:
            if options.ignore_whitespace:
                line = line.strip()
            if options.ignore_case:
                line = line.casefold()
            return line

        na, nb = [norm(x) for x in a], [norm(x) for x in b]
        autojunk = max(len(na), len(nb)) > options.max_lines_exact
        if autojunk:
            ctx.warn("heuristic-diff", "Large file: difflib's autojunk heuristic was used; hunks may be coarser")
        sm = difflib.SequenceMatcher(None, na, nb, autojunk=autojunk)
        hunks = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            ctx.check()
            if tag == "equal":
                continue
            hunks += 1
            old_text = "\n".join(a[i1:i2])
            new_text = "\n".join(b[j1:j2])
            loc = f"lines {i1 + 1}-{i2}" if i2 > i1 else f"after line {i1}"
            new_loc = f"lines {j1 + 1}-{j2}" if j2 > j1 else f"after line {j1}"
            details = {"old_start": i1 + 1, "old_end": i2, "new_start": j1 + 1, "new_end": j2}
            if tag == "insert":
                ctx.add_change(
                    ChangeKind.ADDED,
                    ChangeCategory.TEXT,
                    f"new:{new_loc}",
                    f"{j2 - j1} line(s) added",
                    new=truncate(new_text),
                    details=details,
                    evidence="difflib.SequenceMatcher line alignment",
                )
            elif tag == "delete":
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.TEXT,
                    f"old:{loc}",
                    f"{i2 - i1} line(s) removed",
                    old=truncate(old_text),
                    details=details,
                    evidence="difflib.SequenceMatcher line alignment",
                )
            else:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.TEXT,
                    f"old:{loc}",
                    f"{i2 - i1} line(s) replaced by {j2 - j1} line(s) (new {new_loc})",
                    old=truncate(old_text),
                    new=truncate(new_text),
                    details=details,
                    evidence="difflib.SequenceMatcher line alignment",
                )
        ctx.metric("old_lines", len(a))
        ctx.metric("new_lines", len(b))
        ctx.metric("hunks", hunks)
