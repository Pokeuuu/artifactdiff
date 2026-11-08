"""Section-aware INI comparison, written only against the public SDK (artifactdiff_sdk)."""

from __future__ import annotations

import configparser
from typing import Any

from pydantic import Field

from artifactdiff_sdk import (
    ArtifactFile,
    ArtifactPlugin,
    ChangeCategory,
    ChangeKind,
    ChangeSeverity,
    ComparisonContext,
    PluginInputError,
    PluginOptions,
)


class IniOptions(PluginOptions):
    case_sensitive_keys: bool = Field(False, description="configparser lower-cases keys by default.")
    ignore_sections: list[str] = Field(default_factory=list, description="Sections not compared.")
    max_size_mb: float = Field(16, gt=0)


class IniPlugin(ArtifactPlugin):
    name = "ini"
    version = "1.0.0"
    display_name = "INI configuration"
    description = "Compares INI/CFG files section by section."
    format_id = "ini"
    format_name = "INI"
    supported_extensions = (".ini", ".cfg")
    capabilities = ("sections", "keys")
    limitations = ("Comments and key order are not compared; interpolation is disabled.",)
    priority = 10  # prefer over the generic text fallback
    Options = IniOptions

    def _parse(self, file: ArtifactFile, opts: IniOptions) -> configparser.ConfigParser:
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        if opts.case_sensitive_keys:
            parser.optionxform = str  # type: ignore[assignment,method-assign]
        raw = file.read_bytes(int(opts.max_size_mb * 1024 * 1024))
        try:
            parser.read_string(raw.decode("utf-8-sig"), source=file.display_name)
        except (UnicodeDecodeError, configparser.Error) as exc:
            raise PluginInputError(f"{file.display_name} is not a valid INI file: {exc}") from exc
        return parser

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        p = self._parse(file, IniOptions())
        return {"sections": p.sections(), "keys": sum(len(p[s]) for s in p.sections())}

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, IniOptions)
        a, b = self._parse(old, options), self._parse(new, options)
        sa = [s for s in a.sections() if s not in options.ignore_sections]
        sb = [s for s in b.sections() if s not in options.ignore_sections]
        for s in sa:
            if s not in sb:
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.STRUCTURE,
                    f"[{s}]",
                    f"Section [{s}] removed",
                    old=dict(a[s]),
                    severity=ChangeSeverity.MEDIUM,
                    evidence="Section only in old file",
                )
        for s in sb:
            if s not in sa:
                ctx.add_change(
                    ChangeKind.ADDED,
                    ChangeCategory.STRUCTURE,
                    f"[{s}]",
                    f"Section [{s}] added",
                    new=dict(b[s]),
                    evidence="Section only in new file",
                )
        for s in (x for x in sa if x in sb):
            ctx.check()
            ka, kb = dict(a.items(s, raw=True)), dict(b.items(s, raw=True))
            for k in sorted(set(ka) | set(kb)):
                path = f"[{s}].{k}"
                if k not in kb:
                    ctx.add_change(
                        ChangeKind.REMOVED,
                        ChangeCategory.DATA,
                        path,
                        f"Key '{k}' removed",
                        old=ka[k],
                        severity=ChangeSeverity.MEDIUM,
                        evidence="Key only in old section",
                    )
                elif k not in ka:
                    ctx.add_change(
                        ChangeKind.ADDED,
                        ChangeCategory.DATA,
                        path,
                        f"Key '{k}' added",
                        new=kb[k],
                        evidence="Key only in new section",
                    )
                elif ka[k] != kb[k]:
                    ctx.add_change(
                        ChangeKind.MODIFIED,
                        ChangeCategory.DATA,
                        path,
                        f"Value of '{k}' changed",
                        old=ka[k],
                        new=kb[k],
                        evidence="Compared raw string values",
                    )
