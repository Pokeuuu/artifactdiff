# Quickstart

```bash
pipx install "artifactdiff[all]"
```

## 1. Compare two files

Every example used below ships in the repository's [`examples/`](../examples) directory.

```bash
artifactdiff diff examples/csv/old.csv examples/csv/new.csv
```

You get a summary line, a table of changes (severity, kind, structural path, description, old → new
value), warnings — here that the `id` column was **auto-selected** as primary key — and the exit code
`1` because differences were found.

Useful variations:

```bash
artifactdiff diff examples/csv/old.csv examples/csv/new.csv --details        # evidence for every change
artifactdiff diff examples/csv/old.csv examples/csv/new.csv -O primary_key=id # explicit key
artifactdiff diff examples/csv/old.csv examples/csv/new.csv -f json -o result.json
artifactdiff report result.json -f html -o report.html                       # re-render later
artifactdiff plugins info csv                                                 # all options
```

## 2. Get a shareable report

```bash
artifactdiff diff examples/gltf/old.glb examples/gltf/new.glb --format html --output report.html
```

The HTML file is self-contained (no network access, embedded visualizations) and can be attached to
issues or archived.

## 3. Compare directories

```bash
artifactdiff diff data-v1/ data-v2/ --recursive --exclude "*.log" --format markdown
```

Files are paired by relative path; byte-identical files that moved are reported as renames, and
added/removed files are listed.

## 4. Use it in CI

Fail a build only for significant changes:

```bash
artifactdiff diff baseline.parquet output.parquet --fail-on medium -q
```

Or add the GitHub Action to get a report on every pull request — see [github-action.md](github-action.md).

## 5. Open the web UI

```bash
artifactdiff serve
```

Open http://127.0.0.1:8000, drop two files on the **Compare** page and explore the result viewers.
Uploads stay on your machine and are deleted after 24 hours by default.

## 6. Make it yours

```bash
artifactdiff config init      # writes a commented artifactdiff.toml
```

Set default plugin options (e.g. a CSV primary key or image tolerance), exclusions, limits and the
`fail_on` threshold there. See [cli.md](cli.md#configuration).
