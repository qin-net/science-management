"""Registration, login and session lifecycle."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from researchos import events
from researchos.config import get_settings
from researchos.db import get_db
from researchos.errors import ApiError
from researchos.models import User, Workspace, WorkspaceMember
from researchos.schemas import LoginRequest, RegisterRequest, UserOut
from researchos.security import (
    COOKIE_NAME,
    create_session,
    current_user,
    hash_password,
    revoke_session,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Verified against when the account does not exist, so both failures cost the same.
_DECOY_HASH = hash_password("researchos-decoy-password")


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", status_code=201, response_model=UserOut)
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)) -> User:
    if not get_settings().allow_registration:
        raise ApiError(403, "registration_closed", "registration is disabled on this server")
    user = User(
        email=payload.email.lower(),
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "email_taken", "an account with this email already exists") from exc
    name = payload.workspace_name or f"{user.display_name} 的工作区"
    workspace = Workspace(name=name, created_by=user.id)
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
    token = create_session(db, user)
    db.commit()
    _set_cookie(response, token)
    return user


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> User:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    stored = user.password_hash if user else _DECOY_HASH
    if not verify_password(stored, payload.password) or user is None or not user.is_active:
        raise ApiError(401, "invalid_credentials", "email or password is incorrect")
    token = create_session(db, user)
    db.commit()
    _set_cookie(response, token)
    return user


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    revoke_session(db, request.cookies[COOKIE_NAME])
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user
