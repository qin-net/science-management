"""Contract checks against the three real sources (specs/002 section 4, "在线契约测试").

Off by default: it needs the network and spends a little of the shared OpenAlex budget. Run with

    TEST_LIVE_SOURCES=1 uv run pytest tests/test_literature_live.py -s

It makes one search (one request per source) and two lookups, and respects the same pacing as the
application. A source that answers with a limit or an outage is reported, not hidden: the test
fails on a changed response shape and skips only when every answer was a limit or an outage.
"""

import os

import pytest

from tests.conftest import Account

pytestmark = pytest.mark.skipif(
    os.environ.get("TEST_LIVE_SOURCES") != "1",
    reason="live source contract tests not run: set TEST_LIVE_SOURCES=1",
)

REAL = {
    "arxiv_base_url": "https://export.arxiv.org",
    "openalex_base_url": "https://api.openalex.org",
    "crossref_base_url": "https://api.crossref.org",
    "arxiv_min_interval_seconds": 3.0,
    "search_source_timeout_seconds": 20.0,
    "search_max_wait_seconds": 20.0,
}
FAILURES = {
    "timeout",
    "unavailable",
    "rate_limited",
    "quota_exhausted",
    "cooling_down",
    "throttled",
}
SHAPE_BROKEN = {"bad_response", "bad_request"}


@pytest.fixture
def live(settings):
    settings.set(**REAL)


def test_live_search_returns_the_shape_we_parse(owner: Account, live):
    project = owner.new_project()
    response = owner.client.post(
        f"/api/v1/projects/{project['id']}/papers/search",
        json={"query": "attention is all you need", "limit": 5},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    report = {name: (view["status"], view["count"]) for name, view in body["sources"].items()}
    print("live search:", report)
    broken = {n: v["status"] for n, v in body["sources"].items() if v["status"] in SHAPE_BROKEN}
    assert not broken, f"a source no longer matches the parser: {broken}"
    ok = [name for name, view in body["sources"].items() if view["status"] == "ok"]
    if not ok:
        pytest.skip(f"no source answered (limits or outage): {report}")
    for name in ok:
        assert body["sources"][name]["count"] > 0, f"{name} returned nothing for a famous title"
    assert body["results"]
    for result in body["results"]:
        assert result["title"] and set(result["sources"]) <= set(ok)
    # The paper itself should be found somewhere.
    assert any(
        r["arxiv_id"] == "1706.03762" or "attention is all you need" == r["title"].lower()
        for r in body["results"]
    )


def test_live_lookup_by_identifier(owner: Account, live):
    project = owner.new_project()
    arxiv = owner.client.post(
        f"/api/v1/projects/{project['id']}/papers/import", json={"arxiv_id": "1706.03762"}
    )
    crossref = owner.client.post(
        f"/api/v1/projects/{project['id']}/papers/import", json={"doi": "10.1038/nature14539"}
    )
    print("live import:", arxiv.status_code, crossref.status_code)
    for response in (arxiv, crossref):
        if response.status_code == 502:
            states = {
                n: v["status"] for n, v in response.json()["error"]["details"]["sources"].items()
            }
            if set(states.values()) <= FAILURES:
                pytest.skip(f"source unavailable: {states}")
        assert response.status_code == 201, response.text
    assert arxiv.json()["title"] == "Attention Is All You Need"
    assert arxiv.json()["arxiv_id"] == "1706.03762"
    assert crossref.json()["title"] == "Deep learning"
    assert crossref.json()["metadata_provenance"]["title"] == "crossref"
