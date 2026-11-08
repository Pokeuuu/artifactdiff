"""Run a comparison in a separate process.

Inline comparisons rely on cooperative cancellation (plugins call ``ctx.check()``). A worker
process additionally gives a *hard* timeout, cancellation that works even inside long native
calls, containment of crashes in native libraries, and (on POSIX) an address-space limit.
"""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from artifactdiff.config import ArtifactDiffConfig
from artifactdiff.errors import ArtifactDiffError, ComparisonCancelled, ComparisonTimeout
from artifactdiff.models import ComparisonResult
from artifactdiff.plugin import ProgressCallback


@dataclass
class IsolatedRequest:
    old_path: str
    new_path: str
    plugin: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    old_name: str | None = None
    new_name: str | None = None
    generate_assets: bool = True


def _worker(req: IsolatedRequest, config_json: str, conn: Any) -> None:  # pragma: no cover - subprocess
    try:
        cfg = ArtifactDiffConfig.model_validate_json(config_json)
        if cfg.limits.memory_limit_mb and sys.platform != "win32":
            import resource

            limit = cfg.limits.memory_limit_mb * 1024 * 1024
            # ValueError/OSError: e.g. a hard limit already below the request (documented in docs/security.md)
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        from artifactdiff.engine import Engine

        def progress(fraction: float, message: str) -> None:
            conn.send(("progress", fraction, message))

        result = Engine(config=cfg).compare(
            req.old_path,
            req.new_path,
            plugin=req.plugin,
            options=req.options,
            old_name=req.old_name,
            new_name=req.new_name,
            progress=progress,
            generate_assets=req.generate_assets,
        )
        conn.send(("result", result.model_dump_json()))
    except ArtifactDiffError as exc:
        conn.send(("error", type(exc).__name__, str(exc)))
    except BaseException as exc:
        conn.send(("error", type(exc).__name__, f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


class WorkerError(ArtifactDiffError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def run_isolated(
    req: IsolatedRequest,
    config: ArtifactDiffConfig | None = None,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    grace_seconds: float = 5.0,
) -> ComparisonResult:
    """Compare in a spawned worker. Raises ComparisonTimeout/ComparisonCancelled/WorkerError."""
    cfg = config or ArtifactDiffConfig()
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_worker, args=(req, cfg.model_dump_json(), child), daemon=True)
    proc.start()
    child.close()
    # The worker enforces the soft (cooperative) timeout itself; the hard limit adds a grace period.
    deadline = time.monotonic() + cfg.limits.timeout_seconds + grace_seconds
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise ComparisonCancelled("Comparison cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ComparisonTimeout(f"Comparison exceeded {cfg.limits.timeout_seconds:g}s and was killed")
            if parent.poll(min(0.2, remaining)):
                try:
                    msg = parent.recv()
                except EOFError:
                    proc.join(1)
                    raise WorkerError(
                        "crash", f"Comparison worker exited unexpectedly (exit code {proc.exitcode})"
                    ) from None
                if msg[0] == "progress" and progress is not None:
                    progress(msg[1], msg[2])
                elif msg[0] == "result":
                    return ComparisonResult.model_validate_json(msg[1])
                elif msg[0] == "error":
                    raise WorkerError(msg[1], msg[2])
            elif not proc.is_alive():
                raise WorkerError("crash", f"Comparison worker died (exit code {proc.exitcode})")
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(2)
            if proc.is_alive():
                proc.kill()
        parent.close()
