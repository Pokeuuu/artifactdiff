"""Record-level and statistical comparison of two tables (shared by CSV and Parquet).

Row matching
------------
* With a primary key: rows are joined on the key. Rows only in the old table are *removed*, rows
  only in the new table are *added*, and rows whose non-key values differ are *modified* (with the
  changed cells listed).
* Without a key: rows are compared as a multiset of full-row hashes. A changed row therefore
  shows up as one removal plus one addition — rows are never paired by guesswork.

Only key columns plus a per-row hash are materialised for matching; full rows are fetched only
for the (capped) rows that are listed individually. Queries run on Polars' streaming engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from artifactdiff.errors import PluginInputError
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ComparisonContext
from artifactdiff_plugins._common import json_safe, rel_change

if TYPE_CHECKING:
    import polars as pl

HASH_SEED = 0x5EED
_O, _N, _H, _IDX = "__ad_old", "__ad_new", "__ad_hash", "__ad_row"


def collect(lf: pl.LazyFrame) -> pl.DataFrame:
    import polars as pl

    try:
        return lf.collect(engine="streaming")
    except pl.exceptions.PolarsError as exc:
        raise PluginInputError(f"Could not read table: {exc}".split("\n")[0]) from exc


def row_hash(columns: list[str]) -> pl.Expr:
    import polars as pl

    if not columns:
        return pl.lit(0, dtype=pl.UInt64).alias(_H)
    return pl.struct([pl.col(c) for c in columns]).hash(seed=HASH_SEED).alias(_H)


def fmt_key(key: list[str], values: dict[str, Any]) -> str:
    return ",".join(f"{k}={json_safe(values[k])!r}" if isinstance(values[k], str) else f"{k}={values[k]}" for k in key)


@dataclass
class TableDiffOptions:
    key: list[str] | None
    max_row_changes: int
    label: str = "row"  # path prefix, e.g. "row" or "table:users/row"


def diff_rows(
    ctx: ComparisonContext, old: pl.LazyFrame, new: pl.LazyFrame, compare_cols: list[str], opts: TableDiffOptions
) -> dict[str, int]:
    """Report added/removed/modified rows. Returns counts."""
    import polars as pl

    if opts.key:
        key = opts.key
        old_schema, new_schema = old.collect_schema(), new.collect_schema()
        casts = [pl.col(k).cast(pl.String) for k in key if old_schema[k] != new_schema[k]]
        if casts:
            old, new = old.with_columns(casts), new.with_columns(casts)
            ctx.warn("key-type-cast", "Key column types differ between files; keys were compared as strings")
        dup_old = collect(old.group_by(key).len().filter(pl.col("len") > 1).select(pl.len())).item()
        dup_new = collect(new.group_by(key).len().filter(pl.col("len") > 1).select(pl.len())).item()
        nulls = collect(
            pl.concat([old.select(key), new.select(key)])
            .filter(pl.any_horizontal([pl.col(k).is_null() for k in key]))
            .select(pl.len())
        ).item()
        if dup_old or dup_new:
            ctx.warn(
                "key-not-unique",
                f"Primary key {key} is not unique ({dup_old} duplicated key(s) in old, {dup_new} in new); "
                "rows were matched by full content instead",
            )
        elif nulls:
            ctx.warn("key-has-nulls", f"{nulls} row(s) have a null primary key; matched by full content instead")
        else:
            return _diff_keyed(ctx, old, new, key, compare_cols, opts)
    return _diff_multiset(ctx, old, new, compare_cols, opts)


def _diff_keyed(
    ctx: ComparisonContext,
    old: pl.LazyFrame,
    new: pl.LazyFrame,
    key: list[str],
    compare_cols: list[str],
    opts: TableDiffOptions,
) -> dict[str, int]:
    import polars as pl

    value_cols = [c for c in compare_cols if c not in key]
    a = old.select([*key, row_hash(value_cols)]).with_columns(pl.lit(True).alias(_O))
    b = new.select([*key, row_hash(value_cols)]).with_columns(pl.lit(True).alias(_N))
    joined = a.join(b, on=key, how="full", coalesce=True, suffix="_new", nulls_equal=False)
    status = (
        pl.when(pl.col(_N).is_null())
        .then(pl.lit("removed"))
        .when(pl.col(_O).is_null())
        .then(pl.lit("added"))
        .when(pl.col(_H) != pl.col(_H + "_new"))
        .then(pl.lit("modified"))
        .otherwise(pl.lit("same"))
        .alias("__ad_status")
    )
    classified = joined.select([*key, status]).filter(pl.col("__ad_status") != "same")
    counts = {r["__ad_status"]: r["len"] for r in collect(classified.group_by("__ad_status").len()).to_dicts()}
    ctx.check()
    listed = collect(classified.sort(key, nulls_last=True).head(opts.max_row_changes))
    ctx.check()

    def rows(lf: pl.LazyFrame, keys: pl.DataFrame) -> dict[tuple[Any, ...], dict[str, Any]]:
        if keys.height == 0:
            return {}
        df = collect(lf.join(keys.lazy(), on=key, how="semi"))
        return {tuple(r[k] for k in key): r for r in df.to_dicts()}

    keys_df = listed.select(key)
    old_rows = rows(old.select([*key, *[c for c in old.collect_schema() if c not in key]]), keys_df)
    new_rows = rows(new.select([*key, *[c for c in new.collect_schema() if c not in key]]), keys_df)
    for rec in listed.to_dicts():
        st = rec["__ad_status"]
        k = tuple(rec[c] for c in key)
        path = f"{opts.label}[{fmt_key(key, rec)}]"
        if st == "removed":
            ctx.add_change(
                ChangeKind.REMOVED,
                ChangeCategory.DATA,
                path,
                "Row removed",
                old=json_safe(old_rows.get(k)),
                severity=ChangeSeverity.LOW,
                evidence=f"Key {key} present only in the old table",
                details={"key": json_safe(dict(zip(key, k, strict=True)))},
            )
        elif st == "added":
            ctx.add_change(
                ChangeKind.ADDED,
                ChangeCategory.DATA,
                path,
                "Row added",
                new=json_safe(new_rows.get(k)),
                severity=ChangeSeverity.LOW,
                evidence=f"Key {key} present only in the new table",
                details={"key": json_safe(dict(zip(key, k, strict=True)))},
            )
        else:
            o, n = old_rows.get(k, {}), new_rows.get(k, {})
            changed = [c for c in value_cols if o.get(c) != n.get(c)]
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.DATA,
                path,
                f"{len(changed)} cell(s) changed: {', '.join(changed[:6])}{'…' if len(changed) > 6 else ''}",
                old=json_safe({c: o.get(c) for c in changed}),
                new=json_safe({c: n.get(c) for c in changed}),
                severity=ChangeSeverity.LOW,
                evidence=f"Rows joined on key {key}; row hashes over {len(value_cols)} common column(s) differ",
                details={"key": json_safe(dict(zip(key, k, strict=True))), "columns": changed},
            )
    _count_rest(ctx, counts, listed)
    for name in ("added", "removed", "modified"):
        counts.setdefault(name, 0)
    ctx.metric("rows_added", counts["added"])
    ctx.metric("rows_removed", counts["removed"])
    ctx.metric("rows_modified", counts["modified"])
    ctx.metric("row_matching", f"primary key {key}")
    return counts


def _count_rest(ctx: ComparisonContext, counts: dict[str, int], listed: pl.DataFrame) -> None:
    shown = {r["__ad_status"]: r["len"] for r in listed.group_by("__ad_status").len().to_dicts()}
    kinds = {"added": ChangeKind.ADDED, "removed": ChangeKind.REMOVED, "modified": ChangeKind.MODIFIED}
    for st, kind in kinds.items():
        ctx.count_omitted(counts.get(st, 0) - shown.get(st, 0), kind, ChangeCategory.DATA)


def _diff_multiset(
    ctx: ComparisonContext, old: pl.LazyFrame, new: pl.LazyFrame, compare_cols: list[str], opts: TableDiffOptions
) -> dict[str, int]:
    import polars as pl

    ha = old.select(row_hash(compare_cols)).group_by(_H).len().rename({"len": "n_old"})
    hb = new.select(row_hash(compare_cols)).group_by(_H).len().rename({"len": "n_new"})
    # len() is UInt32: cast before subtracting, or removed rows underflow into huge positive counts.
    j = ha.join(hb, on=_H, how="full", coalesce=True).with_columns(
        pl.col("n_old").fill_null(0).cast(pl.Int64), pl.col("n_new").fill_null(0).cast(pl.Int64)
    )
    delta = collect(
        j.filter(pl.col("n_old") != pl.col("n_new")).select(_H, (pl.col("n_new") - pl.col("n_old")).alias("d"))
    )
    ctx.check()
    added_h = delta.filter(pl.col("d") > 0)
    removed_h = delta.filter(pl.col("d") < 0)
    n_added = int(added_h["d"].sum() or 0)
    n_removed = int(-(removed_h["d"].sum() or 0))

    def sample(lf: pl.LazyFrame, hashes: pl.DataFrame, limit: int) -> list[dict[str, Any]]:
        if hashes.height == 0 or limit <= 0:
            return []
        df = collect(
            lf.with_row_index(_IDX)
            .with_columns(row_hash(compare_cols))
            .join(hashes.select(_H).lazy(), on=_H, how="semi")
            .sort(_IDX)
            .head(limit)
        )
        return df.drop(_H).to_dicts()

    limit = opts.max_row_changes
    # Each excess occurrence of a hash is one changed row; we list the earliest rows carrying it.
    removed_rows = sample(old, removed_h, limit)
    added_rows = sample(new, added_h, max(0, limit - len(removed_rows)))
    for r in removed_rows:
        i = r.pop(_IDX)
        ctx.add_change(
            ChangeKind.REMOVED,
            ChangeCategory.DATA,
            f"old_{opts.label}[{i}]",
            f"Row {i} of the old table has no identical row in the new table",
            old=json_safe(r),
            evidence="Full-row hash multiset comparison (no primary key)",
        )
    for r in added_rows:
        i = r.pop(_IDX)
        ctx.add_change(
            ChangeKind.ADDED,
            ChangeCategory.DATA,
            f"new_{opts.label}[{i}]",
            f"Row {i} of the new table has no identical row in the old table",
            new=json_safe(r),
            evidence="Full-row hash multiset comparison (no primary key)",
        )
    ctx.count_omitted(n_removed - len(removed_rows), ChangeKind.REMOVED, ChangeCategory.DATA)
    ctx.count_omitted(n_added - len(added_rows), ChangeKind.ADDED, ChangeCategory.DATA)
    if n_added or n_removed:
        ctx.warn(
            "no-primary-key",
            "No primary key: a modified row is reported as one removed and one added row. "
            "Set the primary_key option to get cell-level changes",
        )
    ctx.metric("rows_added", n_added)
    ctx.metric("rows_removed", n_removed)
    ctx.metric("row_matching", "full-row content (no key)")
    return {"added": n_added, "removed": n_removed, "modified": 0}


# ------------------------------------------------------------------------- statistics
def column_stats(lf: pl.LazyFrame, columns: list[str], numeric: set[str]) -> dict[str, dict[str, Any]]:
    import polars as pl

    if not columns:
        return {}
    exprs: list[pl.Expr] = [pl.len().alias("__rows")]
    for i, c in enumerate(columns):
        col = pl.col(c)
        exprs += [col.null_count().alias(f"{i}:nulls"), col.n_unique().alias(f"{i}:distinct")]
        if c in numeric:
            x = col.cast(pl.Float64, strict=False)
            exprs += [
                x.mean().alias(f"{i}:mean"),
                x.std().alias(f"{i}:std"),
                x.min().alias(f"{i}:min"),
                x.max().alias(f"{i}:max"),
            ]
    row = collect(lf.select(exprs)).to_dicts()[0]
    out: dict[str, dict[str, Any]] = {}
    for i, c in enumerate(columns):
        out[c] = {k.split(":", 1)[1]: v for k, v in row.items() if k.startswith(f"{i}:")}
        out[c]["rows"] = row["__rows"]
    return out


def diff_stats(
    ctx: ComparisonContext,
    a: dict[str, dict[str, Any]],
    b: dict[str, dict[str, Any]],
    threshold: float,
    prefix: str = "column",
) -> None:
    for col in a:
        if col not in b:
            continue
        sa, sb = a[col], b[col]
        path = f"{prefix}:{col}"
        if sa["nulls"] != sb["nulls"]:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.STATISTICS,
                f"{path}/missing",
                f"Missing values changed from {sa['nulls']:,} to {sb['nulls']:,}",
                old=sa["nulls"],
                new=sb["nulls"],
                severity=ChangeSeverity.INFO,
                evidence="Null counts over all rows",
            )
        for stat in ("mean", "std"):
            va, vb = sa.get(stat), sb.get(stat)
            if va is None or vb is None or (isinstance(va, float) and math.isnan(va)):
                continue
            rc = rel_change(va, vb)
            if rc is not None and abs(rc) > threshold:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.STATISTICS,
                    f"{path}/{stat}",
                    f"{stat} changed by {rc:+.1%}" if math.isfinite(rc) else f"{stat} changed from 0",
                    old=va,
                    new=vb,
                    severity=ChangeSeverity.INFO,
                    evidence=f"Descriptive statistic over all non-null values; reported when the "
                    f"relative change exceeds {threshold:.0%}",
                    details={"relative_change": rc if math.isfinite(rc) else None},
                )
        for stat in ("min", "max"):
            if stat in sa and sa.get(stat) != sb.get(stat):
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.STATISTICS,
                    f"{path}/{stat}",
                    f"{stat} changed",
                    old=sa.get(stat),
                    new=sb.get(stat),
                    severity=ChangeSeverity.INFO,
                    evidence="Exact minimum/maximum over all non-null values",
                )
        rc = rel_change(sa["distinct"], sb["distinct"])
        if rc is not None and abs(rc) > threshold:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.STATISTICS,
                f"{path}/distinct",
                f"Distinct values changed from {sa['distinct']:,} to {sb['distinct']:,}",
                old=sa["distinct"],
                new=sb["distinct"],
                severity=ChangeSeverity.INFO,
                evidence=f"Exact distinct count; reported when the change exceeds {threshold:.0%}",
            )


def duplicate_rows(lf: pl.LazyFrame, columns: list[str]) -> int:
    import polars as pl

    if not columns:
        return 0
    df = collect(lf.select(row_hash(columns)).select(pl.len().alias("n"), pl.col(_H).n_unique().alias("u")))
    return int(df["n"][0] - df["u"][0])


AUTO_KEY_NAMES = ("id", "uuid", "key", "pk")


def auto_key(a: pl.LazyFrame, b: pl.LazyFrame, cols: list[str]) -> list[str] | None:
    """First column named id/uuid/key/pk (case-insensitive) that is unique and non-null in both tables."""
    import polars as pl

    for c in cols:
        if c.lower() not in AUTO_KEY_NAMES:
            continue
        ok = True
        for lf in (a, b):
            df = collect(
                lf.select(pl.len().alias("n"), pl.col(c).n_unique().alias("u"), pl.col(c).null_count().alias("z"))
            )
            ok = ok and df["n"][0] == df["u"][0] and df["z"][0] == 0
        if ok:
            return [c]
    return None
