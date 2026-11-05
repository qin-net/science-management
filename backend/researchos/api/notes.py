"""Notes and block-anchored annotations (specs/003-p0-notes-annotations.md)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchos import events
from researchos.db import get_db
from researchos.errors import ApiError, not_found
from researchos.models import Note, Paper, PaperBlock, PaperVersion, Project, User
from researchos.schemas import NoteAnchorOut, NoteCreate, NoteOut, NotePatch, NotesPage
from researchos.security import ROLE_RANK, current_user, require_project

router = APIRouter(prefix="/projects/{project_id}", tags=["notes"])


def _kind(note: Note) -> str:
    if note.anchor is not None:
        return "annotation"
    return "paper" if note.paper_id is not None else "project"


def _event_payload(note: Note) -> dict:
    # No body and no quoted text: the activity log is read by people who may not need either.
    return {"kind": _kind(note), "paper_id": str(note.paper_id) if note.paper_id else None}


def _render(
    db: Session, notes: list[Note], user: User, project: Project, role: str
) -> list[NoteOut]:
    """Turn rows into responses. Anchor status is worked out here, against the version's current
    parse, so it is always as fresh as the blocks."""
    paper_ids = {n.paper_id for n in notes if n.paper_id}
    titles = {}
    if paper_ids:
        query = select(Paper.id, Paper.title).where(Paper.id.in_(paper_ids))
        titles = dict(db.execute(query).all())
    authors = {}
    author_ids = {n.author_id for n in notes}
    if author_ids:
        authors = dict(
            db.execute(select(User.id, User.display_name).where(User.id.in_(author_ids))).all()
        )
    version_ids = {n.version_id for n in notes if n.version_id}
    versions: dict[uuid.UUID, PaperVersion] = {}
    if version_ids:
        found_versions = db.scalars(select(PaperVersion).where(PaperVersion.id.in_(version_ids)))
        versions = {v.id: v for v in found_versions}
    # Smallest seq wins when several blocks on a page share a hash.
    job_ids: set[uuid.UUID] = set()
    hashes: set[str] = set()
    for n in notes:
        job_id = versions[n.version_id].current_parse_job_id if n.version_id else None
        if n.anchor and job_id:
            job_ids.add(job_id)
            hashes.add(n.anchor["content_hash"])
    found: dict[tuple[uuid.UUID, int, str], PaperBlock] = {}
    if job_ids:
        rows = db.scalars(
            select(PaperBlock)
            .where(PaperBlock.parse_job_id.in_(job_ids), PaperBlock.content_hash.in_(hashes))
            .order_by(PaperBlock.seq.desc())
        )
        for block in rows:  # descending, so the last write per key is the smallest seq
            found[(block.parse_job_id, block.page_no, block.content_hash)] = block

    writable = project.status == "active" and ROLE_RANK[role] >= ROLE_RANK["member"]
    manager = ROLE_RANK[role] >= ROLE_RANK["admin"]
    out = []
    for note in notes:
        anchor = None
        if note.anchor is not None and note.version_id is not None:
            version = versions[note.version_id]
            located: PaperBlock | None = None
            if version.current_parse_job_id is None:
                status = "unparsed"
            else:
                key = (
                    version.current_parse_job_id,
                    note.anchor["page_no"],
                    note.anchor["content_hash"],
                )
                located = found.get(key)
                status = "located" if located else "stale"
            anchor = NoteAnchorOut(
                version_id=version.id,
                version_no=version.version_no,
                page_no=note.anchor["page_no"],
                text=note.anchor["text"],
                status=status,
                block_id=located.id if located else None,
                bbox=located.bbox if located else None,
                granularity=located.granularity if located else note.anchor["granularity"],
                # Older anchors (specs/003) carry neither, and keep meaning "the whole block".
                range=note.anchor.get("range"),
                quote=note.anchor.get("quote"),
            )
        mine = note.author_id == user.id
        out.append(
            NoteOut(
                id=note.id,
                kind=_kind(note),
                paper_id=note.paper_id,
                paper_title=titles.get(note.paper_id) if note.paper_id else None,
                author_id=note.author_id,
                author_name=authors.get(note.author_id, ""),
                body=note.body,
                version=note.version,
                editable=writable and mine,
                deletable=writable and (mine or manager),
                anchor=anchor,
                created_at=note.created_at,
                updated_at=note.updated_at,
            )
        )
    return out


def _load_note(db: Session, project_id: uuid.UUID, note_id: uuid.UUID, lock: bool = False) -> Note:
    query = select(Note).where(Note.id == note_id, Note.project_id == project_id)
    note = db.scalar(query.with_for_update() if lock else query)
    if note is None:
        raise not_found("note")
    return note


def _anchor_snapshot(
    db: Session,
    project: Project,
    paper: Paper,
    version_id: uuid.UUID,
    block_id: uuid.UUID,
    requested_range: tuple[int, int] | None = None,
) -> tuple[PaperVersion, dict]:
    version = db.scalar(
        select(PaperVersion).where(
            PaperVersion.id == version_id, PaperVersion.project_id == project.id
        )
    )
    if version is None:
        raise not_found("paper version")
    if version.paper_id != paper.id:
        raise ApiError(422, "invalid_anchor", "the version does not belong to this paper")
    block = None
    if version.current_parse_job_id is not None:
        block = db.scalar(
            select(PaperBlock).where(
                PaperBlock.id == block_id,
                PaperBlock.paper_version_id == version.id,
                PaperBlock.parse_job_id == version.current_parse_job_id,
            )
        )
    if block is None:
        raise ApiError(
            422,
            "invalid_anchor",
            "the block is not part of the current parse of this version",
        )
    if requested_range is not None:
        start, end = requested_range
        if not 0 <= start < end <= len(block.text):
            raise ApiError(
                422,
                "invalid_anchor",
                "the range does not fit inside the block's text",
                {"length": len(block.text)},
            )
    snapshot = {
        "page_no": block.page_no,
        "text": block.text,
        "content_hash": block.content_hash,
        "bbox": block.bbox,
        "granularity": block.granularity,
        "block_seq": block.seq,
    }
    if requested_range is not None:
        start, end = requested_range
        # The quote is cut here, from the block the server just read; a client cannot supply it.
        snapshot["range"] = [start, end]
        snapshot["quote"] = block.text[start:end]
    return version, snapshot


@router.get("/notes", response_model=NotesPage)
def list_notes(
    project_id: uuid.UUID,
    paper_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> NotesPage:
    project, role = require_project(db, user, project_id, "viewer")
    where = [Note.project_id == project.id]
    if paper_id is not None:
        where.append(Note.paper_id == paper_id)
    total = db.scalar(select(func.count()).select_from(Note).where(*where)) or 0
    rows = list(
        db.scalars(
            select(Note)
            .where(*where)
            .order_by(Note.created_at.desc(), Note.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return NotesPage(
        items=_render(db, rows, user, project, role), total=total, offset=offset, limit=limit
    )


@router.post("/notes", response_model=NoteOut, status_code=201)
def create_note(
    project_id: uuid.UUID,
    payload: NoteCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> NoteOut:
    project, role = require_project(db, user, project_id, "member")
    if project.status != "active":
        raise ApiError(409, "project_archived", "archived projects are read-only")
    if payload.anchor is not None and payload.paper_id is None:
        raise ApiError(422, "invalid_anchor", "an annotation needs the paper it belongs to")
    if payload.anchor is None and not payload.body:
        raise ApiError(422, "validation_error", "the note is empty", {"fields": [{"loc": "body"}]})
    paper = None
    if payload.paper_id is not None:
        paper = db.scalar(
            select(Paper).where(Paper.id == payload.paper_id, Paper.project_id == project.id)
        )
        if paper is None:
            raise not_found("paper")
    version_id = None
    anchor = None
    if payload.anchor is not None and paper is not None:
        wanted = None
        if (payload.anchor.start is None) != (payload.anchor.end is None):
            raise ApiError(422, "invalid_anchor", "start and end must be given together")
        if payload.anchor.start is not None and payload.anchor.end is not None:
            wanted = (payload.anchor.start, payload.anchor.end)
        version, anchor = _anchor_snapshot(
            db, project, paper, payload.anchor.version_id, payload.anchor.block_id, wanted
        )
        version_id = version.id
    note = Note(
        workspace_id=project.workspace_id,
        project_id=project.id,
        paper_id=paper.id if paper else None,
        version_id=version_id,
        author_id=user.id,
        body=payload.body,
        anchor=anchor,
        version=1,
    )
    db.add(note)
    db.flush()
    events.record(
        db,
        event_type="note.created",
        object_type="note",
        object_id=note.id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        actor_id=user.id,
        payload=_event_payload(note),
    )
    db.commit()
    db.refresh(note)
    return _render(db, [note], user, project, role)[0]


@router.patch("/notes/{note_id}", response_model=NoteOut)
def update_note(
    project_id: uuid.UUID,
    note_id: uuid.UUID,
    payload: NotePatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> NoteOut:
    project, role = require_project(db, user, project_id, "member")
    if project.status != "active":
        raise ApiError(409, "project_archived", "archived projects are read-only")
    note = _load_note(db, project_id, note_id, lock=True)
    if note.author_id != user.id:
        raise ApiError(403, "forbidden", "only the author can edit a note")
    if note.version != payload.expected_version:
        raise ApiError(
            409,
            "version_conflict",
            "the note changed since it was loaded",
            {"current_version": note.version},
        )
    if note.anchor is None and not payload.body:
        raise ApiError(422, "validation_error", "the note is empty", {"fields": [{"loc": "body"}]})
    note.body = payload.body
    note.version += 1
    events.record(
        db,
        event_type="note.updated",
        object_type="note",
        object_id=note.id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        actor_id=user.id,
        payload=_event_payload(note),
    )
    db.commit()
    db.refresh(note)
    return _render(db, [note], user, project, role)[0]


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(
    project_id: uuid.UUID,
    note_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    project, role = require_project(db, user, project_id, "member")
    if project.status != "active":
        raise ApiError(409, "project_archived", "archived projects are read-only")
    note = _load_note(db, project_id, note_id, lock=True)
    if note.author_id != user.id and ROLE_RANK[role] < ROLE_RANK["admin"]:
        raise ApiError(403, "forbidden", "only the author or an admin can delete a note")
    events.record(
        db,
        event_type="note.deleted",
        object_type="note",
        object_id=note.id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        actor_id=user.id,
        payload=_event_payload(note),
    )
    db.delete(note)
    db.commit()
