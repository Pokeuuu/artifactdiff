"""Built-in ArtifactDiff plugins.

Plugin modules import their heavy third-party dependencies lazily (inside methods), so the
registry can list every plugin — and report missing extras — without importing NumPy, Polars,
Pillow, etc. up front.
"""

from __future__ import annotations

#: Explicit list of built-in plugins. Nothing else is registered automatically.
BUILTIN_PLUGINS: tuple[str, ...] = (
    "artifactdiff_plugins.binary:BinaryPlugin",
    "artifactdiff_plugins.text:TextPlugin",
    "artifactdiff_plugins.json:JsonPlugin",
    "artifactdiff_plugins.csv:CsvPlugin",
    "artifactdiff_plugins.parquet:ParquetPlugin",
    "artifactdiff_plugins.sqlite:SqlitePlugin",
    "artifactdiff_plugins.image:ImagePlugin",
    "artifactdiff_plugins.audio:AudioPlugin",
    "artifactdiff_plugins.pdf:PdfPlugin",
    "artifactdiff_plugins.gltf:GltfPlugin",
    "artifactdiff_plugins.safetensors:SafetensorsPlugin",
)
