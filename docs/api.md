# REST API

Start with `artifactdiff serve` (or `artifactdiff-api`, or Docker). Interactive OpenAPI docs are at
`/api/docs`, the schema at `/api/openapi.json`. All endpoints are under `/api/v1`.

## Authentication

None by default — the server binds to `127.0.0.1` and is meant for local use. Set
`ARTIFACTDIFF_API_TOKEN` to require `Authorization: Bearer <token>` on every endpoint except
`/health`. Do this (or put an authenticating reverse proxy in front) whenever anyone else can reach
the server.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Status, version, plugin availability, database check (public) |
| GET | `/config` | Server limits and retention, for clients |
| GET | `/plugins` | All plugins with metadata and options JSON Schema |
| GET | `/plugins/{name}` | One plugin |
| POST | `/compare` | Start a comparison (multipart). Returns `202` with the comparison id |
| GET | `/comparisons` | List: `q`, `plugin`, `status`, `sort` (`-created_at`, `created_at`, `-total_changes`, `total_changes`, `name`), `limit` (≤200), `offset` |
| GET | `/comparisons/{id}` | Detail: job status, progress, options, full result |
| GET | `/comparisons/{id}/report?format=html\|markdown\|json` | Download a standalone report (assets embedded) |
| GET | `/comparisons/{id}/assets/{asset_id}` | A generated visualization |
| GET | `/comparisons/{id}/files/{old\|new}` | A retained input (served as an attachment; `410` after retention) |
| POST | `/comparisons/{id}/cancel` | Cancel a queued/running comparison |
| DELETE | `/comparisons/{id}` | Delete a comparison, its assets and inputs |
| DELETE | `/comparisons?confirm=true` | Delete all comparisons |
| GET | `/stats` | Dashboard metrics computed from stored comparisons |

### Starting a comparison

```bash
curl -F old=@old.parquet -F new=@new.parquet \
     -F 'options={"primary_key":["id"]}' \
     http://127.0.0.1:8000/api/v1/compare
# {"id":"3f2a…","job_status":"queued","url":"/api/v1/comparisons/3f2a…"}
```

Form fields: `old`, `new` (files), optional `plugin`, optional `options` (JSON object). Plugin and
options are validated before the job is queued (`422` with a message on error). Then poll
`GET /comparisons/{id}` until `job_status` is `completed`, `failed` or `cancelled`;
`progress` (0–1) and `progress_message` are available while running. `result` follows the
[result schema](schema/result.schema.json), with asset `url`s instead of inline data.

### Errors

Errors use `{"detail": "..."}` with: `401` (token), `404`, `409` (e.g. report requested before the
result exists), `410` (inputs deleted by retention), `413` (upload too large), `422` (validation),
`429` (rate limit).

## Configuration

Environment variables (or a `.env` file), all prefixed `ARTIFACTDIFF_` — see
[`.env.example`](../.env.example):

| Variable | Default | |
|---|---|---|
| `DATA_DIR` | user data dir | Uploads, assets, SQLite database |
| `DATABASE_URL` | SQLite in `DATA_DIR` | Any SQLAlchemy URL; PostgreSQL via `postgresql+psycopg://…` (`postgres` extra) |
| `MAX_UPLOAD_MB` | 512 | Per file; enforced while streaming |
| `INPUT_RETENTION_HOURS` | 24 | Uploaded files are deleted after this |
| `RESULT_RETENTION_DAYS` | 0 | Delete whole comparisons after this (0 = keep) |
| `TIMEOUT_SECONDS` | 300 | Per comparison |
| `MAX_CHANGES` | 10000 | Changes kept per result |
| `ISOLATION` | `process` | `process` = worker per comparison (hard timeout, crash isolation); `inline` |
| `MAX_CONCURRENT_JOBS` | 2 | Parallel comparisons |
| `MEMORY_LIMIT_MB` | — | Per worker, POSIX only |
| `RATE_LIMIT_PER_MINUTE` | 30 | `POST /compare` per client address; 0 disables |
| `API_TOKEN` | — | Require a bearer token |
| `CORS_ORIGINS` | Vite dev origins | JSON list |
| `WEB_DIR` | bundled | Serve a different web UI build |

## Storage and retention

Uploads are written under `DATA_DIR/comparisons/<id>/inputs/` using server-generated names (client
file names are only display labels). Inputs are kept for `INPUT_RETENTION_HOURS` so the UI can show
source diffs, audio playback and the 3D viewer, then deleted by a background sweeper; results and
generated visualizations stay until deleted or until `RESULT_RETENTION_DAYS`. Database migrations
(Alembic) run automatically at startup.
