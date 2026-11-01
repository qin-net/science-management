"""Search behavior (specs/002 F, G, H). Real PostgreSQL, real sockets to a local fake upstream
that replays the responses recorded on 2026-10-31."""

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from researchos.db import get_engine
from tests.conftest import Account, add_member, sign_up
from tests.fake_upstream import (
    ARXIV_EMPTY,
    ARXIV_ERROR,
    Reply,
    default_arxiv,
    default_crossref,
    default_openalex,
)

OPENALEX_HEADERS = {"Content-Type": "application/json"}
COLUMNS_OF = "SELECT column_name FROM information_schema.columns WHERE table_name = :table"


def search(account: Account, project_id: str, **body):
    body.setdefault("query", "attention is all you need")
    return account.client.post(f"/api/v1/projects/{project_id}/papers/search", json=body)


@pytest.fixture
def project(owner: Account) -> dict:
    return owner.new_project()


def ok(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


# --- F: sources and statuses ---------------------------------------------------------


def test_f1_all_sources_ok(owner, project, upstream):
    data = ok(search(owner, project["id"]))
    assert data["outcome"] == "results"
    assert data["ranking"] == "reciprocal_rank_fusion"
    assert {name: s["status"] for name, s in data["sources"].items()} == {
        "arxiv": "ok",
        "openalex": "ok",
        "crossref": "ok",
    }
    assert data["sources"]["arxiv"]["total"] == 47
    for source in data["sources"].values():
        assert source["cached"] is False and source["duration_ms"] >= 0
    paper = next(r for r in data["results"] if r["arxiv_id"] == "1706.03762")
    assert paper["sources"] == ["arxiv"] and paper["provenance"]["title"] == "arxiv"
    assert "venue" in paper["missing"]
    assert paper["in_project"] is None
    openalex = next(r for r in data["results"] if r["doi"] == "10.65215/2q58a426")
    assert openalex["citations"] == {"openalex": 120309}
    # No key configured: the response says the quota is shared.
    assert data["sources"]["openalex"]["quota"]["shared"] is True
    assert data["sources"]["openalex"]["quota"]["remaining_usd"] == pytest.approx(0.0708)


def test_f2_timeout_retries_once_and_others_still_answer(owner, project, upstream):
    upstream.set("crossref", Reply(body=b"{}", delay=5))
    started = time.monotonic()
    data = ok(search(owner, project["id"]))
    elapsed = time.monotonic() - started
    assert data["sources"]["crossref"]["status"] == "timeout"
    assert "重试 1 次" in data["sources"]["crossref"]["message"]
    assert upstream.count("crossref") == 2
    assert data["outcome"] == "partial" and data["results"]
    assert elapsed < 2 * 2 + 3  # two attempts at the 2 s timeout plus slack


@pytest.mark.parametrize("reply", [Reply(503, "down"), Reply(drop=True)])
def test_f3_unavailable_retries_once(owner, project, upstream, reply):
    upstream.set("arxiv", reply)
    data = ok(search(owner, project["id"]))
    assert data["sources"]["arxiv"]["status"] == "unavailable"
    assert upstream.count("arxiv") == 2
    assert data["outcome"] == "partial"


def test_f4_rate_limit_sets_a_cooldown_that_expires(owner, project, upstream):
    upstream.set("crossref", Reply(429, "slow down", {"Retry-After": "120"}))
    first = ok(search(owner, project["id"]))["sources"]["crossref"]
    assert first["status"] == "rate_limited" and first["retry_after"] == 120
    assert upstream.count("crossref") == 1  # a 429 is not retried

    second = ok(search(owner, project["id"], query="another query"))["sources"]["crossref"]
    assert second["status"] == "cooling_down" and second["until"]
    assert upstream.count("crossref") == 1  # the cooldown sent nothing

    with get_engine().begin() as conn:
        conn.execute(text("UPDATE source_state SET cooldown_until = now() - interval '1 second'"))
    upstream.set("crossref", default_crossref)
    third = ok(search(owner, project["id"], query="third query"))["sources"]["crossref"]
    assert third["status"] == "ok" and upstream.count("crossref") == 2


def test_f4_retry_after_as_http_date(owner, project, upstream):
    when = (datetime.now(UTC) + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    upstream.set("crossref", Reply(429, "", {"Retry-After": when}))
    status = ok(search(owner, project["id"]))["sources"]["crossref"]
    assert status["status"] == "rate_limited" and 80 <= status["retry_after"] <= 91


def test_f4_rate_limit_without_retry_after_uses_the_default_cooldown(owner, project, upstream):
    upstream.set("arxiv", Reply(429, ""))
    assert ok(search(owner, project["id"]))["sources"]["arxiv"]["retry_after"] == 60


def test_f5_openalex_quota_from_headers_blocks_further_requests(owner, project, upstream):
    low = {**OPENALEX_HEADERS, "x-ratelimit-remaining-usd": "0.0005", "x-ratelimit-reset": "3600"}
    recorded = default_openalex

    def depleted(seen):
        reply = recorded(seen)
        reply.headers.update(low)
        return reply

    upstream.set("openalex", depleted)
    first = ok(search(owner, project["id"]))["sources"]["openalex"]
    assert first["status"] == "ok"  # this response is fine; it reported the low balance
    assert first["quota"]["remaining_usd"] == pytest.approx(0.0005)
    assert upstream.count("openalex") == 1

    second = ok(search(owner, project["id"], query="different"))["sources"]["openalex"]
    assert second["status"] == "quota_exhausted" and second["reset_at"]
    assert upstream.count("openalex") == 1

    with get_engine().begin() as conn:
        conn.execute(text("UPDATE source_state SET quota_reset_at = now() - interval '1 second'"))
    upstream.set("openalex", recorded)
    third = ok(search(owner, project["id"], query="after reset"))["sources"]["openalex"]
    assert third["status"] == "ok" and upstream.count("openalex") == 2


def test_f5_429_with_zero_remaining_means_quota_exhausted(owner, project, upstream):
    headers = {
        "x-ratelimit-remaining": "0",
        "x-ratelimit-remaining-usd": "0",
        "x-ratelimit-reset": "7200",
    }
    upstream.set("openalex", Reply(429, "{}", headers))
    state = ok(search(owner, project["id"]))["sources"]["openalex"]
    assert state["status"] == "quota_exhausted" and state["reset_at"]
    assert upstream.count("openalex") == 1
    again = ok(search(owner, project["id"], query="again"))["sources"]["openalex"]
    assert again["status"] == "quota_exhausted" and upstream.count("openalex") == 1


def test_f5_429_without_quota_headers_is_a_plain_rate_limit(owner, project, upstream):
    upstream.set("openalex", Reply(429, "{}"))
    assert ok(search(owner, project["id"]))["sources"]["openalex"]["status"] == "rate_limited"


def test_f6_api_key_is_only_sent_as_a_bearer_header(owner, project, upstream, settings, caplog):
    secret = "fake-openalex-credential-for-test-only"
    settings.set(openalex_api_key=secret)
    caplog.set_level(logging.DEBUG)
    response = search(owner, project["id"])
    data = ok(response)
    assert data["sources"]["openalex"]["quota"]["shared"] is False
    seen = upstream.seen["openalex"][0]
    assert seen.headers["authorization"] == f"Bearer {secret}"
    for source_log in upstream.seen.values():
        for request in source_log:
            assert secret not in request.request_line
    assert secret not in response.text
    assert secret not in caplog.text


def test_f7_bad_request_carries_the_upstream_reason(owner, project, upstream):
    body = '{"error": "Invalid query parameter", "message": "field xyz is not valid"}'
    upstream.set("openalex", Reply(400, body, {"Content-Type": "application/json"}))
    state = ok(search(owner, project["id"]))["sources"]["openalex"]
    assert state["status"] == "bad_request" and "field xyz is not valid" in state["message"]
    assert upstream.count("openalex") == 1


@pytest.mark.parametrize(
    "source, reply",
    [
        ("openalex", Reply(body="not json")),
        ("openalex", Reply(body='{"unexpected": true}')),
        ("crossref", Reply(body="<html>gateway</html>")),
        ("crossref", Reply(body='{"message": {}}')),
        ("arxiv", Reply(body="<feed")),
        ("arxiv", Reply(body=ARXIV_ERROR)),
        ("arxiv", Reply(body=b"x" * (2 * 1024 * 1024 + 10))),
        ("crossref", Reply(body=b'{"message": {"items": []', cut_after=10)),
        ("arxiv", Reply(404, "")),
    ],
)
def test_f7_unusable_responses_are_bad_response_not_500(owner, project, upstream, source, reply):
    upstream.set(source, reply)
    data = ok(search(owner, project["id"]))
    assert data["sources"][source]["status"] in {"bad_response", "unavailable"}
    assert data["sources"][source]["count"] == 0
    assert data["results"], "the other sources still answered"
    with get_engine().connect() as conn:
        cached = conn.execute(
            text("SELECT count(*) FROM search_cache WHERE source = :s"), {"s": source}
        ).scalar()
    assert cached == 0


def test_f7_an_oversize_body_is_bad_response_exactly(owner, project, upstream):
    upstream.set("openalex", Reply(body=b"x" * (2 * 1024 * 1024 + 10)))
    assert ok(search(owner, project["id"]))["sources"]["openalex"]["status"] == "bad_response"


def test_f8_all_failed_is_not_no_match(owner, project, upstream):
    for source in ("arxiv", "openalex", "crossref"):
        upstream.set(source, Reply(503, ""))
    data = ok(search(owner, project["id"]))
    assert data["outcome"] == "all_failed" and data["results"] == []
    assert all(s["status"] == "unavailable" for s in data["sources"].values())


def test_f8_all_ok_without_hits_is_no_match(owner, project, upstream):
    upstream.set("arxiv", Reply(body=ARXIV_EMPTY))
    upstream.set("openalex", Reply(body='{"meta": {"count": 0}, "results": []}'))
    upstream.set("crossref", Reply(body='{"message": {"total-results": 0, "items": []}}'))
    data = ok(search(owner, project["id"]))
    assert data["outcome"] == "no_match" and data["results"] == []
    assert {s["status"] for s in data["sources"].values()} == {"ok"}


def test_f8_partial_with_no_hits_is_not_no_match(owner, project, upstream):
    upstream.set("arxiv", Reply(body=ARXIV_EMPTY))
    upstream.set("openalex", Reply(503, ""))
    upstream.set("crossref", Reply(body='{"message": {"total-results": 0, "items": []}}'))
    assert ok(search(owner, project["id"]))["outcome"] == "partial"


def test_f9_cache_hit_expiry_and_sharing(owner, project, upstream, settings):
    first = ok(search(owner, project["id"]))
    assert all(not s["cached"] for s in first["sources"].values())
    counts = {name: upstream.count(name) for name in upstream.seen}
    second = ok(search(owner, project["id"]))
    assert all(s["cached"] and s["fetched_at"] for s in second["sources"].values())
    assert {name: upstream.count(name) for name in upstream.seen} == counts
    assert second["results"] == first["results"]

    # Another user's project: shared upstream data, but in_project is their own.
    other = sign_up()
    other_project = other.new_project()
    shared = ok(search(other, other_project["id"]))
    assert all(s["cached"] for s in shared["sources"].values())
    assert {name: upstream.count(name) for name in upstream.seen} == counts

    with get_engine().connect() as conn:
        columns = conn.execute(text(COLUMNS_OF), {"table": "search_cache"}).scalars().all()
        payloads = conn.execute(text("SELECT payload::text FROM search_cache")).scalars().all()
    assert not {"user_id", "project_id"} & set(columns)
    assert not any(owner.user["email"] in p or project["id"] in p for p in payloads)

    with get_engine().begin() as conn:
        conn.execute(text("UPDATE search_cache SET expires_at = now() - interval '1 second'"))
    again = ok(search(owner, project["id"]))
    assert all(not s["cached"] for s in again["sources"].values())
    assert upstream.count("crossref") == counts["crossref"] + 1


def test_f9_expired_rows_are_cleaned_when_new_ones_are_written(owner, project, upstream):
    ok(search(owner, project["id"]))
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE search_cache SET expires_at = now() - interval '1 hour'"))
    ok(search(owner, project["id"], query="a different question"))
    with get_engine().connect() as conn:
        expired = conn.execute(
            text("SELECT count(*) FROM search_cache WHERE expires_at < now()")
        ).scalar()
    assert expired == 0


def test_f9_failures_are_not_cached(owner, project, upstream):
    upstream.set("crossref", Reply(503, ""))
    ok(search(owner, project["id"]))
    calls = upstream.count("crossref")
    upstream.set("crossref", default_crossref)
    data = ok(search(owner, project["id"]))
    assert data["sources"]["crossref"]["status"] == "ok"
    assert upstream.count("crossref") == calls + 1


def test_f10_arxiv_and_crossref_requests_never_overlap_and_keep_their_spacing(
    owner, project, upstream, settings
):
    def slow(handler):
        def run(seen):
            time.sleep(0.1)
            return handler(seen)

        return run

    upstream.set("arxiv", slow(default_arxiv))
    upstream.set("crossref", slow(default_crossref))
    settings.set(search_max_wait_seconds=30.0)
    results: list[int] = []

    def run(index: int) -> None:
        response = search(owner, project["id"], query=f"concurrent query {index}")
        results.append(response.status_code)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [200] * 4
    for source, gap in (("arxiv", 0.3), ("crossref", 1.0)):
        calls = sorted(upstream.seen[source], key=lambda s: s.started)
        assert len(calls) == 4
        for before, after in zip(calls, calls[1:], strict=False):
            assert after.started >= before.finished, f"{source} requests overlapped"
            assert after.started - before.finished >= gap - 0.05, f"{source} spacing too short"


def test_f11_waiting_longer_than_the_limit_is_throttled_without_a_request(
    owner, project, upstream, settings
):
    settings.set(search_max_wait_seconds=0.2, arxiv_min_interval_seconds=5.0)
    ok(search(owner, project["id"]))
    assert upstream.count("arxiv") == 1
    started = time.monotonic()
    data = ok(search(owner, project["id"], query="second try"))
    assert data["sources"]["arxiv"]["status"] == "throttled"
    assert upstream.count("arxiv") == 1
    assert data["sources"]["openalex"]["status"] == "ok"  # others are not held up
    assert time.monotonic() - started < 2.5


def test_f12_per_user_limit_blocks_before_any_upstream_request(owner, project, upstream, settings):
    settings.set(search_user_per_minute=2)
    ok(search(owner, project["id"], query="one"))
    ok(search(owner, project["id"], query="two"))
    before = {name: upstream.count(name) for name in upstream.seen}
    response = search(owner, project["id"], query="three")
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "search_rate_limited" and 1 <= error["details"]["retry_after"] <= 61
    assert {name: upstream.count(name) for name in upstream.seen} == before

    other = sign_up()
    assert search(other, other.new_project()["id"], query="one").status_code == 200

    with get_engine().connect() as conn:
        columns = conn.execute(text(COLUMNS_OF), {"table": "search_calls"}).scalars().all()
    assert "query" not in columns


# --- G: input, permissions ---------------------------------------------------------


def test_g1_permissions_and_no_upstream_traffic(owner, project, upstream):
    viewer = add_member(owner, "viewer")
    member = add_member(owner, "member")
    stranger = sign_up()
    assert search(viewer, project["id"]).status_code == 403
    assert search(stranger, project["id"]).status_code == 404
    assert (
        viewer.client.post(
            f"/api/v1/projects/{project['id']}/papers/import", json={"arxiv_id": "1706.03762"}
        ).status_code
        == 403
    )
    assert (
        stranger.client.post(
            f"/api/v1/projects/{project['id']}/papers/import", json={"arxiv_id": "1706.03762"}
        ).status_code
        == 404
    )
    archived = owner.client.patch(
        f"/api/v1/projects/{project['id']}", json={"expected_version": 1, "status": "archived"}
    )
    assert archived.status_code == 200
    assert search(member, project["id"]).status_code == 409
    assert (
        owner.client.post(
            f"/api/v1/projects/{project['id']}/papers/import", json={"arxiv_id": "1706.03762"}
        ).status_code
        == 409
    )
    assert sum(upstream.count(name) for name in upstream.seen) == 0


def test_g1_unauthenticated_is_refused(project, upstream):
    from fastapi.testclient import TestClient

    from researchos.main import app

    response = TestClient(app).post(
        f"/api/v1/projects/{project['id']}/papers/search", json={"query": "x"}
    )
    assert response.status_code in {401, 403}
    assert sum(upstream.count(name) for name in upstream.seen) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 301},
        {"query": "bad\x00char"},
        {"query": "line\nbreak"},
        {"query": "ok", "sources": []},
        {"query": "ok", "sources": ["arxiv", "arxiv"]},
        {"query": "ok", "sources": ["google"]},
        {"query": "ok", "year_from": 2020, "year_to": 2019},
        {"query": "ok", "year_from": 1400},
        {"query": "ok", "year_to": 9999},
        {"query": "ok", "limit": 0},
        {"query": "ok", "limit": 26},
        {"query": "ok", "unknown": 1},
        {},
    ],
)
def test_g2_invalid_input_is_422_with_no_upstream_request(owner, project, upstream, body):
    response = owner.client.post(f"/api/v1/projects/{project['id']}/papers/search", json=body)
    assert response.status_code == 422, response.text
    assert sum(upstream.count(name) for name in upstream.seen) == 0
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM search_calls")).scalar() == 0


def test_g3_special_characters_arrive_as_one_encoded_parameter(owner, project, upstream):
    queries = [
        "a&b=c",
        "tag#fragment",
        "100% sure",
        "../../etc/passwd",
        'ti:"x" AND (y OR z)',
        "plus+sign and space",
        "naïve 注意力 😀",
        "semi;colon?question",
    ]
    for query in queries:
        ok(search(owner, project["id"], query=query, sources=["openalex", "crossref"]))
    for request in upstream.seen["openalex"]:
        assert request.path == "/openalex/works"
        assert set(request.query) == {"search", "select", "per_page"}
    for request in upstream.seen["crossref"]:
        assert request.path == "/crossref/works"
        assert set(request.query) == {"query.bibliographic", "select", "rows"}
    sent_openalex = [r.query["search"][0] for r in upstream.seen["openalex"]]
    assert sent_openalex == queries
    sent_crossref = [r.query["query.bibliographic"][0] for r in upstream.seen["crossref"]]
    assert sent_crossref == queries
    for request in upstream.seen["openalex"]:
        assert " " not in request.request_line.split(" ")[1]  # no raw space in the target


def test_g3_arxiv_receives_only_escaped_all_terms(owner, project, upstream):
    ok(search(owner, project["id"], query='ti:"x" OR (y) +z', sources=["arxiv"]))
    sent = upstream.seen["arxiv"][0]
    assert set(sent.query) == {"search_query", "start", "max_results"}
    assert all(term.startswith("all:") for term in sent.query["search_query"][0].split(" AND "))
    assert "(" not in sent.query["search_query"][0] and '"' not in sent.query["search_query"][0]


def test_g3_a_query_with_nothing_searchable_on_arxiv_is_reported_not_sent(owner, project, upstream):
    data = ok(search(owner, project["id"], query='"()"', sources=["arxiv", "openalex"]))
    assert data["sources"]["arxiv"]["status"] == "bad_request"
    assert upstream.count("arxiv") == 0 and data["sources"]["openalex"]["status"] == "ok"


def test_g3_user_agent_identifies_the_app_but_not_the_user(owner, project, upstream, settings):
    settings.set(contact_email="ops@example.org")
    ok(search(owner, project["id"]))
    for source_log in upstream.seen.values():
        agent = source_log[0].headers["user-agent"]
        assert agent.startswith("ResearchOS/") and "mailto:ops@example.org" in agent
        assert owner.user["email"] not in agent and project["id"] not in agent
    assert upstream.seen["crossref"][0].query["mailto"] == ["ops@example.org"]


def test_g4_xml_entities_are_refused_not_expanded(owner, project, upstream):
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&b;</title></entry></feed>'
    )
    external = (
        '<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&x;</title></entry></feed>'
    )
    for body in (bomb, external):
        upstream.set("arxiv", Reply(body=body))
        data = ok(search(owner, project["id"], query=f"q{hash(body)}"))
        assert data["sources"]["arxiv"]["status"] == "bad_response"
        assert "root:" not in str(data)


def test_g5_markup_in_titles_and_abstracts_becomes_plain_text(owner, project, upstream):
    hostile = {
        "message": {
            "total-results": 1,
            "items": [
                {
                    "DOI": "10.1234/xss",
                    "title": ["<img src=x onerror=alert(1)>Title <b>bold</b>"],
                    "abstract": "<jats:p>Text <script>alert(2)</script>more "
                    "<jats:italic>italic</jats:italic></jats:p>",
                    "issued": {"date-parts": [[2020]]},
                    "URL": "javascript:alert(3)",
                    "author": [{"given": "<i>Eve</i>", "family": "Mallory"}],
                }
            ],
        }
    }
    import json

    upstream.set("crossref", Reply(body=json.dumps(hostile)))
    data = ok(search(owner, project["id"], sources=["crossref"]))
    result = data["results"][0]
    assert result["title"] == "Title bold"
    assert result["abstract"] == "Text more italic"
    assert result["authors"] == ["Eve Mallory"]
    assert result["landing_url"] is None  # javascript: links are never passed on
    assert "<" not in str(result) and "alert(3)" not in str(result)


# --- H: merge and dedup --------------------------------------------------------------


def _crossref_body(items: list[dict]) -> str:
    import json

    return json.dumps({"message": {"total-results": len(items), "items": items}})


def _openalex_body(works: list[dict]) -> str:
    import json

    return json.dumps({"meta": {"count": len(works)}, "results": works})


def test_h1_h5_one_paper_from_three_sources_with_different_spellings(owner, project, upstream):
    upstream.set(
        "crossref",
        Reply(
            body=_crossref_body(
                [
                    {
                        "DOI": "10.1234/Merge.Me",
                        "title": ["Merged Paper"],
                        "issued": {"date-parts": [[2019]]},
                        "container-title": ["Journal of Merging"],
                        "is-referenced-by-count": 7,
                    }
                ]
            )
        ),
    )
    upstream.set(
        "openalex",
        Reply(
            body=_openalex_body(
                [
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.1234/merge.me",
                        "title": "Merged Paper",
                        "publication_year": 2018,
                        "cited_by_count": 9,
                        "locations": [{"landing_page_url": "https://arxiv.org/abs/1706.03762v1"}],
                    },
                    {
                        "id": "https://openalex.org/W2",
                        "doi": "https://doi.org/10.48550/arxiv.1706.03762",
                        "title": "Merged Paper (preprint record)",
                        "publication_year": 2017,
                    },
                ]
            ),
            headers=OPENALEX_HEADERS,
        ),
    )
    data = ok(search(owner, project["id"]))
    merged = [r for r in data["results"] if "crossref" in r["sources"]]
    assert len(merged) == 1
    paper = merged[0]
    # Crossref ties to OpenAlex W1 by DOI; W1 ties to arXiv and W2 through the arXiv ID.
    assert paper["sources"] == ["arxiv", "openalex", "crossref"]
    assert paper["doi"] == "10.1234/merge.me" and paper["arxiv_id"] == "1706.03762"
    assert paper["citations"] == {"openalex": 9, "crossref": 7}
    # Two OpenAlex records joined this group; the better-ranked one (2018) speaks for OpenAlex.
    assert paper["conflicts"]["year"] == {"arxiv": 2017, "crossref": 2019, "openalex": 2018}
    assert paper["year"] == 2019 and paper["provenance"]["year"] == "crossref"
    assert paper["venue"] == "Journal of Merging"
    assert len([r for r in data["results"] if r["arxiv_id"] == "1706.03762"]) == 1


def test_h2_preprint_and_journal_version_are_not_merged_but_are_linked(owner, project, upstream):
    upstream.set(
        "crossref",
        Reply(body=_crossref_body([{"DOI": "10.1000/journal", "title": ["Same Title!"]}])),
    )
    upstream.set(
        "openalex",
        Reply(
            body=_openalex_body(
                [{"id": "W", "doi": "https://doi.org/10.2000/preprint", "title": "same  title"}]
            ),
            headers=OPENALEX_HEADERS,
        ),
    )
    data = ok(search(owner, project["id"], sources=["openalex", "crossref"]))
    assert len(data["results"]) == 2
    by_doi = {r["doi"]: r for r in data["results"]}
    assert by_doi["10.1000/journal"]["similar_to"] == ["doi:10.2000/preprint"]
    assert by_doi["10.2000/preprint"]["similar_to"] == ["doi:10.1000/journal"]


def test_h3_conflicting_years_are_listed_not_hidden(owner, project, upstream):
    data = ok(search(owner, project["id"]))
    paper = next(r for r in data["results"] if r["doi"] == "10.65215/2q58a426")
    assert paper["year"] == 2025
    # arXiv says 2017 for 1706.03762, OpenAlex 2025 for the record with that landing page.
    merged = [r for r in data["results"] if r["conflicts"]]
    for result in merged:
        assert set(result["conflicts"]) == {"year"}
        assert len(set(result["conflicts"]["year"].values())) > 1


def test_h4_missing_values_are_null_and_listed(owner, project, upstream):
    upstream.set(
        "crossref",
        Reply(
            body=_crossref_body(
                [{"DOI": "10.1234/sparse", "title": ["Sparse"], "issued": {"date-parts": [[None]]}}]
            )
        ),
    )
    data = ok(search(owner, project["id"], sources=["crossref"]))
    paper = data["results"][0]
    assert paper["year"] is None and paper["authors"] == [] and paper["abstract"] is None
    assert paper["venue"] is None
    assert {"year", "authors", "abstract", "venue"} <= set(paper["missing"])
    assert "year" not in paper["provenance"] and "authors" not in paper["provenance"]
    assert paper["citations"] == {}


def test_h5_result_does_not_depend_on_the_order_sources_answer_in(owner, project, upstream):
    first = ok(search(owner, project["id"]))["results"]

    def late_arxiv(seen):
        time.sleep(0.4)
        return default_arxiv(seen)

    upstream.set("arxiv", late_arxiv)
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE search_cache"))
    second = ok(search(owner, project["id"]))["results"]
    assert [r["doi"] or r["arxiv_id"] for r in first] == [r["doi"] or r["arxiv_id"] for r in second]


def test_h6_in_project_and_similar_papers_use_this_project_only(owner, project, upstream):
    mine = owner.client.post(
        f"/api/v1/projects/{project['id']}/papers/import", json={"arxiv_id": "1706.03762"}
    )
    assert mine.status_code == 201, mine.text
    similar = owner.client.post(
        f"/api/v1/projects/{project['id']}/papers/import", json={"doi": "10.1038/nature14539"}
    )
    assert similar.status_code == 201
    owner.client.patch(
        f"/api/v1/projects/{project['id']}/papers/{similar.json()['id']}",
        json={"title": "Tool Attention Is All You Need: dynamic tool gating"[:0] or "x"},
    )
    other_project = owner.new_project("Other")
    elsewhere = owner.client.post(
        f"/api/v1/projects/{other_project['id']}/papers/import", json={"arxiv_id": "2604.21816"}
    )
    assert elsewhere.status_code in {201, 404, 502}

    data = ok(search(owner, project["id"]))
    attention = next(r for r in data["results"] if r["arxiv_id"] == "1706.03762")
    assert attention["in_project"] == mine.json()["id"]
    foreign = next(r for r in data["results"] if r["arxiv_id"] == "2604.21816")
    assert foreign["in_project"] is None  # only imported into the other project


def test_h6_same_title_with_a_different_key_is_only_a_similar_paper(owner, project, upstream):
    created = owner.client.post(
        f"/api/v1/projects/{project['id']}/papers/import", json={"arxiv_id": "1706.03762"}
    )
    assert created.status_code == 201
    paper_id = created.json()["id"]
    owner.client.patch(f"/api/v1/projects/{project['id']}/papers/{paper_id}", json={"arxiv_id": ""})
    data = ok(search(owner, project["id"]))
    same = next(r for r in data["results"] if r["arxiv_id"] == "1706.03762")
    assert same["in_project"] is None and same["similar_papers"] == [paper_id]


def test_h7_order_is_deterministic_and_rank_based(owner, project, upstream):
    first = ok(search(owner, project["id"]))
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE search_cache"))
    second = ok(search(owner, project["id"]))
    key = [(r["doi"], r["arxiv_id"], r["title"]) for r in first["results"]]
    assert key == [(r["doi"], r["arxiv_id"], r["title"]) for r in second["results"]]
    assert first["ranking"] == "reciprocal_rank_fusion"
    assert len(first["results"]) >= 5
