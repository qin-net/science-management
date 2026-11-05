"""Group meetings of a project (specs/010-p0-meetings.md)."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from researchos import events
from researchos.db import get_db
from researchos.errors import ApiError, not_found
from researchos.models import Meeting, Project, User
from researchos.schemas import MeetingCreate, MeetingOut, MeetingPatch, MeetingsPage
from researchos.security import ROLE_RANK, current_user, require_project

router = APIRouter(prefix="/projects/{project_id}", tags=["meetings"])

LIST_LIMIT = 200


def _writable_project(db: Session, user: User, project_id: uuid.UUID) -> tuple[Project, str]:
    project, role = require_project(db, user, project_id, "member")
    if project.status != "active":
        raise ApiError(409, "project_archived", "archived projects are read-only")
    return project, role


def _load(db: Session, project_id: uuid.UUID, meeting_id: uuid.UUID, lock: bool = False) -> Meeting:
    query = select(Meeting).where(Meeting.id == meeting_id, Meeting.project_id == project_id)
    meeting = db.scalar(query.with_for_update() if lock else query)
    if meeting is None:
        raise not_found("meeting")
    return meeting


def _render(
    db: Session, meetings: list[Meeting], user: User, project: Project, role: str
) -> list[MeetingOut]:
    names: dict[uuid.UUID, str] = {}
    if meetings:
        names = dict(
            db.execute(
                select(User.id, User.display_name).where(
                    User.id.in_({m.created_by for m in meetings})
                )
            ).all()
        )
    now = datetime.now(UTC)
    writable = project.status == "active" and ROLE_RANK[role] >= ROLE_RANK["member"]
    manager = ROLE_RANK[role] >= ROLE_RANK["admin"]
    return [
        MeetingOut(
            id=m.id,
            title=m.title,
            starts_at=m.starts_at,
            location=m.location,
            past=m.starts_at < now,
            created_by=m.created_by,
            created_by_name=names.get(m.created_by, ""),
            version=m.version,
            editable=writable,
            deletable=writable and (m.created_by == user.id or manager),
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
        for m in meetings
    ]


def _event(db: Session, kind: str, project: Project, meeting: Meeting, user: User, payload=None):
    # Field names only: no title, place or time, as with the other activity events.
    events.record(
        db,
        event_type=f"meeting.{kind}",
        object_type="meeting",
        object_id=meeting.id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        actor_id=user.id,
        payload=payload or {},
    )


@router.get("/meetings", response_model=MeetingsPage)
def list_meetings(
    project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> MeetingsPage:
    project, role = require_project(db, user, project_id, "viewer")
    rows = list(
        db.scalars(
            select(Meeting)
            .where(Meeting.project_id == project.id)
            .order_by(Meeting.starts_at, Meeting.created_at, Meeting.id)
            .limit(LIST_LIMIT)
        )
    )
    return MeetingsPage(items=_render(db, rows, user, project, role), total=len(rows))


@router.post("/meetings", response_model=MeetingOut, status_code=201)
def create_meeting(
    project_id: uuid.UUID,
    payload: MeetingCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MeetingOut:
    project, role = _writable_project(db, user, project_id)
    meeting = Meeting(
        workspace_id=project.workspace_id,
        project_id=project.id,
        title=payload.title,
        starts_at=payload.starts_at,
        location=payload.location,
        created_by=user.id,
        version=1,
    )
    db.add(meeting)
    db.flush()
    _event(db, "created", project, meeting, user)
    db.commit()
    db.refresh(meeting)
    return _render(db, [meeting], user, project, role)[0]


@router.patch("/meetings/{meeting_id}", response_model=MeetingOut)
def update_meeting(
    project_id: uuid.UUID,
    meeting_id: uuid.UUID,
    payload: MeetingPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MeetingOut:
    project, role = _writable_project(db, user, project_id)
    meeting = _load(db, project_id, meeting_id, lock=True)
    if meeting.version != payload.expected_version:
        raise ApiError(
            409,
            "version_conflict",
            "the meeting changed since it was loaded",
            {"current_version": meeting.version},
        )
    sent = payload.model_fields_set
    changed = []
    for name in ("title", "starts_at", "location"):
        if name in sent and getattr(meeting, name) != getattr(payload, name):
            setattr(meeting, name, getattr(payload, name))
            changed.append(name)
    if not changed:
        return _render(db, [meeting], user, project, role)[0]
    meeting.version += 1
    _event(db, "updated", project, meeting, user, {"changed": changed})
    db.commit()
    db.refresh(meeting)
    return _render(db, [meeting], user, project, role)[0]


@router.delete("/meetings/{meeting_id}", status_code=204)
def delete_meeting(
    project_id: uuid.UUID,
    meeting_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    project, role = _writable_project(db, user, project_id)
    meeting = _load(db, project_id, meeting_id, lock=True)
    if meeting.created_by != user.id and ROLE_RANK[role] < ROLE_RANK["admin"]:
        raise ApiError(403, "forbidden", "only the creator or an admin can delete a meeting")
    _event(db, "deleted", project, meeting, user)
    db.delete(meeting)
    db.commit()
