import sys
from pathlib import Path

# Make example plugins importable without installing them.
for src in Path(__file__).parent.glob("*/src"):
    sys.path.insert(0, str(src))
