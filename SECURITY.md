# Security policy

ArtifactDiff parses untrusted files — often from pull requests by strangers — so parser safety,
resource limits and the GitHub Action's permission model are security-relevant.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | ✅ security fixes |

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability reporting
("Security" tab → "Report a vulnerability") on the ArtifactDiff repository. Include affected
version, a description, and — if possible — a minimal synthetic file that reproduces the issue.

We aim to acknowledge reports within 3 working days and to publish a fix and advisory within 30
days for confirmed issues, crediting reporters who wish to be named.

## In scope

- Code execution, file writes or network access triggered by a crafted input file
- Path traversal (glTF external URIs, uploads, report paths)
- Resource exhaustion that bypasses the documented limits (size, pixels, duration, timeouts)
- GitHub Action issues: injection via file names/inputs, token misuse, executing PR code
- API issues: authentication bypass when `ARTIFACTDIFF_API_TOKEN` is set, access to other
  comparisons' files, XSS in the web UI or HTML reports

## Out of scope

- Running the API on a public network **without** `ARTIFACTDIFF_API_TOKEN` or a reverse proxy with
  authentication (documented as unsupported)
- Enabling a malicious third-party plugin yourself (plugins are trusted code by design)
- Denial of service with inputs within the limits you configured

The full threat model and hardening guide is in [docs/security.md](docs/security.md).
