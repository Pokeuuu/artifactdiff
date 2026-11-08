"""Generate docs/supported-formats.md from plugin metadata plus hand-written notes.

Option tables, capabilities and limitations come from the code, so the doc cannot drift
(tests/core/test_docs.py checks that the committed file is current).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from artifactdiff.registry import default_registry

OUT = Path(__file__).resolve().parents[1] / "docs" / "supported-formats.md"

NOTES: dict[str, str] = {
    "json": """\
Compares parsed documents, so formatting and key order never count as changes.
Paths are JSONPath-like (`$.model.layers[2].weights`, `$["odd key"]`); `details.pointer` holds the
RFC 6901 JSON Pointer. Arrays: **lcs** aligns elements by content (an insertion is one change, not a
cascade), **index** compares by position, **key** matches objects by a field and reports reorderings
as `moved`. Integer vs. float literals with equal value (`1` vs `1.0`) are an `info` change; booleans
are never equal to numbers. Duplicate keys produce a warning.

Severity: type change **high** · removed key/element **medium** · added **low** · value change **low**.""",
    "csv": """\
Cell values are compared as the exact text in the file; column types are *inferred* (Polars) and type
changes are reported separately. With a primary key (explicit, or auto-selected `id`/`uuid`/`key`/`pk`
when unique and non-null in both files — always with a warning), rows are joined and modified rows list
the changed cells. Without a key, rows are compared as a multiset of full-row hashes: a changed row is
one removal plus one addition, never a guessed pairing. Duplicate keys or null keys fall back to the
multiset with a warning. Also reported: added/removed/reordered columns, delimiter, duplicate-row count,
missing values per column, and mean/std/min/max/distinct changes beyond `stats_threshold` (descriptive
statistics, not significance tests).

Severity: removed column / type change **medium** · added column, row changes **low** · statistics **info**.""",
    "parquet": """\
Schema and metadata come from the Parquet footer (no data read): column additions/removals, Arrow type
changes, nullability, column order, row count, compression, row groups, writer. Records are compared like
CSV (primary key or row multiset) over columns whose type did not change; statistics over numeric columns.
Polars' streaming engine processes row groups in chunks.

Severity: removed column / type change **high** · nullability **medium** · added column, row changes **low**.""",
    "sqlite": """\
Opened strictly read-only. Schema: tables, columns (declared type, NOT NULL, default, primary-key
position), constraint/option changes in `CREATE TABLE`, indexes, triggers and views (whitespace-normalised
SQL). Data: tables with an identical declared primary key are compared by a streaming merge-join ordered
by that key (memory does not grow with table size); other tables as row multisets, or by `rowid` if
`match_by_rowid` is enabled (with a warning — rowids change after `VACUUM`). Values must match in storage
class too (`1` vs `'1'` is a change). Also compares `user_version`, `application_id`, encoding, page size.

Severity: removed table/column **high** · type/constraint/index/trigger changes **medium** · additions, rows **low**.""",
    "image": """\
Visual differences (category `visual`) are reported separately from metadata (`metadata`). With equal
dimensions, pixels are compared in strips: a pixel changed if any RGBA channel differs by more than
`tolerance`. Changed pixels are grouped into `region_block`-sized blocks; 8-connected blocks form regions
with bounding boxes. Metrics: changed pixels and ratio, max and mean channel difference, **SSIM**
(Wang et al. 2004, grayscale, 7×7 uniform window, on images downscaled to `ssim_max_side`) and **dHash
distance** (Hamming distance of 64-bit difference hashes; size-independent). These are measurements, not
probabilities. Assets: previews, heatmap (black → red → yellow → white), overlay with region boxes.
Metadata: format, mode, ICC profile (hash), DPI, EXIF tags, PNG text chunks, frame count.

Severity of the pixel change: < 1 % of pixels **low**, < 10 % **medium**, otherwise **high**; dimensions **high**.""",
    "audio": """\
Decoded block by block with libsndfile. Reports format/subtype, sample rate, channels, duration, RMS and
peak level (dBFS — not LUFS), leading/trailing/total silence (segments below `silence_threshold_db`),
energy per octave band from averaged Hann-windowed FFT power spectra of the mono mix (bands below
`spectral_floor_db` in both files are skipped), and — with equal sample rate and channel count — the
sample-wise difference: max absolute difference, difference RMS, signal-to-difference ratio, first
differing sample and the time ranges where the difference exceeds `difference_threshold_db`. Assets:
waveform envelopes and log-frequency spectrograms. No semantic interpretation of the audio is attempted.

Severity: sample rate/channels **high** · duration **medium** · level, silence, waveform ranges **low** · spectral bands **info**.""",
    "pdf": """\
Pages are aligned by their extracted text (LCS), so an inserted page is one addition. Matched pages are
compared line by line, by media-box size, by image objects (count and placement), and visually: both
pages are rendered by PDFium at `render_dpi` and compared pixel by pixel (changes above
`min_changed_ratio`), with previews and an overlay for the first `max_preview_pages`. Document
information (Title, Author, …; `ModDate` ignored by default) and PDF version are compared. Pages with
images but no text layer trigger a "looks scanned" warning: no OCR is performed.

Severity: removed page / page size **medium** · added page, text, images, render **low** · metadata **info**.""",
    "gltf": """\
glTF 2.0 (`.gltf` JSON or `.glb`) is parsed directly. Nodes, meshes, materials and images are matched by
name (unnamed ones by index, with a warning). Reported: added/removed objects and meshes, renames (only
when exactly one removed/added pair has identical geometry hash and transform; confidence 0.9), local
transform changes, mesh reassignment, hierarchy changes, vertex/face counts, vertex attributes, material
assignment, geometry changes (SHA-256 of positions + indices — equal counts never imply equal meshes; with
equal counts the max vertex displacement and number of moved vertices are reported), material factors and
texture references, embedded texture content (hash/size/MIME/pixel size), counts of animations, skins,
cameras and lights, extensions, and the world-space scene bounding box. A wireframe preview (front/top) is
generated for reports; the web UI shows both models in a synchronized 3D viewer.

Severity: removed object/mesh, geometry and count changes **medium** · additions, transforms, materials **low**.""",
    "safetensors": """\
Validated with the official `safetensors` library, header bounds-checked, then tensors are read in
`chunk_elements` chunks (memory independent of tensor size; BF16 supported). Reports metadata changes,
added/removed tensors (with dtype, shape, parameters and statistics), renames (removed/added pair with
equal dtype/shape and byte-identical data, unique; confidence 0.95), dtype and shape changes, total
parameter count and file size. For changed tensors: min/max/mean/std of both versions, L2 difference,
max absolute difference, relative difference ‖new−old‖/‖old‖ and the number of differing elements
(beyond `atol`). Byte-identical tensors are counted, not listed. Weights are never executed.

Severity: dtype/shape **high** · removed tensor, parameter count **medium** · value changes by relative
difference: < 1e-3 **low**, < 0.1 **medium**, otherwise **high**.""",
    "text": """\
Fallback for text formats without a dedicated plugin (YAML, TOML, Markdown, XML, source code, and
unknown files that look like text). Line diff via `difflib`; reports encoding and line-ending changes.""",
    "binary": """\
Fallback for everything else: size change and the exact differing byte ranges (found with a streaming
XOR scan), plus appended/truncated tails. The meaning of the bytes is unknown, which the result says.""",
}

ORDER = ["json", "csv", "parquet", "sqlite", "image", "audio", "pdf", "gltf", "safetensors", "text", "binary"]


def type_of(spec: dict) -> str:  # type: ignore[type-arg]
    if "enum" in spec:
        return " \\| ".join(f"`{v}`" for v in spec["enum"])
    if "anyOf" in spec:
        return " \\| ".join(type_of(s) for s in spec["anyOf"])
    t = spec.get("type", "?")
    if t == "array":
        return f"list[{type_of(spec.get('items', {}))}]"
    return str(t)


def render() -> str:
    reg = default_registry()
    lines = [
        "# Supported formats",
        "",
        "<!-- Generated by scripts/gen_format_docs.py from plugin metadata. Do not edit by hand. -->",
        "",
        "Every plugin's options can be set with `-O key=value`, in `[plugins.options.<name>]` of",
        "`artifactdiff.toml`, in the web UI, or via the API's `options` field. Severities are documented",
        "heuristics to help triage — not verdicts. See also [limitations.md](limitations.md).",
        "",
        "| Plugin | Extensions | Extra |",
        "|---|---|---|",
    ]
    for name in ORDER:
        m = reg.metadata(name)
        exts = ", ".join(f"`{e}`" for e in m.formats[0].extensions) or "any"
        cls = reg.get_class(name)
        hint = cls.install_hint.split("[")[1].split("]")[0] if cls.install_hint else "core"
        lines.append(f"| [{m.display_name}](#{name}) | {exts} | `{hint}` |")
    for name in ORDER:
        m = reg.metadata(name)
        lines += ["", f"## {name}", "", f"**{m.display_name}** — {m.description}", "", NOTES[name], ""]
        props = m.options_schema.get("properties", {})
        if props:
            lines += ["| Option | Type | Default | Description |", "|---|---|---|---|"]
            for key, spec in props.items():
                default = json.dumps(spec.get("default")) if "default" in spec else "—"
                desc = spec.get("description", "").replace("|", "\\|")
                lines.append(f"| `{key}` | {type_of(spec)} | `{default}` | {desc} |")
            lines.append("")
        if m.limitations:
            lines.append("Limitations:")
            lines += [f"- {t}" for t in m.limitations]
            lines.append("")
        lines.append(f"Capabilities: {', '.join(f'`{c}`' for c in m.capabilities)}.")
    return "\n".join(lines) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        return 0 if OUT.read_text(encoding="utf-8") == text else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
