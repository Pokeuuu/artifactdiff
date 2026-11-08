"""FastAPI application factory."""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import ColumnElement, func, or_, select, text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from artifactdiff import __version__
from artifactdiff.engine import Engine
from artifactdiff.errors import ArtifactDiffError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeSeverity, ComparisonResult
from artifactdiff.registry import default_registry
from artifactdiff.reporting import render_result
from artifactdiff_api import schemas
from artifactdiff_api.db import Comparison, Database, utcnow
from artifactdiff_api.jobs import JobManager
from artifactdiff_api.settings import Settings, get_settings
from artifactdiff_api.storage import Storage, UploadTooLarge, display_name, safe_extension

log = logging.getLogger("artifactdiff_api")
API = "/api/v1"


# ---------------------------------------------------------------------------- middleware
class BodyLimitMiddleware:
    """Rejects request bodies above ``limit`` while they stream in (not after spooling to disk)."""

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return
        for k, v in scope["headers"]:
            if k == b"content-length" and v.isdigit() and int(v) > self.limit:
                await self._reject(send)
                return
        received = 0
        exceeded = False
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > self.limit:
                    # Stop feeding the app. Frameworks turn errors raised here into generic 400s, so we
                    # signal a disconnect instead and rewrite whatever response follows into a 413.
                    exceeded = True
                    return {"type": "http.disconnect"}
            return msg

        async def guarded_send(msg: Message) -> None:
            nonlocal rejected
            if not exceeded:
                await send(msg)
            elif not rejected and msg["type"] == "http.response.start":
                rejected = True
                await self._reject(send)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not rejected:
            await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"detail":"Request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; worker-src 'self' blob:; connect-src 'self' blob:; font-src 'self' data:; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    ),
}


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < now - 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


# ------------------------------------------------------------------------------- state
class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_url)
        self.storage = Storage(settings.data_dir)
        self.jobs = JobManager(settings, self.db, self.storage)
        self.limiter = RateLimiter(settings.rate_limit_per_minute)
        self.engine = Engine(config=self.jobs.config)


def get_state(request: Request) -> AppState:
    st: AppState = request.app.state.ad
    return st


StateDep = Annotated[AppState, Depends(get_state)]


def require_token(request: Request, state: StateDep) -> None:
    token = state.settings.api_token
    if not token:
        return
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(401, "Missing or invalid API token", headers={"WWW-Authenticate": "Bearer"})


def _summary(
    c: Comparison, state: AppState, cls: type[schemas.ComparisonSummary] = schemas.ComparisonSummary, **extra: Any
) -> Any:
    prog = state.jobs.progress(c.id)
    return cls(
        id=c.id,
        created_at=c.created_at,
        finished_at=c.finished_at,
        job_status=c.job_status,
        result_status=c.result_status,
        plugin=c.plugin,
        plugin_version=c.plugin_version,
        old_name=c.old_name,
        new_name=c.new_name,
        old_size=c.old_size,
        new_size=c.new_size,
        total_changes=c.total_changes,
        max_severity=c.max_severity,
        summary=c.summary,
        error=c.error,
        duration_ms=c.duration_ms,
        inputs_available=not c.inputs_deleted,
        progress=prog[0] if prog else None,
        progress_message=prog[1] if prog else None,
        **extra,
    )


def _get(state: AppState, cid: str) -> Comparison:
    if len(cid) != 32 or not all(ch in "0123456789abcdef" for ch in cid):
        raise HTTPException(404, "Comparison not found")
    with state.db.session() as s:
        c = s.get(Comparison, cid)
    if c is None:
        raise HTTPException(404, "Comparison not found")
    return c


def _result(c: Comparison) -> ComparisonResult | None:
    return ComparisonResult.model_validate_json(c.result_json) if c.result_json else None


# ------------------------------------------------------------------------------ routes
def build_router() -> APIRouter:
    public = APIRouter(prefix=API)
    r = APIRouter(prefix=API, dependencies=[Depends(require_token)])

    @public.get("/health", response_model=schemas.Health, tags=["meta"])
    def health(state: StateDep) -> schemas.Health:
        try:
            with state.db.session() as s:
                s.execute(text("SELECT 1"))
            db_ok: Literal["ok", "error"] = "ok"
        except Exception:  # health must report, not raise
            log.exception("database health check failed")
            db_ok = "error"
        metas = state.engine.registry.all_metadata()
        return schemas.Health(
            status="ok",
            version=__version__,
            plugins_available=sum(m.available for m in metas),
            plugins_total=len(metas),
            database=db_ok,
        )

    @r.get("/config", response_model=schemas.ServerConfig, tags=["meta"])
    def server_config(state: StateDep) -> schemas.ServerConfig:
        st = state.settings
        return schemas.ServerConfig(
            max_upload_mb=st.max_upload_mb,
            input_retention_hours=st.input_retention_hours,
            result_retention_days=st.result_retention_days,
            timeout_seconds=st.timeout_seconds,
            isolation=st.isolation,
            auth_required=bool(st.api_token),
            version=__version__,
        )

    @r.get("/plugins", response_model=schemas.PluginList, tags=["plugins"])
    def plugins(state: StateDep) -> schemas.PluginList:
        return schemas.PluginList(plugins=state.engine.registry.all_metadata())

    @r.get("/plugins/{name}", tags=["plugins"])
    def plugin(name: str, state: StateDep) -> Any:
        try:
            return state.engine.registry.metadata(name)
        except ArtifactDiffError:
            raise HTTPException(404, f"Unknown plugin {name!r}") from None

    @r.post("/compare", status_code=202, response_model=schemas.Accepted, tags=["comparisons"])
    def compare(
        request: Request,
        state: StateDep,
        old: Annotated[UploadFile, File(description="Old version of the artifact")],
        new: Annotated[UploadFile, File(description="New version of the artifact")],
        plugin: Annotated[str | None, Form()] = None,
        options: Annotated[str | None, Form(description="JSON object with plugin options")] = None,
    ) -> schemas.Accepted:
        client = request.client.host if request.client else "unknown"
        if not state.limiter.allow(client):
            raise HTTPException(429, "Too many comparisons; try again in a minute")
        try:
            opts = json.loads(options) if options else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(422, f"options is not valid JSON: {exc.msg}") from None
        if not isinstance(opts, dict):
            raise HTTPException(422, "options must be a JSON object")
        plugin = plugin or None
        cid = uuid.uuid4().hex
        names = (display_name(old.filename, "old"), display_name(new.filename, "new"))
        paths = []
        sizes = []
        try:
            for side, upload, name in (("old", old, names[0]), ("new", new, names[1])):
                dest = state.storage.input_path(cid, side, safe_extension(name))
                sizes.append(state.storage.save_upload(upload.file, dest, state.settings.max_upload_bytes))
                paths.append(dest)
            # Validate plugin and options now, so mistakes are reported synchronously.
            fa, fb = ArtifactFile(paths[0], names[0]), ArtifactFile(paths[1], names[1])
            sel = state.engine.registry.select(fa, fb, plugin)
            if sel is not None:
                state.engine.resolve_options(sel.plugin, opts)
        except UploadTooLarge as exc:
            state.storage.delete(cid)
            raise HTTPException(413, str(exc)) from None
        except ArtifactDiffError as exc:
            state.storage.delete(cid)
            raise HTTPException(422, str(exc)) from None
        now = utcnow()
        with state.db.session() as s:
            s.add(
                Comparison(
                    id=cid,
                    created_at=now,
                    job_status="queued",
                    requested_plugin=plugin,
                    old_name=names[0],
                    new_name=names[1],
                    old_size=sizes[0],
                    new_size=sizes[1],
                    options_json=json.dumps(opts),
                    inputs_expire_at=now + timedelta(hours=state.settings.input_retention_hours),
                )
            )
        state.jobs.submit(cid, str(paths[0]), str(paths[1]), names[0], names[1], plugin, opts)
        return schemas.Accepted(id=cid, job_status="queued", url=f"{API}/comparisons/{cid}")

    @r.get("/comparisons", response_model=schemas.ComparisonPage, tags=["comparisons"])
    def list_comparisons(
        state: StateDep,
        q: Annotated[str | None, Query(max_length=200)] = None,
        plugin: str | None = None,
        status: str | None = None,
        sort: Literal["-created_at", "created_at", "-total_changes", "total_changes", "name"] = "-created_at",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> schemas.ComparisonPage:
        stmt = select(Comparison)
        if q:
            like = f"%{q.replace('%', '').replace('_', '')}%"
            stmt = stmt.where(
                or_(Comparison.old_name.ilike(like), Comparison.new_name.ilike(like), Comparison.summary.ilike(like))
            )
        if plugin:
            stmt = stmt.where(Comparison.plugin == plugin)
        if status:
            stmt = stmt.where(or_(Comparison.job_status == status, Comparison.result_status == status))
        orders: dict[str, ColumnElement[Any]] = {
            "-created_at": Comparison.created_at.desc(),
            "created_at": Comparison.created_at.asc(),
            "-total_changes": Comparison.total_changes.desc(),
            "total_changes": Comparison.total_changes.asc(),
            "name": Comparison.new_name.asc(),
        }
        order = orders[sort]
        with state.db.session() as s:
            total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = list(s.scalars(stmt.order_by(order, Comparison.id).limit(limit).offset(offset)))
        return schemas.ComparisonPage(items=[_summary(c, state) for c in rows], total=total, limit=limit, offset=offset)

    @r.get("/comparisons/{cid}", response_model=schemas.ComparisonDetail, tags=["comparisons"])
    def get_comparison(cid: str, state: StateDep) -> schemas.ComparisonDetail:
        c = _get(state, cid)
        detail: schemas.ComparisonDetail = _summary(
            c, state, schemas.ComparisonDetail, options=json.loads(c.options_json or "{}"), result=_result(c)
        )
        return detail

    @r.get("/comparisons/{cid}/report", tags=["comparisons"])
    def report(cid: str, state: StateDep, format: Literal["json", "markdown", "html"] = "html") -> Response:
        c = _get(state, cid)
        result = _result(c)
        if result is None:
            raise HTTPException(409, "Comparison has no result yet")
        full = state.storage.inline_assets(cid, result)
        body = render_result(full, format)
        media = {
            "json": "application/json",
            "markdown": "text/markdown; charset=utf-8",
            "html": "text/html; charset=utf-8",
        }[format]
        ext = {"json": "json", "markdown": "md", "html": "html"}[format]
        return Response(
            body,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="artifactdiff-{cid[:8]}.{ext}"'},
        )

    @r.get("/comparisons/{cid}/assets/{asset_id}", tags=["comparisons"])
    def asset(cid: str, asset_id: str, state: StateDep) -> Response:
        c = _get(state, cid)
        result = _result(c)
        meta = next((a for a in (result.assets if result else []) if a.id == asset_id), None)
        path = state.storage.asset_path(cid, asset_id)
        if meta is None or path is None:
            raise HTTPException(404, "Asset not found")
        media = (
            meta.media_type
            if meta.media_type in ("image/png", "image/jpeg", "image/webp")
            else "application/octet-stream"
        )
        return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=86400"})

    @r.get("/comparisons/{cid}/files/{side}", tags=["comparisons"])
    def input_file(cid: str, side: Literal["old", "new"], state: StateDep) -> Response:
        c = _get(state, cid)
        path = None if c.inputs_deleted else state.storage.find_input(cid, side)
        if path is None:
            raise HTTPException(410, "Input files are no longer retained (see input_retention_hours)")
        name = c.old_name if side == "old" else c.new_name
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=name,
            headers={"Content-Security-Policy": "sandbox", "Cache-Control": "no-store"},
        )

    @r.post("/comparisons/{cid}/cancel", response_model=schemas.ComparisonSummary, tags=["comparisons"])
    def cancel(cid: str, state: StateDep) -> schemas.ComparisonSummary:
        c = _get(state, cid)
        if c.job_status not in ("queued", "running"):
            raise HTTPException(409, f"Comparison is already {c.job_status}")
        state.jobs.cancel(cid)
        out: schemas.ComparisonSummary = _summary(c, state)
        return out

    @r.delete("/comparisons/{cid}", status_code=204, tags=["comparisons"])
    def delete_comparison(cid: str, state: StateDep) -> Response:
        c = _get(state, cid)
        state.jobs.cancel(cid)
        with state.db.session() as s:
            obj = s.get(Comparison, c.id)
            if obj is not None:
                s.delete(obj)
        state.storage.delete(cid)
        return Response(status_code=204)

    @r.delete("/comparisons", status_code=204, tags=["comparisons"])
    def clear_history(state: StateDep, confirm: Annotated[bool, Query()] = False) -> Response:
        if not confirm:
            raise HTTPException(400, "Pass confirm=true to delete all comparisons")
        with state.db.session() as s:
            ids = list(s.scalars(select(Comparison.id)))
            for cid in ids:
                state.jobs.cancel(cid)
                state.storage.delete(cid)
            s.query(Comparison).delete()
        return Response(status_code=204)

    @r.get("/stats", response_model=schemas.Stats, tags=["meta"])
    def stats(state: StateDep) -> schemas.Stats:
        return _stats(state)

    root = APIRouter()
    root.include_router(public)
    root.include_router(r)
    return root


def _stats(state: AppState) -> schemas.Stats:
    since = utcnow() - timedelta(days=29)
    with state.db.session() as s:
        total = s.scalar(select(func.count(Comparison.id))) or 0
        completed = s.scalar(select(func.count()).where(Comparison.job_status == "completed")) or 0
        failed = s.scalar(select(func.count()).where(Comparison.job_status == "failed")) or 0
        bytes_analyzed = s.scalar(select(func.coalesce(func.sum(Comparison.old_size + Comparison.new_size), 0))) or 0
        total_changes = s.scalar(select(func.coalesce(func.sum(Comparison.total_changes), 0))) or 0
        by_status = dict(
            s.execute(
                select(Comparison.result_status, func.count())
                .where(Comparison.result_status.is_not(None))
                .group_by(Comparison.result_status)
            ).all()
        )
        usage = s.execute(
            select(Comparison.plugin, func.count())
            .where(Comparison.plugin.is_not(None))
            .group_by(Comparison.plugin)
            .order_by(func.count().desc())
        ).all()
        recent_rows = list(
            s.execute(
                select(Comparison.created_at, Comparison.total_changes).where(Comparison.created_at >= since)
            ).all()
        )
        latest = list(
            s.scalars(
                select(Comparison)
                .where(Comparison.total_changes > 0, Comparison.result_json.is_not(None))
                .order_by(Comparison.created_at.desc())
                .limit(5)
            )
        )
    days: dict[str, list[int]] = {}
    for i in range(30):
        days[(since + timedelta(days=i)).date().isoformat()] = [0, 0]
    for created, changes in recent_rows:
        key = created.date().isoformat()
        if key in days:
            days[key][0] += 1
            days[key][1] += changes or 0
    recent: list[schemas.RecentChange] = []
    rank = {s.value: s.rank for s in ChangeSeverity}
    for c in latest:
        res = _result(c)
        if res is None:
            continue
        top = sorted(res.changes, key=lambda ch: -rank[ch.severity.value])[:3]
        recent += [
            schemas.RecentChange(
                comparison_id=c.id,
                file=c.new_name,
                path=ch.path,
                kind=ch.kind.value,
                severity=ch.severity.value,
                description=ch.description,
            )
            for ch in top
        ]
    metas = [m for m in state.engine.registry.all_metadata() if m.available]
    exts = sorted({e for m in metas for f in m.formats for e in f.extensions})
    return schemas.Stats(
        total_comparisons=total,
        completed=completed,
        failed=failed,
        files_analyzed=total * 2,
        bytes_analyzed=int(bytes_analyzed),
        total_changes=int(total_changes),
        by_result_status={str(k): int(v) for k, v in by_status.items()},
        plugin_usage=[schemas.PluginUsage(plugin=str(p), comparisons=int(n)) for p, n in usage],
        activity=[schemas.ActivityPoint(date=d, comparisons=v[0], changes=v[1]) for d, v in days.items()],
        recent_changes=recent[:10],
        supported_formats=len([m for m in metas if m.name not in ("binary", "text")]),
        supported_extensions=exts,
        storage=state.storage.usage_bytes(),
    )


# --------------------------------------------------------------------------------- app
def _web_dir(settings: Settings) -> Path | None:
    candidates = [settings.web_dir] if settings.web_dir else []
    candidates.append(Path(__file__).parent / "web")
    for c in candidates:
        if c is not None and (c / "index.html").is_file():
            return c
    return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    default_registry()  # import built-in plugins once, before worker threads start

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = AppState(settings)
        state.db.migrate()
        state.jobs.start()
        app.state.ad = state
        try:
            yield
        finally:
            state.jobs.shutdown()
            state.db.dispose()

    app = FastAPI(
        title="ArtifactDiff API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        description="Local-first comparison of non-text artifacts. Uploaded files stay on this server "
        "and are deleted after the configured retention period.",
    )
    app.include_router(build_router())

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=False,
        )
    app.add_middleware(BodyLimitMiddleware, limit=settings.max_upload_bytes * 2 + 1024 * 1024)

    web = _web_dir(settings)
    if web is not None:
        if (web / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=web / "assets"), name="web-assets")
        index = web / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> Response:
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            candidate = (web / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(web.resolve()):
                return FileResponse(candidate)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
    else:

        @app.get("/", include_in_schema=False)
        def root() -> dict[str, str]:
            return {
                "name": "ArtifactDiff API",
                "docs": "/api/docs",
                "web": "The web UI is not built. Run `npm run build` in apps/web, or `npm run dev` for development.",
            }

    return app
