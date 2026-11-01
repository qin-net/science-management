"""Project tasks and the board (specs/004-p0-tasks.md)."""

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session

from researchos import events
from researchos.db import get_db
from researchos.errors import ApiError, not_found
from researchos.models import (
    OPEN_TASK_STATUSES,
    TASK_STATUSES,
    Paper,
    Project,
    Task,
    User,
    WorkspaceMember,
)
from researchos.schemas import TaskCreate, TaskOut, TaskPatch, TasksPage
from researchos.security import ROLE_RANK, current_user, require_project

router = APIRouter(prefix="/projects/{project_id}", tags=["tasks"])

# Fields a PATCH may change, in the order they are reported in the event.
PATCHABLE = (
    "title",
    "description",
    "status",
    "priority",
    "blocked_reason",
    "assignee_id",
    "due_date",
    "paper_id",
)


def today_utc() -> date:
    return datetime.now(UTC).date()


def overdue_condition(today: date) -> ColumnElement[bool]:
    """The one SQL definition of overdue (specs/004 2.4); the cockpit counts with it too."""
    return and_(Task.status.in_(OPEN_TASK_STATUSES), Task.due_date < today)


def _overdue(task: Task, today: date) -> bool:
    return task.due_date is not None and task.due_date < today and task.status in OPEN_TASK_STATUSES


def _event_payload(task: Task) -> dict:
    # No title, description or reason: the activity log is read by people who may not need them.
    return {"status": task.status, "paper_id": str(task.paper_id) if task.paper_id else None}


def _render(
    db: Session, tasks: list[Task], user: User, project: Project, role: str
) -> list[TaskOut]:
    today = today_utc()
    people = {t.created_by for t in tasks} | {t.assignee_id for t in tasks if t.assignee_id}
    names: dict[uuid.UUID, str] = {}
    if people:
        names = dict(
            db.execute(select(User.id, User.display_name).where(User.id.in_(people))).all()
        )
    members = set(
        db.scalars(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.workspace_id == project.workspace_id,
                WorkspaceMember.user_id.in_({t.assignee_id for t in tasks if t.assignee_id}),
            )
        )
    )
    paper_ids = {t.paper_id for t in tasks if t.paper_id}
    titles: dict[uuid.UUID, str | None] = {}
    if paper_ids:
        titles = dict(
            db.execute(select(Paper.id, Paper.title).where(Paper.id.in_(paper_ids))).all()
        )

    writable = project.status == "active" and ROLE_RANK[role] >= ROLE_RANK["member"]
    manager = ROLE_RANK[role] >= ROLE_RANK["admin"]
    return [
        TaskOut(
            id=t.id,
            title=t.title,
            description=t.description,
            status=t.status,
            priority=t.priority,
            blocked_reason=t.blocked_reason,
            assignee_id=t.assignee_id,
            assignee_name=names.get(t.assignee_id) if t.assignee_id else None,
            assignee_active=(t.assignee_id in members) if t.assignee_id else None,
            due_date=t.due_date,
            overdue=_overdue(t, today),
            paper_id=t.paper_id,
            paper_title=titles.get(t.paper_id) if t.paper_id else None,
            created_by=t.created_by,
            created_by_name=names.get(t.created_by, ""),
            completed_at=t.completed_at,
            version=t.version,
            editable=writable,
            deletable=writable and (t.created_by == user.id or manager),
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in tasks
    ]


def _load_task(db: Session, project_id: uuid.UUID, task_id: uuid.UUID, lock: bool = False) -> Task:
    query = select(Task).where(Task.id == task_id, Task.project_id == project_id)
    task = db.scalar(query.with_for_update() if lock else query)
    if task is None:
        raise not_found("task")
    return task


def _check_paper(db: Session, project: Project, paper_id: uuid.UUID) -> None:
    found = db.scalar(select(Paper.id).where(Paper.id == paper_id, Paper.project_id == project.id))
    if found is None:
        raise not_found("paper")


def _check_assignee(db: Session, project: Project, user_id: uuid.UUID) -> None:
    found = db.scalar(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == project.workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if found is None:
        raise ApiError(422, "invalid_assignee", "the assignee is not a member of this workspace")


def _check_reason(status: str, reason: str | None) -> None:
    if status == "blocked" and not reason:
        raise ApiError(422, "blocked_reason_required", "a blocked task needs a reason")
    if status != "blocked" and reason is not None:
        raise ApiError(
            422, "blocked_reason_not_allowed", "only a blocked task can have a blocked reason"
        )


def _writable_project(db: Session, user: User, project_id: uuid.UUID) -> tuple[Project, str]:
    project, role = require_project(db, user, project_id, "member")
    if project.status != "active":
        raise ApiError(409, "project_archived", "archived projects are read-only")
    return project, role


@router.get("/tasks", response_model=TasksPage)
def list_tasks(
    project_id: uuid.UUID,
    status: list[str] | None = Query(default=None),
    assignee: str | None = None,
    paper_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TasksPage:
    project, role = require_project(db, user, project_id, "viewer")
    where = [Task.project_id == project.id]
    if status:
        unknown = [s for s in status if s not in TASK_STATUSES]
        if unknown:
            raise ApiError(422, "validation_error", "unknown status", {"status": unknown})
        where.append(Task.status.in_(status))
    if assignee is not None:
        if assignee == "none":
            where.append(Task.assignee_id.is_(None))
        else:
            try:
                where.append(Task.assignee_id == uuid.UUID(assignee))
            except ValueError:
                raise ApiError(422, "validation_error", "assignee is a user id or 'none'") from None
    if paper_id is not None:
        where.append(Task.paper_id == paper_id)

    total = db.scalar(select(func.count()).select_from(Task).where(*where)) or 0
    rows = list(
        db.scalars(
            select(Task)
            .where(*where)
            .order_by(Task.created_at.desc(), Task.id)
            .limit(limit)
            .offset(offset)
        )
    )
    # The counts describe the whole project, so the column headers stay true while filtering.
    grouped = (
        select(Task.status, func.count()).where(Task.project_id == project.id).group_by(Task.status)
    )
    by_status = dict(db.execute(grouped).all())
    counts = {name: by_status.get(name, 0) for name in TASK_STATUSES}
    counts["overdue"] = (
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.project_id == project.id, overdue_condition(today_utc()))
        )
        or 0
    )
    return TasksPage(
        items=_render(db, rows, user, project, role),
        total=total,
        offset=offset,
        limit=limit,
        counts=counts,
    )


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(
    project_id: uuid.UUID,
    payload: TaskCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TaskOut:
    project, role = _writable_project(db, user, project_id)
    _check_reason(payload.status, payload.blocked_reason)
    if payload.paper_id is not None:
        _check_paper(db, project, payload.paper_id)
    if payload.assignee_id is not None:
        _check_assignee(db, project, payload.assignee_id)
    task = Task(
        workspace_id=project.workspace_id,
        project_id=project.id,
        title=payload.title,
        description=payload.description,
        status=payload.status,
        priority=payload.priority,
        blocked_reason=payload.blocked_reason,
        assignee_id=payload.assignee_id,
        due_date=payload.due_date,
        paper_id=payload.paper_id,
        created_by=user.id,
        version=1,
    )
    db.add(task)
    db.flush()
    events.record(
        db,
        event_type="task.created",
        object_type="task",
        object_id=task.id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        actor_id=user.id,
        payload=_event_payload(task),
    )
    db.commit()
    db.refresh(task)
    return _render(db, [task], user, project, role)[0]


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: TaskPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TaskOut:
    project, role = _writable_project(db, user, project_id)
    task = _load_task(db, project_id, task_id, lock=True)
    if task.version != payload.expected_version:
        raise ApiError(
            409,
            "version_conflict",
            "the task changed since it was loaded",
            {"current_version": task.version},
        )
    sent = payload.model_fields_set
    new_status = payload.status if "status" in sent and payload.status else task.status
    if "blocked_reason" in sent:
        new_reason = payload.blocked_reason
    else:
        new_reason = task.blocked_reason if new_status == "blocked" else None
    _check_reason(new_status, new_reason)

    if "paper_id" in sent and payload.paper_id is not None:
        _check_paper(db, project, payload.paper_id)
    if (
        "assignee_id" in sent
        and payload.assignee_id is not None
        and payload.assignee_id != task.assignee_id
    ):
        _check_assignee(db, project, payload.assignee_id)

    previous_status = task.status
    wanted = {
        "title": payload.title,
        "description": payload.description,
        "status": new_status,
        "priority": payload.priority,
        "blocked_reason": new_reason,
        "assignee_id": payload.assignee_id,
        "due_date": payload.due_date,
        "paper_id": payload.paper_id,
    }
    changed = []
    for name in PATCHABLE:
        if name not in sent and name not in ("status", "blocked_reason"):
            continue
        if getattr(task, name) != wanted[name]:
            setattr(task, name, wanted[name])
            changed.append(name)
    if not changed:
        return _render(db, [task], user, project, role)[0]

    if task.status == "done" and previous_status != "done":
        task.completed_at = datetime.now(UTC)
    elif task.status != "done":
        task.completed_at = None
    task.version += 1
    body = _event_payload(task) | {"changed": changed}
    if previous_status != task.status:
        body |= {"from": previous_status, "to": task.status}
    events.record(
        db,
        event_type="task.updated",
        object_type="task",
        object_id=task.id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        actor_id=user.id,
        payload=body,
    )
    db.commit()
    db.refresh(task)
    return _render(db, [task], user, project, role)[0]


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    project, role = _writable_project(db, user, project_id)
    task = _load_task(db, project_id, task_id, lock=True)
    if task.created_by != user.id and ROLE_RANK[role] < ROLE_RANK["admin"]:
        raise ApiError(403, "forbidden", "only the creator or an admin can delete a task")
    events.record(
        db,
        event_type="task.deleted",
        object_type="task",
        object_id=task.id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        actor_id=user.id,
        payload=_event_payload(task),
    )
    db.delete(task)
    db.commit()
