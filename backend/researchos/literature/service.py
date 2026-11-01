"""Search and import orchestration. Talks to the gateway, the merge step and the database."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from researchos.config import Settings
from researchos.errors import ApiError
from researchos.literature import gateway
from researchos.literature import sources as http
from researchos.literature.ids import arxiv_id_from_doi, normalize_arxiv_id, normalize_doi
from researchos.literature.merge import Merged, merge, title_key
from researchos.literature.types import Record, SourceOutcome
from researchos.models import Paper

RANKING = "reciprocal_rank_fusion"
LOOKUP_TTL_SECONDS = 24 * 3600
FAILED = {
    "timeout",
    "unavailable",
    "rate_limited",
    "quota_exhausted",
    "cooling_down",
    "throttled",
    "bad_request",
    "bad_response",
}


def charge_user(db: Session, user_id: uuid.UUID, sources: list[str], limit: int) -> None:
    """Count this call against the user's per-minute allowance, or refuse it. Runs before any
    upstream request, and commits so that no transaction stays open during the network calls."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:u))"), {"u": str(user_id)})
    row = db.execute(
        text(
            "SELECT count(*) AS n, min(created_at) AS oldest, now() AS db_now FROM search_calls "
            "WHERE user_id = :u AND created_at > now() - interval '1 minute'"
        ),
        {"u": user_id},
    ).one()
    if row.n >= limit:
        retry = max(1, int(60 - (row.db_now - row.oldest).total_seconds()) + 1)
        db.rollback()
        raise ApiError(
            429,
            "search_rate_limited",
            "too many searches; wait a moment and try again",
            {"retry_after": retry},
        )
    db.execute(
        text(
            "INSERT INTO search_calls (id, user_id, created_at, sources) "
            "VALUES (:id, :u, now(), CAST(:s AS jsonb))"
        ),
        {"id": uuid.uuid4(), "u": user_id, "s": json.dumps(sources)},
    )
    # Housekeeping: the ledger only needs the last minute.
    db.execute(text("DELETE FROM search_calls WHERE created_at < now() - interval '1 hour'"))
    db.commit()


def _search_one(settings: Settings, source: str, q: dict) -> SourceOutcome:
    request = http.build_search(
        settings, source, q["query"], q["limit"], q["year_from"], q["year_to"]
    )
    key = gateway.cache_key(source, "search", q)
    return gateway.call_source(
        settings,
        source,
        "search",
        key,
        settings.search_cache_ttl_seconds,
        request,
        lambda body: http.parse_search(source, body),
    )


def run_search(settings: Settings, sources: list[str], q: dict) -> list[SourceOutcome]:
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = [pool.submit(_search_one, settings, s, q) for s in sources]
        return [f.result() for f in futures]


def outcome_label(outcomes: list[SourceOutcome], any_results: bool) -> str:
    ok = [o for o in outcomes if o.status == "ok"]
    if not ok:
        return "all_failed"
    if any_results:
        return "partial" if len(ok) < len(outcomes) else "results"
    return "partial" if len(ok) < len(outcomes) else "no_match"


def source_view(settings: Settings, outcome: SourceOutcome, quota: dict | None) -> dict:
    view = {
        "status": outcome.status,
        "count": len(outcome.records),
        "total": outcome.total,
        "cached": outcome.cached,
        "fetched_at": outcome.fetched_at,
        "duration_ms": outcome.duration_ms,
        "message": outcome.message,
        **outcome.extra,
    }
    if outcome.source == "openalex":
        view["quota"] = {**(quota or {}), "shared": not settings.openalex_api_key}
    return view


def openalex_quota(db: Session) -> dict:
    row = db.execute(
        text(
            "SELECT quota_remaining_usd, quota_reset_at FROM source_state WHERE source = 'openalex'"
        )
    ).first()
    return {
        "remaining_usd": row.quota_remaining_usd if row else None,
        "reset_at": row.quota_reset_at.isoformat() if row and row.quota_reset_at else None,
    }


def project_index(
    db: Session, project_id: uuid.UUID
) -> tuple[dict[str, uuid.UUID], dict[str, list[uuid.UUID]]]:
    """DOI / arXiv key -> paper id, and normalized title -> paper ids, for one project only."""
    by_key: dict[str, uuid.UUID] = {}
    by_title: dict[str, list[uuid.UUID]] = {}
    for paper in db.execute(
        select(Paper.id, Paper.title, Paper.doi, Paper.arxiv_id).where(
            Paper.project_id == project_id
        )
    ):
        if paper.doi:
            by_key[f"doi:{paper.doi}"] = paper.id
            if via := arxiv_id_from_doi(paper.doi):
                by_key[f"arxiv:{via}"] = paper.id
        if paper.arxiv_id:
            by_key[f"arxiv:{paper.arxiv_id}"] = paper.id
        if key := title_key(paper.title):
            by_title.setdefault(key, []).append(paper.id)
    return by_key, by_title


def result_view(merged: Merged, by_key: dict, by_title: dict) -> dict:
    in_project = next((by_key[k] for k in merged.keys if k in by_key), None)
    similar = [] if in_project else list(by_title.get(title_key(merged.title) or "", []))
    return {
        "title": merged.title,
        "authors": merged.authors,
        "year": merged.year,
        "venue": merged.venue,
        "doi": merged.doi,
        "arxiv_id": merged.arxiv_id,
        "abstract": merged.abstract,
        "sources": merged.sources,
        "provenance": merged.provenance,
        "conflicts": merged.conflicts,
        "missing": merged.missing,
        "citations": merged.citations,
        "open_access_url": merged.open_access_url,
        "landing_url": merged.landing_url,
        "in_project": in_project,
        "similar_to": merged.similar_to,
        "similar_papers": similar,
    }


def search(db: Session, settings: Settings, project_id: uuid.UUID, params: dict) -> dict:
    sources = params["sources"]
    q = {k: params[k] for k in ("query", "limit", "year_from", "year_to")}
    outcomes = run_search(settings, sources, q)
    per_source = {o.source: o.records for o in outcomes if o.status == "ok"}
    merged = merge(per_source)
    by_key, by_title = project_index(db, project_id)
    quota = openalex_quota(db) if "openalex" in sources else None
    return {
        "outcome": outcome_label(outcomes, bool(merged)),
        "ranking": RANKING,
        "sources": {o.source: source_view(settings, o, quota) for o in outcomes},
        "results": [result_view(m, by_key, by_title) for m in merged],
    }


# --- import by identifier ----------------------------------------------------------------


def _lookup(
    settings: Settings, source: str, identifier: str
) -> tuple[SourceOutcome, Record | None]:
    request = http.build_lookup(settings, source, identifier)
    key = gateway.cache_key(source, "lookup", {"id": identifier})
    outcome = gateway.call_source(
        settings,
        source,
        "lookup",
        key,
        LOOKUP_TTL_SECONDS,
        request,
        lambda body: (http.parse_lookup(source, body), None),
    )
    record = outcome.records[0] if outcome.status == "ok" and outcome.records else None
    return outcome, record


def _failure_details(outcomes: list[SourceOutcome]) -> dict:
    return {
        "sources": {
            o.source: {"status": o.status, "message": o.message, **o.extra} for o in outcomes
        }
    }


def fetch_metadata(settings: Settings, doi: str | None, arxiv_id: str | None) -> Record:
    """Look the identifier up at the sources. The returned record is always the one that was
    asked for; a different record is never substituted."""
    if doi and (via := arxiv_id_from_doi(doi)):
        doi, arxiv_id = None, via
    outcomes: list[SourceOutcome] = []
    if arxiv_id:
        outcome, record = _lookup(settings, "arxiv", arxiv_id)
        outcomes.append(outcome)
        if record is not None and record.arxiv_id == arxiv_id:
            return record
        if outcome.status == "ok" or outcome.status == "not_found":
            raise ApiError(404, "arxiv_not_found", "arXiv has no paper with this ID")
        raise ApiError(
            502, "sources_unavailable", "no source could answer", _failure_details(outcomes)
        )
    assert doi
    for source in ("crossref", "openalex"):
        outcome, record = _lookup(settings, source, doi)
        outcomes.append(outcome)
        if record is not None and normalize_doi(record.doi) == doi:
            return record
        if record is not None:
            # OpenAlex has been seen answering a DOI lookup with another paper's record.
            outcome.status = "not_found"
            outcome.message = f"{gateway.NAMES[source]} 返回了 DOI 不同的记录，已忽略"
    if all(o.status == "not_found" for o in outcomes):
        raise ApiError(404, "doi_not_found", "no source has a paper with this DOI")
    raise ApiError(502, "sources_unavailable", "no source could answer", _failure_details(outcomes))


def requested_identifier(
    payload_doi: str | None, payload_arxiv: str | None
) -> tuple[str | None, str | None]:
    if (payload_doi is None) == (payload_arxiv is None):
        raise ApiError(422, "validation_error", "send exactly one of doi or arxiv_id")
    if payload_doi is not None:
        doi = normalize_doi(payload_doi)
        if doi is None:
            raise ApiError(422, "invalid_doi", "DOI must look like 10.1234/abc")
        return doi, None
    arxiv_id = normalize_arxiv_id(payload_arxiv)
    if arxiv_id is None:
        raise ApiError(422, "invalid_arxiv_id", "arXiv ID must look like 1706.03762")
    return None, arxiv_id


def existing_paper(db: Session, project_id: uuid.UUID, doi: str | None, arxiv_id: str | None):
    conditions = []
    if doi:
        conditions.append(Paper.doi == doi)
        if via := arxiv_id_from_doi(doi):
            conditions.append(Paper.arxiv_id == via)
    if arxiv_id:
        conditions.append(Paper.arxiv_id == arxiv_id)
        conditions.append(Paper.doi == f"10.48550/arxiv.{arxiv_id}")
    if not conditions:
        return None
    return db.scalar(select(Paper.id).where(Paper.project_id == project_id, or_(*conditions)))
