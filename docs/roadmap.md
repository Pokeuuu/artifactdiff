# Roadmap

Priorities are set by what makes reviews of real artifacts better. Proposals welcome via issues.

## Next (0.2)

- **ONNX** model diff: graph nodes/ops, inputs/outputs, opset, and initializer tensors through the
  existing format-neutral tensor core (`_tensors.TensorStore`).
- **GGUF** weights: metadata key/values, tensor list, quantization types; values for non-quantized types.
- **Git integration**: `artifactdiff git-setup` to register ArtifactDiff as a `git diff` driver /
  textconv for configured patterns.
- **Parquet/CSV**: keyed comparison on disk for datasets larger than memory; numeric tolerance option.
- **Audio**: LUFS (ITU-R BS.1770) loudness; optional alignment before waveform comparison.

## Later

- glTF: Draco/meshopt decoding, animation channel comparison, USD and OBJ plugins.
- PDF: opt-in OCR via a separate, explicitly installed plugin; annotation and form-field changes.
- Office documents (docx/xlsx/pptx structure), Excel cell-level diff.
- Video (container metadata, frame sampling), point clouds (PLY/LAS).
- Web UI: shareable read-only report links, side-by-side table viewer for large datasets.
- VS Code extension using the CLI's JSON output.
- Signed releases and SBOMs.

## Principles that will not change

Local-first processing, no implicit execution of third-party code, every change backed by evidence,
and an open-source core.
