from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # makes `samples` and `helpers` importable

from artifactdiff.config import ArtifactDiffConfig
from artifactdiff.engine import Engine


@pytest.fixture
def engine() -> Engine:
    return Engine(config=ArtifactDiffConfig())
