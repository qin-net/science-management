"""Research projects and their activity log."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from researchos import events
from researchos.db import get_db
from researchos.errors import ApiError
from researchos.models import Project, ResearchEvent, User
from researchos.schemas import EventOut, ProjectIn, ProjectOut, ProjectPatch
from researchos.security import current_user, require_project, require_workspace

router = APIRouter(tags=["projects"])


@router.get("/workspaces/{workspace_id}/projects", response_model=list[ProjectOut])
def list_projects(
    workspace_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[Project]:
    require_workspace(db, user, workspace_id)
    return list(
        db.scalars(
            select(Project)
            .where(Project.workspace_id == workspace_id)
            .order_by(Project.created_at.desc())
        )
    )


@router.post("/workspaces/{workspace_id}/projects", status_code=201, response_model=ProjectOut)
def create_project(
    workspace_id: uuid.UUID,
    payload: ProjectIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Project:
    require_workspace(db, user, workspace_id, "member")
    project = Project(
        workspace_id=workspace_id,
        name=payload.name.strip(),
        goal=payload.goal,
        background=payload.background,
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    events.record(
        db,
        event_type="project.created",
        object_type="project",
        object_id=project.id,
        workspace_id=workspace_id,
        project_id=project.id,
        actor_id=user.id,
        payload={"name": project.name},
    )
    db.commit()
    return project


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Project:
    project, _ = require_project(db, user, project_id)
    return project


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def patch_project(
    project_id: uuid.UUID,
    payload: ProjectPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Project:
    require_project(db, user, project_id, "member")
    project = db.get_one(Project, project_id, with_for_update=True, populate_existing=True)
    if project.version != payload.expected_version:
        raise ApiError(
            409,
            "version_conflict",
            "the project changed since it was loaded",
            {"current_version": project.version},
        )
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    if project.status == "archived" and set(changes) - {"status"}:
        raise ApiError(409, "project_archived", "archived projects are read-only")
    for field, value in changes.items():
        if value is not None:
            setattr(project, field, value)
    project.version += 1
    events.record(
        db,
        event_type="project.updated",
        object_type="project",
        object_id=project.id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        actor_id=user.id,
        payload={"fields": sorted(changes)},
    )
    db.commit()
    return project


@router.get("/projects/{project_id}/events", response_model=list[EventOut])
def list_events(
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ResearchEvent]:
    require_project(db, user, project_id)
    return list(
        db.scalars(
            select(ResearchEvent)
            .where(ResearchEvent.project_id == project_id)
            .order_by(ResearchEvent.created_at.desc())
            .limit(limit)
        )
    )
