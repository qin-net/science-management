"""Request/response models. Unknown fields are rejected so silent typos surface as 422."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(Strict):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    workspace_name: str | None = Field(default=None, min_length=1, max_length=200)


class LoginRequest(Strict):
    email: EmailStr
    password: str = Field(max_length=200)


class UserOut(Out):
    id: uuid.UUID
    email: str
    display_name: str


class WorkspaceIn(Strict):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceOut(Out):
    id: uuid.UUID
    name: str
    role: str | None = None


class MemberIn(Strict):
    email: EmailStr
    role: str = Field(pattern="^(owner|admin|member|viewer)$")


class RoleIn(Strict):
    role: str = Field(pattern="^(owner|admin|member|viewer)$")


class MemberOut(Out):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str


class ProjectIn(Strict):
    name: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=20_000)
    background: str = Field(default="", max_length=20_000)


class ProjectPatch(Strict):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    goal: str | None = Field(default=None, max_length=20_000)
    background: str | None = Field(default=None, max_length=20_000)
    status: str | None = Field(default=None, pattern="^(active|archived)$")


class ProjectOut(Out):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    goal: str
    background: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class JobOut(Out):
    id: uuid.UUID
    kind: str
    subject_id: uuid.UUID
    status: str
    stage: str | None
    attempts: int
    max_attempts: int
    parser: str
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    result: dict
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class VersionOut(Out):
    id: uuid.UUID
    version_no: int
    sha256: str
    size_bytes: int
    original_filename: str
    page_count: int | None
    current_parse_job_id: uuid.UUID | None
    created_at: datetime


class PaperPatch(Strict):
    title: str | None = Field(default=None, max_length=2000)
    authors: list[str] | None = Field(default=None, max_length=500)
    year: int | None = Field(default=None, ge=1000, le=3000)
    doi: str | None = Field(default=None, max_length=255)
    arxiv_id: str | None = Field(default=None, max_length=64)


class PaperOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    title: str | None
    authors: list[str]
    year: int | None
    doi: str | None
    arxiv_id: str | None
    metadata_provenance: dict
    current_version: VersionOut | None
    parse_job: JobOut | None
    created_at: datetime


class PapersPage(BaseModel):
    items: list[PaperOut]
    total: int
    offset: int
    limit: int
    worker_online: bool


class BlockOut(Out):
    id: uuid.UUID
    seq: int
    page_no: int
    kind: str
    text: str
    bbox: list[float] | None
    granularity: str
    content_hash: str


class BlocksPage(BaseModel):
    items: list[BlockOut]
    total: int
    offset: int
    limit: int
    # False means no parse has succeeded yet, as opposed to a parse that found nothing.
    parsed: bool


class EventOut(Out):
    id: uuid.UUID
    event_type: str
    object_type: str
    object_id: uuid.UUID
    payload: dict
    created_at: datetime
    project_id: uuid.UUID | None
    actor_id: uuid.UUID | None
