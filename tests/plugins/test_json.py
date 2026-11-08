from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import kinds_paths, write

from artifactdiff.engine import Engine
from artifactdiff.errors import InvalidOptionsError
from artifactdiff.models import ComparisonStatus
from artifactdiff_plugins.json import format_path, json_pointer


def pair(tmp_path: Path, a: object, b: object) -> tuple[Path, Path]:
    return (write(tmp_path / "a.json", json.dumps(a)), write(tmp_path / "b.json", json.dumps(b)))


def test_sample_pair(engine: Engine, tmp_path: Path) -> None:
    from samples import json_pair

    r = engine.compare(*json_pair(tmp_path))
    kp = kinds_paths(r)
    assert ("modified", "$.model.name") in kp
    assert ("modified", "$.model.layers[0].units") in kp
    assert ("added", "$.model.layers[2]") in kp
    assert ("type_changed", "$.training.epochs") in kp
    assert ("added", "$.training.seed") in kp
    assert ("removed", "$.tags[1]") in kp
    tc = next(c for c in r.changes if c.kind.value == "type_changed")
    assert tc.severity.value == "high" and tc.details["pointer"] == "/training/epochs"


def test_added_removed_nested(engine: Engine, tmp_path: Path) -> None:
    r = engine.compare(*pair(tmp_path, {"a": {"b": 1, "c": 2}}, {"a": {"b": 1, "d": 3}}))
    assert kinds_paths(r) == {("removed", "$.a.c"), ("added", "$.a.d")}


def test_bool_vs_int_is_type_change(engine: Engine, tmp_path: Path) -> None:
    r = engine.compare(*pair(tmp_path, {"x": True}, {"x": 1}))
    assert kinds_paths(r) == {("type_changed", "$.x")}


def test_int_float_representation_is_info(engine: Engine, tmp_path: Path) -> None:
    r = engine.compare(*pair(tmp_path, {"x": 1}, {"x": 1.0}))
    assert [c.severity.value for c in r.changes] == ["info"]


def test_float_tolerance(engine: Engine, tmp_path: Path) -> None:
    a, b = pair(tmp_path, {"x": 1.0}, {"x": 1.0000001})
    assert engine.compare(a, b).status == ComparisonStatus.DIFFERENT
    assert engine.compare(a, b, options={"float_tolerance": 1e-3}).status == ComparisonStatus.EQUIVALENT


def test_array_strategies(engine: Engine, tmp_path: Path) -> None:
    a, b = pair(tmp_path, [1, 2, 3], [0, 1, 2, 3])
    assert kinds_paths(engine.compare(a, b)) == {("added", "$[0]")}
    idx = kinds_paths(engine.compare(a, b, options={"array_strategy": "index"}))
    assert ("added", "$[3]") in idx and ("modified", "$[0]") in idx


def test_array_key_strategy_and_moves(engine: Engine, tmp_path: Path) -> None:
    a, b = pair(
        tmp_path,
        [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 3, "v": "c"}],
        [{"id": 3, "v": "c"}, {"id": 1, "v": "A"}, {"id": 4, "v": "d"}],
    )
    r = engine.compare(a, b, options={"array_strategy": "key", "array_key": "id"})
    kp = kinds_paths(r)
    assert ("removed", "$[id=2]") in kp
    assert ("added", "$[id=4]") in kp
    assert ("modified", "$[id=1].v") in kp
    assert any(k == "moved" for k, _ in kp)


def test_key_strategy_requires_key(engine: Engine, tmp_path: Path) -> None:
    a, b = pair(tmp_path, [1], [2])
    with pytest.raises(InvalidOptionsError):
        engine.compare(a, b, options={"array_strategy": "key"})


def test_key_strategy_falls_back_with_warning(engine: Engine, tmp_path: Path) -> None:
    a, b = pair(tmp_path, [{"id": 1}, 2], [{"id": 1}, 3])
    r = engine.compare(a, b, options={"array_strategy": "key", "array_key": "id"})
    assert any(w.code == "array-key-missing" for w in r.warnings)


def test_ignore_paths(engine: Engine, tmp_path: Path) -> None:
    a, b = pair(tmp_path, {"meta": {"ts": 1, "by": "x"}, "v": 1}, {"meta": {"ts": 2, "by": "y"}, "v": 1})
    assert engine.compare(a, b, options={"ignore_paths": ["$.meta.*"]}).status == ComparisonStatus.EQUIVALENT


def test_special_keys_paths() -> None:
    assert format_path(["a b", 0, "c"]) == '$["a b"][0].c'
    assert json_pointer(["a/b", "~x", 2]) == "/a~1b/~0x/2"


def test_invalid_and_empty(engine: Engine, tmp_path: Path) -> None:
    good = write(tmp_path / "g.json", "{}")
    bad = write(tmp_path / "bad.json", '{"a": ')
    empty = write(tmp_path / "e.json", "")
    r = engine.compare(good, bad)
    assert r.status == ComparisonStatus.ERROR and "not valid JSON" in (r.error or "")
    r = engine.compare(good, empty)
    assert r.status == ComparisonStatus.ERROR and "empty" in (r.error or "")


def test_duplicate_keys_warn(engine: Engine, tmp_path: Path) -> None:
    r = engine.compare(write(tmp_path / "a.json", '{"a":1,"a":2}'), write(tmp_path / "b.json", '{"a":3}'))
    assert any(w.code == "duplicate-keys" for w in r.warnings)


def test_deep_nesting_does_not_overflow(engine: Engine, tmp_path: Path) -> None:
    depth = 900
    a = write(tmp_path / "a.json", "[" * depth + "1" + "]" * depth)
    b = write(tmp_path / "b.json", "[" * depth + "2" + "]" * depth)
    r = engine.compare(a, b)
    assert r.status == ComparisonStatus.DIFFERENT and len(r.changes) == 1


def test_large_document(engine: Engine, tmp_path: Path) -> None:
    a_doc = {"items": [{"id": i, "v": i * 2} for i in range(20_000)]}
    b_doc = {"items": [{"id": i, "v": i * 2 + (1 if i % 1000 == 0 else 0)} for i in range(20_000)]}
    r = engine.compare(*pair(tmp_path, a_doc, b_doc))
    assert r.statistics.total_changes == 20


def test_inspect(engine: Engine, tmp_path: Path) -> None:
    meta = engine.inspect(write(tmp_path / "a.json", '{"a": [1, {"b": 2}]}'))
    assert meta.properties["root_type"] == "object" and meta.properties["max_depth"] == 4


def test_regression_huge_integer_is_invalid_input_not_crash(engine: Engine, tmp_path: Path) -> None:
    # json raises a plain ValueError (not JSONDecodeError) beyond the int-string conversion limit
    a = write(tmp_path / "a.json", '{"n": 1}')
    b = write(tmp_path / "b.json", '{"n": ' + "9" * 5000 + "}")
    r = engine.compare(a, b)
    assert r.status == ComparisonStatus.ERROR and "not valid JSON" in (r.error or "")
    assert "failed:" not in (r.error or ""), "must not be reported as a plugin crash"
