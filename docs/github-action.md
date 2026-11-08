# GitHub Action

Adds an ArtifactDiff report to every pull request that changes supported artifacts.

```yaml
name: ArtifactDiff
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  contents: read
  pull-requests: write
jobs:
  compare:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: Pokeuuu/artifactdiff@v1
        with:
          base: ${{ github.event.pull_request.base.sha }}
          head: ${{ github.event.pull_request.head.sha }}
          report-format: markdown
          comment-on-pr: true
```

A copy-paste version lives in [`examples/workflows/artifactdiff.yml`](../examples/workflows/artifactdiff.yml).

## What it does

1. Lists files changed between the merge base of `base` and `head` (`git diff base...head`, rename
   detection on).
2. Keeps files matching `paths` and not matching `exclude` (plus `exclude` from the repository's
   `artifactdiff.toml`), and only those a **format-specific** plugin supports. Plain text and unknown
   binaries are left to GitHub's own diff unless `include-text: true`.
3. Reads both versions straight from git objects (`git cat-file`) into a temporary directory.
4. Compares modified and renamed files; lists added and deleted files; notes renames without content
   changes; flags files above `max-file-size-mb`, files stored in Git LFS, and comparison errors.
5. Writes `report.md`, `report.html` and `report.json`, appends the Markdown to the job summary,
   creates **or updates** one PR comment (identified by a hidden marker), and uploads the reports as
   a workflow artifact.

## Inputs

| Input | Default | Description |
|---|---|---|
| `base` | PR base SHA | Base commit |
| `head` | PR head SHA | Head commit |
| `paths` | all | Globs to include (newline or comma separated) |
| `exclude` | — | Globs to skip |
| `report-format` | `markdown` | Log format; all three report files are always written |
| `comment-on-pr` | `true` | Post/update the PR comment |
| `fail-on` | — | Fail the job for changes at or above `info`/`low`/`medium`/`high` |
| `fail-on-error` | `false` | Fail the job when an artifact cannot be compared |
| `max-files` | `50` | Maximum artifacts to compare |
| `max-file-size-mb` | `200` | Skip larger artifacts |
| `include-text` | `false` | Also report text/unknown files |
| `upload-artifact` | `true` | Upload reports as a workflow artifact |
| `artifact-name` | `artifactdiff-report` | Artifact name |
| `extras` | all formats | ArtifactDiff extras to install (smaller = faster setup) |
| `python-version` | `3.12` | Python used to run ArtifactDiff |
| `github-token` | `github.token` | Token for the comment |

## Outputs

`total-changes`, `max-severity`, `changed-artifacts`, `report-dir`.

```yaml
      - id: diff
        uses: Pokeuuu/artifactdiff@v1
      - if: steps.diff.outputs.max-severity == 'high'
        run: echo "::notice::high-severity artifact changes in this PR"
```

## Permissions and security

| Permission | Why |
|---|---|
| `contents: read` | Read the two commits |
| `pull-requests: write` | Only for `comment-on-pr: true`. Omit it (and set `comment-on-pr: false`) to run read-only |

**Use `on: pull_request`, never `pull_request_target`.** `pull_request_target` runs with a write
token in the context of the base repository; combined with checking out PR content it lets untrusted
contributors act with your permissions. ArtifactDiff does not need it.

The action is designed to be safe on untrusted pull requests:

- ArtifactDiff is installed from the action's own source (`github.action_path`), never from the
  pull request.
- Changed files are read as **data** from git objects. No build scripts, hooks or code from the
  PR run. The repository's `artifactdiff.toml` may tune plugin options and exclusions, but its
  `plugins.enabled`/`plugins.modules` settings are ignored — a PR cannot choose code to execute.
- Workflow inputs reach the script through environment variables, not by interpolation into shell
  commands; git refs are validated and every git argument is a separate argv entry (no shell).
- File names and values from the PR are escaped in the Markdown comment (HTML neutralised, table
  pipes escaped, `@mentions` broken so a PR cannot ping people through the bot).
- Resource limits apply: `max-files`, `max-file-size-mb`, and the engine's per-comparison timeout.

### Pull requests from forks

GitHub gives `pull_request` workflows from forks a **read-only** token, so the comment cannot be
posted. The action then prints a warning, still writes the job summary and uploads the report
artifact, and does not fail. If you need comments on fork PRs, use a two-workflow pattern: this
workflow uploads `report.md` as an artifact, and a separate `workflow_run`-triggered workflow (which
never checks out PR code) downloads it and posts it.

## Git LFS

If artifacts are stored with Git LFS, git history only contains pointer files. The action detects
pointers and says so. To compare LFS content, check out with `lfs: true` and run the CLI on working
tree copies, for example:

```yaml
      - uses: actions/checkout@v7
        with: { fetch-depth: 0, lfs: true }
      - run: |
          pip install "artifactdiff[all]"
          git show ${{ github.event.pull_request.base.sha }}:models/model.safetensors > base.safetensors || true
          git lfs smudge < base.safetensors > base.resolved.safetensors
          artifactdiff diff base.resolved.safetensors models/model.safetensors -f markdown >> "$GITHUB_STEP_SUMMARY"
```

## Pinning

For supply-chain hygiene, pin the action (and the actions it uses: `actions/setup-python`,
`actions/upload-artifact`) to full commit SHAs in security-sensitive repositories.
