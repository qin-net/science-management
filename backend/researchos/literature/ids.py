"""Identifier normalization for DOIs and arXiv IDs."""

import re

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
DOI_PREFIX_RE = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", re.I)
ARXIV_NEW_RE = re.compile(r"^\d{4}\.\d{4,5}$")
ARXIV_OLD_RE = re.compile(r"^[a-z][a-z\-]*(\.[a-z]{2})?/\d{7}$")
ARXIV_PREFIX_RE = re.compile(r"^(arxiv:\s*|https?://(www\.)?arxiv\.org/(abs|pdf)/)", re.I)
ARXIV_DOI_PREFIX = "10.48550/arxiv."


def normalize_doi(raw: str | None) -> str | None:
    """Lower-cased bare DOI, or None when the value is empty or not shaped like a DOI."""
    if raw is None:
        return None
    doi = DOI_PREFIX_RE.sub("", raw.strip()).lower()
    if not DOI_RE.match(doi):
        return None
    # A dot segment would be rewritten by URL normalization when the DOI is used in a path.
    if any(part in {".", ".."} for part in doi.split("/")):
        return None
    return doi


def normalize_arxiv_id(raw: str | None) -> str | None:
    """Version-less, lower-cased arXiv ID, or None when it does not look like one."""
    if raw is None:
        return None
    value = ARXIV_PREFIX_RE.sub("", raw.strip())
    value = re.sub(r"\.pdf$", "", value, flags=re.I)
    value = re.sub(r"v\d+$", "", value, flags=re.I).lower()
    if ARXIV_NEW_RE.match(value) or ARXIV_OLD_RE.match(value):
        return value
    return None


def arxiv_id_from_doi(doi: str | None) -> str | None:
    """DataCite gives every arXiv paper the DOI 10.48550/arXiv.<id>."""
    if doi and doi.lower().startswith(ARXIV_DOI_PREFIX):
        return normalize_arxiv_id(doi[len(ARXIV_DOI_PREFIX) :])
    return None


def safe_url(value: object) -> str | None:
    """Only http(s) links from third parties are passed on; javascript: and data: are not."""
    if isinstance(value, str) and re.match(r"^https?://[^\s]+$", value.strip(), re.I):
        return value.strip()[:2000]
    return None
