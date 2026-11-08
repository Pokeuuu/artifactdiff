"""Parquet comparison: Arrow schema (types, nullability), file metadata, rows and statistics.

Schema and metadata come from the Parquet footer via PyArrow (no data is read). Record and
statistics comparisons use Polars' lazy streaming engine, which processes row groups in chunks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

from artifactdiff.errors import PluginInputError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, PluginOptions
from artifactdiff_plugins import _tabular as tab

if TYPE_CHECKING:
    import polars as pl


class ParquetOptions(PluginOptions):
    primary_key: list[str] | None = Field(None, description="Column(s) identifying a record.")
    auto_key: bool = Field(True, description="Use a unique, non-null id/uuid/key/pk column if present.")
    ignore_columns: list[str] = Field(default_factory=list, description="Columns excluded from all comparisons.")
    compare_records: bool = Field(True, description="Disable to compare schema and statistics only.")
    max_row_changes: int = Field(500, ge=0, description="Rows listed individually (all are counted).")
    compare_statistics: bool = Field(True, description="Compare per-column descriptive statistics.")
    stats_threshold: float = Field(0.05, ge=0, description="Relative change that makes a statistic reportable.")


def _footer(file: ArtifactFile) -> Any:
    import pyarrow.parquet as pq

    try:
        return pq.ParquetFile(file.path)
    except Exception as exc:  # pyarrow raises ArrowInvalid/OSError variants for corrupt files
        raise PluginInputError(f"{file.display_name} is not a readable Parquet file: {exc}") from exc


def _fields(pf: Any) -> dict[str, tuple[str, bool]]:
    schema = pf.schema_arrow
    return {f.name: (str(f.type), bool(f.nullable)) for f in schema}


def _compression(pf: Any) -> str | None:
    md = pf.metadata
    if md.num_row_groups == 0 or md.num_columns == 0:
        return None
    return str(md.row_group(0).column(0).compression)


class ParquetPlugin(ArtifactPlugin):
    name = "parquet"
    version = "1.0.0"
    display_name = "Apache Parquet"
    description = "Schema, nullability, metadata, record and statistics comparison for Parquet files."
    format_id = "parquet"
    format_name = "Apache Parquet"
    supported_extensions = (".parquet", ".pq")
    mime_types = ("application/vnd.apache.parquet",)
    signatures = (b"PAR1",)
    capabilities = ("schema-diff", "nullability", "row-diff", "primary-key", "statistics", "chunked")
    limitations = (
        "Columns whose type changed are excluded from record matching (values are not comparable).",
        "Nested (list/struct) columns are compared by whole-value hash, not element by element.",
    )
    requires = ("pyarrow", "polars")
    install_hint = 'pip install "artifactdiff[tabular]"'
    Options = ParquetOptions

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        pf = _footer(file)
        md = pf.metadata
        kv = pf.schema_arrow.metadata or {}
        return {
            "rows": md.num_rows,
            "row_groups": md.num_row_groups,
            "columns": {k: {"type": t, "nullable": n} for k, (t, n) in _fields(pf).items()},
            "created_by": md.created_by,
            "format_version": str(md.format_version),
            "compression": _compression(pf),
            "metadata_keys": sorted(k.decode("utf-8", "replace") for k in kv),
        }

    def _scan(self, file: ArtifactFile) -> pl.LazyFrame:
        import polars as pl

        try:
            lf = pl.scan_parquet(file.path)
            lf.collect_schema()
            return lf
        except pl.exceptions.PolarsError as exc:
            raise PluginInputError(f"{file.display_name}: {str(exc).splitlines()[0]}") from exc

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, ParquetOptions)
        pa_, pb_ = _footer(old), _footer(new)
        fa = {k: v for k, v in _fields(pa_).items() if k not in options.ignore_columns}
        fb = {k: v for k, v in _fields(pb_).items() if k not in options.ignore_columns}

        for c, (t, _) in fa.items():
            if c not in fb:
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.SCHEMA,
                    f"column:{c}",
                    f"Column '{c}' removed",
                    old=t,
                    severity=ChangeSeverity.HIGH,
                    evidence="Arrow schema in the Parquet footer",
                )
        for c, (t, _) in fb.items():
            if c not in fa:
                ctx.add_change(
                    ChangeKind.ADDED,
                    ChangeCategory.SCHEMA,
                    f"column:{c}",
                    f"Column '{c}' added",
                    new=t,
                    severity=ChangeSeverity.LOW,
                    evidence="Arrow schema in the Parquet footer",
                )
        comparable: list[str] = []
        for c, (t, n) in fa.items():
            if c not in fb:
                continue
            t2, n2 = fb[c]
            if t != t2:
                ctx.add_change(
                    ChangeKind.TYPE_CHANGED,
                    ChangeCategory.SCHEMA,
                    f"column:{c}",
                    f"Type of '{c}' changed from {t} to {t2}",
                    old=t,
                    new=t2,
                    severity=ChangeSeverity.HIGH,
                    evidence="Arrow schema in the Parquet footer",
                )
            else:
                comparable.append(c)
            if n != n2:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.SCHEMA,
                    f"column:{c}/nullable",
                    f"Column '{c}' became {'nullable' if n2 else 'non-nullable'}",
                    old=n,
                    new=n2,
                    severity=ChangeSeverity.MEDIUM,
                    evidence="Field nullability in the Arrow schema",
                )
        common_order_a = [c for c in fa if c in fb]
        common_order_b = [c for c in fb if c in fa]
        if common_order_a != common_order_b:
            ctx.add_change(
                ChangeKind.MOVED,
                ChangeCategory.SCHEMA,
                "columns",
                "Column order changed",
                old=common_order_a,
                new=common_order_b,
                severity=ChangeSeverity.INFO,
                evidence="Arrow schema field order",
            )

        ma, mb = pa_.metadata, pb_.metadata
        if ma.num_rows != mb.num_rows:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.STRUCTURE,
                "rows",
                f"Row count changed by {mb.num_rows - ma.num_rows:+,}",
                old=ma.num_rows,
                new=mb.num_rows,
                severity=ChangeSeverity.LOW,
                evidence="num_rows in the Parquet footer",
            )
        for label, va, vb in (
            ("compression", _compression(pa_), _compression(pb_)),
            ("row_groups", ma.num_row_groups, mb.num_row_groups),
            ("created_by", ma.created_by, mb.created_by),
        ):
            if va != vb:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.METADATA,
                    f"file:{label}",
                    f"{label} changed",
                    old=va,
                    new=vb,
                    severity=ChangeSeverity.INFO,
                    evidence="Parquet footer metadata",
                )
        ctx.metric("old_rows", ma.num_rows)
        ctx.metric("new_rows", mb.num_rows)
        ctx.check()

        type_changed = [c for c in common_order_a if c not in comparable]
        if type_changed:
            ctx.warn(
                "type-changed-columns-skipped",
                f"Columns with changed types were excluded from record comparison: {', '.join(type_changed)}",
            )
        a, b = self._scan(old), self._scan(new)
        if options.compare_records and comparable:
            key = options.primary_key
            if key:
                missing = [k for k in key if k not in comparable]
                if missing:
                    raise PluginInputError(f"Primary key column(s) {missing} are not comparable in both files")
            elif options.auto_key:
                key = tab.auto_key(a, b, comparable)
                if key:
                    ctx.warn(
                        "pk-auto-selected",
                        f"Using column '{key[0]}' as primary key (unique and non-null "
                        "in both files). Set primary_key to override, or auto_key=false to disable",
                    )
            tab.diff_rows(
                ctx,
                a.select(comparable),
                b.select(comparable),
                comparable,
                tab.TableDiffOptions(key=key, max_row_changes=options.max_row_changes),
            )
        if options.compare_statistics and comparable:
            sa, sb = a.collect_schema(), b.collect_schema()
            numeric = {c for c in comparable if sa[c].is_numeric() and sb[c].is_numeric()}
            tab.diff_stats(
                ctx,
                tab.column_stats(a, comparable, numeric),
                tab.column_stats(b, comparable, numeric),
                options.stats_threshold,
            )
