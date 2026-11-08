"""CSV/TSV dataset comparison: columns, inferred types, rows, duplicates, missing values, statistics.

Cell values are compared as the exact text in the file (so ``1`` vs ``1.0`` is a change); column
types are inferred separately from the first ``infer_rows`` rows and reported as schema changes.
"""

from __future__ import annotations

import csv as stdcsv
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from artifactdiff.errors import PluginInputError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, PluginOptions
from artifactdiff_plugins import _tabular as tab

if TYPE_CHECKING:
    import polars as pl


class CsvOptions(PluginOptions):
    primary_key: list[str] | None = Field(
        None, description="Column(s) identifying a row. Without one, rows are matched by full content."
    )
    auto_key: bool = Field(
        True,
        description="If no primary_key is set, use a column named id/uuid/key/pk when it is unique "
        "and non-null in both files (a warning says so).",
    )
    delimiter: str | None = Field(None, min_length=1, max_length=1, description="Default: sniffed.")
    has_header: bool = Field(True, description="First line contains column names.")
    encoding: Literal["utf8", "utf8-lossy"] = Field(
        "utf8", description="utf8-lossy replaces invalid bytes instead of failing."
    )
    null_values: list[str] = Field(default_factory=list, description="Extra strings treated as missing.")
    ignore_columns: list[str] = Field(default_factory=list, description="Columns excluded from all comparisons.")
    max_row_changes: int = Field(500, ge=0, description="Rows listed individually (all are counted).")
    compare_statistics: bool = Field(True, description="Compare per-column descriptive statistics.")
    stats_threshold: float = Field(0.05, ge=0, description="Relative change that makes a statistic reportable.")
    infer_rows: int = Field(10_000, ge=1, description="Rows used for type inference.")


class CsvPlugin(ArtifactPlugin):
    name = "csv"
    version = "1.0.0"
    display_name = "CSV / TSV"
    description = "Row- and column-level dataset comparison with optional primary keys."
    format_id = "csv"
    format_name = "CSV"
    supported_extensions = (".csv", ".tsv", ".tab", ".psv")
    mime_types = ("text/csv", "text/tab-separated-values")
    capabilities = ("schema-diff", "row-diff", "primary-key", "statistics", "duplicates", "streaming")
    limitations = (
        "Column types are inferred from a sample of rows; they are not declared by the CSV format.",
        "Without a primary key, a modified row is reported as one removal plus one addition.",
    )
    requires = ("polars",)
    install_hint = 'pip install "artifactdiff[tabular]"'
    Options = CsvOptions

    # ----------------------------------------------------------------------- reading
    def _delimiter(self, file: ArtifactFile, opts: CsvOptions) -> str:
        if opts.delimiter:
            return opts.delimiter
        if file.extension in (".tsv", ".tab"):
            return "\t"
        if file.extension == ".psv":
            return "|"
        sample = file.head(64 * 1024).decode("utf-8", errors="replace")
        try:
            return stdcsv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except stdcsv.Error:
            return ","

    def _scan(self, file: ArtifactFile, opts: CsvOptions, *, infer: bool) -> pl.LazyFrame:
        import polars as pl

        head = file.head(64 * 1024)
        if b"\x00" in head:
            raise PluginInputError(f"{file.display_name} contains NUL bytes; it is binary data, not CSV")
        if opts.encoding == "utf8":
            try:
                head.decode("utf-8")
            except UnicodeDecodeError as exc:
                if exc.start < len(head) - 3:  # not just a character cut by the sample boundary
                    raise PluginInputError(
                        f"{file.display_name} is not UTF-8 text (use encoding='utf8-lossy' to read it anyway)"
                    ) from exc

        kwargs: dict[str, Any] = {
            "separator": self._delimiter(file, opts),
            "has_header": opts.has_header,
            "encoding": opts.encoding,
            "raise_if_empty": False,
        }
        if opts.null_values:
            kwargs["null_values"] = opts.null_values
        if infer:
            kwargs["infer_schema_length"] = opts.infer_rows
        else:
            kwargs["infer_schema"] = False
        try:
            lf = pl.scan_csv(file.path, **kwargs)
            lf.collect_schema()
        except pl.exceptions.PolarsError as exc:
            raise PluginInputError(
                f"{file.display_name} is not a readable CSV file: {str(exc).splitlines()[0]}"
            ) from exc
        return lf

    def _types(self, file: ArtifactFile, opts: CsvOptions) -> dict[str, str]:
        import polars as pl

        try:
            schema = self._scan(file, opts, infer=True).collect_schema()
        except PluginInputError:
            return {}
        except pl.exceptions.PolarsError:
            return {}
        return {k: str(v) for k, v in schema.items()}

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        import polars as pl

        opts = CsvOptions()
        lf = self._scan(file, opts, infer=False)
        cols = lf.collect_schema().names()
        rows = tab.collect(lf.select(pl.len())).item() if cols else 0
        return {
            "delimiter": self._delimiter(file, opts),
            "columns": cols,
            "rows": rows,
            "inferred_types": self._types(file, opts),
        }

    # ----------------------------------------------------------------------- compare
    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, CsvOptions)
        a = self._scan(old, options, infer=False)
        b = self._scan(new, options, infer=False)
        cols_a = [c for c in a.collect_schema().names() if c not in options.ignore_columns]
        cols_b = [c for c in b.collect_schema().names() if c not in options.ignore_columns]
        for side, cols in (("old", cols_a), ("new", cols_b)):
            if len(set(cols)) != len(cols):
                raise PluginInputError(f"The {side} CSV has duplicate column names")
        da, db = self._delimiter(old, options), self._delimiter(new, options)
        if da != db:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.METADATA,
                "delimiter",
                "Delimiter changed",
                old=da,
                new=db,
                severity=ChangeSeverity.INFO,
                evidence="csv.Sniffer on the first 64 KiB",
            )
        common = [c for c in cols_a if c in cols_b]

        # --- schema
        for c in cols_a:
            if c not in cols_b:
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.SCHEMA,
                    f"column:{c}",
                    f"Column '{c}' removed",
                    severity=ChangeSeverity.MEDIUM,
                    evidence="Header comparison",
                )
        for c in cols_b:
            if c not in cols_a:
                ctx.add_change(
                    ChangeKind.ADDED,
                    ChangeCategory.SCHEMA,
                    f"column:{c}",
                    f"Column '{c}' added",
                    severity=ChangeSeverity.LOW,
                    evidence="Header comparison",
                )
        if common and [c for c in cols_a if c in common] != [c for c in cols_b if c in common]:
            ctx.add_change(
                ChangeKind.MOVED,
                ChangeCategory.SCHEMA,
                "columns",
                "Column order changed",
                old=[c for c in cols_a if c in common],
                new=[c for c in cols_b if c in common],
                severity=ChangeSeverity.INFO,
                evidence="Header comparison",
            )
        ta, tb = self._types(old, options), self._types(new, options)
        for c in common:
            if c in ta and c in tb and ta[c] != tb[c]:
                ctx.add_change(
                    ChangeKind.TYPE_CHANGED,
                    ChangeCategory.SCHEMA,
                    f"column:{c}",
                    f"Inferred type of '{c}' changed from {ta[c]} to {tb[c]}",
                    old=ta[c],
                    new=tb[c],
                    severity=ChangeSeverity.MEDIUM,
                    evidence=f"Polars type inference over the first {options.infer_rows:,} rows",
                )
        ctx.check()

        # --- rows
        import polars as pl

        rows_a = tab.collect(a.select(pl.len())).item() if cols_a else 0
        rows_b = tab.collect(b.select(pl.len())).item() if cols_b else 0
        ctx.metric("old_rows", rows_a)
        ctx.metric("new_rows", rows_b)
        ctx.metric("common_columns", len(common))

        key = options.primary_key
        if key:
            missing = [k for k in key if k not in common]
            if missing:
                raise PluginInputError(f"Primary key column(s) {missing} are not present in both files")
        elif options.auto_key and common:
            key = tab.auto_key(a, b, common)
            if key:
                ctx.warn(
                    "pk-auto-selected",
                    f"Using column '{key[0]}' as primary key (unique and non-null in "
                    "both files). Set primary_key explicitly to override, or auto_key=false to disable",
                )
        if common:
            tab.diff_rows(
                ctx,
                a.select(common),
                b.select(common),
                common,
                tab.TableDiffOptions(key=key, max_row_changes=options.max_row_changes),
            )
        ctx.check()

        # --- duplicates and statistics
        if common:
            dup_a, dup_b = tab.duplicate_rows(a, common), tab.duplicate_rows(b, common)
            ctx.metric("old_duplicate_rows", dup_a)
            ctx.metric("new_duplicate_rows", dup_b)
            if dup_a != dup_b:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.STATISTICS,
                    "duplicates",
                    f"Duplicate rows changed from {dup_a:,} to {dup_b:,}",
                    old=dup_a,
                    new=dup_b,
                    severity=ChangeSeverity.INFO,
                    evidence="Rows minus distinct full-row hashes",
                )
        if options.compare_statistics and common:
            numeric = {
                c
                for c in common
                if ta.get(c, "").startswith(("Int", "UInt", "Float", "Decimal"))
                and tb.get(c, "").startswith(("Int", "UInt", "Float", "Decimal"))
            }
            tab.diff_stats(
                ctx, tab.column_stats(a, common, numeric), tab.column_stats(b, common, numeric), options.stats_threshold
            )
