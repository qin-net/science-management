"""Workspaces and their members. Membership is the permission boundary in P0 (D-008)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from researchos import events
from researchos.db import get_db
from researchos.errors import ApiError, not_found
from researchos.models import User, Workspace, WorkspaceMember
from researchos.schemas import MemberIn, MemberOut, RoleIn, WorkspaceIn, WorkspaceOut
from researchos.security import current_user, require_workspace

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _member_out(member: WorkspaceMember, user: User) -> MemberOut:
    return MemberOut(
        user_id=user.id, email=user.email, display_name=user.display_name, role=member.role
    )


def _lock_owners(db: Session, workspace_id: uuid.UUID) -> list[WorkspaceMember]:
    """Serialize owner changes, so two owners cannot demote each other at the same time."""
    return list(
        db.scalars(
            select(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner")
            .with_for_update()
        )
    )


def _load_member(db: Session, workspace_id: uuid.UUID, user_id: uuid.UUID) -> WorkspaceMember:
    member = db.get(WorkspaceMember, (workspace_id, user_id), with_for_update=True)
    if member is None:
        raise not_found("member")
    return member


def _guard_owner_change(
    db: Session, actor_role: str, member: WorkspaceMember, new_role: str | None
) -> None:
    if (member.role == "owner" or new_role == "owner") and actor_role != "owner":
        raise ApiError(403, "forbidden", "only an owner can grant or remove the owner role")
    if member.role == "owner" and new_role != "owner":
        owners = _lock_owners(db, member.workspace_id)
        if len(owners) == 1:
            raise ApiError(409, "last_owner", "a workspace must keep at least one owner")


def _record(db: Session, event_type: str, workspace_id, target, actor, payload) -> None:
    events.record(
        db,
        event_type=event_type,
        object_type="workspace_member",
        object_id=target,
        workspace_id=workspace_id,
        project_id=None,
        actor_id=actor,
        payload=payload,
    )


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[WorkspaceOut]:
    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at)
    )
    return [WorkspaceOut(id=ws.id, name=ws.name, role=role) for ws, role in rows]


@router.post("", status_code=201, response_model=WorkspaceOut)
def create_workspace(
    payload: WorkspaceIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> WorkspaceOut:
    workspace = Workspace(name=payload.name.strip(), created_by=user.id)
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    events.record(
        db,
        event_type="workspace.created",
        object_type="workspace",
        object_id=workspace.id,
        workspace_id=workspace.id,
        project_id=None,
        actor_id=user.id,
    )
    db.commit()
    return WorkspaceOut(id=workspace.id, name=workspace.name, role="owner")


@router.get("/{workspace_id}/members", response_model=list[MemberOut])
def list_members(
    workspace_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[MemberOut]:
    require_workspace(db, user, workspace_id)
    rows = db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.created_at)
    )
    return [_member_out(member, member_user) for member, member_user in rows]


@router.post("/{workspace_id}/members", status_code=201, response_model=MemberOut)
def add_member(
    workspace_id: uuid.UUID,
    payload: MemberIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MemberOut:
    actor_role = require_workspace(db, user, workspace_id, "admin")
    if payload.role == "owner" and actor_role != "owner":
        raise ApiError(403, "forbidden", "only an owner can grant the owner role")
    target = db.scalar(select(User).where(User.email == payload.email.lower()))
    if target is None:
        raise ApiError(404, "user_not_found", "no registered user has this email")
    member = WorkspaceMember(workspace_id=workspace_id, user_id=target.id, role=payload.role)
    db.add(member)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "already_member", "this user is already a member") from exc
    _record(db, "member.added", workspace_id, target.id, user.id, {"role": payload.role})
    db.commit()
    return _member_out(member, target)


@router.patch("/{workspace_id}/members/{user_id}", response_model=MemberOut)
def change_role(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: RoleIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MemberOut:
    actor_role = require_workspace(db, user, workspace_id, "admin")
    member = _load_member(db, workspace_id, user_id)
    _guard_owner_change(db, actor_role, member, payload.role)
    previous = member.role
    member.role = payload.role
    _record(
        db,
        "member.role_changed",
        workspace_id,
        user_id,
        user.id,
        {"from": previous, "to": payload.role},
    )
    db.commit()
    return _member_out(member, db.get_one(User, user_id))


@router.delete("/{workspace_id}/members/{user_id}", status_code=204)
def remove_member(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    actor_role = require_workspace(db, user, workspace_id, "admin")
    member = _load_member(db, workspace_id, user_id)
    _guard_owner_change(db, actor_role, member, None)
    role = member.role
    db.delete(member)
    _record(db, "member.removed", workspace_id, user_id, user.id, {"role": role})
    db.commit()
