"""Request building and bounded HTTP for the three sources. No database access here."""

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

from researchos import __version__
from researchos.config import Settings
from researchos.literature import parsers
from researchos.literature.types import BadResponse, Record

MAX_BODY_BYTES = 2 * 1024 * 1024
CONNECT_TIMEOUT = 3.0
OPENALEX_SELECT = (
    "id,doi,title,publication_year,authorships,cited_by_count,primary_location,"
    "best_oa_location,locations,open_access,abstract_inverted_index"
)
CROSSREF_SELECT = (
    "DOI,title,author,issued,published,container-title,abstract,is-referenced-by-count,URL"
)
_ARXIV_TERM_DROP = re.compile(r"[^\w.\-]", re.UNICODE)


class FetchTimeout(Exception):
    pass


class FetchUnavailable(Exception):
    pass


@dataclass
class HttpResult:
    status: int
    body: bytes
    headers: httpx.Headers


@dataclass
class Request:
    url: str
    params: dict[str, str | int]
    headers: dict[str, str]


# arXiv's index leaves out the usual Lucene English stop words, so `all:is` matches nothing and
# one such word makes an AND query return zero results. Observed live on 2026-10-31:
# "attention is all you need" gave 0 hits, without "is" it gave the paper.
_ARXIV_STOP_WORDS = frozenset(
    "a an and are as at be but by for if in into is it no not of on or such that the their then "
    "there these they this to was will with".split()
)


def arxiv_search_query(query: str, year_from: int | None, year_to: int | None) -> str | None:
    """Every word becomes an `all:` term, except stop words (kept only if nothing else is left).
    Anything that is query syntax in arXiv's grammar is removed from the word first, so user
    input cannot add fields, groups or operators."""
    words = [_ARXIV_TERM_DROP.sub("", word) for word in query.split()]
    words = [word for word in words if word.strip(".-")]
    content = [word for word in words if word.lower() not in _ARXIV_STOP_WORDS]
    terms = [f"all:{term}" for term in content or words]
    if not terms:
        return None
    text = " AND ".join(terms)
    if year_from is not None or year_to is not None:
        low = f"{year_from or 1000:04d}01010000"
        high = f"{year_to or 2999:04d}12312359"
        text += f" AND submittedDate:[{low} TO {high}]"
    return text


def _headers(settings: Settings, source: str) -> dict[str, str]:
    agent = f"ResearchOS/{__version__}"
    if settings.contact_email:
        agent += f" (mailto:{settings.contact_email})"
    headers = {"User-Agent": agent, "Accept": "application/json, application/atom+xml"}
    if source == "openalex" and settings.openalex_api_key:
        # In a header, not the URL: URLs end up in logs and error messages.
        headers["Authorization"] = f"Bearer {settings.openalex_api_key}"
    return headers


def build_search(
    settings: Settings,
    source: str,
    query: str,
    limit: int,
    year_from: int | None,
    year_to: int | None,
) -> Request | None:
    """None means the query has nothing this source can search for."""
    headers = _headers(settings, source)
    if source == "arxiv":
        text = arxiv_search_query(query, year_from, year_to)
        if text is None:
            return None
        return Request(
            f"{settings.arxiv_base_url}/api/query",
            {"search_query": text, "start": 0, "max_results": limit},
            headers,
        )
    if source == "openalex":
        params: dict[str, str | int] = {
            "search": query,
            "select": OPENALEX_SELECT,
            "per_page": limit,
        }
        if year_from is not None and year_to is not None:
            params["filter"] = f"publication_year:{year_from}-{year_to}"
        elif year_from is not None:
            params["filter"] = f"publication_year:>{year_from - 1}"
        elif year_to is not None:
            params["filter"] = f"publication_year:<{year_to + 1}"
        return Request(f"{settings.openalex_base_url}/works", params, headers)
    params = {"query.bibliographic": query, "select": CROSSREF_SELECT, "rows": limit}
    filters = []
    if year_from is not None:
        filters.append(f"from-pub-date:{year_from}")
    if year_to is not None:
        filters.append(f"until-pub-date:{year_to}")
    if filters:
        params["filter"] = ",".join(filters)
    if settings.contact_email:
        params["mailto"] = settings.contact_email
    return Request(f"{settings.crossref_base_url}/works", params, headers)


def build_lookup(settings: Settings, source: str, identifier: str) -> Request:
    """`identifier` has already passed the DOI / arXiv ID validators; it is still encoded as
    a path segment so that no character can change the path."""
    headers = _headers(settings, source)
    if source == "arxiv":
        return Request(
            f"{settings.arxiv_base_url}/api/query",
            {"search_query": "", "id_list": identifier, "start": 0, "max_results": 1},
            headers,
        )
    if source == "openalex":
        return Request(
            f"{settings.openalex_base_url}/works/doi:{quote(identifier, safe='/')}",
            {"select": OPENALEX_SELECT},
            headers,
        )
    params: dict[str, str | int] = {}
    if settings.contact_email:
        params["mailto"] = settings.contact_email
    url = f"{settings.crossref_base_url}/works/{quote(identifier, safe='/')}"
    return Request(url, params, headers)


def fetch(request: Request, timeout: float) -> HttpResult:
    """One GET with a hard overall deadline and a body cap. Redirects are not followed: the
    three hosts are fixed, and a redirect would be a way to reach another one."""
    deadline = time.monotonic() + timeout
    limits = httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT, timeout))
    try:
        with httpx.Client(timeout=limits, follow_redirects=False) as client:
            with client.stream(
                "GET", request.url, params=request.params, headers=request.headers
            ) as response:
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
                    raise BadResponse("response is larger than 2 MB")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BODY_BYTES:
                        raise BadResponse("response is larger than 2 MB")
                    if time.monotonic() > deadline:
                        raise FetchTimeout()
                    chunks.append(chunk)
                return HttpResult(response.status_code, b"".join(chunks), response.headers)
    except httpx.TimeoutException as exc:
        raise FetchTimeout() from exc
    except httpx.TransportError as exc:
        raise FetchUnavailable(type(exc).__name__) from exc
    except httpx.InvalidURL as exc:
        raise FetchUnavailable("invalid url") from exc


def retry_after_seconds(headers: httpx.Headers, default: float) -> float:
    raw = headers.get("retry-after", "").strip()
    if raw.isdigit():
        return min(float(raw), 86_400.0)
    if raw:
        try:
            when = parsedate_to_datetime(raw)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            return min(max((when - datetime.now(UTC)).total_seconds(), 0.0), 86_400.0)
        except (TypeError, ValueError):
            pass
    return default


def parse_search(source: str, body: bytes) -> tuple[list[Record], int | None]:
    if source == "arxiv":
        return parsers.parse_arxiv(body)
    if source == "openalex":
        return parsers.parse_openalex_search(body)
    return parsers.parse_crossref_search(body)


def parse_lookup(source: str, body: bytes) -> list[Record]:
    if source == "arxiv":
        records, _ = parsers.parse_arxiv(body)
        return records[:1]
    if source == "openalex":
        return [parsers.parse_openalex_work(body)]
    return [parsers.parse_crossref_work(body)]


def error_reason(body: bytes) -> str | None:
    """The upstream's own explanation of a 400, reduced to short plain text."""
    import json

    try:
        data = json.loads(body)
    except ValueError:
        return parsers.plain(body[:500].decode("utf-8", "replace"), 200)
    if isinstance(data, dict):
        for key in ("message", "error", "detail"):
            value = data.get(key)
            if isinstance(value, str):
                return parsers.plain(value, 200)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                text = value[0].get("message")
                if isinstance(text, str):
                    return parsers.plain(text, 200)
    return None
