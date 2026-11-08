# Releasing

Releases are automated by [`.github/workflows/release.yml`](../.github/workflows/release.yml) and
triggered by pushing a tag `vX.Y.Z` that matches `artifactdiff.__version__`.

## Steps

1. Update `__version__` in `packages/core/src/artifactdiff/__init__.py` and `version` in
   `pyproject.toml`; move the *Unreleased* notes in `CHANGELOG.md` under the new version.
2. Make sure CI is green on `main`.
3. Tag and push:

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

The workflow then:

| Job | Result |
|---|---|
| `build` | Checks the tag matches the version, runs the tests, builds the web UI into the wheel, builds wheel + sdist |
| `github-release` | Creates the GitHub release with the wheel/sdist attached, and moves the major tag (`v0`, `v1`, …) used by `uses: Pokeuuu/artifactdiff@v1` |
| `docker` | Publishes `ghcr.io/pokeuuu/artifactdiff:<tag>` and `:latest` |
| `publish-pypi` | Publishes to PyPI — **only when enabled** (below) |

## Enabling PyPI publishing (one-time)

ArtifactDiff uses PyPI [Trusted Publishing](https://docs.pypi.org/trusted-publishers/): no API token is
stored in GitHub.

1. Sign in to https://pypi.org (create an account and enable 2FA if needed).
2. Go to *Your account → Publishing → Add a new pending publisher* and enter:
   - PyPI project name: `artifactdiff`
   - Owner: `Pokeuuu`
   - Repository name: `artifactdiff`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. In the GitHub repository: *Settings → Secrets and variables → Actions → Variables → New repository
   variable* `PUBLISH_PYPI` = `true`.
4. Publish an existing tag again with *Actions → Release → Re-run all jobs*, or push the next tag.

## The GitHub Action tag

Users reference the action as `Pokeuuu/artifactdiff@v1` once a `v1.x.y` release exists. Before 1.0,
releases move the `v0` tag, so use `Pokeuuu/artifactdiff@v0` (or a full commit SHA) until then.
