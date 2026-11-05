"""Database engine, session management and portable column types.

Money and quantities use `Dec`, which is NUMERIC on PostgreSQL and a scaled
BIGINT on SQLite, so values are exact on both backends (SQLite NUMERIC is
float-backed). Rule: never multiply/divide two `Dec` columns inside SQL;
SUM/compare is fine, arithmetic happens in Python with `decimal.Decimal`.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, DateTime, Numeric, create_engine, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator, Uuid

from app.config import get_settings


class Dec(TypeDecorator):
    """Exact decimal type. `Dec(2)` for money, `Dec(3)` for quantities."""

    impl = Numeric
    cache_ok = True

    def __init__(self, scale: int = 2, precision: int = 18):
        super().__init__()
        self.scale = scale
        self.precision = precision
        self._factor = Decimal(10) ** scale

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(BigInteger())
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        d = Decimal(str(value)).quantize(Decimal(1).scaleb(-self.scale))
        if dialect.name == "sqlite":
            return int(d * self._factor)
        return d

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "sqlite":
            return (Decimal(int(value)) / self._factor).quantize(Decimal(1).scaleb(-self.scale))
        return Decimal(value).quantize(Decimal(1).scaleb(-self.scale))

    @property
    def python_type(self):
        return Decimal


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


def _make_engine(url: str):
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs["pool_pre_ping"] = True
        # No server-side prepared statements: hosted poolers (Neon, PgBouncer in
        # transaction mode) can route consecutive statements to different backends.
        kwargs["connect_args"] = {"prepare_threshold": None}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return eng


engine = _make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=True)


def configure_engine(url: str) -> None:
    """Rebind the session factory (used by tests)."""
    global engine
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)


def create_schema() -> None:
    """Create any missing tables (see Settings.auto_create_schema). On PostgreSQL a
    transaction-scoped advisory lock stops simultaneous cold starts from racing."""
    import app.models  # noqa: F401  registers every table on Base.metadata

    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(727463)"))
        Base.metadata.create_all(conn)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
