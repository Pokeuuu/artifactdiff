from __future__ import annotations

from pathlib import Path

import pytest
from helpers import kinds_paths, write

from artifactdiff.engine import Engine
from artifactdiff.models import ComparisonStatus

pytest.importorskip("polars")


# ----------------------------------------------------------------------------------- CSV
def test_csv_sample_with_auto_key(engine: Engine, tmp_path: Path) -> None:
    from samples import csv_pair

    r = engine.compare(*csv_pair(tmp_path))
    kp = kinds_paths(r)
    assert ("added", "column:country") in kp
    assert ("removed", "row[id='3']") in kp
    assert ("added", "row[id='11']") in kp
    assert ("modified", "row[id='1']") in kp
    assert ("modified", "row[id='6']") in kp  # name changed
    assert any(w.code == "pk-auto-selected" for w in r.warnings)
    mod = next(c for c in r.changes if c.path == "row[id='1']")
    assert mod.old_value == {"score": "51.5"} and mod.new_value == {"score": "99.5"}
    assert any(c.path == "column:age/missing" for c in r.changes)
    assert r.statistics.metrics["rows_modified"] == 3


def test_csv_without_key_never_pairs_rows(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.csv", "name,v\nx,1\ny,2\n")
    b = write(tmp_path / "b.csv", "name,v\nx,1\ny,3\n")
    r = engine.compare(a, b)
    assert kinds_paths(r) >= {("removed", "old_row[1]"), ("added", "new_row[1]")}
    assert any(w.code == "no-primary-key" for w in r.warnings)


def test_csv_explicit_key_and_composite(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.csv", "k1,k2,v\na,1,x\na,2,y\n")
    b = write(tmp_path / "b.csv", "k1,k2,v\na,1,x\na,2,z\n")
    r = engine.compare(a, b, options={"primary_key": ["k1", "k2"]})
    assert ("modified", "row[k1='a',k2='2']") in kinds_paths(r)


def test_csv_missing_key_column_is_error(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.csv", "a\n1\n")
    r = engine.compare(a, write(tmp_path / "b.csv", "a\n2\n"), options={"primary_key": ["nope"]})
    assert r.status == ComparisonStatus.ERROR


def test_csv_duplicate_key_falls_back(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.csv", "k,v\n1,a\n1,b\n")
    b = write(tmp_path / "b.csv", "k,v\n1,a\n1,c\n")
    r = engine.compare(a, b, options={"primary_key": ["k"]})
    assert any(w.code == "key-not-unique" for w in r.warnings)


def test_csv_type_change_and_duplicates(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.csv", "id,v\n1,10\n2,20\n")
    b = write(tmp_path / "b.csv", "id,v\n1,ten\n2,20\n2,20\n")
    r = engine.compare(a, b, options={"auto_key": False})
    kp = kinds_paths(r)
    assert ("type_changed", "column:v") in kp
    assert ("modified", "duplicates") in kp


def test_csv_tsv_and_sniffing(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.tsv", "id\tv\n1\ta\n")
    b = write(tmp_path / "b.tsv", "id\tv\n1\tb\n")
    assert ("modified", "row[id='1']") in kinds_paths(engine.compare(a, b))
    c = write(tmp_path / "c.csv", "id;v\n1;a\n")
    d = write(tmp_path / "d.csv", "id;v\n1;b\n")
    assert ("modified", "row[id='1']") in kinds_paths(engine.compare(c, d))


def test_csv_empty_and_header_only(engine: Engine, tmp_path: Path) -> None:
    empty = write(tmp_path / "e.csv", "")
    header = write(tmp_path / "h.csv", "a,b\n")
    full = write(tmp_path / "f.csv", "a,b\n1,2\n")
    r = engine.compare(empty, full)
    assert r.status == ComparisonStatus.DIFFERENT
    r = engine.compare(header, full)
    assert r.statistics.metrics["rows_added"] == 1


def test_csv_ragged_is_error(engine: Engine, tmp_path: Path) -> None:
    a = write(tmp_path / "a.csv", "a,b\n1,2\n")
    b = write(tmp_path / "b.csv", "a,b\n1,2,3,4\n")
    assert engine.compare(a, b).status == ComparisonStatus.ERROR


def test_csv_large_counts_everything_lists_capped(engine: Engine, tmp_path: Path) -> None:
    n = 50_000
    a = write(tmp_path / "a.csv", "id,v\n" + "".join(f"{i},{i}\n" for i in range(n)))
    b = write(tmp_path / "b.csv", "id,v\n" + "".join(f"{i},{i + (i % 10 == 0)}\n" for i in range(n)))
    r = engine.compare(a, b, options={"max_row_changes": 100, "compare_statistics": False})
    assert r.statistics.metrics["rows_modified"] == n // 10
    assert r.statistics.total_changes == n // 10
    assert len([c for c in r.changes if c.path.startswith("row[")]) == 100
    # rows beyond the cap are counted, and the engine tells the user that the listing is partial
    assert any(w.code == "changes-truncated" for w in r.warnings)


def test_csv_deterministic(engine: Engine, tmp_path: Path) -> None:
    from samples import csv_pair

    a, b = csv_pair(tmp_path)
    assert engine.compare(a, b).fingerprint == engine.compare(a, b).fingerprint


# ------------------------------------------------------------------------------- Parquet
def test_parquet_sample(engine: Engine, tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from samples import parquet_pair

    r = engine.compare(*parquet_pair(tmp_path))
    kp = kinds_paths(r)
    assert ("type_changed", "column:qty") in kp
    assert ("added", "column:note") in kp
    assert ("modified", "column:id/nullable") in kp
    assert ("modified", "file:compression") in kp
    assert ("removed", "row[id=3]") in kp and ("added", "row[id=5]") in kp
    assert ("modified", "row[id=2]") in kp
    assert any(w.code == "type-changed-columns-skipped" for w in r.warnings)


def test_parquet_corrupted(engine: Engine, tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from samples import parquet_pair

    a, b = parquet_pair(tmp_path)
    data = b.read_bytes()
    bad = write(tmp_path / "bad.parquet", data[: len(data) // 2])
    r = engine.compare(a, bad)
    assert r.status == ComparisonStatus.ERROR


def test_parquet_schema_only(engine: Engine, tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from samples import parquet_pair

    r = engine.compare(*parquet_pair(tmp_path), options={"compare_records": False, "compare_statistics": False})
    assert all(not c.path.startswith("row[") for c in r.changes)


# -------------------------------------------------------------------------------- SQLite
def test_sqlite_sample(engine: Engine, tmp_path: Path) -> None:
    from samples import sqlite_pair

    a, b = sqlite_pair(tmp_path)
    before = (a.read_bytes(), b.read_bytes())
    r = engine.compare(a, b)
    kp = kinds_paths(r)
    assert ("removed", "table:legacy") in kp
    assert ("added", "table:audit") in kp
    assert ("added", "table:users/column:active") in kp
    assert ("modified", "index:idx_users_email") in kp
    assert ("removed", "trigger:trg") in kp
    assert ("modified", "table:users/row[id=2]") in kp
    assert ("removed", "table:users/row[id=3]") in kp
    assert ("added", "table:users/row[id=4]") in kp
    mod = next(c for c in r.changes if c.path == "table:users/row[id=2]")
    assert mod.old_value == {"name": "Bob"} and mod.new_value == {"name": "Robert"}
    # logs has no PK: a modified row = removed + added
    assert ("removed", "table:logs/old_row[1]") in kp and ("added", "table:logs/new_row[1]") in kp
    assert (a.read_bytes(), b.read_bytes()) == before, "source databases must not be modified"


def test_sqlite_storage_class_change(engine: Engine, tmp_path: Path) -> None:
    from samples import sqlite_db

    a = sqlite_db(tmp_path / "a.db", ["CREATE TABLE t (id INTEGER PRIMARY KEY, v)", "INSERT INTO t VALUES (1, 1)"])
    b = sqlite_db(tmp_path / "b.db", ["CREATE TABLE t (id INTEGER PRIMARY KEY, v)", "INSERT INTO t VALUES (1, '1')"])
    assert ("modified", "table:t/row[id=1]") in kinds_paths(engine.compare(a, b))


def test_sqlite_text_keys_merge_join_order(engine: Engine, tmp_path: Path) -> None:
    from samples import sqlite_db

    keys = ["b", "B", "a", "é", "z", "Z", "10", "9"]
    a = sqlite_db(
        tmp_path / "a.db",
        ["CREATE TABLE t (k TEXT PRIMARY KEY, v)", *[f"INSERT INTO t VALUES ('{k}', 1)" for k in keys]],
    )
    b = sqlite_db(
        tmp_path / "b.db",
        [
            "CREATE TABLE t (k TEXT PRIMARY KEY, v)",
            *[f"INSERT INTO t VALUES ('{k}', {2 if k == 'é' else 1})" for k in keys],
        ],
    )
    r = engine.compare(a, b)
    assert kinds_paths(r) == {("modified", "table:t/row[k='é']")}


def test_sqlite_large_table_streaming(engine: Engine, tmp_path: Path) -> None:
    from samples import sqlite_db

    n = 30_000
    a = sqlite_db(
        tmp_path / "a.db",
        [
            "CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)",
            f"WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<{n}) "
            "INSERT INTO t SELECT x, x FROM c",
        ],
    )
    b = sqlite_db(
        tmp_path / "b.db",
        [
            "CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)",
            f"WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<{n}) "
            "INSERT INTO t SELECT x, CASE WHEN x % 100 = 0 THEN -x ELSE x END FROM c",
        ],
    )
    r = engine.compare(a, b, options={"max_row_changes": 10})
    assert r.statistics.metrics["t.rows_modified"] == n // 100
    assert r.statistics.total_changes == n // 100


def test_sqlite_invalid_and_ext_without_signature(engine: Engine, tmp_path: Path) -> None:
    from samples import sqlite_pair

    a, _ = sqlite_pair(tmp_path)
    bad = write(tmp_path / "bad.sqlite", b"SQLite format 3\x00" + b"\xff" * 200)
    r = engine.compare(a, bad, plugin="sqlite")
    assert r.status == ComparisonStatus.ERROR
    notdb = write(tmp_path / "x.db", b"not a database at all")
    other = write(tmp_path / "y.db", b"not a database either")
    assert engine.compare(notdb, other).plugin.name != "sqlite"  # type: ignore[union-attr]


def test_sqlite_schema_only(engine: Engine, tmp_path: Path) -> None:
    from samples import sqlite_pair

    r = engine.compare(*sqlite_pair(tmp_path), options={"compare_data": False})
    assert all(c.category.value != "data" for c in r.changes)
