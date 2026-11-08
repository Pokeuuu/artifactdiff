"""Standalone HTML report: one file, inline CSS/JS, embedded visualizations, no network access."""

from __future__ import annotations

import json
from html import escape

from artifactdiff.models import Asset, ComparisonResult, Report, ReportEntry
from artifactdiff.reporting import format_value

_CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--fg:#e6edf3;--muted:#8d96a0;--acc:#7aa2f7;
--add:#3fb950;--rem:#f85149;--mod:#d29922;--info:#8d96a0;color-scheme:dark}
@media (prefers-color-scheme:light){:root{--bg:#fff;--panel:#f6f8fa;--line:#d0d7de;--fg:#1f2328;
--muted:#59636e;--acc:#0969da;--add:#1a7f37;--rem:#cf222e;--mod:#9a6700;color-scheme:light}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}
h2{font-size:17px;margin:32px 0 8px;word-break:break-all}h3{font-size:14px;margin:20px 0 8px;color:var(--muted);
text-transform:uppercase;letter-spacing:.04em}
code,.mono{font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.card b{display:block;font-size:20px}.meta{display:grid;grid-template-columns:1fr 1fr;gap:8px}
@media (max-width:700px){.meta{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;margin-top:6px}th,td{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600;font-size:12px}
td.v{max-width:260px;overflow-wrap:anywhere}.tbl{overflow-x:auto}
.pill{display:inline-block;border-radius:999px;padding:0 8px;font-size:12px;border:1px solid var(--line)}
.s-high{color:var(--rem)}.s-medium{color:var(--mod)}.s-low{color:var(--add)}.s-info{color:var(--info)}
.k-added{color:var(--add)}.k-removed{color:var(--rem)}.k-modified,.k-type_changed{color:var(--mod)}
.assets{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
figure{margin:0}figure img{max-width:100%;border:1px solid var(--line);border-radius:6px;
background:repeating-conic-gradient(#8882 0 25%,transparent 0 50%) 0 0/16px 16px}
figcaption{font-size:12px;color:var(--muted)}details{margin:6px 0}summary{cursor:pointer}
input[type=search]{width:100%;max-width:360px;padding:6px 10px;border-radius:6px;border:1px solid var(--line);
background:var(--panel);color:var(--fg)}.warn{border-left:3px solid var(--mod);padding:4px 10px;margin:4px 0}
.err{border-left:3px solid var(--rem);padding:6px 10px}footer{margin-top:40px;color:var(--muted);font-size:12px}
"""

_JS = """
document.querySelectorAll('input[data-filter]').forEach(function(inp){
  inp.addEventListener('input',function(){var q=inp.value.toLowerCase();
    document.querySelectorAll('#'+inp.dataset.filter+' tbody tr').forEach(function(tr){
      tr.style.display=tr.textContent.toLowerCase().indexOf(q)>=0?'':'none';});});});
"""


def _e(x: object) -> str:
    return escape(str(x), quote=True)


def _asset(a: Asset) -> str:
    if a.data_base64 and a.media_type.startswith("image/"):
        src = f"data:{_e(a.media_type)};base64,{a.data_base64}"
        return f'<figure><img alt="{_e(a.label)}" src="{src}"><figcaption>{_e(a.label)}</figcaption></figure>'
    return f"<figure><figcaption>{_e(a.label)} ({_e(a.media_type)}, not embedded)</figcaption></figure>"


def _props(props: dict[str, object]) -> str:
    if not props:
        return '<span class="muted">—</span>'
    rows = "".join(
        f"<tr><th>{_e(k)}</th><td class='v mono'>{_e(format_value(v, 200))}</td></tr>" for k, v in props.items()
    )
    return f"<table>{rows}</table>"


def _result(r: ComparisonResult, idx: int) -> str:
    out: list[str] = []
    s = r.statistics
    plugin = f"{r.plugin.name} {r.plugin.version}" if r.plugin else "none"
    out.append(
        f"<p><b>{_e(r.summary)}</b> <span class='pill'>{_e(r.status.value)}</span> "
        f"<span class='muted'>plugin {_e(plugin)} · {s.duration_ms:.0f} ms</span></p>"
    )
    if r.error:
        out.append(f"<div class='err'>{_e(r.error)}</div>")
    cards = [("Changes", f"{s.total_changes:,}")] + [(k.replace("_", " "), str(v)) for k, v in s.by_kind.items()]
    cards += [(f"severity {k}", str(v)) for k, v in s.by_severity.items()]
    out.append(
        "<div class='grid'>"
        + "".join(f"<div class='card'><span class='muted'>{_e(k)}</span><b>{_e(v)}</b></div>" for k, v in cards)
        + "</div>"
    )
    out.append("<div class='meta'>")
    for side, m in (("Old", r.old), ("New", r.new)):
        out.append(
            f"<div class='card'><b style='font-size:14px'>{side}: <span class='mono'>{_e(m.name)}</span></b>"
            f"<div class='muted mono'>{m.size_bytes:,} bytes · sha256 {_e(m.sha256[:16])}…</div>"
            f"<details><summary>Properties</summary>{_props(m.properties)}</details></div>"
        )
    out.append("</div>")
    if r.assets:
        out.append("<h3>Visualizations</h3><div class='assets'>" + "".join(_asset(a) for a in r.assets) + "</div>")
    if r.changes:
        tid = f"changes-{idx}"
        out.append(f"<h3>Changes</h3><input type='search' placeholder='Filter changes…' data-filter='{tid}'>")
        rows = []
        for c in r.changes:
            conf = f"<br><span class='muted'>confidence {c.confidence:.2f}</span>" if c.confidence is not None else ""
            details = (
                f"<details><summary class='muted'>details</summary><pre class='mono'>"
                f"{_e(json.dumps(c.details, indent=2, default=str, ensure_ascii=False))}</pre></details>"
                if c.details
                else ""
            )
            rows.append(
                f"<tr><td class='s-{c.severity.value}'>{c.severity.value}</td>"
                f"<td class='k-{c.kind.value}'>{c.kind.value}</td><td>{c.category.value}</td>"
                f"<td class='mono v'>{_e(c.path)}</td><td>{_e(c.description)}{conf}"
                f"<br><span class='muted'>Evidence: {_e(c.evidence)}</span>{details}</td>"
                f"<td class='mono v'>{_e(format_value(c.old_value, 300))}</td>"
                f"<td class='mono v'>{_e(format_value(c.new_value, 300))}</td></tr>"
            )
        out.append(
            f"<div class='tbl'><table id='{tid}'><thead><tr><th>Severity</th><th>Kind</th><th>Category</th>"
            "<th>Path</th><th>Description</th><th>Old</th><th>New</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )
        if s.total_changes > len(r.changes):
            out.append(
                f"<p class='muted'>{s.total_changes - len(r.changes):,} more change(s) not listed "
                "(limits.max_changes).</p>"
            )
    if s.metrics:
        out.append("<h3>Metrics</h3>" + _props(dict(s.metrics)))
    if r.warnings:
        out.append(
            "<h3>Warnings</h3>"
            + "".join(f"<div class='warn'><b>{_e(w.code)}</b>: {_e(w.message)}</div>" for w in r.warnings)
        )
    if r.limitations:
        out.append("<h3>Limitations</h3><ul>" + "".join(f"<li>{_e(t)}</li>" for t in r.limitations) + "</ul>")
    if r.reproducibility:
        rep = r.reproducibility.model_dump(mode="json")
        out.append(
            "<details><summary class='muted'>Reproducibility</summary><pre class='mono'>"
            + _e(json.dumps({**rep, "options": r.options, "fingerprint": r.fingerprint}, indent=2))
            + "</pre></details>"
        )
    return "\n".join(out)


def _entry(e: ReportEntry, idx: int) -> str:
    title = _e(e.path) if not e.old_path or e.old_path == e.path else f"{_e(e.old_path)} → {_e(e.path)}"
    body = f"<p class='muted'>{_e(e.note)}</p>" if e.note else ""
    if e.result is not None:
        body += _result(e.result, idx)
    else:
        body += f"<p>File {_e(e.file_status.value)}.</p>"
    return f"<section><h2 class='mono'>{title} <span class='pill'>{_e(e.file_status.value)}</span></h2>{body}</section>"


def render_html(report: Report) -> str:
    s = report.summary
    cards = [
        ("Files", s.files),
        ("With changes", s.with_changes),
        ("Changes", f"{s.total_changes:,}"),
        ("Errors", s.errors),
        ("Max severity", s.max_severity.value if s.max_severity else "—"),
    ]
    head = (
        "<div class='grid'>"
        + "".join(f"<div class='card'><span class='muted'>{_e(k)}</span><b>{_e(v)}</b></div>" for k, v in cards)
        + "</div>"
    )
    body = "\n".join(_entry(e, i) for i, e in enumerate(report.entries)) or "<p>No entries.</p>"
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; img-src data:; "
        "style-src 'unsafe-inline'; script-src 'unsafe-inline'\">"
        f"<title>{_e(report.title)}</title><style>{_CSS}</style></head><body><main>"
        f"<h1>{_e(report.title)}</h1><p class='muted'>Generated {report.generated_at:%Y-%m-%d %H:%M:%S} UTC by "
        f"ArtifactDiff {_e(report.artifactdiff_version)}</p>{head}{body}"
        "<footer>Self-contained report: works offline, loads no external resources.</footer>"
        f"</main><script>{_JS}</script></body></html>\n"
    )
