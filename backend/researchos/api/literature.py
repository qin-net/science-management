"""Literature search and metadata import (specs/002)."""

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from researchos import events
from researchos.api.papers import _paper_out
from researchos.config import get_settings
from researchos.db import get_db
from researchos.errors import ApiError
from researchos.literature import service
from researchos.literature.ids import arxiv_id_from_doi
from researchos.literature.types import SOURCES, Record
from researchos.models import Paper, User
from researchos.schemas import ImportIn, PaperOut, SearchIn
from researchos.security import current_user, require_writable_project

router = APIRouter(prefix="/projects/{project_id}", tags=["literature"])

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _invalid(message: str, field: str) -> ApiError:
    return ApiError(422, "validation_error", message, {"fields": [{"loc": field, "msg": message}]})


def _validated(payload: SearchIn) -> dict:
    query = payload.query.strip()
    if not query or len(query) > 300:
        raise _invalid("query must be 1 to 300 characters", "query")
    if _CONTROL.search(query):
        raise _invalid("query must not contain control characters", "query")
    sources = list(SOURCES) if payload.sources is None else payload.sources
    if not sources or len(set(sources)) != len(sources) or not set(sources) <= set(SOURCES):
        raise _invalid("sources must be a non-empty list of distinct known sources", "sources")
    ceiling = datetime.now(UTC).year + 1
    for name in ("year_from", "year_to"):
        value = getattr(payload, name)
        if value is not None and value > ceiling:
            raise _invalid(f"{name} must not be after {ceiling}", name)
    if (
        payload.year_from is not None
        and payload.year_to is not None
        and payload.year_from > payload.year_to
    ):
        raise _invalid("year_from must not be after year_to", "year_from")
    return {
        "query": query,
        "sources": sources,
        "limit": payload.limit,
        "year_from": payload.year_from,
        "year_to": payload.year_to,
    }


@router.post("/papers/search")
def search_papers(
    project_id: uuid.UUID,
    payload: SearchIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    params = _validated(payload)
    settings = get_settings()
    require_writable_project(db, user, project_id)
    service.charge_user(db, user.id, params["sources"], settings.search_user_per_minute)
    return service.search(db, settings, project_id, params)


def _record_fields(record: Record) -> dict:
    fields = {
        "title": record.title,
        "authors": record.authors or None,
        "year": record.year,
        "doi": record.doi,
        "arxiv_id": record.arxiv_id,
    }
    return {k: v for k, v in fields.items() if v}


@router.post("/papers/import", status_code=201, response_model=PaperOut)
def import_paper(
    project_id: uuid.UUID,
    payload: ImportIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PaperOut:
    settings = get_settings()
    doi, arxiv_id = service.requested_identifier(payload.doi, payload.arxiv_id)
    project = require_writable_project(db, user, project_id)
    if (existing := service.existing_paper(db, project_id, doi, arxiv_id)) is not None:
        raise _duplicate(existing)
    service.charge_user(db, user.id, ["import"], settings.search_user_per_minute)
    record = service.fetch_metadata(settings, doi, arxiv_id)
    if not record.title:
        raise ApiError(422, "record_incomplete", "the source record has no title")

    fields = _record_fields(record)
    # The lookup key is the identifier the user asked for, even if the record spells it
    # differently (an arXiv DOI is stored as the arXiv ID, never as a second identifier).
    if via := arxiv_id_from_doi(fields.get("doi")):
        fields.pop("doi")
        fields["arxiv_id"] = via
    paper = Paper(
        workspace_id=project.workspace_id,
        project_id=project.id,
        created_by=user.id,
        abstract=record.abstract,
        venue=record.venue,
        source_links={
            k: v
            for k, v in (
                ("open_access_url", record.open_access_url),
                ("landing_url", record.landing_url),
            )
            if v
        }
        or None,
        metadata_provenance={
            **{name: record.source for name in fields},
            **{n: record.source for n in ("abstract", "venue") if getattr(record, n)},
        },
        **{k: v for k, v in fields.items() if k != "authors"},
        authors=fields.get("authors", []),
    )
    db.add(paper)
    try:
        db.flush()
        events.record(
            db,
            event_type="paper.imported",
            object_type="paper",
            object_id=paper.id,
            workspace_id=paper.workspace_id,
            project_id=paper.project_id,
            actor_id=user.id,
            payload={"method": "metadata", "source": record.source},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = service.existing_paper(db, project_id, paper.doi, paper.arxiv_id)
        raise _duplicate(existing) from exc
    return _paper_out(db, paper)


def _duplicate(paper_id: uuid.UUID | None) -> ApiError:
    return ApiError(
        409,
        "duplicate_paper",
        "this paper is already in the project",
        {"paper_id": str(paper_id)} if paper_id else {},
    )
