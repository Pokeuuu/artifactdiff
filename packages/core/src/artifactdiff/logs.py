"""Structured logging. Log records carry metadata (plugin, sizes, timings) — never file contents."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(f"{k}={v}" for k, v in vars(record).items() if k not in _RESERVED and not k.startswith("_"))
        base = f"{record.levelname.lower():7} {record.name}: {record.getMessage()}"
        text = f"{base} {extras}".rstrip()
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


def configure_logging(level: str | int = "WARNING", fmt: str = "text") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else KeyValueFormatter())
    root = logging.getLogger("artifactdiff")
    root.handlers[:] = [handler]
    root.setLevel(level if isinstance(level, int) else level.upper())
    root.propagate = False
    for name in ("artifactdiff_plugins", "artifactdiff_api", "artifactdiff_cli"):
        lg = logging.getLogger(name)
        lg.handlers[:] = [handler]
        lg.setLevel(root.level)
        lg.propagate = False
