# syntax=docker/dockerfile:1.7
# ArtifactDiff: API + web UI in one image. Build from the repository root:
#   docker build -f docker/Dockerfile -t artifactdiff .

# --- 1. web UI ------------------------------------------------------------------------
FROM node:22-bookworm-slim AS web
WORKDIR /src/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

# --- 2. Python wheel (with the built UI bundled) -----------------------------------------
FROM python:3.12-slim-bookworm AS build
RUN pip install --no-cache-dir build==1.2.2
WORKDIR /src
COPY pyproject.toml README.md LICENSE CHANGELOG.md ./
COPY packages/ packages/
COPY cli/ cli/
COPY apps/api/ apps/api/
COPY --from=web /src/apps/web/dist/ apps/api/src/artifactdiff_api/web/
RUN python -m build --wheel --outdir /dist

# --- 3. runtime -----------------------------------------------------------------------
FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.title="ArtifactDiff" \
      org.opencontainers.image.description="The universal diff for everything Git cannot meaningfully compare" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ARTIFACTDIFF_DATA_DIR=/data ARTIFACTDIFF_HOST=0.0.0.0 ARTIFACTDIFF_PORT=8000
COPY --from=build /dist/*.whl /tmp/
RUN WHEEL="$(ls /tmp/*.whl)" && pip install --no-cache-dir "${WHEEL}[all,postgres]" && rm -f /tmp/*.whl \
 && useradd --system --uid 10001 --home-dir /data artifactdiff && mkdir -p /data && chown artifactdiff /data
USER artifactdiff
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"
ENTRYPOINT ["artifactdiff-api"]
