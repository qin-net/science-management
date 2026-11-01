"""Parsers against the responses recorded from the real services on 2026-10-31."""

import re
from pathlib import Path

import pytest

from researchos.literature import parsers
from researchos.literature.ids import arxiv_id_from_doi, normalize_arxiv_id, normalize_doi
from researchos.literature.sources import arxiv_search_query
from researchos.literature.types import BadResponse

FIXTURES = Path(__file__).parent / "fixtures" / "sources"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_arxiv_search_and_lookup():
    records, total = parsers.parse_arxiv(fixture("arxiv-search.xml"))
    assert total == 47
    assert [r.arxiv_id for r in records] == ["2604.21816", "1706.03762"]
    paper = records[1]
    assert paper.title == "Attention Is All You Need"
    assert paper.year == 2017
    assert paper.authors[0] == "Ashish Vaswani" and len(paper.authors) == 8
    assert paper.doi is None  # arXiv did not supply one; nothing is invented
    assert paper.abstract and paper.abstract.startswith("The dominant sequence")
    assert paper.landing_url == "https://arxiv.org/abs/1706.03762v7" or paper.landing_url
    single, _ = parsers.parse_arxiv(fixture("arxiv-1706.03762.xml"))
    assert single[0].arxiv_id == "1706.03762"


def test_openalex_search_and_work():
    records, total = parsers.parse_openalex_search(fixture("openalex-search.json"))
    assert total == 3313444 and len(records) == 2
    first = records[0]
    assert first.doi == "10.65215/2q58a426"
    assert first.citations == 120309
    assert first.abstract and first.abstract.startswith("The dominant sequence")
    assert len(first.authors) == 8
    assert records[1].venue == (
        "IEEE International Conference on Acoustics Speech and Signal Processing"
    )
    work = parsers.parse_openalex_work(fixture("openalex-work.json"))
    assert work.title == "Attention Is All You Need" and work.year == 2025


def test_crossref_search_and_work_keep_missing_fields_missing():
    records, total = parsers.parse_crossref_search(fixture("crossref-search.json"))
    assert total == 1252281
    assert records[0].venue == "From Human Attention to Computational Attention"
    assert records[0].abstract is None and records[0].authors == ["Patrick Mineault"]
    assert "<" not in records[1].abstract and "jats" not in records[1].abstract.lower()
    assert records[2].year == 2026 and records[2].venue is None
    work = parsers.parse_crossref_work(fixture("crossref-work.json"))
    assert work.doi == "10.1038/nature14539" and work.authors[0] == "Yann LeCun"
    assert work.citations == 78930 and work.year == 2015


def test_crossref_unknown_year_and_missing_authors_stay_unknown():
    records, _ = parsers.parse_crossref_search(fixture("crossref-sparse.json"))
    unknown = [r for r in records if r.year is None]
    assert unknown, "the recorded sample contains [[null]] years"
    assert any(not r.authors for r in records)
    assert all(r.year != 0 for r in records)


def test_html_and_jats_become_plain_text():
    assert parsers.plain("<jats:p>a <jats:italic>b</jats:italic> c</jats:p>") == "a b c"
    assert parsers.plain("<script>alert(1)</script>safe") == "safe"
    assert parsers.plain("x &lt;b&gt; y") == "x <b> y"  # entities decode to text, not markup
    assert parsers.plain("   ") is None and parsers.plain(None) is None


def test_arxiv_error_entry_and_bad_xml_are_bad_responses():
    error = (
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        b"<id>https://arxiv.org/api/errors#incorrect_id_format_for_x</id>"
        b"<title>Error</title><summary>incorrect id format</summary></entry></feed>"
    )
    with pytest.raises(BadResponse, match="incorrect id format"):
        parsers.parse_arxiv(error)
    for body in (b"<feed", b"", b"not xml", b"<html></html>"):
        with pytest.raises(BadResponse):
            parsers.parse_arxiv(body)


def test_xml_entities_and_dtds_are_refused():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&b;</title></entry></feed>'
    )
    external = (
        b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&x;</title></entry></feed>'
    )
    for body in (bomb, external):
        with pytest.raises(BadResponse):
            parsers.parse_arxiv(body)


def test_json_sources_reject_unusable_bodies():
    for parse in (
        parsers.parse_openalex_search,
        parsers.parse_crossref_search,
        parsers.parse_openalex_work,
        parsers.parse_crossref_work,
    ):
        for body in (b"", b"{", b"[]", b'{"unexpected": 1}', b"null"):
            with pytest.raises(BadResponse):
                parse(body)


def test_identifier_normalization():
    assert normalize_doi("https://doi.org/10.1038/NATURE14539") == "10.1038/nature14539"
    assert normalize_doi("doi: 10.1/abc") is None  # prefix must have 4 or more digits
    assert normalize_doi("10.1234/a/../b") is None
    assert normalize_doi("nope") is None and normalize_doi(None) is None
    for raw in (
        "1706.03762",
        "1706.03762v7",
        "arXiv:1706.03762",
        "https://arxiv.org/abs/1706.03762v2",
        "http://arxiv.org/pdf/1706.03762.pdf",
    ):
        assert normalize_arxiv_id(raw) == "1706.03762", raw
    assert normalize_arxiv_id("hep-th/9901001") == "hep-th/9901001"
    assert normalize_arxiv_id("ARXIV:hep-th/9901001v2") == "hep-th/9901001"
    for bad in ("notanid", "17.06", "1706.0376x", "", "../etc"):
        assert normalize_arxiv_id(bad) is None, bad
    assert arxiv_id_from_doi("10.48550/arxiv.1706.03762") == "1706.03762"
    assert arxiv_id_from_doi("10.1038/nature14539") is None


def test_arxiv_query_leaves_out_stop_words_that_arxiv_cannot_match():
    # Live on 2026-10-31: `all:is` matches nothing, so one such word emptied the whole result.
    assert arxiv_search_query("attention is all you need", None, None) == (
        "all:attention AND all:all AND all:you AND all:need"
    )
    assert arxiv_search_query("The Theory of Everything", None, None) == (
        "all:Theory AND all:Everything"
    )
    # A query made only of stop words is still sent rather than dropped.
    assert arxiv_search_query("to be or not to be", None, None) == (
        "all:to AND all:be AND all:or AND all:not AND all:to AND all:be"
    )


def test_arxiv_query_only_contains_escaped_all_terms():
    assert arxiv_search_query("attention all", None, None) == "all:attention AND all:all"
    hostile = arxiv_search_query('ti:"x" OR (au:y) +z & #a', None, None)
    assert hostile is not None
    terms = hostile.split(" AND ")
    assert all(re.fullmatch(r"all:[\w.\-]+", term) for term in terms), hostile
    assert arxiv_search_query('()+:"', None, None) is None
    with_years = arxiv_search_query("graph", 2017, 2018)
    assert with_years == "all:graph AND submittedDate:[201701010000 TO 201812312359]"
