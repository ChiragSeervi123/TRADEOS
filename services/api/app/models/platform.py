import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime, Uuid, utcnow


class OrgScoped:
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True, nullable=False
    )


class AuditEvent(IdMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_org_created", "organization_id", "created_at"),)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("organizations.id"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict | None] = mapped_column(JSONType)
    after: Mapped[dict | None] = mapped_column(JSONType)
    meta: Mapped[dict | None] = mapped_column(JSONType)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ExceptionItem(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "exceptions"
    __table_args__ = (
        Index("ix_exc_org_status", "organization_id", "status"),
        UniqueConstraint("organization_id", "dedupe_key", name="uq_exception_dedupe"),
    )
    exception_type: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(10), default="medium")  # low|medium|high|critical
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|in_progress|resolved|dismissed
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    evidence: Mapped[dict | None] = mapped_column(JSONType)
    suggested_action: Mapped[str | None] = mapped_column(String(500))
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    resolution_reason: Mapped[str | None] = mapped_column(Text)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    is_synthetic: Mapped[bool] = mapped_column(default=False)


class Comment(IdMixin, OrgScoped, Base):
    __tablename__ = "comments"
    __table_args__ = (Index("ix_comment_entity", "entity_type", "entity_id"),)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class IdempotencyKey(IdMixin, OrgScoped, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("organization_id", "scope", "key"),)
    scope: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Job(IdMixin, TimestampMixin, Base):
    """Database-backed job queue (see ADR 0003)."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_job_status_run", "status", "run_after"),)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("organizations.id"))
    job_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|succeeded|failed|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONType)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)


class IntegrationConnection(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "integration_connections"
    __table_args__ = (UniqueConstraint("organization_id", "provider"),)
    provider: Mapped[str] = mapped_column(String(40))
    # not_configured | simulated | configured | error  ("connected" only after a real round trip)
    status: Mapped[str] = mapped_column(String(20), default="not_configured")
    config: Mapped[dict] = mapped_column(JSONType, default=dict)  # non-secret settings only
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)


class IntegrationSyncRun(IdMixin, OrgScoped, Base):
    __tablename__ = "integration_sync_runs"
    connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("integration_connections.id"), index=True
    )
    direction: Mapped[str] = mapped_column(String(10), default="inbound")
    status: Mapped[str] = mapped_column(String(20), default="running")
    records_processed: Mapped[int] = mapped_column(Integer, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list | None] = mapped_column(JSONType)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ImportBatch(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "import_batches"
    entity: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="previewed")  # previewed|committed|failed
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    column_mapping: Mapped[dict | None] = mapped_column(JSONType)
    errors: Mapped[list | None] = mapped_column(JSONType)
    rows: Mapped[list | None] = mapped_column(JSONType)  # normalised rows kept until commit
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    committed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
