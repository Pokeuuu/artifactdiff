"""Structural JSON diff with configurable array matching.

Paths use JSONPath-like notation (``$.model.layers[2].weights``); each change also carries the
equivalent RFC 6901 JSON Pointer in ``details.pointer`` when one exists.

Severity heuristic: type change = high, removed key/element = medium, added = low,
value change = low, numeric representation only (``1`` vs ``1.0``) = info.
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import math
import re
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import Field, model_validator

from artifactdiff.errors import PluginInputError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, PluginOptions

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A path part is a key (str), an index (int) or a keyed array element (field, value, new_index).
Part = str | int | tuple[str, Any, int]


class JsonOptions(PluginOptions):
    array_strategy: Literal["lcs", "index", "key"] = Field(
        "lcs",
        description="lcs: align elements by content (detects insertions); index: compare by position; "
        "key: match objects by `array_key`.",
    )
    array_key: str | None = Field(None, description="Object field identifying array elements (strategy 'key').")
    ignore_paths: list[str] = Field(default_factory=list, description="Glob patterns such as '$.meta.*'.")
    float_tolerance: float = Field(0.0, ge=0, description="Absolute tolerance for numeric comparisons.")
    max_size_mb: float = Field(256, gt=0, description="JSON is parsed in memory; larger files are rejected.")

    @model_validator(mode="after")
    def _key_needed(self) -> JsonOptions:
        if self.array_strategy == "key" and not self.array_key:
            raise ValueError("array_strategy 'key' requires array_key")
        return self


def json_type(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


def format_path(parts: Iterable[Part]) -> str:
    out = "$"
    for p in parts:
        if isinstance(p, tuple):
            out += f"[{p[0]}={json.dumps(p[1], ensure_ascii=False)}]"
        elif isinstance(p, int):
            out += f"[{p}]"
        elif _IDENT.match(p):
            out += f".{p}"
        else:
            out += f"[{json.dumps(p, ensure_ascii=False)}]"
    return out


def json_pointer(parts: Iterable[Part]) -> str:
    out = ""
    for p in parts:
        if isinstance(p, tuple):
            out += f"/{p[2]}"
        elif isinstance(p, int):
            out += f"/{p}"
        else:
            out += "/" + p.replace("~", "~0").replace("/", "~1")
    return out


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _preview(v: Any) -> Any:
    """Keep old/new values readable: large containers are summarised."""
    if isinstance(v, (dict, list)):
        text = _canon(v)
        if len(text) > 500:
            kind = "object" if isinstance(v, dict) else "array"
            return {"$summary": f"{kind} with {len(v)} entries", "$preview": text[:200] + "…"}
    return v


class _Differ:
    def __init__(self, opts: JsonOptions, ctx: ComparisonContext) -> None:
        self.opts = opts
        self.ctx = ctx
        self.stack: list[tuple[tuple[Part, ...], Any, Any]] = []
        self.visited = 0

    def ignored(self, path: str) -> bool:
        return any(fnmatch.fnmatchcase(path, pat) for pat in self.opts.ignore_paths)

    def emit(
        self,
        kind: ChangeKind,
        parts: tuple[Part, ...],
        desc: str,
        *,
        old: Any = None,
        new: Any = None,
        sev: ChangeSeverity,
        evidence: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        path = format_path(parts)
        if self.ignored(path):
            return
        cat = (
            ChangeCategory.STRUCTURE
            if kind in (ChangeKind.ADDED, ChangeKind.REMOVED, ChangeKind.MOVED)
            else (ChangeCategory.SCHEMA if kind == ChangeKind.TYPE_CHANGED else ChangeCategory.DATA)
        )
        details: dict[str, Any] = {"pointer": json_pointer(parts)}
        if extra:
            details.update(extra)
        self.ctx.add_change(
            kind,
            cat,
            path,
            desc,
            old=_preview(old),
            new=_preview(new),
            severity=sev,
            evidence=evidence,
            details=details,
        )

    def run(self, a: Any, b: Any) -> None:
        self.stack.append(((), a, b))
        while self.stack:
            self.visited += 1
            if self.visited % 5000 == 0:
                self.ctx.check()
            parts, x, y = self.stack.pop()
            if self.opts.ignore_paths and self.ignored(format_path(parts)):
                continue
            self.node(parts, x, y)

    def node(self, parts: tuple[Part, ...], a: Any, b: Any) -> None:
        ta, tb = json_type(a), json_type(b)
        if ta != tb:
            if {ta, tb} == {"integer", "number"}:
                self.numbers(parts, a, b)
            else:
                self.emit(
                    ChangeKind.TYPE_CHANGED,
                    parts,
                    f"Type changed from {ta} to {tb}",
                    old=a,
                    new=b,
                    sev=ChangeSeverity.HIGH,
                    evidence="Compared JSON value types",
                    extra={"old_type": ta, "new_type": tb},
                )
            return
        if ta == "object":
            children = []
            for k, v in a.items():
                if k in b:
                    children.append(((*parts, k), v, b[k]))
                else:
                    self.emit(
                        ChangeKind.REMOVED,
                        (*parts, k),
                        f"Key '{k}' removed",
                        old=v,
                        sev=ChangeSeverity.MEDIUM,
                        evidence="Key present only in the old object",
                    )
            for k, v in b.items():
                if k not in a:
                    self.emit(
                        ChangeKind.ADDED,
                        (*parts, k),
                        f"Key '{k}' added",
                        new=v,
                        sev=ChangeSeverity.LOW,
                        evidence="Key present only in the new object",
                    )
            self.stack.extend(reversed(children))
        elif ta == "array":
            self.array(parts, a, b)
        elif ta in ("integer", "number"):
            self.numbers(parts, a, b)
        elif a != b:
            self.emit(
                ChangeKind.MODIFIED,
                parts,
                "Value changed",
                old=a,
                new=b,
                sev=ChangeSeverity.LOW,
                evidence=f"Compared {ta} values",
            )

    def numbers(self, parts: tuple[Part, ...], a: float, b: float) -> None:
        if type(a) is type(b) and (a == b or (math.isnan(a) and math.isnan(b))):
            return
        if a == b:
            self.emit(
                ChangeKind.MODIFIED,
                parts,
                "Numeric representation changed (value is equal)",
                old=a,
                new=b,
                sev=ChangeSeverity.INFO,
                evidence="Integer vs. floating-point literal",
            )
            return
        diff = abs(b - a)
        if diff <= self.opts.float_tolerance:
            return
        self.emit(
            ChangeKind.MODIFIED,
            parts,
            f"Number changed by {b - a:+g}",
            old=a,
            new=b,
            sev=ChangeSeverity.LOW,
            evidence="Numeric comparison"
            + (f" with tolerance {self.opts.float_tolerance:g}" if self.opts.float_tolerance else ""),
            extra={"delta": b - a},
        )

    # ------------------------------------------------------------------ arrays
    def array(self, parts: tuple[Part, ...], a: list[Any], b: list[Any]) -> None:
        strategy = self.opts.array_strategy
        key = self.opts.array_key
        if strategy == "key" and key is not None:
            if all(isinstance(x, dict) and key in x for x in (*a, *b)):
                self.keyed(parts, a, b, key)
                return
            self.ctx.warn(
                "array-key-missing",
                f"Not every element has key '{key}'; fell back to content alignment (lcs)",
                format_path(parts),
            )
            strategy = "lcs"
        children: list[tuple[tuple[Part, ...], Any, Any]] = []
        if strategy == "index":
            for i in range(min(len(a), len(b))):
                children.append(((*parts, i), a[i], b[i]))
            for i in range(len(b), len(a)):
                self.emit(
                    ChangeKind.REMOVED,
                    (*parts, i),
                    f"Element {i} removed",
                    old=a[i],
                    sev=ChangeSeverity.MEDIUM,
                    evidence="Array shortened (positional matching)",
                )
            for i in range(len(a), len(b)):
                self.emit(
                    ChangeKind.ADDED,
                    (*parts, i),
                    f"Element {i} added",
                    new=b[i],
                    sev=ChangeSeverity.LOW,
                    evidence="Array extended (positional matching)",
                )
        else:
            ha, hb = [_canon(x) for x in a], [_canon(x) for x in b]
            sm = difflib.SequenceMatcher(None, ha, hb, autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    continue
                paired = min(i2 - i1, j2 - j1) if tag == "replace" else 0
                for k in range(paired):
                    children.append(((*parts, j1 + k), a[i1 + k], b[j1 + k]))
                for i in range(i1 + paired, i2):
                    self.emit(
                        ChangeKind.REMOVED,
                        (*parts, i),
                        f"Element at old index {i} removed",
                        old=a[i],
                        sev=ChangeSeverity.MEDIUM,
                        evidence="Content alignment (LCS) of array elements",
                        extra={"old_index": i},
                    )
                for j in range(j1 + paired, j2):
                    self.emit(
                        ChangeKind.ADDED,
                        (*parts, j),
                        f"Element inserted at index {j}",
                        new=b[j],
                        sev=ChangeSeverity.LOW,
                        evidence="Content alignment (LCS) of array elements",
                        extra={"new_index": j},
                    )
        self.stack.extend(reversed(children))

    def keyed(self, parts: tuple[Part, ...], a: list[dict[str, Any]], b: list[dict[str, Any]], key: str) -> None:
        def index(items: list[dict[str, Any]], side: str) -> dict[str, tuple[int, dict[str, Any]]]:
            out: dict[str, tuple[int, dict[str, Any]]] = {}
            for i, x in enumerate(items):
                k = _canon(x[key])
                if k in out:
                    self.ctx.warn(
                        "duplicate-array-key",
                        f"Duplicate {key}={x[key]!r} in {side} array; later element ignored",
                        format_path(parts),
                    )
                    continue
                out[k] = (i, x)
            return out

        ia, ib = index(a, "old"), index(b, "new")
        children: list[tuple[tuple[Part, ...], Any, Any]] = []
        for k, (i, x) in ia.items():
            if k not in ib:
                self.emit(
                    ChangeKind.REMOVED,
                    (*parts, (key, x[key], i)),
                    f"Element {key}={x[key]!r} removed",
                    old=x,
                    sev=ChangeSeverity.MEDIUM,
                    evidence=f"Matched array elements by '{key}'",
                )
        for k, (j, y) in ib.items():
            if k in ia:
                children.append(((*parts, (key, y[key], j)), ia[k][1], y))
            else:
                self.emit(
                    ChangeKind.ADDED,
                    (*parts, (key, y[key], j)),
                    f"Element {key}={y[key]!r} added",
                    new=y,
                    sev=ChangeSeverity.LOW,
                    evidence=f"Matched array elements by '{key}'",
                )
        # Reordering: common keys outside the longest common subsequence moved.
        common_a = [k for k in ia if k in ib]
        common_b = [k for k in ib if k in ia]
        sm = difflib.SequenceMatcher(None, common_a, common_b, autojunk=False)
        in_lcs = {common_a[blk.a + n] for blk in sm.get_matching_blocks() for n in range(blk.size)}
        for k in common_b:
            if k not in in_lcs:
                (i, _), (j, y) = ia[k], ib[k]
                self.emit(
                    ChangeKind.MOVED,
                    (*parts, (key, y[key], j)),
                    f"Element moved from index {i} to {j}",
                    old=i,
                    new=j,
                    sev=ChangeSeverity.INFO,
                    evidence="Element order differs from the longest common ordering",
                )
        self.stack.extend(reversed(children))


class JsonPlugin(ArtifactPlugin):
    name = "json"
    version = "1.0.0"
    display_name = "JSON"
    description = "Structural comparison of JSON documents (configs, API schemas, model configs)."
    format_id = "json"
    format_name = "JSON"
    supported_extensions = (".json", ".geojson", ".jsonld", ".webmanifest", ".ipynb")
    mime_types = ("application/json",)
    capabilities = ("structural-diff", "json-pointer", "array-lcs", "array-key-matching", "ignore-paths")
    limitations = ("JSON documents are parsed fully in memory (bounded by max_size_mb).",)
    Options = JsonOptions

    def _load(self, file: ArtifactFile, opts: JsonOptions, ctx: ComparisonContext | None) -> Any:
        raw = file.read_bytes(int(opts.max_size_mb * 1024 * 1024))
        if not raw.strip():
            raise PluginInputError(f"{file.display_name} is empty (not valid JSON)")
        dupes: list[str] = []

        def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            obj: dict[str, Any] = {}
            for k, v in pairs:
                if k in obj:
                    dupes.append(k)
                obj[k] = v
            return obj

        try:
            text = raw.decode("utf-8-sig")
            data = json.loads(text, object_pairs_hook=hook)
        except UnicodeDecodeError as exc:
            raise PluginInputError(f"{file.display_name} is not UTF-8 encoded: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise PluginInputError(
                f"{file.display_name} is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}"
            ) from exc
        except RecursionError as exc:
            raise PluginInputError(f"{file.display_name} is nested too deeply to parse") from exc
        except ValueError as exc:  # e.g. integers beyond Python's int-string conversion limit
            raise PluginInputError(f"{file.display_name} is not valid JSON: {exc}") from exc
        if dupes and ctx is not None:
            ctx.warn(
                "duplicate-keys",
                f"{file.display_name} contains duplicate object keys ({', '.join(sorted(set(dupes))[:5])}); "
                "the last occurrence wins, as in most JSON parsers",
            )
        return data

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        data = self._load(file, JsonOptions(), None)
        nodes, depth = 0, 0
        stack: list[tuple[Any, int]] = [(data, 1)]
        while stack:
            v, d = stack.pop()
            nodes += 1
            depth = max(depth, d)
            if isinstance(v, dict):
                stack.extend((x, d + 1) for x in v.values())
            elif isinstance(v, list):
                stack.extend((x, d + 1) for x in v)
        props: dict[str, Any] = {"root_type": json_type(data), "nodes": nodes, "max_depth": depth}
        if isinstance(data, dict):
            props["top_level_keys"] = len(data)
        elif isinstance(data, list):
            props["length"] = len(data)
        return props

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, JsonOptions)
        a = self._load(old, options, ctx)
        b = self._load(new, options, ctx)
        differ = _Differ(options, ctx)
        differ.run(a, b)
        ctx.metric("nodes_visited", differ.visited)
