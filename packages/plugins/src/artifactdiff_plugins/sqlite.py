"""SQLite database comparison: schema (tables, columns, indexes, triggers, views) and table data.

Safety: databases are opened with ``mode=ro`` (plus ``immutable=1`` when no WAL file exists),
``PRAGMA query_only`` and ``trusted_schema=OFF``. Only ``sqlite_master``, ``PRAGMA table_info``
and ``SELECT`` on ordinary tables are executed; views and virtual tables are never queried.

Data matching: tables with a declared primary key (identical in both files) are compared with a
streaming merge-join ordered by that key, so memory use is independent of table size. Tables
without one are compared as a multiset of row hashes (or by rowid if ``match_by_rowid``).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field

from artifactdiff.errors import PluginInputError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, Detection, PluginOptions
from artifactdiff_plugins._common import json_safe

_WS = re.compile(r"\s+")


class SqliteOptions(PluginOptions):
    compare_data: bool = Field(True, description="Compare table contents (not only the schema).")
    tables: list[str] = Field(default_factory=list, description="Only compare these tables (default: all).")
    ignore_tables: list[str] = Field(default_factory=list, description="Tables excluded from data comparison.")
    match_by_rowid: bool = Field(
        False, description="For tables without a primary key, match rows by rowid (unstable after VACUUM)."
    )
    max_row_changes: int = Field(200, ge=0, description="Rows listed per table (all are counted).")


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _norm_sql(sql: str | None) -> str:
    return _WS.sub(" ", sql or "").strip().rstrip(";")


def connect(file: ArtifactFile) -> sqlite3.Connection:
    path = Path(file.path).resolve()
    wal = path.with_name(path.name + "-wal")
    params = "mode=ro" if wal.exists() else "mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?{params}", uri=True, check_same_thread=False)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA trusted_schema = OFF")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError as exc:
        raise PluginInputError(f"{file.display_name} is not a readable SQLite database: {exc}") from exc
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return conn


@dataclass
class Column:
    name: str
    type: str
    notnull: bool
    default: str | None
    pk: int


@dataclass
class Schema:
    tables: dict[str, str]  # name -> normalised SQL
    virtual: set[str]
    without_rowid: set[str]
    indexes: dict[str, tuple[str, str]]  # name -> (table, sql)
    triggers: dict[str, tuple[str, str]]
    views: dict[str, str]
    columns: dict[str, list[Column]]


def read_schema(conn: sqlite3.Connection) -> Schema:
    s = Schema({}, set(), set(), {}, {}, {}, {})
    rows = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    for typ, name, tbl, sql in rows:
        nsql = _norm_sql(sql)
        if typ == "table":
            s.tables[name] = nsql
            if nsql.upper().startswith("CREATE VIRTUAL TABLE"):
                s.virtual.add(name)
            if re.search(r"\)\s*WITHOUT\s+ROWID", nsql, re.IGNORECASE):
                s.without_rowid.add(name)
        elif typ == "index":
            s.indexes[name] = (tbl, nsql or "(automatic index)")
        elif typ == "trigger":
            s.triggers[name] = (tbl, nsql)
        elif typ == "view":
            s.views[name] = nsql
    for t in s.tables:
        if t in s.virtual:
            continue
        info = conn.execute(f"PRAGMA table_info({q(t)})").fetchall()
        s.columns[t] = [Column(r[1], r[2] or "", bool(r[3]), r[4], int(r[5])) for r in info]
    return s


def _pk(cols: list[Column]) -> list[str]:
    return [c.name for c in sorted((c for c in cols if c.pk), key=lambda c: c.pk)]


def _sort_key(values: tuple[Any, ...]) -> tuple[tuple[int, Any], ...]:
    """Python ordering identical to SQLite's ORDER BY ... COLLATE BINARY across storage classes."""
    out: list[tuple[int, Any]] = []
    for v in values:
        if v is None:
            out.append((0, 0))
        elif isinstance(v, (int, float)):
            out.append((1, v))
        elif isinstance(v, str):
            out.append((2, v.encode("utf-8", "surrogatepass")))
        else:
            out.append((3, bytes(v)))
    return tuple(out)


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def _iter(conn: sqlite3.Connection, sql: str) -> Iterator[tuple[Any, ...]]:
    cur = conn.execute(sql)
    while batch := cur.fetchmany(2000):
        yield from batch


class SqlitePlugin(ArtifactPlugin):
    name = "sqlite"
    version = "1.0.0"
    display_name = "SQLite"
    description = "Schema and data comparison of SQLite databases (opened read-only)."
    format_id = "sqlite"
    format_name = "SQLite database"
    supported_extensions = (".sqlite", ".sqlite3", ".db", ".db3")
    mime_types = ("application/vnd.sqlite3", "application/x-sqlite3")
    signatures = (b"SQLite format 3\x00",)
    capabilities = ("schema-diff", "index-diff", "trigger-diff", "view-diff", "row-diff", "streaming", "read-only")
    limitations = (
        "Views and virtual tables are compared by definition only; their contents are not queried.",
        "Tables without a primary key are compared as row multisets (a changed row = removal + addition) "
        "unless match_by_rowid is enabled.",
    )
    Options = SqliteOptions

    def supports(self, file: ArtifactFile) -> Detection | None:
        det = super().supports(file)
        if det is not None and det.method == "extension" and file.size and not self.sniff(file.head(16)):
            return None  # e.g. a ".db" file that is not SQLite
        return det

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        with closing(connect(file)) as conn:
            s = read_schema(conn)
            props: dict[str, Any] = {
                "sqlite_version": sqlite3.sqlite_version,
                "page_size": conn.execute("PRAGMA page_size").fetchone()[0],
                "encoding": conn.execute("PRAGMA encoding").fetchone()[0],
                "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
                "tables": {},
                "indexes": len(s.indexes),
                "triggers": len(s.triggers),
                "views": len(s.views),
            }
            for t in s.tables:
                rows = None if t in s.virtual else conn.execute(f"SELECT count(*) FROM {q(t)}").fetchone()[0]
                props["tables"][t] = {"rows": rows, "columns": [c.name for c in s.columns.get(t, [])]}
            return props

    # ------------------------------------------------------------------- compare
    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, SqliteOptions)
        with closing(connect(old)) as ca, closing(connect(new)) as cb:
            sa, sb = read_schema(ca), read_schema(cb)
            self._schema(ctx, sa, sb)
            for pragma in ("user_version", "application_id", "encoding", "page_size"):
                va = ca.execute(f"PRAGMA {pragma}").fetchone()[0]
                vb = cb.execute(f"PRAGMA {pragma}").fetchone()[0]
                if va != vb:
                    ctx.add_change(
                        ChangeKind.MODIFIED,
                        ChangeCategory.METADATA,
                        f"pragma:{pragma}",
                        f"PRAGMA {pragma} changed",
                        old=va,
                        new=vb,
                        severity=ChangeSeverity.INFO,
                        evidence=f"PRAGMA {pragma} on both databases",
                    )
            if not options.compare_data:
                ctx.limitation("Data comparison disabled (compare_data=false); only the schema was compared.")
                return
            tables = [
                t
                for t in sa.tables
                if t in sb.tables
                and t not in sa.virtual
                and t not in sb.virtual
                and (not options.tables or t in options.tables)
                and t not in options.ignore_tables
            ]
            for i, t in enumerate(tables):
                ctx.progress(i / max(1, len(tables)), f"table {t}")
                self._table_data(ctx, ca, cb, t, sa, sb, options)
            ctx.metric("tables_compared", len(tables))

    def _schema(self, ctx: ComparisonContext, sa: Schema, sb: Schema) -> None:
        ev = "sqlite_master and PRAGMA table_info"
        for t in sa.tables:
            if t not in sb.tables:
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.SCHEMA,
                    f"table:{t}",
                    f"Table '{t}' removed",
                    old=sa.tables[t],
                    severity=ChangeSeverity.HIGH,
                    evidence=ev,
                )
        for t in sb.tables:
            if t not in sa.tables:
                ctx.add_change(
                    ChangeKind.ADDED,
                    ChangeCategory.SCHEMA,
                    f"table:{t}",
                    f"Table '{t}' added",
                    new=sb.tables[t],
                    severity=ChangeSeverity.LOW,
                    evidence=ev,
                )
        for t in sa.tables:
            if t not in sb.tables:
                continue
            ca = {c.name: c for c in sa.columns.get(t, [])}
            cb = {c.name: c for c in sb.columns.get(t, [])}
            explained = False
            for name, c in ca.items():
                if name not in cb:
                    explained = True
                    ctx.add_change(
                        ChangeKind.REMOVED,
                        ChangeCategory.SCHEMA,
                        f"table:{t}/column:{name}",
                        f"Column '{name}' removed from '{t}'",
                        old=c.type,
                        severity=ChangeSeverity.HIGH,
                        evidence=ev,
                    )
            for name, c in cb.items():
                if name not in ca:
                    explained = True
                    ctx.add_change(
                        ChangeKind.ADDED,
                        ChangeCategory.SCHEMA,
                        f"table:{t}/column:{name}",
                        f"Column '{name}' added to '{t}'",
                        new=c.type,
                        severity=ChangeSeverity.LOW,
                        evidence=ev,
                    )
            for name, c in ca.items():
                d = cb.get(name)
                if d is None:
                    continue
                if c.type.upper() != d.type.upper():
                    explained = True
                    ctx.add_change(
                        ChangeKind.TYPE_CHANGED,
                        ChangeCategory.SCHEMA,
                        f"table:{t}/column:{name}",
                        f"Declared type of '{t}.{name}' changed",
                        old=c.type,
                        new=d.type,
                        severity=ChangeSeverity.MEDIUM,
                        evidence=ev,
                    )
                for attr, label in (("notnull", "NOT NULL"), ("default", "DEFAULT"), ("pk", "PRIMARY KEY position")):
                    if getattr(c, attr) != getattr(d, attr):
                        explained = True
                        ctx.add_change(
                            ChangeKind.MODIFIED,
                            ChangeCategory.SCHEMA,
                            f"table:{t}/column:{name}/{attr}",
                            f"{label} of '{t}.{name}' changed",
                            old=getattr(c, attr),
                            new=getattr(d, attr),
                            severity=ChangeSeverity.MEDIUM,
                            evidence=ev,
                        )
            if sa.tables[t] != sb.tables[t] and not explained:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.SCHEMA,
                    f"table:{t}",
                    f"Definition of table '{t}' changed (constraints or options)",
                    old=sa.tables[t],
                    new=sb.tables[t],
                    severity=ChangeSeverity.MEDIUM,
                    evidence="Whitespace-normalised CREATE TABLE statements differ",
                )
        for kind_name, da, db in (("index", sa.indexes, sb.indexes), ("trigger", sa.triggers, sb.triggers)):
            self._objects(ctx, kind_name, {k: v[1] for k, v in da.items()}, {k: v[1] for k, v in db.items()})
        self._objects(ctx, "view", sa.views, sb.views)

    def _objects(self, ctx: ComparisonContext, kind: str, a: dict[str, str], b: dict[str, str]) -> None:
        ev = f"Whitespace-normalised CREATE {kind.upper()} statements from sqlite_master"
        for name in a:
            if name not in b:
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.SCHEMA,
                    f"{kind}:{name}",
                    f"{kind.capitalize()} '{name}' removed",
                    old=a[name],
                    severity=ChangeSeverity.MEDIUM,
                    evidence=ev,
                )
            elif a[name] != b[name]:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.SCHEMA,
                    f"{kind}:{name}",
                    f"{kind.capitalize()} '{name}' changed",
                    old=a[name],
                    new=b[name],
                    severity=ChangeSeverity.MEDIUM,
                    evidence=ev,
                )
        for name in b:
            if name not in a:
                ctx.add_change(
                    ChangeKind.ADDED,
                    ChangeCategory.SCHEMA,
                    f"{kind}:{name}",
                    f"{kind.capitalize()} '{name}' added",
                    new=b[name],
                    severity=ChangeSeverity.LOW,
                    evidence=ev,
                )

    # ---------------------------------------------------------------------- data
    def _table_data(
        self,
        ctx: ComparisonContext,
        ca: sqlite3.Connection,
        cb: sqlite3.Connection,
        t: str,
        sa: Schema,
        sb: Schema,
        opts: SqliteOptions,
    ) -> None:
        cols_a = [c.name for c in sa.columns[t]]
        cols_b = {c.name for c in sb.columns[t]}
        common = [c for c in cols_a if c in cols_b]
        pk_a, pk_b = _pk(sa.columns[t]), _pk(sb.columns[t])
        count_a = ca.execute(f"SELECT count(*) FROM {q(t)}").fetchone()[0]
        count_b = cb.execute(f"SELECT count(*) FROM {q(t)}").fetchone()[0]
        if count_a != count_b:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.STRUCTURE,
                f"table:{t}/rows",
                f"Row count of '{t}' changed by {count_b - count_a:+,}",
                old=count_a,
                new=count_b,
                severity=ChangeSeverity.LOW,
                evidence="SELECT count(*)",
            )
        if not common:
            return
        key: list[str] | None = None
        if pk_a and pk_a == pk_b and all(k in common for k in pk_a):
            nulls = sum(
                c.execute(
                    f"SELECT count(*) FROM {q(t)} WHERE " + " OR ".join(f"{q(k)} IS NULL" for k in pk_a)
                ).fetchone()[0]
                for c in (ca, cb)
            )
            if nulls:
                ctx.warn("pk-null", f"Table '{t}' has NULL primary key values; compared rows by content", f"table:{t}")
            else:
                key = pk_a
        elif pk_a != pk_b:
            ctx.warn(
                "pk-changed", f"Primary key of '{t}' differs between files; compared rows by content", f"table:{t}"
            )
        if key is None and opts.match_by_rowid and t not in sa.without_rowid and t not in sb.without_rowid:
            key = ["rowid"]
            ctx.warn("rowid-matching", f"Rows of '{t}' matched by rowid (match_by_rowid=true)", f"table:{t}")
        if key is not None:
            self._merge_join(ctx, ca, cb, t, key, common, opts)
        else:
            ordered = t not in sa.without_rowid and t not in sb.without_rowid
            self._multiset(ctx, ca, cb, t, common, opts, ordered)

    def _merge_join(
        self,
        ctx: ComparisonContext,
        ca: sqlite3.Connection,
        cb: sqlite3.Connection,
        t: str,
        key: list[str],
        common: list[str],
        opts: SqliteOptions,
    ) -> None:
        values = [c for c in common if c not in key]
        select = ", ".join(q(c) if c != "rowid" else "rowid" for c in [*key, *values])
        order = ", ".join("rowid" if k == "rowid" else f"{q(k)} COLLATE BINARY" for k in key)
        sql = f"SELECT {select} FROM {q(t)} ORDER BY {order}"
        nk = len(key)
        listed = 0
        counts: Counter[str] = Counter()
        it_a, it_b = _iter(ca, sql), _iter(cb, sql)
        ra, rb = next(it_a, None), next(it_b, None)
        steps = 0

        def path(row: tuple[Any, ...]) -> str:
            return f"table:{t}/row[" + ",".join(f"{k}={json_safe(v)!r}" for k, v in zip(key, row, strict=False)) + "]"

        def emit(kind: ChangeKind, row_a: tuple[Any, ...] | None, row_b: tuple[Any, ...] | None) -> None:
            nonlocal listed
            counts[kind.value] += 1
            if listed >= opts.max_row_changes:
                ctx.count_omitted(1, kind, ChangeCategory.DATA)
                return
            listed += 1
            ref = row_a if row_a is not None else row_b
            assert ref is not None
            if kind == ChangeKind.MODIFIED and row_a is not None and row_b is not None:
                changed = [c for i, c in enumerate(values) if not _same(row_a[nk + i], row_b[nk + i])]
                ctx.add_change(
                    kind,
                    ChangeCategory.DATA,
                    path(ref),
                    f"{len(changed)} cell(s) changed in '{t}'",
                    old={c: json_safe(row_a[nk + values.index(c)]) for c in changed},
                    new={c: json_safe(row_b[nk + values.index(c)]) for c in changed},
                    evidence=f"Merge-join on {key} ordered by key; compared values and storage classes",
                    details={"table": t, "columns": changed},
                )
            else:
                row = dict(zip([*key, *values], ref, strict=True))
                ctx.add_change(
                    kind,
                    ChangeCategory.DATA,
                    path(ref),
                    f"Row {'added to' if kind == ChangeKind.ADDED else 'removed from'} '{t}'",
                    old=json_safe(row) if kind == ChangeKind.REMOVED else None,
                    new=json_safe(row) if kind == ChangeKind.ADDED else None,
                    evidence=f"Key {key} present in only one database",
                    details={"table": t},
                )

        while ra is not None or rb is not None:
            steps += 1
            if steps % 10_000 == 0:
                ctx.check()
            if rb is None or (ra is not None and _sort_key(ra[:nk]) < _sort_key(rb[:nk])):
                emit(ChangeKind.REMOVED, ra, None)
                ra = next(it_a, None)
            elif ra is None or _sort_key(rb[:nk]) < _sort_key(ra[:nk]):
                emit(ChangeKind.ADDED, None, rb)
                rb = next(it_b, None)
            else:
                if any(not _same(x, y) for x, y in zip(ra[nk:], rb[nk:], strict=True)):
                    emit(ChangeKind.MODIFIED, ra, rb)
                ra, rb = next(it_a, None), next(it_b, None)
        for k in ("added", "removed", "modified"):
            ctx.metric(f"{t}.rows_{k}", counts[k])

    def _multiset(
        self,
        ctx: ComparisonContext,
        ca: sqlite3.Connection,
        cb: sqlite3.Connection,
        t: str,
        common: list[str],
        opts: SqliteOptions,
        ordered: bool,
    ) -> None:
        """Row-hash multiset diff; ``ordered`` = the table has a rowid (stable listing order)."""
        sql = f"SELECT {', '.join(q(c) for c in common)} FROM {q(t)}" + (" ORDER BY rowid" if ordered else "")

        def h(row: tuple[Any, ...]) -> bytes:
            return hashlib.blake2b(repr(tuple((type(v).__name__, v) for v in row)).encode(), digest_size=12).digest()

        count: Counter[bytes] = Counter()
        for sign, conn in ((1, ca), (-1, cb)):
            for i, row in enumerate(_iter(conn, sql)):
                count[h(row)] += sign
                if i % 20_000 == 0:
                    ctx.check()
        removed = {k: v for k, v in count.items() if v > 0}
        added = {k: -v for k, v in count.items() if v < 0}
        totals = {ChangeKind.REMOVED: sum(removed.values()), ChangeKind.ADDED: sum(added.values())}
        shown = {ChangeKind.REMOVED: 0, ChangeKind.ADDED: 0}
        budget = opts.max_row_changes
        for conn, pending, kind in ((ca, removed, ChangeKind.REMOVED), (cb, added, ChangeKind.ADDED)):
            if not pending:
                continue
            side, other = ("old", "new") if kind == ChangeKind.REMOVED else ("new", "old")
            for i, row in enumerate(_iter(conn, sql)):
                if budget <= 0:
                    break
                hv = h(row)
                if pending.get(hv, 0) <= 0:
                    continue
                pending[hv] -= 1
                budget -= 1
                shown[kind] += 1
                rec = json_safe(dict(zip(common, row, strict=True)))
                ctx.add_change(
                    kind,
                    ChangeCategory.DATA,
                    f"table:{t}/{side}_row[{i}]",
                    f"Row {i} of '{t}' has no identical row in the {other} database",
                    old=rec if side == "old" else None,
                    new=rec if side == "new" else None,
                    evidence="Row-hash multiset comparison (table has no usable primary key)",
                    details={"table": t, "row_number": i},
                )
        for kind in (ChangeKind.REMOVED, ChangeKind.ADDED):
            ctx.count_omitted(totals[kind] - shown[kind], kind, ChangeCategory.DATA)
        if any(totals.values()):
            ctx.warn(
                "no-primary-key",
                f"Table '{t}' has no usable primary key: a modified row is reported as one removed and one added row",
                f"table:{t}",
            )
        ctx.metric(f"{t}.rows_added", totals[ChangeKind.ADDED])
        ctx.metric(f"{t}.rows_removed", totals[ChangeKind.REMOVED])
