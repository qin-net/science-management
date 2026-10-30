"""Append-only research events. Callers add them inside the same transaction as the change."""

import uuid

from sqlalchemy.orm import Session

from researchos.models import ResearchEvent


def record(
    db: Session,
    *,
    event_type: str,
    object_type: str,
    object_id: uuid.UUID,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    payload: dict | None = None,
) -> ResearchEvent:
    event = ResearchEvent(
        event_type=event_type,
        object_type=object_type,
        object_id=object_id,
        workspace_id=workspace_id,
        project_id=project_id,
        actor_id=actor_id,
        payload=payload or {},
    )
    db.add(event)
    return event
