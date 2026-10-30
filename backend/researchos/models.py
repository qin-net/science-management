"""SQLAlchemy models for the P0 slice. See specs/001-p0-workspace-paper-import.md."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def timestamp(**kwargs) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, **kwargs)


ROLES = ("owner", "admin", "member", "viewer")
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
ACTIVE_JOB_STATUSES = ("queued", "running")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = timestamp()


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = timestamp()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = timestamp()


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        CheckConstraint("role in ('owner','admin','member','viewer')", name="ck_member_role"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = timestamp()


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("status in ('active','archived')", name="ck_project_status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text, default="")
    background: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = timestamp()
    updated_at: Mapped[datetime] = timestamp(onupdate=utcnow)


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (
        Index(
            "uq_papers_project_doi",
            "project_id",
            "doi",
            unique=True,
            postgresql_where=text("doi IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[list[str]] = mapped_column(JSONB, default=list)
    year: Mapped[int | None] = mapped_column(Integer)
    doi: Mapped[str | None] = mapped_column(String(255))
    arxiv_id: Mapped[str | None] = mapped_column(String(64))
    # Per-field origin ("user" / "pdf_metadata"); a missing key means the value is unknown.
    metadata_provenance: Mapped[dict] = mapped_column(JSONB, default=dict)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_versions.id", use_alter=True, name="fk_papers_current_version")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = timestamp()
    updated_at: Mapped[datetime] = timestamp(onupdate=utcnow)


class PaperVersion(Base):
    """An immutable uploaded source file. New files always create a new row."""

    __tablename__ = "paper_versions"
    __table_args__ = (
        UniqueConstraint("paper_id", "version_no", name="uq_paper_version_no"),
        UniqueConstraint("project_id", "sha256", name="uq_paper_version_project_sha256"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(255))
    page_count: Mapped[int | None] = mapped_column(Integer)
    pdf_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    # The last successful parse. A failed re-parse leaves it, and its blocks, in place.
    current_parse_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", use_alter=True, name="fk_versions_current_parse_job")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = timestamp()


class Job(Base):
    """Durable background work with lease-based ownership."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued','running','succeeded','failed','cancelled')",
            name="ck_job_status",
        ),
        # One active parse per subject: double clicks and API retries cannot fan out.
        Index(
            "uq_jobs_active_subject",
            "kind",
            "subject_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        Index("ix_jobs_claim", "status", "next_run_at"),
        Index("ix_jobs_subject", "subject_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    parser: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    stage: Mapped[str | None] = mapped_column(String(64))
    progress_done: Mapped[int | None] = mapped_column(Integer)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    next_run_at: Mapped[datetime] = timestamp()
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = timestamp()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperBlock(Base):
    """A located unit of source text. Blocks are written once by the job that produced them."""

    __tablename__ = "paper_blocks"
    __table_args__ = (
        UniqueConstraint("parse_job_id", "seq", name="uq_block_job_seq"),
        CheckConstraint("granularity in ('block','page')", name="ck_block_granularity"),
        Index("ix_blocks_job_page", "parse_job_id", "page_no"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    paper_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_versions.id"), index=True)
    parse_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    seq: Mapped[int] = mapped_column(Integer)
    page_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    # [x0, y0, x1, y1] normalized to the displayed page, origin top-left; SQL NULL for
    # page-level blocks.
    bbox: Mapped[list[float] | None] = mapped_column(JSONB(none_as_null=True))
    granularity: Mapped[str] = mapped_column(String(8))
    content_hash: Mapped[str] = mapped_column(String(71))


class ResearchEvent(Base):
    """Append-only activity log; written in the same transaction as the change it records."""

    __tablename__ = "research_events"
    __table_args__ = (Index("ix_events_project_time", "project_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str] = mapped_column(String(32))
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = timestamp()


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parsers: Mapped[list[str]] = mapped_column(JSONB, default=list)
    started_at: Mapped[datetime] = timestamp()
    last_seen_at: Mapped[datetime] = timestamp()
