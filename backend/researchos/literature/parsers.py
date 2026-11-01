"""Pure parsers: upstream bytes in, normalized Records out. Missing data stays missing."""

import html
import json
import re
from xml.etree.ElementTree import ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from researchos.literature.ids import (
    arxiv_id_from_doi,
    normalize_arxiv_id,
    normalize_doi,
    safe_url,
)
from researchos.literature.types import BadResponse, Record

ATOM = "{http://www.w3.org/2005/Atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
ARXIV = "{http://arxiv.org/schemas/atom}"

_BLOCKS = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
_TAGS = re.compile(r"</?[A-Za-z][^<>]*>")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MAX_ABSTRACT_WORDS = 20_000


def plain(value: object, limit: int = 20_000) -> str | None:
    """Text only: markup (Crossref JATS, stray HTML) is removed, never interpreted."""
    if not isinstance(value, str):
        return None
    text = _BLOCKS.sub(" ", value)
    text = _TAGS.sub(" ", text) if "<" in text else text
    text = html.unescape(text).replace("\xa0", " ").replace("&nbsp;", " ")
    text = _CONTROL.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Tags that were only separators should not leave "word ," gaps behind.
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text[:limit] or None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _year(value: object) -> int | None:
    year = _int(value)
    return year if year is not None and 1000 <= year <= 3000 else None


# --- arXiv -------------------------------------------------------------------------


def parse_arxiv(body: bytes) -> tuple[list[Record], int | None]:
    try:
        root = fromstring(body, forbid_dtd=True)
    except (ParseError, DefusedXmlException, ValueError) as exc:
        raise BadResponse("arXiv returned XML that could not be read") from exc
    if root.tag != f"{ATOM}feed":
        raise BadResponse("arXiv returned an unexpected document")
    entries = root.findall(f"{ATOM}entry")
    # Bad requests come back as HTTP 200 with a single entry titled "Error".
    for entry in entries:
        entry_id = entry.findtext(f"{ATOM}id") or ""
        if "arxiv.org/api/errors" in entry_id:
            reason = plain(entry.findtext(f"{ATOM}summary")) or "unknown error"
            raise BadResponse(f"arXiv rejected the request: {reason[:200]}")
    total_text = root.findtext(f"{OPENSEARCH}totalResults")
    total = int(total_text) if total_text and total_text.strip().isdigit() else None
    records = [_arxiv_entry(entry) for entry in entries]
    return records, total


def _arxiv_entry(entry) -> Record:
    entry_id = entry.findtext(f"{ATOM}id") or ""
    published = entry.findtext(f"{ATOM}published") or ""
    year = _year(int(published[:4])) if published[:4].isdigit() else None
    authors = [
        name
        for author in entry.findall(f"{ATOM}author")
        if (name := plain(author.findtext(f"{ATOM}name"), 500))
    ]
    landing = pdf = None
    for link in entry.findall(f"{ATOM}link"):
        if link.get("rel") == "alternate":
            landing = safe_url(link.get("href"))
        elif link.get("title") == "pdf":
            pdf = safe_url(link.get("href"))
    return Record(
        source="arxiv",
        title=plain(entry.findtext(f"{ATOM}title"), 2000),
        authors=authors,
        year=year,
        doi=normalize_doi(entry.findtext(f"{ARXIV}doi")),
        arxiv_id=normalize_arxiv_id(entry_id),
        abstract=plain(entry.findtext(f"{ATOM}summary")),
        open_access_url=pdf,
        landing_url=landing,
    )


# --- OpenAlex ----------------------------------------------------------------------


def _json(body: bytes, source: str) -> dict:
    try:
        data = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise BadResponse(f"{source} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise BadResponse(f"{source} returned an unexpected document")
    return data


def parse_openalex_search(body: bytes) -> tuple[list[Record], int | None]:
    data = _json(body, "OpenAlex")
    results = data.get("results")
    if not isinstance(results, list):
        raise BadResponse("OpenAlex response has no results list")
    meta = data.get("meta")
    total = _int(meta.get("count")) if isinstance(meta, dict) else None
    return [_openalex_work(work) for work in results if isinstance(work, dict)], total


def parse_openalex_work(body: bytes) -> Record:
    data = _json(body, "OpenAlex")
    if "id" not in data:
        raise BadResponse("OpenAlex response is not a work")
    return _openalex_work(data)


def _abstract_from_index(index: object) -> str | None:
    if not isinstance(index, dict):
        return None
    positions: dict[int, str] = {}
    for word, places in index.items():
        if not isinstance(places, list):
            continue
        for place in places:
            if isinstance(place, int) and 0 <= place < _MAX_ABSTRACT_WORDS:
                positions[place] = str(word)
    return plain(" ".join(positions[i] for i in sorted(positions)))


def _openalex_work(work: dict) -> Record:
    doi = normalize_doi(work.get("doi"))
    arxiv_id = arxiv_id_from_doi(doi)
    locations = [loc for loc in (work.get("locations") or []) if isinstance(loc, dict)]
    primary = work.get("primary_location")
    primary = primary if isinstance(primary, dict) else {}
    for location in locations + [primary]:
        landing = location.get("landing_page_url")
        if arxiv_id is None and isinstance(landing, str) and "arxiv.org/abs/" in landing:
            arxiv_id = normalize_arxiv_id(landing)
    authors = []
    for authorship in work.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author")
        name = (author.get("display_name") if isinstance(author, dict) else None) or authorship.get(
            "raw_author_name"
        )
        if cleaned := plain(name, 500):
            authors.append(cleaned)
    origin = primary.get("source")
    venue = plain(origin.get("display_name") if isinstance(origin, dict) else None, 1000)
    best = work.get("best_oa_location")
    best = best if isinstance(best, dict) else {}
    open_access = work.get("open_access")
    open_access = open_access if isinstance(open_access, dict) else {}
    return Record(
        source="openalex",
        title=plain(work.get("title") or work.get("display_name"), 2000),
        authors=authors,
        year=_year(work.get("publication_year")),
        venue=venue,
        doi=doi,
        arxiv_id=arxiv_id,
        abstract=_abstract_from_index(work.get("abstract_inverted_index")),
        citations=_int(work.get("cited_by_count")),
        open_access_url=safe_url(best.get("pdf_url"))
        or safe_url(open_access.get("oa_url"))
        or safe_url(best.get("landing_page_url")),
        landing_url=safe_url(primary.get("landing_page_url")),
    )


# --- Crossref ----------------------------------------------------------------------


def parse_crossref_search(body: bytes) -> tuple[list[Record], int | None]:
    data = _json(body, "Crossref")
    message = data.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("items"), list):
        raise BadResponse("Crossref response has no items list")
    items = message["items"]
    total = _int(message.get("total-results"))
    return [_crossref_item(item) for item in items if isinstance(item, dict)], total


def parse_crossref_work(body: bytes) -> Record:
    data = _json(body, "Crossref")
    message = data.get("message")
    if not isinstance(message, dict) or "DOI" not in message:
        raise BadResponse("Crossref response is not a work")
    return _crossref_item(message)


def _first(value: object) -> object:
    return value[0] if isinstance(value, list) and value else None


def _crossref_year(item: dict) -> int | None:
    for key in ("issued", "published", "published-print", "published-online"):
        parts = item.get(key)
        parts = parts.get("date-parts") if isinstance(parts, dict) else None
        first = _first(parts)
        year = _year(_first(first))  # [[null]] means the year is unknown
        if year is not None:
            return year
    return None


def _crossref_item(item: dict) -> Record:
    authors = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        given, family = author.get("given"), author.get("family")
        name = " ".join(p for p in (given, family) if isinstance(p, str) and p.strip())
        if cleaned := plain(name or author.get("name"), 500):
            authors.append(cleaned)
    doi = normalize_doi(item.get("DOI"))
    return Record(
        source="crossref",
        title=plain(_first(item.get("title")), 2000),
        authors=authors,
        year=_crossref_year(item),
        venue=plain(_first(item.get("container-title")), 1000),
        doi=doi,
        arxiv_id=arxiv_id_from_doi(doi),
        abstract=plain(item.get("abstract")),
        citations=_int(item.get("is-referenced-by-count")),
        landing_url=safe_url(item.get("URL")),
    )
