"""Creating parse jobs, shared by the upload and manual re-parse paths."""

import uuid

from sqlalchemy.orm import Session

from researchos import events
from researchos.config import get_settings
from researchos.errors import ApiError
from researchos.models import Job, PaperVersion, User
from researchos.parsers.registry import parser_commands

PARSE_KIND = "paper.parse"


def enqueue_parse(
    db: Session, version: PaperVersion, user: User, parser: str | None = None
) -> Job:
    settings = get_settings()
    parser = parser or settings.parser_default
    if parser not in parser_commands():
        raise ApiError(
            409,
            "parser_unavailable",
            f"parser {parser!r} is not available on this server",
            {"available": sorted(parser_commands())},
        )
    job = Job(
        id=uuid.uuid4(),
        workspace_id=version.workspace_id,
        project_id=version.project_id,
        kind=PARSE_KIND,
        subject_id=version.id,
        parser=parser,
        max_attempts=settings.parse_max_attempts,
        created_by=user.id,
    )
    db.add(job)
    events.record(
        db,
        event_type="paper.parse_queued",
        object_type="paper_version",
        object_id=version.id,
        workspace_id=version.workspace_id,
        project_id=version.project_id,
        actor_id=user.id,
        payload={"job_id": str(job.id), "parser": parser},
    )
    return job
