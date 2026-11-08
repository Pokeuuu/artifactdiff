# ArtifactDiff developer tasks. Requires: Python 3.12+, uv (https://docs.astral.sh/uv/), Node.js 20+.
# On Windows without make, run the commands shown under each target directly (see CONTRIBUTING.md).

PY      ?= uv run
WEB     := apps/web

.PHONY: setup dev dev-api dev-web test test-fast test-e2e lint format typecheck build web examples demo \
        schema benchmark docker clean

setup:            ## Install Python (all extras + dev) and web dependencies, and git hooks
	uv sync --all-extras
	cd $(WEB) && npm ci
	-$(PY) pre-commit install

dev:              ## API on :8000 and Vite dev server on :5173 (proxying /api)
	$(MAKE) -j2 dev-api dev-web

dev-api:
	ARTIFACTDIFF_ISOLATION=inline $(PY) artifactdiff serve --port 8000

dev-web:
	cd $(WEB) && npm run dev

test:             ## All Python tests (incl. slow) + web typecheck/lint + end-to-end tests
	$(PY) pytest
	cd $(WEB) && npm run typecheck && npm run lint
	$(MAKE) test-e2e

test-fast:        ## Python tests without slow/memory tests
	$(PY) pytest -m "not slow"

test-e2e: web     ## Playwright tests against the real API serving the built UI
	cd $(WEB) && npx playwright install chromium && npm run e2e

lint:             ## Ruff, mypy (strict), ESLint, Prettier
	$(PY) ruff check .
	$(PY) ruff format --check .
	$(PY) mypy
	cd $(WEB) && npm run lint && npm run format:check

format:
	$(PY) ruff check --fix .
	$(PY) ruff format .
	cd $(WEB) && npm run format

typecheck:
	$(PY) mypy
	cd $(WEB) && npm run typecheck

web:              ## Build the web UI and bundle it into the API package
	$(PY) python scripts/build_web.py

build: web        ## Wheel + sdist (with the bundled web UI) into dist/
	uv build

examples:         ## Regenerate example artifacts and expected outputs
	$(PY) python scripts/generate_examples.py

demo:             ## Run every example and check expected outputs
	$(PY) artifactdiff demo --check --output-dir reports/demo

schema:           ## Regenerate docs/schema/*.schema.json
	$(PY) python scripts/export_schema.py

benchmark:
	$(PY) python scripts/benchmark.py

docker:
	docker compose build

clean:
	rm -rf dist build .pytest_cache .mypy_cache .ruff_cache $(WEB)/dist apps/api/src/artifactdiff_api/web reports
