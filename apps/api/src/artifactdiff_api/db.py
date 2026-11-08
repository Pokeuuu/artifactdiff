"""Database models and session handling (SQLAlchemy 2, migrations via Alembic)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

MIGRATIONS = Path(__file__).parent / "migrations"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Comparison(Base):
    __tablename__ = "comparisons"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_status: Mapped[str] = mapped_column(String(16), index=True)  # queued|running|completed|failed|cancelled
    result_status: Mapped[str | None] = mapped_column(String(16))  # identical|equivalent|different|error|...
    plugin: Mapped[str | None] = mapped_column(String(64), index=True)
    plugin_version: Mapped[str | None] = mapped_column(String(32))
    requested_plugin: Mapped[str | None] = mapped_column(String(64))
    old_name: Mapped[str] = mapped_column(String(255))
    new_name: Mapped[str] = mapped_column(String(255))
    old_size: Mapped[int] = mapped_column(Integer)
    new_size: Mapped[int] = mapped_column(Integer)
    total_changes: Mapped[int] = mapped_column(Integer, default=0)
    max_severity: Mapped[str | None] = mapped_column(String(16))
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    options_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    inputs_expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inputs_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    assets_bytes: Mapped[int] = mapped_column(Integer, default=0)


class Database:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.url = url
        self.engine: Engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()

        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self) -> None:
        """Bring the schema to the latest Alembic revision."""
        from alembic import command
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS))
        cfg.set_main_option("sqlalchemy.url", self.url.replace("%", "%%"))
        with self.engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._sessions()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()
