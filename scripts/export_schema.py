"""Write the JSON Schemas for reports and results to docs/schema/ (checked by the test suite)."""

from __future__ import annotations

import json
from pathlib import Path

from artifactdiff.models import ComparisonResult, Report

OUT = Path(__file__).resolve().parents[1] / "docs" / "schema"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, model in (("report", Report), ("result", ComparisonResult)):
        path = OUT / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
