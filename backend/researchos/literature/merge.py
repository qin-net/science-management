"""Merge records from several sources into one view per paper. Pure functions, no I/O.

Records are joined only by DOI and arXiv ID (connected components, so the result does not
depend on the order sources answered in). Matching titles never merge anything.
"""

import hashlib
import re
from dataclasses import dataclass, field

from researchos.literature.ids import arxiv_id_from_doi
from researchos.literature.types import SOURCES, Record

RRF_K = 60
# First non-empty value wins, in this order (spec 2.4).
PRIORITY = {
    "title": ("arxiv", "crossref", "openalex"),
    "authors": ("arxiv", "crossref", "openalex"),
    "year": ("crossref", "openalex", "arxiv"),
    "venue": ("crossref", "openalex", "arxiv"),
    "doi": ("crossref", "openalex", "arxiv"),
    "arxiv_id": ("arxiv", "openalex", "crossref"),
    "abstract": ("arxiv", "crossref", "openalex"),
    "open_access_url": ("openalex", "arxiv", "crossref"),
    "landing_url": ("openalex", "arxiv", "crossref"),
}
REPORTED_MISSING = ("title", "authors", "year", "venue", "doi", "abstract")
_TITLE_DROP = re.compile(r"[\W_]+", re.UNICODE)


def title_key(title: str | None) -> str | None:
    if not title:
        return None
    return _TITLE_DROP.sub("", title.casefold()) or None


@dataclass
class Merged:
    id: str
    keys: list[str]
    title: str | None
    authors: list[str]
    year: int | None
    venue: str | None
    doi: str | None
    arxiv_id: str | None
    abstract: str | None
    sources: list[str]
    provenance: dict[str, str]
    conflicts: dict[str, dict[str, int]]
    missing: list[str]
    citations: dict[str, int]
    open_access_url: str | None
    landing_url: str | None
    score: float = 0.0
    similar_to: list[str] = field(default_factory=list)
    similar_papers: list[str] = field(default_factory=list)


def record_keys(record: Record) -> set[str]:
    keys = set()
    if record.doi:
        keys.add(f"doi:{record.doi}")
    arxiv_id = record.arxiv_id or arxiv_id_from_doi(record.doi)
    if arxiv_id:
        keys.add(f"arxiv:{arxiv_id}")
    return keys


def merge(per_source: dict[str, list[Record]]) -> list[Merged]:
    """`per_source[name]` is in the source's own rank order."""
    ranked: list[tuple[Record, int]] = []
    for source in SOURCES:
        for rank, record in enumerate(per_source.get(source, [])):
            ranked.append((record, rank))

    parent = list(range(len(ranked)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for index, (record, _) in enumerate(ranked):
        for key in record_keys(record):
            if key in owner:
                parent[find(index)] = find(owner[key])
            else:
                owner[key] = index

    groups: dict[int, list[tuple[Record, int]]] = {}
    for index, item in enumerate(ranked):
        groups.setdefault(find(index), []).append(item)

    results = [_combine(group) for group in groups.values()]
    results.sort(key=lambda m: (-m.score, -(m.year or 0), (m.title or "").casefold(), m.id))
    _link_similar(results)
    return results


def _combine(group: list[tuple[Record, int]]) -> Merged:
    group.sort(key=lambda item: (item[1], SOURCES.index(item[0].source)))
    records = [record for record, _ in group]

    def pick(name: str):
        for source in PRIORITY[name]:
            for record in records:
                value = getattr(record, name)
                if record.source == source and value:
                    return value, source
        return None, None

    values: dict = {}
    provenance: dict[str, str] = {}
    for name in PRIORITY:
        value, source = pick(name)
        values[name] = value if value is not None else ([] if name == "authors" else None)
        if source:
            provenance[name] = source

    # Several records of one source can land in a group; the best-ranked one speaks for it.
    years: dict[str, int] = {}
    citations: dict[str, int] = {}
    for record in records:
        if record.year is not None:
            years.setdefault(record.source, record.year)
        if record.citations is not None:
            citations.setdefault(record.source, record.citations)
    conflicts = {"year": dict(sorted(years.items()))} if len(set(years.values())) > 1 else {}

    best_rank: dict[str, int] = {}
    for record, rank in group:
        best_rank[record.source] = min(rank, best_rank.get(record.source, rank))
    score = sum(1.0 / (RRF_K + rank + 1) for rank in best_rank.values())

    present = [s for s in SOURCES if s in best_rank]
    keys = sorted(set().union(*(record_keys(r) for r in records)))
    # The id must not depend on which source happened to supply the DOI.
    identity = (
        keys[0]
        if keys
        else "title:"
        + hashlib.sha256(
            f"{title_key(values['title'])}|{sorted(best_rank.items())}".encode()
        ).hexdigest()[:16]
    )
    return Merged(
        id=identity,
        keys=keys,
        missing=[name for name in REPORTED_MISSING if not values[name]],
        sources=present,
        provenance=provenance,
        conflicts=conflicts,
        citations=citations,
        score=score,
        **values,
    )


def _link_similar(results: list[Merged]) -> None:
    by_title: dict[str, list[Merged]] = {}
    for merged in results:
        if key := title_key(merged.title):
            by_title.setdefault(key, []).append(merged)
    for same in by_title.values():
        for merged in same:
            merged.similar_to = [other.id for other in same if other is not merged]
