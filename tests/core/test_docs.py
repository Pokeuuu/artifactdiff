import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_supported_formats_doc_is_current() -> None:
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_format_docs.py"), "--check"], check=False)
    assert res.returncode == 0, "run: python scripts/gen_format_docs.py"


def test_docs_exist() -> None:
    for name in [
        "installation",
        "quickstart",
        "architecture",
        "plugin-development",
        "github-action",
        "cli",
        "api",
        "security",
        "supported-formats",
        "limitations",
        "roadmap",
        "benchmarks",
    ]:
        assert (ROOT / "docs" / f"{name}.md").is_file(), name
