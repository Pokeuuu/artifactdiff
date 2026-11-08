"""Background comparison jobs with progress, cancellation and retention sweeping."""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select

from artifactdiff.config import ArtifactDiffConfig, Limits
from artifactdiff.engine import Engine
from artifactdiff.errors import ArtifactDiffError, ComparisonCancelled, ComparisonTimeout
from artifactdiff.isolation import IsolatedRequest, run_isolated
from artifactdiff.models import ComparisonResult
from artifactdiff_api.db import Comparison, Database, utcnow
from artifactdiff_api.settings import Settings
from artifactdiff_api.storage import Storage

log = logging.getLogger("artifactdiff_api.jobs")


@dataclass
class JobState:
    progress: float = 0.0
    message: str = "queued"
    cancel: threading.Event = field(default_factory=threading.Event)
    future: Future[None] | None = None


class JobManager:
    def __init__(self, settings: Settings, db: Database, storage: Storage) -> None:
        self.settings = settings
        self.db = db
        self.storage = storage
        self.pool = ThreadPoolExecutor(max_workers=settings.max_concurrent_jobs, thread_name_prefix="compare")
        self.state: dict[str, JobState] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sweeper: threading.Thread | None = None
        self.config = ArtifactDiffConfig(
            limits=Limits(
                max_file_size_mb=settings.max_upload_mb,
                timeout_seconds=settings.timeout_seconds,
                max_changes=settings.max_changes,
                memory_limit_mb=settings.memory_limit_mb,
            )
        )

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._recover()
        self._sweeper = threading.Thread(target=self._sweep_loop, name="retention", daemon=True)
        self._sweeper.start()

    def shutdown(self) -> None:
        self._stop.set()
        for st in self.state.values():
            st.cancel.set()
        self.pool.shutdown(wait=True, cancel_futures=True)

    def _recover(self) -> None:
        """Jobs interrupted by a restart cannot resume; mark them failed instead of leaving them queued."""
        with self.db.session() as s:
            for c in s.scalars(select(Comparison).where(Comparison.job_status.in_(("queued", "running")))):
                c.job_status = "failed"
                c.error = "Interrupted by a server restart"
                c.finished_at = utcnow()

    # ---------------------------------------------------------------------- jobs
    def submit(
        self,
        cid: str,
        old_path: str,
        new_path: str,
        old_name: str,
        new_name: str,
        plugin: str | None,
        options: dict[str, Any],
    ) -> None:
        st = JobState()
        with self._lock:
            self.state[cid] = st
        st.future = self.pool.submit(self._run, cid, old_path, new_path, old_name, new_name, plugin, options, st)

    def progress(self, cid: str) -> tuple[float, str] | None:
        st = self.state.get(cid)
        return (st.progress, st.message) if st else None

    def cancel(self, cid: str) -> bool:
        st = self.state.get(cid)
        if st is None:
            return False
        st.cancel.set()
        return True

    def _run(
        self,
        cid: str,
        old_path: str,
        new_path: str,
        old_name: str,
        new_name: str,
        plugin: str | None,
        options: dict[str, Any],
        st: JobState,
    ) -> None:
        if st.cancel.is_set():
            self._finish(cid, "cancelled", error="Cancelled before it started")
            return
        with self.db.session() as s:
            c = s.get(Comparison, cid)
            if c is None:
                return
            c.job_status = "running"

        def on_progress(fraction: float, message: str) -> None:
            st.progress, st.message = fraction, message

        started = time.monotonic()
        try:
            if self.settings.isolation == "process":
                result = run_isolated(
                    IsolatedRequest(old_path, new_path, plugin, options, old_name, new_name),
                    self.config,
                    cancel_event=st.cancel,
                    progress=on_progress,
                )
            else:
                result = Engine(config=self.config).compare(
                    old_path,
                    new_path,
                    plugin=plugin,
                    options=options,
                    old_name=old_name,
                    new_name=new_name,
                    cancel_event=st.cancel,
                    progress=on_progress,
                )
        except ComparisonCancelled:
            self._finish(cid, "cancelled", error="Cancelled")
            return
        except ComparisonTimeout as exc:
            self._finish(cid, "failed", error=str(exc))
            return
        except ArtifactDiffError as exc:
            self._finish(cid, "failed", error=str(exc))
            return
        except Exception as exc:  # unexpected server-side failure: record it, keep the worker alive
            log.exception("comparison job crashed", extra={"comparison": cid})
            self._finish(cid, "failed", error=f"Internal error: {type(exc).__name__}")
            return
        if result.error == "Comparison cancelled":
            self._finish(cid, "cancelled", error="Cancelled")
            return
        assets = self.storage.extract_assets(cid, result)
        self._finish(cid, "completed", result=result, assets_bytes=assets, duration=(time.monotonic() - started) * 1000)

    def _finish(
        self,
        cid: str,
        status: str,
        *,
        error: str | None = None,
        result: ComparisonResult | None = None,
        assets_bytes: int = 0,
        duration: float | None = None,
    ) -> None:
        with self.db.session() as s:
            c = s.get(Comparison, cid)
            if c is None:
                return
            c.job_status = status
            c.finished_at = utcnow()
            c.error = error
            c.duration_ms = duration
            if result is not None:
                c.result_status = result.status.value
                c.plugin = result.plugin.name if result.plugin else None
                c.plugin_version = result.plugin.version if result.plugin else None
                c.total_changes = result.statistics.total_changes
                c.max_severity = result.statistics.max_severity.value if result.statistics.max_severity else None
                c.summary = result.summary
                c.error = result.error
                c.result_json = result.model_dump_json()
                c.assets_bytes = assets_bytes
                c.options_json = json.dumps(result.options)
        st = self.state.get(cid)
        if st is not None:
            st.progress, st.message = 1.0, status
        with self._lock:
            self.state.pop(cid, None)

    # ------------------------------------------------------------------ retention
    def sweep(self) -> dict[str, int]:
        now = utcnow()
        expired_inputs = deleted = 0
        with self.db.session() as s:
            for c in s.scalars(
                select(Comparison).where(Comparison.inputs_deleted.is_(False), Comparison.inputs_expire_at.is_not(None))
            ):
                exp = c.inputs_expire_at
                if exp is not None and exp.tzinfo is None:
                    exp = exp.replace(tzinfo=now.tzinfo)
                if exp is not None and exp <= now and c.job_status not in ("queued", "running"):
                    self.storage.delete_inputs(c.id)
                    c.inputs_deleted = True
                    expired_inputs += 1
            if self.settings.result_retention_days > 0:
                cutoff = now - timedelta(days=self.settings.result_retention_days)
                old = list(
                    s.scalars(
                        select(Comparison.id).where(
                            Comparison.created_at < cutoff, Comparison.job_status.not_in(("queued", "running"))
                        )
                    )
                )
                for cid in old:
                    self.storage.delete(cid)
                if old:
                    s.execute(delete(Comparison).where(Comparison.id.in_(old)))
                deleted = len(old)
        if expired_inputs or deleted:
            log.info("retention sweep", extra={"inputs_deleted": expired_inputs, "comparisons_deleted": deleted})
        return {"inputs_deleted": expired_inputs, "comparisons_deleted": deleted}

    def _sweep_loop(self) -> None:
        delay = 0.0  # sweep once at startup, then periodically
        while not self._stop.wait(delay):
            delay = self.settings.sweep_interval_seconds
            try:
                self.sweep()
            except Exception:  # keep sweeping on transient DB/filesystem errors; logged for operators
                log.exception("retention sweep failed")
