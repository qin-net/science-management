"""PDF import, versions, located blocks and parse-job control."""

import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from researchos import events, storage
from researchos.config import get_settings
from researchos.db import get_db
from researchos.errors import ApiError, not_found
from researchos.jobs import PARSE_KIND, enqueue_parse
from researchos.models import (
    ACTIVE_JOB_STATUSES,
    Job,
    Paper,
    PaperBlock,
    PaperVersion,
    User,
    WorkerHeartbeat,
)
from researchos.schemas import (
    BlockOut,
    BlocksPage,
    JobOut,
    PaperOut,
    PaperPatch,
    PapersPage,
    VersionOut,
)
from researchos.security import current_user, require_project, require_writable_project

router = APIRouter(prefix="/projects/{project_id}", tags=["papers"])

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
DOI_PREFIX_RE = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", re.I)
STATUSES = "^(queued|running|succeeded|failed|cancelled)$"


def _latest_job(db: Session, version_id: uuid.UUID | None) -> Job | None:
    if version_id is None:
        return None
    return db.scalar(
        select(Job)
        .where(Job.subject_id == version_id, Job.kind == PARSE_KIND)
        .order_by(Job.created_at.desc())
        .limit(1)
    )


def _paper_out(db: Session, paper: Paper) -> PaperOut:
    version = db.get(PaperVersion, paper.current_version_id) if paper.current_version_id else None
    job = _latest_job(db, paper.current_version_id)
    return PaperOut(
        id=paper.id,
        workspace_id=paper.workspace_id,
        project_id=paper.project_id,
        title=paper.title,
        authors=paper.authors,
        year=paper.year,
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
        metadata_provenance=paper.metadata_provenance,
        current_version=VersionOut.model_validate(version) if version else None,
        parse_job=JobOut.model_validate(job) if job else None,
        created_at=paper.created_at,
    )


def _load_paper(
    db: Session, project_id: uuid.UUID, paper_id: uuid.UUID, lock: bool = False
) -> Paper:
    query = select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
    paper = db.scalar(query.with_for_update() if lock else query)
    if paper is None:
        raise not_found("paper")
    return paper


def _load_version(
    db: Session, project_id: uuid.UUID, paper_id: uuid.UUID, version_id: uuid.UUID
) -> PaperVersion:
    version = db.scalar(
        select(PaperVersion).where(
            PaperVersion.id == version_id,
            PaperVersion.paper_id == paper_id,
            PaperVersion.project_id == project_id,
        )
    )
    if version is None:
        raise not_found("paper version")
    return version


def _duplicate_file(db: Session, project_id: uuid.UUID, sha256: str) -> ApiError:
    existing = db.scalar(
        select(PaperVersion).where(
            PaperVersion.project_id == project_id, PaperVersion.sha256 == sha256
        )
    )
    return ApiError(
        409,
        "duplicate_file",
        "this file is already in the project",
        {"paper_id": str(existing.paper_id)} if existing else {},
    )


async def _receive(request: Request) -> storage.IncomingFile:
    """Spool the request body to storage. Runs after authorization, so unauthorized
    clients cannot make the server write anything."""
    max_bytes = get_settings().max_upload_bytes
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/pdf":
        raise ApiError(415, "not_a_pdf", "send the file as Content-Type: application/pdf")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ApiError(413, "file_too_large", f"file exceeds {max_bytes // (1024 * 1024)} MB")
    incoming = await run_in_threadpool(storage.IncomingFile, max_bytes)
    try:
        async for chunk in request.stream():
            if chunk:
                await run_in_threadpool(incoming.write, chunk)
    except ClientDisconnect as exc:
        incoming.discard()
        raise ApiError(400, "upload_aborted", "the client disconnected during upload") from exc
    except BaseException:
        incoming.discard()
        raise
    return incoming


def _store_version(
    db: Session,
    paper: Paper,
    user: User,
    incoming: storage.IncomingFile,
    sha256: str,
    filename: str,
    version_no: int,
) -> PaperVersion:
    """Insert first and move the file second: the unique index settles concurrent duplicates
    while nothing has reached storage. The caller commits through _commit_version."""
    version_id = uuid.uuid4()
    version = PaperVersion(
        id=version_id,
        workspace_id=paper.workspace_id,
        project_id=paper.project_id,
        paper_id=paper.id,
        version_no=version_no,
        sha256=sha256,
        size_bytes=incoming.size,
        original_filename=filename,
        storage_key=storage.storage_key(paper.workspace_id, paper.project_id, version_id),
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    paper.current_version_id = version.id
    enqueue_parse(db, version, user)
    db.flush()
    events.record(
        db,
        event_type="paper.imported",
        object_type="paper",
        object_id=paper.id,
        workspace_id=paper.workspace_id,
        project_id=paper.project_id,
        actor_id=user.id,
        payload={"version_id": str(version.id), "version_no": version_no, "sha256": sha256},
    )
    incoming.commit(version.storage_key)
    return version


def _commit_version(db: Session, version: PaperVersion) -> None:
    """The file is already in place, so a failed commit must not leave it behind."""
    key = version.storage_key
    try:
        db.commit()
    except BaseException:
        db.rollback()
        storage.delete(key)
        raise


def _ingest_new(
    db: Session,
    project_id: uuid.UUID,
    user: User,
    incoming: storage.IncomingFile,
    filename: str,
) -> PaperOut:
    sha256 = incoming.finish()
    project = require_writable_project(db, user, project_id)
    paper = Paper(workspace_id=project.workspace_id, project_id=project.id, created_by=user.id)
    db.add(paper)
    try:
        db.flush()
        version = _store_version(db, paper, user, incoming, sha256, filename, 1)
    except IntegrityError as exc:
        db.rollback()
        raise _duplicate_file(db, project_id, sha256) from exc
    _commit_version(db, version)
    return _paper_out(db, paper)


def _ingest_version(
    db: Session,
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    user: User,
    incoming: storage.IncomingFile,
    filename: str,
) -> PaperOut:
    sha256 = incoming.finish()
    require_writable_project(db, user, project_id)
    paper = _load_paper(db, project_id, paper_id, lock=True)
    last = db.scalar(
        select(func.max(PaperVersion.version_no)).where(PaperVersion.paper_id == paper.id)
    )
    try:
        version = _store_version(db, paper, user, incoming, sha256, filename, (last or 0) + 1)
    except IntegrityError as exc:
        db.rollback()
        raise _duplicate_file(db, project_id, sha256) from exc
    _commit_version(db, version)
    return _paper_out(db, paper)


@router.post("/papers", status_code=202, response_model=PaperOut)
async def upload_paper(
    project_id: uuid.UUID,
    request: Request,
    filename: str | None = Query(default=None, max_length=1024),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PaperOut:
    await run_in_threadpool(require_writable_project, db, user, project_id)
    # A slow client must not hold a transaction open for the length of its upload.
    await run_in_threadpool(db.rollback)
    incoming = await _receive(request)
    try:
        return await run_in_threadpool(
            _ingest_new, db, project_id, user, incoming, storage.clean_filename(filename)
        )
    finally:
        incoming.discard()


@router.post("/papers/{paper_id}/versions", status_code=202, response_model=PaperOut)
async def upload_version(
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    request: Request,
    filename: str | None = Query(default=None, max_length=1024),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PaperOut:
    await run_in_threadpool(require_writable_project, db, user, project_id)
    await run_in_threadpool(_load_paper, db, project_id, paper_id)
    await run_in_threadpool(db.rollback)
    incoming = await _receive(request)
    try:
        return await run_in_threadpool(
            _ingest_version,
            db,
            project_id,
            paper_id,
            user,
            incoming,
            storage.clean_filename(filename),
        )
    finally:
        incoming.discard()


@router.get("/papers", response_model=PapersPage)
def list_papers(
    project_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None, pattern=STATUSES),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PapersPage:
    require_project(db, user, project_id)
    latest = (
        select(Job.subject_id, Job.status)
        .where(Job.project_id == project_id, Job.kind == PARSE_KIND)
        .ext(distinct_on(Job.subject_id))
        .order_by(Job.subject_id, Job.created_at.desc())
        .subquery()
    )
    query = select(Paper).where(Paper.project_id == project_id)
    if status:
        query = query.join(latest, latest.c.subject_id == Paper.current_version_id).where(
            latest.c.status == status
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    papers = db.scalars(query.order_by(Paper.created_at.desc()).offset(offset).limit(limit))
    stale_before = datetime.now(UTC) - timedelta(seconds=get_settings().worker_stale_seconds)
    online = db.scalar(
        select(func.count())
        .select_from(WorkerHeartbeat)
        .where(WorkerHeartbeat.last_seen_at >= stale_before)
    )
    return PapersPage(
        items=[_paper_out(db, paper) for paper in papers],
        total=total,
        offset=offset,
        limit=limit,
        worker_online=bool(online),
    )


@router.get("/papers/{paper_id}", response_model=PaperOut)
def get_paper(
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PaperOut:
    require_project(db, user, project_id)
    return _paper_out(db, _load_paper(db, project_id, paper_id))


def _normalize_doi(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    doi = DOI_PREFIX_RE.sub("", raw.strip()).lower()
    if not DOI_RE.match(doi):
        raise ApiError(422, "invalid_doi", "DOI must look like 10.1234/abc")
    return doi


@router.patch("/papers/{paper_id}", response_model=PaperOut)
def patch_paper(
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    payload: PaperPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PaperOut:
    require_writable_project(db, user, project_id)
    paper = _load_paper(db, project_id, paper_id, lock=True)
    changes = payload.model_dump(exclude_unset=True)
    if "doi" in changes:
        changes["doi"] = _normalize_doi(changes["doi"])
    if "authors" in changes:
        changes["authors"] = [a.strip() for a in changes["authors"] or [] if a.strip()]
    for field in ("title", "arxiv_id"):
        if field in changes:
            changes[field] = (changes[field] or "").strip() or None
    provenance = dict(paper.metadata_provenance)
    for field, value in changes.items():
        setattr(paper, field, value)
        provenance[field] = "user"
    paper.metadata_provenance = provenance
    events.record(
        db,
        event_type="paper.metadata_updated",
        object_type="paper",
        object_id=paper.id,
        workspace_id=paper.workspace_id,
        project_id=paper.project_id,
        actor_id=user.id,
        payload={"fields": sorted(changes)},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(Paper.id).where(Paper.project_id == project_id, Paper.doi == changes.get("doi"))
        )
        raise ApiError(
            409,
            "duplicate_doi",
            "another paper in this project has this DOI",
            {"paper_id": str(existing)} if existing else {},
        ) from exc
    return _paper_out(db, paper)


@router.delete("/papers/{paper_id}", status_code=204)
def delete_paper(
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    require_writable_project(db, user, project_id)
    paper = _load_paper(db, project_id, paper_id, lock=True)
    versions = list(db.scalars(select(PaperVersion).where(PaperVersion.paper_id == paper.id)))
    version_ids = [version.id for version in versions]
    active = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.subject_id.in_(version_ids), Job.status.in_(ACTIVE_JOB_STATUSES))
    )
    if active:
        raise ApiError(409, "job_active", "cancel the running parse before deleting this paper")
    keys = [version.storage_key for version in versions]
    db.execute(delete(PaperBlock).where(PaperBlock.paper_version_id.in_(version_ids)))
    paper.current_version_id = None
    db.execute(
        update(PaperVersion)
        .where(PaperVersion.paper_id == paper.id)
        .values(current_parse_job_id=None)
    )
    db.flush()
    db.execute(delete(Job).where(Job.subject_id.in_(version_ids)))
    db.execute(delete(PaperVersion).where(PaperVersion.paper_id == paper.id))
    events.record(
        db,
        event_type="paper.deleted",
        object_type="paper",
        object_id=paper.id,
        workspace_id=paper.workspace_id,
        project_id=paper.project_id,
        actor_id=user.id,
        payload={"title": paper.title, "versions": len(versions)},
    )
    db.delete(paper)
    db.commit()
    for key in keys:
        storage.delete(key)


@router.get("/papers/{paper_id}/versions", response_model=list[VersionOut])
def list_versions(
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[PaperVersion]:
    require_project(db, user, project_id)
    _load_paper(db, project_id, paper_id)
    return list(
        db.scalars(
            select(PaperVersion)
            .where(PaperVersion.paper_id == paper_id)
            .order_by(PaperVersion.version_no.desc())
        )
    )


@router.get("/papers/{paper_id}/versions/{version_id}/file")
def get_file(
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    version_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    require_project(db, user, project_id)
    version = _load_version(db, project_id, paper_id, version_id)
    return FileResponse(
        storage.open_pdf(version.storage_key),
        media_type="application/pdf",
        filename=version.original_filename,
        content_disposition_type="inline",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/papers/{paper_id}/versions/{version_id}/blocks", response_model=BlocksPage)
def get_blocks(
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    version_id: uuid.UUID,
    page: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> BlocksPage:
    require_project(db, user, project_id)
    version = _load_version(db, project_id, paper_id, version_id)
    if version.current_parse_job_id is None:
        return BlocksPage(items=[], total=0, offset=offset, limit=limit, parsed=False)
    query = select(PaperBlock).where(PaperBlock.parse_job_id == version.current_parse_job_id)
    if page is not None:
        query = query.where(PaperBlock.page_no == page)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    blocks = db.scalars(query.order_by(PaperBlock.seq).offset(offset).limit(limit))
    return BlocksPage(
        items=[BlockOut.model_validate(block) for block in blocks],
        total=total,
        offset=offset,
        limit=limit,
        parsed=True,
    )


@router.post(
    "/papers/{paper_id}/versions/{version_id}/parse", status_code=202, response_model=JobOut
)
def reparse(
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    version_id: uuid.UUID,
    parser: str | None = Query(default=None, max_length=32),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Job:
    require_writable_project(db, user, project_id)
    _load_paper(db, project_id, paper_id, lock=True)
    version = _load_version(db, project_id, paper_id, version_id)
    job = enqueue_parse(db, version, user, parser)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "job_active", "a parse is already queued or running") from exc
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Job:
    require_project(db, user, project_id, "member")
    job = db.scalar(
        select(Job).where(Job.id == job_id, Job.project_id == project_id).with_for_update()
    )
    if job is None:
        raise not_found("job")
    if job.status not in ACTIVE_JOB_STATUSES:
        raise ApiError(409, "job_finished", f"this job already ended as {job.status}")
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = datetime.now(UTC)
        job.error_code = None
        job.error_message = None
        events.record(
            db,
            event_type="paper.parse_cancelled",
            object_type="paper_version",
            object_id=job.subject_id,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            actor_id=user.id,
            payload={"job_id": str(job.id)},
        )
    else:
        # The owning worker sees this at its next lease renewal and kills the parser.
        job.cancel_requested = True
    db.commit()
    return job
