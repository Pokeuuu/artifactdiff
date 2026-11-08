# Security and privacy

ArtifactDiff routinely parses files it did not create — in CI, from strangers' pull requests. This
page describes the threat model and the controls in place. Report vulnerabilities as described in
[SECURITY.md](../SECURITY.md).

## Privacy

- **Local-first.** The CLI, the GitHub Action and the server analyse files on the machine they run
  on. Nothing is uploaded anywhere; no account is needed.
- **No telemetry.** ArtifactDiff makes no network requests except the GitHub Action posting its PR
  comment to the GitHub API. If telemetry is ever added, it will be opt-in.
- **Logs never contain file contents** — only metadata (plugin, sizes, timings, status, errors).
- **The web UI loads nothing from the internet.** Monaco and all assets are bundled; the server's
  Content-Security-Policy only allows same-origin resources.
- **Retention.** The server deletes uploaded inputs after `input_retention_hours` (default 24) and
  can delete whole comparisons after `result_retention_days`.

## Threats and controls

### Malicious input files

| Risk | Control |
|---|---|
| Code execution via file content | No plugin evaluates content: no pickle, no `eval`, no macros/scripts. safetensors weights are read as numbers only; SQLite views/virtual tables are never queried; PDFs are parsed by PDFium without JavaScript |
| Decompression / "bomb" inputs | Size limit before parsing; image pixel limit checked from the header before decoding; glTF JSON chunk cap; safetensors header ≤ 100 MB with bounds-checked offsets; audio duration limit; PDF page limit |
| Excessive CPU/memory | Per-comparison timeout (cooperative `ctx.check()` everywhere, hard kill with process isolation), `max_changes` and asset budgets, streaming/chunked processing (see [benchmarks](benchmarks.md)), POSIX address-space limit for workers |
| Path traversal | glTF external URIs resolve only inside the model's own directory (no absolute paths, `..`, or URL schemes); API storage uses server-generated IDs and validated extensions; recursive comparison does not follow symlinks |
| Modifying inputs | SQLite opened with `mode=ro`, `immutable=1` (when no WAL), `query_only`, `trusted_schema=OFF`; other formats are opened read-only |
| Network access | Plugins never fetch remote resources; external references are reported as unavailable |

### Plugins

Third-party plugins are ordinary Python code. ArtifactDiff therefore **never imports one
implicitly**: entry points are only enumerated until a user lists the plugin in `plugins.enabled`
(or passes `--plugin-module`). Built-ins come from a fixed list. All plugin classes are validated
against the plugin contract before use.

### GitHub Action

Runs on `pull_request` (never `pull_request_target`) with `contents: read` and optionally
`pull-requests: write`; installs ArtifactDiff from the action itself; reads PR files as git objects;
ignores repository config that would load plugins; passes inputs via environment variables; uses
argv-only git invocations with validated refs; escapes untrusted text in comments. Details in
[github-action.md](github-action.md).

### Web server / API

- Binds to `127.0.0.1` by default. **No authentication unless `ARTIFACTDIFF_API_TOKEN` is set** —
  set it (or use an authenticating reverse proxy) before exposing the server to anyone else.
  Tokens are compared in constant time.
- Upload size enforced while streaming at the ASGI layer (including chunked requests without
  `Content-Length`) and again when writing to disk; rejected uploads are deleted.
- Rate limit on `POST /compare` per client address.
- Retained inputs are served as `application/octet-stream` attachments with `Content-Security-Policy:
  sandbox`, so an uploaded HTML/SVG can never execute in the UI's origin. Generated assets are served
  only with image media types.
- Security headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, a strict CSP.
- HTML reports escape all artifact-derived text and carry their own restrictive CSP
  (`default-src 'none'`).
- The Docker image runs as a non-root user; Compose adds a read-only root filesystem and
  `no-new-privileges`.

### Subprocesses

Only the GitHub Action spawns external programs (`git`), always with an argument list and never
through a shell. Process isolation spawns Python workers of ArtifactDiff itself.

## Known gaps

- Memory limits are not enforced on Windows (no `RLIMIT_AS`); rely on the timeout and size limits.
- Inline (non-isolated) comparisons cannot interrupt a long call inside a native library; use
  `--isolate` (CLI) or `ISOLATION=process` (server, default) for untrusted input.
- Parsers in third-party libraries (PDFium, libsndfile, Pillow, Arrow) are trusted; keep them updated.
