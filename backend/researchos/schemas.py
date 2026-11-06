"""Request/response models. Unknown fields are rejected so silent typos surface as 422."""

import uuid
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StrictInt,
    field_validator,
)


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
    abstract: str | None
    venue: str | None
    source_links: dict | None
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


class SearchIn(Strict):
    query: str = Field(min_length=1, max_length=300)
    sources: list[str] | None = None
    year_from: int | None = Field(default=None, ge=1500)
    year_to: int | None = Field(default=None, ge=1500)
    limit: int = Field(default=20, ge=1, le=25)


class ImportIn(Strict):
    doi: str | None = Field(default=None, max_length=255)
    arxiv_id: str | None = Field(default=None, max_length=200)


NOTE_MAX_CHARS = 20000


def _clean_note_body(value: str) -> str:
    value = value.strip()
    if len(value) > NOTE_MAX_CHARS:
        raise ValueError(f"the note is longer than {NOTE_MAX_CHARS} characters")
    if any(ord(c) < 32 and c not in "\n\t" or ord(c) == 127 for c in value):
        raise ValueError("the note contains control characters")
    return value


class NoteAnchorIn(Strict):
    version_id: uuid.UUID
    block_id: uuid.UUID
    # Character offsets into the block's own text, left-closed right-open (specs/011). Strict,
    # so "1" is refused rather than turned into 1; whether they fit is checked against the block.
    start: StrictInt | None = None
    end: StrictInt | None = None


class NoteCreate(Strict):
    body: str
    paper_id: uuid.UUID | None = None
    anchor: NoteAnchorIn | None = None

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        return _clean_note_body(value)


class NotePatch(Strict):
    body: str
    expected_version: int = Field(ge=1)

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        return _clean_note_body(value)


class NoteAnchorOut(BaseModel):
    version_id: uuid.UUID
    version_no: int
    page_no: int
    text: str
    status: str  # located | stale | unparsed
    block_id: uuid.UUID | None
    bbox: list[float] | None
    granularity: str
    range: list[int] | None = None
    quote: str | None = None


class NoteOut(BaseModel):
    id: uuid.UUID
    kind: str  # project | paper | annotation
    paper_id: uuid.UUID | None
    paper_title: str | None
    author_id: uuid.UUID
    author_name: str
    body: str
    version: int
    editable: bool
    deletable: bool
    anchor: NoteAnchorOut | None
    created_at: datetime
    updated_at: datetime


class NotesPage(BaseModel):
    items: list[NoteOut]
    total: int
    offset: int
    limit: int


TaskStatus = Literal["todo", "doing", "blocked", "done", "cancelled"]
TaskPriority = Literal["low", "normal", "high"]


def _plain_text(value: str, *, limit: int, field: str) -> str:
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{field} is longer than {limit} characters")
    if any(ord(c) < 32 and c not in "\n\t" or ord(c) == 127 for c in value):
        raise ValueError(f"{field} contains control characters")
    return value


def _title(value: str) -> str:
    value = _plain_text(value, limit=200, field="the title")
    if not value:
        raise ValueError("the title is empty")
    return value


def _reason(value: str | None) -> str | None:
    if value is None:
        return None
    return _plain_text(value, limit=500, field="the reason") or None


class TaskCreate(Strict):
    title: str
    description: str = ""
    status: Literal["todo", "doing", "blocked"] = "todo"
    priority: TaskPriority = "normal"
    blocked_reason: str | None = None
    assignee_id: uuid.UUID | None = None
    due_date: date | None = None
    paper_id: uuid.UUID | None = None

    _title_ok = field_validator("title")(lambda cls, v: _title(v))
    _description_ok = field_validator("description")(
        lambda cls, v: _plain_text(v, limit=5000, field="the description")
    )
    _reason_ok = field_validator("blocked_reason")(lambda cls, v: _reason(v))


class TaskPatch(Strict):
    expected_version: int = Field(ge=1)
    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    blocked_reason: str | None = None
    assignee_id: uuid.UUID | None = None
    due_date: date | None = None
    paper_id: uuid.UUID | None = None

    @field_validator("title")
    @classmethod
    def _title_ok(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("the title cannot be cleared")
        return _title(v)

    @field_validator("description")
    @classmethod
    def _description_ok(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("the description cannot be cleared; send an empty string")
        return _plain_text(v, limit=5000, field="the description")

    @field_validator("status", "priority")
    @classmethod
    def _not_null(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("this field cannot be cleared")
        return v

    _reason_ok = field_validator("blocked_reason")(lambda cls, v: _reason(v))


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    priority: str
    blocked_reason: str | None
    assignee_id: uuid.UUID | None
    assignee_name: str | None
    assignee_active: bool | None
    due_date: date | None
    overdue: bool
    paper_id: uuid.UUID | None
    paper_title: str | None
    created_by: uuid.UUID
    created_by_name: str
    completed_at: datetime | None
    version: int
    editable: bool
    deletable: bool
    created_at: datetime
    updated_at: datetime


class TasksPage(BaseModel):
    items: list[TaskOut]
    total: int
    offset: int
    limit: int
    counts: dict[str, int]


def _place(value: str | None) -> str | None:
    if value is None:
        return None
    return _plain_text(value, limit=200, field="the location") or None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("the time needs a time zone offset, for example +08:00 or Z")
    if not 2000 <= value.astimezone(UTC).year <= 2100:
        raise ValueError("the year must be between 2000 and 2100")
    return value.astimezone(UTC)


class MeetingCreate(Strict):
    title: str
    starts_at: datetime
    location: str | None = None

    _title_ok = field_validator("title")(lambda cls, v: _title(v))
    _time_ok = field_validator("starts_at")(lambda cls, v: _aware(v))
    _place_ok = field_validator("location")(lambda cls, v: _place(v))


class MeetingPatch(Strict):
    expected_version: int = Field(ge=1)
    title: str | None = None
    starts_at: datetime | None = None
    location: str | None = None

    @field_validator("title")
    @classmethod
    def _title_ok(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("the title cannot be cleared")
        return _title(v)

    @field_validator("starts_at")
    @classmethod
    def _time_ok(cls, v: datetime | None) -> datetime:
        if v is None:
            raise ValueError("the time cannot be cleared")
        return _aware(v)

    _place_ok = field_validator("location")(lambda cls, v: _place(v))


class MeetingOut(BaseModel):
    id: uuid.UUID
    title: str
    starts_at: datetime
    location: str | None
    past: bool
    created_by: uuid.UUID
    created_by_name: str
    version: int
    editable: bool
    deletable: bool
    created_at: datetime
    updated_at: datetime


class MeetingsPage(BaseModel):
    items: list[MeetingOut]
    total: int


class OverviewMeeting(BaseModel):
    id: uuid.UUID
    title: str
    starts_at: datetime
    location: str | None
    project_id: uuid.UUID
    project_name: str
    seconds_until: int


class OverviewTotals(BaseModel):
    active_projects: int
    archived_projects: int
    papers: int
    open_tasks: int
    overdue_tasks: int
    parsed_papers: int
    noted_papers: int


class OverviewProject(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    paper_count: int
    parsed_paper_count: int
    noted_paper_count: int
    tasks: dict[str, int]
    last_activity_at: datetime | None


class OverviewTask(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    priority: str
    due_date: date | None
    overdue: bool
    project_id: uuid.UUID
    project_name: str


class OverviewMyTasks(BaseModel):
    total: int
    items: list[OverviewTask]


class OverviewEvent(BaseModel):
    id: uuid.UUID
    event_type: str
    project_id: uuid.UUID | None
    project_name: str | None
    actor_name: str | None
    created_at: datetime


class OverviewDay(BaseModel):
    date: date
    total: int
    papers: int
    notes: int
    tasks: int
    projects: int
    meetings: int


class Overview(BaseModel):
    today: date
    totals: OverviewTotals
    projects: list[OverviewProject]
    my_tasks: OverviewMyTasks
    recent_events: list[OverviewEvent]
    activity: list[OverviewDay]
    next_meeting: OverviewMeeting | None


class AskRequest(Strict):
    question: str
    paper_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)

    @field_validator("question")
    @classmethod
    def _question_ok(cls, v: str) -> str:
        v = _plain_text(v, limit=2000, field="the question")
        if not v:
            raise ValueError("the question is empty")
        return v


class EvidenceOut(BaseModel):
    block_id: uuid.UUID
    paper_id: uuid.UUID
    paper_title: str | None
    version_id: uuid.UUID
    seq: int
    page_no: int
    text: str


class AnswerSegmentOut(BaseModel):
    text: str
    supported: bool
    evidence: list[EvidenceOut]


class UnavailablePaperOut(BaseModel):
    paper_id: uuid.UUID
    paper_title: str | None
    reason: Literal["no_file", "not_parsed", "no_text"]


class AskUsageOut(BaseModel):
    input_tokens: int | None
    output_tokens: int | None


class AskOut(BaseModel):
    answerable: bool
    segments: list[AnswerSegmentOut]
    unavailable: list[UnavailablePaperOut]
    dropped_citations: int
    model: str
    usage: AskUsageOut


class SummaryRequest(Strict):
    language: Literal["zh", "en"] = "zh"


class TranslateRequest(Strict):
    block_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    language: Literal["zh", "en"] = "zh"


class TranslationOut(BaseModel):
    block_id: uuid.UUID
    text: str
    model: str
    created_at: datetime


class TranslationsOut(BaseModel):
    """`remaining`: asked for but not sent this time; `missing`: sent, but no usable translation
    came back (specs/012 3.2). `model` and `usage` are null when no call was made."""

    items: list[TranslationOut]
    remaining: list[uuid.UUID]
    missing: list[uuid.UUID]
    model: str | None
    usage: AskUsageOut | None
