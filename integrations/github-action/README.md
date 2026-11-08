# GitHub Action runner

`run.py` is executed by the composite action defined in [`/action.yml`](../../action.yml) (it must live at
the repository root for `uses: owner/artifactdiff@v1`). Documentation: [docs/github-action.md](../../docs/github-action.md).
Tests: `tests/action/` run it against a real git repository and a fake GitHub API.
