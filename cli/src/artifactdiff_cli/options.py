"""Parsing of ``-O key=value`` options into a dict validated later against the plugin model."""

from __future__ import annotations

import json
import types
import typing
from pathlib import Path
from typing import Any

from artifactdiff.errors import InvalidOptionsError
from artifactdiff.plugin import PluginOptions


def _is_list_field(model: type[PluginOptions], key: str) -> bool:
    field = model.model_fields.get(key)
    if field is None:
        return False
    ann = field.annotation
    candidates = (
        typing.get_args(ann) if isinstance(ann, types.UnionType) or typing.get_origin(ann) is typing.Union else (ann,)
    )
    return any(typing.get_origin(c) is list or c is list for c in candidates)


def parse_option_pairs(pairs: list[str], model: type[PluginOptions] | None = None) -> dict[str, Any]:
    """``["primary_key=id,name", "tolerance=8"]`` -> ``{"primary_key": ["id","name"], "tolerance": 8}``.

    Values are parsed as JSON when possible; for list-typed options a bare string is split on commas.
    """
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        key = key.strip().replace("-", "_")
        if not sep or not key:
            raise InvalidOptionsError(f"Options must look like key=value, got {pair!r}")
        try:
            value: Any = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        if model is not None and isinstance(value, str) and _is_list_field(model, key):
            value = [v.strip() for v in value.split(",") if v.strip()]
        out[key] = value
    return out


def load_options_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidOptionsError(f"Cannot read options file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidOptionsError(f"Options file {path} must contain a JSON object")
    return data
