"""PDF comparison using PDFium (pypdfium2): pages, text, page sizes, images, metadata, visuals.

Pages are aligned by their extracted text (longest common subsequence), so inserting a page
does not make every later page look changed. Matched pages are compared line by line and,
optionally, rendered at low resolution and compared pixel by pixel.

PDFium extracts the text layer only. Scanned pages (images without text) cannot be compared
textually — ArtifactDiff performs no OCR and says so in a warning.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from artifactdiff.errors import PluginInputError, ResourceLimitError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, PluginOptions
from artifactdiff_plugins._common import png_bytes, truncate

if TYPE_CHECKING:
    from PIL import Image

METADATA_KEYS = ("Title", "Author", "Subject", "Keywords", "Creator", "Producer", "CreationDate", "ModDate")


class PdfOptions(PluginOptions):
    visual_compare: Literal["all", "changed", "none"] = Field(
        "all", description="Render matched pages and compare pixels: all pages, only text-changed pages, or none."
    )
    render_dpi: int = Field(50, ge=10, le=300, description="Resolution used for visual page comparison.")
    pixel_tolerance: int = Field(16, ge=0, le=255, description="Per-channel difference ignored when rendering.")
    min_changed_ratio: float = Field(0.0005, ge=0, le=1, description="Rendered pages below this are equal.")
    max_rendered_pages: int = Field(50, ge=0, description="Matched pages rendered for visual comparison.")
    max_preview_pages: int = Field(6, ge=0, description="Pages with visual previews in the report.")
    max_pages: int = Field(5000, gt=0, description="Documents with more pages are rejected.")
    ignore_metadata: list[str] = Field(
        default_factory=lambda: ["ModDate"], description="Metadata keys not compared (ModDate changes on every save)."
    )
    max_text_changes_per_page: int = Field(20, ge=0, description="Text changes listed per page (all are counted).")


@dataclass
class Page:
    index: int
    width: float
    height: float
    text: str
    images: list[list[float]]

    @property
    def key(self) -> str:
        return hashlib.sha256(" ".join(self.text.split()).encode()).hexdigest()


def _open(file: ArtifactFile) -> Any:
    import pypdfium2 as pdfium

    try:
        return pdfium.PdfDocument(str(file.path))
    except pdfium.PdfiumError as exc:
        msg = str(exc)
        if "password" in msg.lower():
            raise PluginInputError(
                f"{file.display_name} is encrypted; password-protected PDFs are not supported"
            ) from exc
        raise PluginInputError(f"{file.display_name} is not a readable PDF: {msg}") from exc


def _pages(doc: Any, file: ArtifactFile, opts: PdfOptions, ctx: ComparisonContext | None) -> list[Page]:
    import pypdfium2.raw as pdfium_c

    n = len(doc)
    if n > opts.max_pages:
        raise ResourceLimitError(f"{file.display_name} has {n} pages (limit {opts.max_pages})")
    pages = []
    for i in range(n):
        if ctx is not None:
            ctx.check()
        page = doc[i]
        w, h = page.get_size()
        tp = page.get_textpage()
        text = tp.get_text_range().replace("\r\n", "\n").replace("\r", "\n")
        images = [
            [round(v, 1) for v in obj.get_pos()] for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE])
        ]
        tp.close()
        page.close()
        pages.append(Page(i, round(w, 2), round(h, 2), text, images))
    return pages


def _render(doc: Any, index: int, dpi: int) -> Image.Image:
    page = doc[index]
    try:
        img: Image.Image = page.render(scale=dpi / 72).to_pil().convert("RGB")
        return img
    finally:
        page.close()


class PdfPlugin(ArtifactPlugin):
    name = "pdf"
    version = "1.0.0"
    display_name = "PDF"
    description = "Page, text, layout, embedded-image, metadata and rendered visual comparison of PDF documents."
    format_id = "pdf"
    format_name = "PDF document"
    supported_extensions = (".pdf",)
    mime_types = ("application/pdf",)
    signatures = (b"%PDF-",)
    capabilities = ("page-alignment", "text-diff", "page-size", "image-objects", "metadata", "render-diff")
    limitations = (
        "Only the PDF text layer is compared; scanned pages need OCR, which ArtifactDiff does not perform.",
        "Embedded images are compared by count and placement, not by pixel content (rendering covers visuals).",
        "Password-protected PDFs are not supported.",
    )
    requires = ("pypdfium2", "PIL", "numpy")
    install_hint = 'pip install "artifactdiff[pdf]"'
    Options = PdfOptions

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        doc = _open(file)
        try:
            pages = _pages(doc, file, PdfOptions(), None)
            sizes = sorted({(p.width, p.height) for p in pages})
            return {
                "pages": len(pages),
                "pdf_version": doc.get_version(),
                "metadata": {k: v for k, v in doc.get_metadata_dict().items() if v},
                "page_sizes": [list(s) for s in sizes[:10]],
                "text_characters": sum(len(p.text) for p in pages),
                "image_objects": sum(len(p.images) for p in pages),
            }
        finally:
            doc.close()

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, PdfOptions)
        da, db = _open(old), _open(new)
        try:
            self._compare(da, db, old, new, options, ctx)
        finally:
            da.close()
            db.close()

    def _compare(
        self, da: Any, db: Any, old: ArtifactFile, new: ArtifactFile, opts: PdfOptions, ctx: ComparisonContext
    ) -> None:
        ma, mb = da.get_metadata_dict(), db.get_metadata_dict()
        for k in METADATA_KEYS:
            if k in opts.ignore_metadata or ma.get(k, "") == mb.get(k, ""):
                continue
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.METADATA,
                f"metadata:{k}",
                f"Document {k} changed",
                old=ma.get(k) or None,
                new=mb.get(k) or None,
                severity=ChangeSeverity.INFO,
                evidence="PDF document information dictionary",
            )
        if da.get_version() != db.get_version():
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.METADATA,
                "pdf_version",
                "PDF version changed",
                old=da.get_version(),
                new=db.get_version(),
                severity=ChangeSeverity.INFO,
                evidence="PDF header version",
            )
        pa, pb = _pages(da, old, opts, ctx), _pages(db, new, opts, ctx)
        ctx.metric("old_pages", len(pa))
        ctx.metric("new_pages", len(pb))
        for side, pages in (("old", pa), ("new", pb)):
            scanned = [p.index + 1 for p in pages if len(p.text.strip()) < 5 and p.images]
            if scanned:
                ctx.warn(
                    "scanned-pages",
                    f"{side} document page(s) {scanned[:10]} contain images but no text layer; "
                    "they look scanned and their text cannot be compared without OCR",
                )
        sm = difflib.SequenceMatcher(None, [p.key for p in pa], [p.key for p in pb], autojunk=False)
        pairs: list[tuple[Page, Page, bool]] = []  # (old, new, text_equal)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                pairs += [(pa[i1 + k], pb[j1 + k], True) for k in range(i2 - i1)]
                continue
            paired = min(i2 - i1, j2 - j1) if tag == "replace" else 0
            pairs += [(pa[i1 + k], pb[j1 + k], False) for k in range(paired)]
            for p in pa[i1 + paired : i2]:
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.STRUCTURE,
                    f"old_page[{p.index + 1}]",
                    f"Page {p.index + 1} removed",
                    old=truncate(p.text, 300),
                    severity=ChangeSeverity.MEDIUM,
                    evidence="Pages aligned by extracted text (LCS); no counterpart in the new document",
                )
            for p in pb[j1 + paired : j2]:
                ctx.add_change(
                    ChangeKind.ADDED,
                    ChangeCategory.STRUCTURE,
                    f"page[{p.index + 1}]",
                    f"Page {p.index + 1} added",
                    new=truncate(p.text, 300),
                    severity=ChangeSeverity.LOW,
                    evidence="Pages aligned by extracted text (LCS); no counterpart in the old document",
                )
        rendered = previews = 0
        for a, b, text_equal in pairs:
            ctx.check()
            loc = f"page[{b.index + 1}]"
            moved = f" (was page {a.index + 1})" if a.index != b.index else ""
            if (a.width, a.height) != (b.width, b.height):
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.STRUCTURE,
                    f"{loc}/size",
                    f"Page size changed{moved}",
                    old=[a.width, a.height],
                    new=[b.width, b.height],
                    severity=ChangeSeverity.MEDIUM,
                    evidence="Page media box (points)",
                )
            if not text_equal:
                self._text(ctx, a, b, loc, moved, opts)
            if len(a.images) != len(b.images) or a.images != b.images:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.STRUCTURE,
                    f"{loc}/images",
                    f"Embedded images changed ({len(a.images)} → {len(b.images)}){moved}",
                    old=a.images[:10],
                    new=b.images[:10],
                    severity=ChangeSeverity.LOW,
                    evidence="Image page objects and their bounding boxes (points)",
                )
            want = opts.visual_compare == "all" or (opts.visual_compare == "changed" and not text_equal)
            if want and rendered < opts.max_rendered_pages and (a.width, a.height) == (b.width, b.height):
                rendered += 1
                if self._visual(ctx, da, db, a, b, loc, opts, previews < opts.max_preview_pages):
                    previews += 1
        if opts.visual_compare != "none" and rendered < sum(1 for _ in pairs) and rendered >= opts.max_rendered_pages:
            ctx.warn("render-capped", f"Only the first {opts.max_rendered_pages} matched pages were rendered")
        ctx.metric("pages_rendered", rendered)

    def _text(self, ctx: ComparisonContext, a: Page, b: Page, loc: str, moved: str, opts: PdfOptions) -> None:
        la, lb = a.text.split("\n"), b.text.split("\n")
        sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
        n = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            n += 1
            if n > opts.max_text_changes_per_page:
                ctx.count_omitted(1, ChangeKind.MODIFIED, ChangeCategory.TEXT)
                continue
            kind = {"insert": ChangeKind.ADDED, "delete": ChangeKind.REMOVED}.get(tag, ChangeKind.MODIFIED)
            ctx.add_change(
                kind,
                ChangeCategory.TEXT,
                f"{loc}/lines[{j1 + 1}-{max(j1 + 1, j2)}]",
                f"Text {kind.value.replace('_', ' ')}{moved}",
                old=truncate("\n".join(la[i1:i2]), 500) or None,
                new=truncate("\n".join(lb[j1:j2]), 500) or None,
                severity=ChangeSeverity.LOW,
                evidence="Line diff of PDFium-extracted text",
                details={"old_page": a.index + 1},
            )

    def _visual(
        self, ctx: ComparisonContext, da: Any, db: Any, a: Page, b: Page, loc: str, opts: PdfOptions, preview: bool
    ) -> bool:
        import numpy as np
        from PIL import Image as PILImage

        ia, ib = _render(da, a.index, opts.render_dpi), _render(db, b.index, opts.render_dpi)
        if ia.size != ib.size:
            return False
        d = np.abs(np.asarray(ia, np.int16) - np.asarray(ib, np.int16)).max(axis=-1)
        mask = d > opts.pixel_tolerance
        ratio = float(mask.mean()) if mask.size else 0.0
        if ratio <= opts.min_changed_ratio:
            return False
        ys, xs = np.nonzero(mask)
        scale = 72 / opts.render_dpi
        bbox = [
            round(float(xs.min()) * scale, 1),
            round(float(ys.min()) * scale, 1),
            round(float(xs.max() + 1) * scale, 1),
            round(float(ys.max() + 1) * scale, 1),
        ]
        ctx.add_change(
            ChangeKind.MODIFIED,
            ChangeCategory.VISUAL,
            f"{loc}/render",
            f"Rendered page differs ({ratio:.2%} of pixels)",
            new={"changed_ratio": round(ratio, 5), "bbox_points": bbox},
            severity=ChangeSeverity.LOW,
            evidence=f"Pages rendered by PDFium at {opts.render_dpi} dpi; pixels differing by more than "
            f"{opts.pixel_tolerance} in any channel",
        )
        if preview and ctx.generate_assets:
            base = ib.convert("L").convert("RGB")
            red = PILImage.new("RGB", base.size, (255, 40, 40))
            overlay = PILImage.composite(
                PILImage.blend(base, red, 0.6), base, PILImage.fromarray(mask.astype(np.uint8) * 255, "L")
            )
            page_no = b.index + 1
            for role, img, label in (
                (f"old-page-{page_no}", ia, f"Old page {a.index + 1}"),
                (f"new-page-{page_no}", ib, f"New page {page_no}"),
                (f"overlay-page-{page_no}", overlay, f"Page {page_no}: changed pixels"),
            ):
                data, w, h = png_bytes(img, 600)
                ctx.add_asset(role, label, "image/png", data, width=w, height=h)
        return True
