"""Passwords, sessions, CSRF and project-level authorization."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from researchos.config import get_settings
from researchos.db import get_db
from researchos.errors import ApiError
from researchos.models import AuthSession, Project, User, WorkspaceMember

COOKIE_NAME = "ros_session"
ROLE_RANK = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}
_hasher = PasswordHasher()


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(stored: str, raw: str) -> bool:
    try:
        return _hasher.verify(stored, raw)
    except (VerifyMismatchError, VerificationError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    ttl = timedelta(hours=get_settings().session_ttl_hours)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=token_hash(token),
            expires_at=datetime.now(UTC) + ttl,
        )
    )
    return token


def revoke_session(db: Session, token: str) -> None:
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)


def csrf_guard(request: Request) -> None:
    """Cookie sessions are SameSite=Lax; state-changing calls from a browser must also come
    from an allowed origin. Requests without Origin are not browser form posts."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if not origin:
        return
    origin = origin.rstrip("/")
    same_host = origin.split("://", 1)[-1] == request.headers.get("host", "")
    if not same_host and origin not in get_settings().origin_allowlist:
        raise ApiError(403, "csrf_rejected", "cross-origin request rejected")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise ApiError(401, "not_authenticated", "sign in to continue")
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
        raise ApiError(401, "session_expired", "session is no longer valid; sign in again")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise ApiError(401, "not_authenticated", "account is not active")
    request.state.user = user
    return user


def workspace_role(db: Session, user_id: uuid.UUID, workspace_id: uuid.UUID) -> str | None:
    return db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.user_id == user_id, WorkspaceMember.workspace_id == workspace_id
        )
    )


def require_workspace(
    db: Session, user: User, workspace_id: uuid.UUID, minimum: str = "viewer"
) -> str:
    role = workspace_role(db, user.id, workspace_id)
    if role is None:
        # Same answer for "absent" and "not yours": workspace existence does not leak.
        raise ApiError(404, "not_found", "workspace not found")
    if ROLE_RANK[role] < ROLE_RANK[minimum]:
        raise ApiError(403, "forbidden", f"this action needs the {minimum} role")
    return role


def require_project(
    db: Session, user: User, project_id: uuid.UUID, minimum: str = "viewer"
) -> tuple[Project, str]:
    project = db.get(Project, project_id)
    if project is None:
        raise ApiError(404, "not_found", "project not found")
    role = require_workspace(db, user, project.workspace_id, minimum)
    return project, role


def require_writable_project(db: Session, user: User, project_id: uuid.UUID) -> Project:
    project, _ = require_project(db, user, project_id, "member")
    if project.status != "active":
        raise ApiError(409, "project_archived", "archived projects are read-only")
    return project
