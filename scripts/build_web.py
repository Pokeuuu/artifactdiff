"""Build the web UI and copy it into the API package so `artifactdiff serve` (and the wheel) ship it.

Usage: python scripts/build_web.py [--skip-npm]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
TARGET = ROOT / "apps" / "api" / "src" / "artifactdiff_api" / "web"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-npm", action="store_true", help="Only copy an existing apps/web/dist")
    args = parser.parse_args()
    npm = shutil.which("npm")
    if not args.skip_npm:
        if npm is None:
            print("npm not found; install Node.js 20+ or pass --skip-npm", file=sys.stderr)
            return 1
        if not (WEB / "node_modules").is_dir():
            subprocess.run([npm, "ci"], cwd=WEB, check=True)
        subprocess.run([npm, "run", "build"], cwd=WEB, check=True)
    dist = WEB / "dist"
    if not (dist / "index.html").is_file():
        print(f"{dist} has no index.html; build the web UI first", file=sys.stderr)
        return 1
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(dist, TARGET)
    print(f"copied {dist} -> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
