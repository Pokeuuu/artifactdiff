"""``artifactdiff-api`` / ``python -m artifactdiff_api``: run the API server (and bundled web UI)."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    import uvicorn

    from artifactdiff_api.app import create_app

    parser = argparse.ArgumentParser(description="ArtifactDiff API server")
    parser.add_argument(
        "--host",
        default=os.environ.get("ARTIFACTDIFF_HOST", "127.0.0.1"),
        help="Bind address (default 127.0.0.1: local only)",
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("ARTIFACTDIFF_PORT", "8000")))
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
