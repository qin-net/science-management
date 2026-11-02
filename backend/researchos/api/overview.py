"""Workspace overview for the home page (specs/005-p0-cockpit.md). Counts only, all from rows."""

import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, nulls_last, select
from sqlalchemy.orm import Session

from researchos.api.tasks import overdue_condition, today_utc
from researchos.db import get_db
from researchos.models import (
    OPEN_TASK_STATUSES,
    TASK_STATUSES,
    Note,
    Paper,
    PaperVersion,
    Project,
    ResearchEvent,
    Task,
    User,
)
from researchos.schemas import (
    Overview,
    OverviewDay,
    OverviewEvent,
    OverviewMyTasks,
    OverviewProject,
    OverviewTask,
    OverviewTotals,
)
from researchos.security import current_user, require_workspace

router = APIRouter(prefix="/workspaces", tags=["overview"])

LIST_LIMIT = 20
ACTIVITY_DAYS = 14
# Event type prefix -> the column it is counted under.
ACTIVITY_KINDS = {"paper": "papers", "note": "notes", "task": "tasks", "project": "projects"}


@router.get("/{workspace_id}/overview", response_model=Overview)
def overview(
    workspace_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Overview:
    require_workspace(db, user, workspace_id)
    today = today_utc()
    projects = list(
        db.scalars(
            select(Project)
            .where(Project.workspace_id == workspace_id)
            .order_by(Project.created_at.desc())
        )
    )
    names = {p.id: p.name for p in projects}
    active = {p.id for p in projects if p.status == "active"}

    # One grouped query per kind of count, not one query per project.
    papers: dict[uuid.UUID, int] = dict(
        db.execute(
            select(Paper.project_id, func.count())
            .where(Paper.workspace_id == workspace_id)
            .group_by(Paper.project_id)
        ).all()
    )
    parsed: dict[uuid.UUID, int] = dict(
        db.execute(
            select(Paper.project_id, func.count())
            .join(PaperVersion, PaperVersion.id == Paper.current_version_id)
            .where(
                Paper.workspace_id == workspace_id, PaperVersion.current_parse_job_id.is_not(None)
            )
            .group_by(Paper.project_id)
        ).all()
    )
    noted: dict[uuid.UUID, int] = dict(
        db.execute(
            select(Note.project_id, func.count(func.distinct(Note.paper_id)))
            .where(Note.workspace_id == workspace_id, Note.paper_id.is_not(None))
            .group_by(Note.project_id)
        ).all()
    )
    by_status: dict[tuple[uuid.UUID, str], int] = {
        (project_id, status): count
        for project_id, status, count in db.execute(
            select(Task.project_id, Task.status, func.count())
            .where(Task.workspace_id == workspace_id)
            .group_by(Task.project_id, Task.status)
        )
    }
    late: dict[uuid.UUID, int] = dict(
        db.execute(
            select(Task.project_id, func.count())
            .where(Task.workspace_id == workspace_id, overdue_condition(today))
            .group_by(Task.project_id)
        ).all()
    )
    last_seen: dict[uuid.UUID, datetime] = {
        project_id: stamp
        for project_id, stamp in db.execute(
            select(ResearchEvent.project_id, func.max(ResearchEvent.created_at))
            .where(
                ResearchEvent.workspace_id == workspace_id, ResearchEvent.project_id.is_not(None)
            )
            .group_by(ResearchEvent.project_id)
        )
        if project_id is not None
    }

    cards = []
    for project in projects:
        counts = {s: by_status.get((project.id, s), 0) for s in TASK_STATUSES}
        counts["overdue"] = late.get(project.id, 0)
        cards.append(
            OverviewProject(
                id=project.id,
                name=project.name,
                status=project.status,
                paper_count=papers.get(project.id, 0),
                parsed_paper_count=parsed.get(project.id, 0),
                noted_paper_count=noted.get(project.id, 0),
                tasks=counts,
                last_activity_at=last_seen.get(project.id),
            )
        )

    # Archived projects are read-only history: their tasks are not work to do.
    totals = OverviewTotals(
        active_projects=len(active),
        archived_projects=len(projects) - len(active),
        papers=sum(papers.values()),
        open_tasks=sum(by_status.get((pid, s), 0) for pid in active for s in OPEN_TASK_STATUSES),
        overdue_tasks=sum(late.get(pid, 0) for pid in active),
        # Papers are counted in every project, archived or not, like the paper total.
        parsed_papers=sum(parsed.values()),
        noted_papers=sum(noted.values()),
    )

    mine = [
        Task.assignee_id == user.id,
        Task.project_id.in_(active) if active else Task.id.is_(None),
        Task.status.in_(OPEN_TASK_STATUSES),
    ]
    my_total = db.scalar(select(func.count()).select_from(Task).where(*mine)) or 0
    my_rows = db.scalars(
        select(Task)
        .where(*mine)
        # Every row is open, so an overdue task is exactly one due before today: ascending due
        # date already puts the overdue ones first, and undated tasks last.
        .order_by(nulls_last(Task.due_date.asc()), Task.created_at.desc(), Task.id)
        .limit(LIST_LIMIT)
    )
    my_items = [
        OverviewTask(
            id=t.id,
            title=t.title,
            status=t.status,
            priority=t.priority,
            due_date=t.due_date,
            overdue=t.due_date is not None and t.due_date < today,
            project_id=t.project_id,
            project_name=names[t.project_id],
        )
        for t in my_rows
    ]

    event_rows = db.execute(
        select(ResearchEvent, User.display_name)
        .outerjoin(User, User.id == ResearchEvent.actor_id)
        .where(ResearchEvent.workspace_id == workspace_id, ResearchEvent.project_id.is_not(None))
        .order_by(ResearchEvent.created_at.desc(), ResearchEvent.id)
        .limit(LIST_LIMIT)
    ).all()
    recent = [
        OverviewEvent(
            id=event.id,
            event_type=event.event_type,
            project_id=event.project_id,
            project_name=names.get(event.project_id) if event.project_id else None,
            actor_name=actor_name,
            created_at=event.created_at,
        )
        for event, actor_name in event_rows
    ]
    return Overview(
        today=today,
        activity=_activity(db, workspace_id, today),
        totals=totals,
        projects=cards,
        my_tasks=OverviewMyTasks(total=my_total, items=my_items),
        recent_events=recent,
    )


def _activity(db: Session, workspace_id: uuid.UUID, today: date) -> list[OverviewDay]:
    """Events per UTC day for the last ACTIVITY_DAYS days, one entry per day even when empty."""
    first = today - timedelta(days=ACTIVITY_DAYS - 1)
    start = datetime.combine(first, time.min, tzinfo=UTC)
    day = func.date(func.timezone("UTC", ResearchEvent.created_at))
    kind = func.split_part(ResearchEvent.event_type, ".", 1)
    rows = db.execute(
        select(day, kind, func.count())
        .where(
            ResearchEvent.workspace_id == workspace_id,
            ResearchEvent.project_id.is_not(None),
            ResearchEvent.created_at >= start,
        )
        .group_by(day, kind)
    ).all()
    counts: dict[tuple[date, str], int] = {(d, k): n for d, k, n in rows}
    days = []
    for offset in range(ACTIVITY_DAYS):
        d = first + timedelta(days=offset)
        by_kind = {column: counts.get((d, prefix), 0) for prefix, column in ACTIVITY_KINDS.items()}
        days.append(OverviewDay(date=d, total=sum(by_kind.values()), **by_kind))
    return days
