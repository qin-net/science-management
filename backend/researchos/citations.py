"""BibTeX and RIS text for papers (specs/006-p0-citation-export.md).

Only fields that exist are written; nothing is guessed. Every piece of third-party text goes
through `_flat` and, for BibTeX, `_tex`, so it cannot end a record or start a new one.
"""

import re
import unicodedata
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import quote

BIBTEX_TYPE = "application/x-bibtex; charset=utf-8"
RIS_TYPE = "application/x-research-info-systems; charset=utf-8"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SPACE = re.compile(r"\s+")
_TEX = {
    "\\": "\\textbackslash{}",
    "{": "\\textbraceleft{}",
    "}": "\\textbraceright{}",
    "^": "\\textasciicircum{}",
    "~": "\\textasciitilde{}",
    "&": "\\&",
    "%": "\\%",
    "$": "\\$",
    "#": "\\#",
    "_": "\\_",
}
_TEX_RE = re.compile("[" + re.escape("".join(_TEX)) + "]")
_UNSAFE_IN_BIBTEX_DOI = re.compile(r"[{}\\]")


class Citable(Protocol):
    title: str | None
    authors: list[str]
    year: int | None
    doi: str | None
    arxiv_id: str | None
    abstract: str | None
    venue: str | None


def _flat(value: str) -> str:
    """One line: controls dropped, every run of whitespace (newlines included) a single space."""
    return _SPACE.sub(" ", _CONTROL.sub("", value)).strip()


def _tex(value: str) -> str:
    return _TEX_RE.sub(lambda match: _TEX[match.group()], _flat(value))


def _ascii(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def _surname(author: str) -> str:
    author = _flat(author)
    name = author.split(",")[0] if "," in author else (author.split(" ")[-1] if author else "")
    return _ascii(name) or "anon"


def cite_key(paper: Citable) -> str:
    first = _surname(paper.authors[0]) if paper.authors else "anon"
    year = str(paper.year) if paper.year is not None else "nd"
    word = next((w for w in re.findall(r"[^\W_]{3,}", _flat(paper.title or "")) if _ascii(w)), "")
    return f"{first}{_ascii(year) or 'nd'}{_ascii(word) or 'paper'}"


def _letters(n: int) -> str:
    """1 -> a, 26 -> z, 27 -> aa."""
    out = ""
    while n:
        n, rest = divmod(n - 1, 26)
        out = chr(ord("a") + rest) + out
    return out


def unique_keys(papers: Sequence[Citable]) -> list[str]:
    """Same data, same keys: repeats get a, b, c... in export order, and no key is used twice."""
    used: set[str] = set()
    repeats: dict[str, int] = {}
    keys = []
    for paper in papers:
        base = cite_key(paper)
        n = repeats.get(base, 0)
        key = base + _letters(n)
        while key in used:
            n += 1
            key = base + _letters(n)
        repeats[base] = n + 1
        used.add(key)
        keys.append(key)
    return keys


def link(paper: Citable) -> str | None:
    """Built only from validated identifiers, never from a URL a source supplied."""
    if paper.doi:
        return "https://doi.org/" + quote(paper.doi, safe="/()-._~:")
    if paper.arxiv_id:
        return "https://arxiv.org/abs/" + paper.arxiv_id
    return None


def bibtex(papers: Sequence[Citable]) -> str:
    entries = []
    for paper, key in zip(papers, unique_keys(papers), strict=True):
        fields: list[tuple[str, str]] = []
        fields.append(("title", "{" + _tex(paper.title or "") + "}"))
        authors = [_tex(a) for a in paper.authors if _flat(a)]
        if authors:
            fields.append(("author", " and ".join(authors)))
        if paper.year is not None:
            fields.append(("year", str(paper.year)))
        if paper.venue and _flat(paper.venue):
            fields.append(("howpublished", _tex(paper.venue)))
        if paper.doi and not _UNSAFE_IN_BIBTEX_DOI.search(paper.doi):
            fields.append(("doi", paper.doi))
        if paper.arxiv_id:
            fields.append(("eprint", paper.arxiv_id))
            fields.append(("archiveprefix", "arXiv"))
        if url := link(paper):
            fields.append(("url", url))
        if paper.abstract and _flat(paper.abstract):
            fields.append(("abstract", _tex(paper.abstract)))
        body = "".join(f"  {name} = {{{value}}},\n" for name, value in fields)
        entries.append(f"@misc{{{key},\n{body}}}\n")
    return "\n".join(entries)


def ris(papers: Sequence[Citable]) -> str:
    lines: list[str] = []
    for paper in papers:
        lines.append("TY  - GEN")
        lines.append(f"T1  - {_flat(paper.title or '')}")
        lines.extend(f"AU  - {_flat(a)}" for a in paper.authors if _flat(a))
        if paper.year is not None:
            lines.append(f"PY  - {paper.year}")
        if paper.venue and _flat(paper.venue):
            lines.append(f"T2  - {_flat(paper.venue)}")
        if paper.doi:
            lines.append(f"DO  - {_flat(paper.doi)}")
        if paper.arxiv_id:
            lines.append(f"AN  - {paper.arxiv_id}")
        if url := link(paper):
            lines.append(f"UR  - {url}")
        if paper.abstract and _flat(paper.abstract):
            lines.append(f"N2  - {_flat(paper.abstract)}")
        lines.append("ER  - ")
        lines.append("")
    return "\r\n".join(lines)
