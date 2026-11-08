# Installation

ArtifactDiff needs **Python 3.12 or newer**. The core has three dependencies (pydantic, typer, rich);
format support comes from optional extras so you only install what you use.

## pipx / pip

```bash
pipx install "artifactdiff[all]"     # recommended for the CLI: isolated environment
pip install "artifactdiff[all]"      # or into the current environment
```

| Extra | Adds | Main dependencies |
|---|---|---|
| *(none)* | JSON, SQLite, text, binary | — (standard library) |
| `tabular` | CSV/TSV, Parquet | polars, pyarrow |
| `image` | PNG/JPEG/WebP/TIFF/BMP/GIF | pillow, numpy |
| `audio` | WAV/FLAC/OGG/MP3 | soundfile (bundles libsndfile), numpy, pillow |
| `pdf` | PDF | pypdfium2 (bundles PDFium), pillow, numpy |
| `gltf` | GLB/glTF | numpy, pillow |
| `safetensors` | `.safetensors` | safetensors, numpy |
| `api` | web UI + REST API | fastapi, uvicorn, sqlalchemy, alembic, python-multipart, pydantic-settings |
| `postgres` | PostgreSQL for the API | psycopg |
| `all` | everything above except `postgres` | |

Check the result:

```bash
artifactdiff doctor
```

It lists each plugin as *ready* or *missing deps* with the exact `pip install` command to fix it, and
reports whether your libsndfile build can decode MP3.

## From a clone

```bash
git clone https://github.com/Pokeuuu/artifactdiff && cd artifactdiff
uv sync --all-extras          # or: python -m venv .venv && pip install -e ".[all]"
uv run artifactdiff --help
```

The web UI is a separate build step (Node.js 20+):

```bash
cd apps/web && npm ci && npm run build && cd ../..
uv run python scripts/build_web.py --skip-npm   # copies the build into the API package
uv run artifactdiff serve
```

Released wheels already contain the built UI.

## Docker

```bash
docker compose up --build            # http://localhost:8000 (bound to 127.0.0.1)
# or
docker build -f docker/Dockerfile -t artifactdiff .
docker run --rm -p 127.0.0.1:8000:8000 -v artifactdiff-data:/data artifactdiff
```

The image runs as an unprivileged user with a read-only root filesystem (in Compose), stores data in
the `/data` volume, and includes a health check. Configuration uses the `ARTIFACTDIFF_*` variables
documented in [`.env.example`](../.env.example) and [api.md](api.md). Use the `postgres` Compose profile
to run against PostgreSQL.

Use the CLI from the image for one-off comparisons:

```bash
docker run --rm -v "$PWD:/work" -w /work --entrypoint artifactdiff artifactdiff diff a.parquet b.parquet
```

## Platform notes

- Wheels exist for Linux, macOS and Windows for every dependency; no compiler is needed.
- On Windows terminals that are not UTF-8, the CLI replaces unprintable characters instead of failing.
- Process isolation (`--isolate`, and the API default) uses the `spawn` start method on all platforms.
  Address-space limits (`memory_limit_mb`) are only enforced on Linux/macOS.
