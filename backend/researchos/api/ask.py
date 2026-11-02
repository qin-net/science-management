"""Questions about the papers in a project, answered with evidence (specs/007)."""

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchos import model
from researchos.config import Settings, get_settings
from researchos.db import get_db
from researchos.errors import ApiError, not_found
from researchos.models import Paper, PaperBlock, PaperVersion, Project, User
from researchos.schemas import (
    AnswerSegmentOut,
    AskOut,
    AskRequest,
    AskUsageOut,
    EvidenceOut,
    SummaryRequest,
    UnavailablePaperOut,
)
from researchos.security import current_user, require_project

router = APIRouter(prefix="/projects/{project_id}", tags=["ask"])

MAX_EVIDENCE = 5

SUMMARY_QUESTIONS = {
    "zh": (
        "请用中文概括这篇论文的核心贡献，写成 3 至 5 个短段落，"
        "说明它解决什么问题、提出什么方法、得到什么结果。"
    ),
    "en": (
        "Summarize the core contributions of this paper in 3 to 5 short paragraphs: what problem "
        "it addresses, what method it proposes, and what results it reports."
    ),
}


@dataclass
class Source:
    """A block as the model saw it, under the number the model was told."""

    number: int
    block: PaperBlock
    paper: Paper


def _select_papers(db: Session, project_id: uuid.UUID, ids: list[uuid.UUID] | None) -> list[Paper]:
    query = select(Paper).where(Paper.project_id == project_id)
    if ids is not None:
        wanted = set(ids)
        query = query.where(Paper.id.in_(wanted))
    papers = list(db.scalars(query.order_by(Paper.created_at, Paper.id)))
    if ids is not None and len(papers) != len(set(ids)):
        # One foreign or unknown id refuses the whole request, so nothing leaks about it.
        raise not_found("paper")
    return papers


def _readable(
    db: Session, papers: list[Paper]
) -> tuple[list[tuple[Paper, PaperVersion]], list[UnavailablePaperOut]]:
    usable: list[tuple[Paper, PaperVersion]] = []
    unavailable: list[UnavailablePaperOut] = []
    for paper in papers:
        reason: Literal["no_file", "not_parsed"]
        version = (
            db.get(PaperVersion, paper.current_version_id) if paper.current_version_id else None
        )
        if version is None:
            reason = "no_file"
        elif version.current_parse_job_id is None:
            reason = "not_parsed"
        else:
            usable.append((paper, version))
            continue
        unavailable.append(
            UnavailablePaperOut(paper_id=paper.id, paper_title=paper.title, reason=reason)
        )
    return usable, unavailable


def _build_prompt(sources: list[Source], question: str) -> str:
    lines = ["<documents>"]
    current: uuid.UUID | None = None
    index = 0
    for source in sources:
        if source.paper.id != current:
            if current is not None:
                lines.append("</document>")
            current = source.paper.id
            index += 1
            title = model.xml_attr(source.paper.title or "(untitled)")
            lines.append(f'<document paper="{index}" title="{title}">')
        lines.append(
            f'<block n="{source.number}" paper="{index}" page="{source.block.page_no}">'
            f"{model.xml_text(source.block.text)}</block>"
        )
    if current is not None:
        lines.append("</document>")
    lines.append("</documents>")
    lines.append(f"<question>{model.xml_text(question)}</question>")
    return "\n".join(lines)


def _present(result: model.ModelResult, sources: dict[int, Source], unavailable: list) -> AskOut:
    segments: list[AnswerSegmentOut] = []
    dropped = 0
    if result.answer.answerable:
        for raw in result.answer.segments:
            text = raw.text.strip()
            if not text:
                continue
            evidence: list[EvidenceOut] = []
            seen: set[int] = set()
            for number in raw.block_numbers:
                source = sources.get(number)
                if source is None:
                    dropped += 1
                    continue
                if number in seen or len(evidence) >= MAX_EVIDENCE:
                    continue
                seen.add(number)
                block = source.block
                evidence.append(
                    EvidenceOut(
                        block_id=block.id,
                        paper_id=source.paper.id,
                        paper_title=source.paper.title,
                        version_id=block.paper_version_id,
                        seq=block.seq,
                        page_no=block.page_no,
                        text=block.text,  # from the database, never the model's wording
                    )
                )
            segments.append(
                AnswerSegmentOut(text=text, supported=bool(evidence), evidence=evidence)
            )
        if not segments:
            raise model.ModelFailure(
                ApiError(
                    502, "model_bad_output", "the model returned an answer that cannot be used"
                ),
                "bad_output",
                result.request_id,
            )
    return AskOut(
        answerable=result.answer.answerable,
        segments=segments,
        unavailable=unavailable,
        dropped_citations=dropped,
        model=result.model,
        usage=AskUsageOut(input_tokens=result.input_tokens, output_tokens=result.output_tokens),
    )


def run_cited_question(
    db: Session,
    user: User,
    project: Project,
    settings: Settings,
    *,
    paper_ids: list[uuid.UUID] | None,
    question: str,
    kind: str,
) -> AskOut:
    """Everything after the permission check, shared by questions and summaries."""
    papers = _select_papers(db, project.id, paper_ids)
    usable, unavailable = _readable(db, papers)

    jobs = {version.current_parse_job_id: (paper, version) for paper, version in usable}
    total = (
        db.scalar(
            select(func.coalesce(func.sum(func.char_length(PaperBlock.text)), 0)).where(
                PaperBlock.parse_job_id.in_(list(jobs))
            )
        )
        or 0
    )
    if total == 0:
        # Parsed, but nothing came out of it: say so per paper rather than ask about nothing.
        for paper, _version in usable:
            unavailable.append(
                UnavailablePaperOut(paper_id=paper.id, paper_title=paper.title, reason="no_text")
            )
        raise ApiError(
            422,
            "no_readable_papers",
            "none of the papers has parsed text to answer from",
            {"unavailable": [u.model_dump(mode="json") for u in unavailable]},
        )
    if total > settings.model_context_chars:
        raise ApiError(
            422,
            "context_too_large",
            "the selected papers are too long to ask about at once; choose fewer papers",
            {"characters": total, "limit": settings.model_context_chars},
        )

    blocks = db.scalars(
        select(PaperBlock)
        .where(PaperBlock.parse_job_id.in_(list(jobs)))
        .order_by(PaperBlock.paper_version_id, PaperBlock.seq)
    ).all()
    order = {version.id: position for position, (_p, version) in enumerate(usable)}
    blocks = sorted(blocks, key=lambda b: (order[b.paper_version_id], b.seq))
    by_version = {version.id: paper for paper, version in usable}
    sources = [
        Source(number=n, block=block, paper=by_version[block.paper_version_id])
        for n, block in enumerate(blocks, start=1)
    ]
    prompt = _build_prompt(sources, question)
    lookup = {source.number: source for source in sources}

    model.require_configured(settings)
    call = model.charge(
        db,
        settings,
        user_id=user.id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        kind=kind,
    )
    try:
        result = model.ask(settings, prompt)
        out = _present(result, lookup, unavailable)
    except model.ModelFailure as failure:
        model.finish(db, call, result=None, failure=failure)
        raise failure.error from None
    model.finish(db, call, result=result)
    return out


@router.post("/ask", response_model=AskOut)
def ask(
    project_id: uuid.UUID,
    body: AskRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AskOut:
    project, _ = require_project(db, user, project_id, "member")
    return run_cited_question(
        db, user, project, settings, paper_ids=body.paper_ids, question=body.question, kind="ask"
    )


@router.post("/papers/{paper_id}/summary", response_model=AskOut)
def summarize(
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    body: SummaryRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AskOut:
    project, _ = require_project(db, user, project_id, "member")
    return run_cited_question(
        db,
        user,
        project,
        settings,
        paper_ids=[paper_id],
        question=SUMMARY_QUESTIONS[body.language],
        kind="summary",
    )
