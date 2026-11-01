"""Metadata import by DOI / arXiv ID (specs/002 I1-I9). Real PostgreSQL, fake upstream."""

import json
import threading
import time

import pytest
from sqlalchemy import text

from researchos.db import get_engine
from tests.conftest import Account, sign_up
from tests.fake_upstream import Reply, default_crossref, default_openalex
from tests.pdf_samples import text_pdf


@pytest.fixture
def project(owner: Account) -> dict:
    return owner.new_project()


def do_import(account: Account, project_id: str, **body):
    return account.client.post(f"/api/v1/projects/{project_id}/papers/import", json=body)


def paper_url(project_id: str, paper_id: str) -> str:
    return f"/api/v1/projects/{project_id}/papers/{paper_id}"


def events(event_type: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT payload FROM research_events WHERE event_type = :t"), {"t": event_type}
        )
        return [row.payload for row in rows]


def test_i1_doi_import_from_crossref(owner, project, upstream):
    response = do_import(owner, project["id"], doi="https://doi.org/10.1038/NATURE14539")
    assert response.status_code == 201, response.text
    paper = response.json()
    assert paper["title"] == "Deep learning" and paper["year"] == 2015
    assert paper["authors"] == ["Yann LeCun", "Yoshua Bengio", "Geoffrey Hinton"]
    assert paper["doi"] == "10.1038/nature14539" and paper["arxiv_id"] is None
    assert paper["venue"] == "Nature"
    assert paper["current_version"] is None and paper["parse_job"] is None
    assert paper["source_links"] == {"landing_url": "https://doi.org/10.1038/nature14539"}
    # Every value says where it came from; what Crossref did not give has no entry.
    assert paper["metadata_provenance"] == {
        "title": "crossref",
        "authors": "crossref",
        "year": "crossref",
        "doi": "crossref",
        "venue": "crossref",
    }
    assert paper["abstract"] is None and "abstract" not in paper["metadata_provenance"]
    assert events("paper.imported") == [{"method": "metadata", "source": "crossref"}]
    # It is a normal paper: listed, readable, deletable.
    listing = owner.client.get(f"/api/v1/projects/{project['id']}/papers").json()
    assert [p["id"] for p in listing["items"]] == [paper["id"]]
    assert upstream.count("openalex") == 0 and upstream.count("arxiv") == 0


def test_i2_crossref_404_falls_back_to_openalex(owner, project, upstream):
    work = json.loads(open("tests/fixtures/sources/openalex-work.json").read())
    work["doi"] = "https://doi.org/10.9999/fallback"
    work["title"] = "Fallback Paper"
    upstream.set(
        "openalex", Reply(body=json.dumps(work), headers={"Content-Type": "application/json"})
    )
    response = do_import(owner, project["id"], doi="10.9999/fallback")
    assert response.status_code == 201, response.text
    paper = response.json()
    assert paper["title"] == "Fallback Paper" and paper["doi"] == "10.9999/fallback"
    assert set(paper["metadata_provenance"].values()) == {"openalex"}
    assert [r.path for r in upstream.seen["crossref"]] == ["/crossref/works/10.9999/fallback"]


def test_i2_both_404_is_doi_not_found_and_creates_nothing(owner, project, upstream):
    response = do_import(owner, project["id"], doi="10.9999/nowhere")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "doi_not_found"
    assert owner.client.get(f"/api/v1/projects/{project['id']}/papers").json()["total"] == 0


def test_i2b_arxiv_doi_goes_to_arxiv_and_never_asks_crossref(owner, project, upstream):
    response = do_import(owner, project["id"], doi="10.48550/arXiv.1706.03762")
    assert response.status_code == 201, response.text
    paper = response.json()
    assert paper["arxiv_id"] == "1706.03762" and paper["doi"] is None
    assert upstream.count("crossref") == 0 and upstream.count("openalex") == 0


def test_i2b_openalex_answering_with_a_different_doi_is_not_used(owner, project, upstream):
    # OpenAlex really did this on 2026-10-31: asked for one DOI, returned another paper.
    upstream.set("openalex", default_openalex)  # path /works/doi:... is a 404 in the default
    work = json.loads(open("tests/fixtures/sources/openalex-work.json").read())
    upstream.set(
        "openalex", Reply(body=json.dumps(work), headers={"Content-Type": "application/json"})
    )
    response = do_import(owner, project["id"], doi="10.9999/different")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "doi_not_found"
    assert upstream.count("openalex") == 1  # it was asked, and its answer was refused
    assert owner.client.get(f"/api/v1/projects/{project['id']}/papers").json()["total"] == 0


@pytest.mark.parametrize(
    "raw",
    [
        "1706.03762",
        "1706.03762v7",
        "arXiv:1706.03762",
        "https://arxiv.org/abs/1706.03762v7",
        "https://arxiv.org/pdf/1706.03762.pdf",
    ],
)
def test_i3_arxiv_id_spellings(owner, project, upstream, raw):
    response = do_import(owner, project["id"], arxiv_id=raw)
    assert response.status_code == 201, response.text
    paper = response.json()
    assert paper["arxiv_id"] == "1706.03762" and paper["year"] == 2017
    assert paper["abstract"].startswith("The dominant sequence")
    assert set(paper["metadata_provenance"].values()) == {"arxiv"}
    assert paper["source_links"]["open_access_url"].startswith("https://arxiv.org/pdf/")
    assert upstream.seen["arxiv"][0].query["id_list"] == ["1706.03762"]


def test_i3_old_style_ids_are_sent_whole(owner, project, upstream):
    do_import(owner, project["id"], arxiv_id="hep-th/9901001v2")
    assert upstream.seen["arxiv"][0].query["id_list"] == ["hep-th/9901001"]


def test_i3_unknown_and_malformed_ids(owner, project, upstream):
    missing = do_import(owner, project["id"], arxiv_id="2001.00001")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "arxiv_not_found"
    for bad in ("notanid", "1706", "../../x", "1706.03762; DROP"):
        response = do_import(owner, project["id"], arxiv_id=bad)
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "invalid_arxiv_id"
    for bad in ("nope", "10.1/short", "10.1234/a/../b"):
        response = do_import(owner, project["id"], doi=bad)
        assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_doi"
    assert upstream.count("arxiv") == 1  # only the well-formed unknown ID reached the source


def test_i3_arxiv_error_entry_is_unavailable_not_not_found(owner, project, upstream):
    upstream.set("arxiv", Reply(body="<feed"))
    response = do_import(owner, project["id"], arxiv_id="1706.03762")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "sources_unavailable"


@pytest.mark.parametrize(
    "reply",
    [Reply(body=b"{}", delay=5), Reply(503, ""), Reply(429, "", {"Retry-After": "30"})],
)
def test_i4_failing_sources_are_502_and_never_not_found(owner, project, upstream, reply):
    upstream.set("crossref", reply)
    upstream.set("openalex", reply)
    response = do_import(owner, project["id"], doi="10.1038/nature14539")
    assert response.status_code == 502, response.text
    error = response.json()["error"]
    assert error["code"] == "sources_unavailable"
    assert set(error["details"]["sources"]) == {"crossref", "openalex"}
    assert owner.client.get(f"/api/v1/projects/{project['id']}/papers").json()["total"] == 0


def test_i4_one_source_failing_and_the_other_not_found_is_still_a_failure(owner, project, upstream):
    upstream.set("crossref", Reply(503, ""))
    response = do_import(owner, project["id"], doi="10.9999/unknown")
    assert response.status_code == 502  # we cannot claim the DOI does not exist


def test_i5_record_without_title_is_refused(owner, project, upstream):
    body = {"message": {"DOI": "10.1234/untitled", "issued": {"date-parts": [[2020]]}}}
    upstream.set("crossref", Reply(body=json.dumps(body)))
    response = do_import(owner, project["id"], doi="10.1234/untitled")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "record_incomplete"
    assert owner.client.get(f"/api/v1/projects/{project['id']}/papers").json()["total"] == 0


def test_i6_duplicates_are_refused_including_the_arxiv_doi_alias(owner, project, upstream):
    first = do_import(owner, project["id"], arxiv_id="1706.03762")
    assert first.status_code == 201
    for body in (
        {"arxiv_id": "arXiv:1706.03762v3"},
        {"doi": "10.48550/arxiv.1706.03762"},
        {"doi": "https://doi.org/10.48550/ARXIV.1706.03762"},
    ):
        again = do_import(owner, project["id"], **body)
        assert again.status_code == 409, body
        error = again.json()["error"]
        assert error["code"] == "duplicate_paper"
        assert error["details"]["paper_id"] == first.json()["id"]
    doi = do_import(owner, project["id"], doi="10.1038/nature14539")
    assert doi.status_code == 201
    dup = do_import(owner, project["id"], doi="10.1038/NATURE14539")
    assert dup.status_code == 409 and dup.json()["error"]["details"]["paper_id"] == doi.json()["id"]
    # A different project is free to hold the same paper.
    other = owner.new_project("Other")
    assert do_import(owner, other["id"], arxiv_id="1706.03762").status_code == 201


def test_i6_concurrent_imports_of_one_identifier_leave_one_paper(owner, project, upstream):
    def slow(seen):
        time.sleep(0.4)
        return default_crossref(seen)

    upstream.set("crossref", slow)
    codes: list[int] = []

    def run() -> None:
        codes.append(do_import(owner, project["id"], doi="10.1038/nature14539").status_code)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(codes) == [201, 409, 409]
    assert owner.client.get(f"/api/v1/projects/{project['id']}/papers").json()["total"] == 1


def test_i7_client_supplied_metadata_is_rejected(owner, project, upstream):
    response = do_import(
        owner,
        project["id"],
        doi="10.1038/nature14539",
        title="Forged",
        authors=["Mallory"],
        metadata_provenance={"title": "crossref"},
    )
    assert response.status_code == 422
    assert do_import(owner, project["id"]).status_code == 422
    both = do_import(owner, project["id"], doi="10.1038/x", arxiv_id="1706.03762")
    assert both.status_code == 422
    assert owner.client.get(f"/api/v1/projects/{project['id']}/papers").json()["total"] == 0
    assert sum(upstream.count(n) for n in upstream.seen) == 0


def test_i8_paper_without_pdf_behaves_and_upload_becomes_v1(owner, project, upstream):
    paper = do_import(owner, project["id"], doi="10.1038/nature14539").json()
    base = paper_url(project["id"], paper["id"])
    fake_version = "00000000-0000-0000-0000-000000000001"
    for method, url in (
        ("get", f"{base}/versions/{fake_version}/blocks"),
        ("get", f"{base}/versions/{fake_version}/file"),
        ("post", f"{base}/versions/{fake_version}/parse"),
    ):
        assert getattr(owner.client, method)(url).status_code == 404, url
    assert owner.client.get(f"{base}/versions").json() == []

    # The title was set by Crossref; the PDF may not take it over.
    pdf = text_pdf("Parsed from the file", title="Embedded Title", author="Embedded Author")
    response = owner.client.post(
        f"{base}/versions",
        params={"filename": "deep.pdf"},
        content=pdf,
        headers={"Content-Type": "application/pdf"},
    )
    assert response.status_code == 202, response.text
    uploaded = response.json()
    assert uploaded["current_version"]["version_no"] == 1
    assert uploaded["parse_job"]["status"] == "queued"

    from researchos.worker import Worker

    Worker().run_once()
    final = owner.client.get(base).json()
    assert final["parse_job"]["status"] == "succeeded"
    assert final["title"] == "Deep learning"
    assert final["authors"] == ["Yann LeCun", "Yoshua Bengio", "Geoffrey Hinton"]
    assert final["metadata_provenance"]["title"] == "crossref"

    assert owner.client.delete(base).status_code == 204


def test_i8_pdf_metadata_still_fills_fields_nobody_has_set(owner, project, upstream):
    body = {"message": {"DOI": "10.1234/titleonly", "title": ["Only A Title"]}}
    upstream.set("crossref", Reply(body=json.dumps(body)))
    paper = do_import(owner, project["id"], doi="10.1234/titleonly").json()
    assert paper["authors"] == [] and "authors" not in paper["metadata_provenance"]
    pdf = text_pdf("text", title="Embedded Title", author="Embedded Author")
    owner.client.post(
        f"{paper_url(project['id'], paper['id'])}/versions",
        content=pdf,
        headers={"Content-Type": "application/pdf"},
    )
    from researchos.worker import Worker

    Worker().run_once()
    final = owner.client.get(paper_url(project["id"], paper["id"])).json()
    assert final["title"] == "Only A Title"
    assert final["authors"] == ["Embedded Author"]
    assert final["metadata_provenance"]["authors"] == "pdf_metadata"
    assert final["metadata_provenance"]["title"] == "crossref"


def test_i8_a_paper_without_pdf_can_be_edited_and_deleted(owner, project, upstream):
    paper = do_import(owner, project["id"], arxiv_id="1706.03762").json()
    url = paper_url(project["id"], paper["id"])
    edited = owner.client.patch(url, json={"year": 2018})
    assert edited.status_code == 200 and edited.json()["metadata_provenance"]["year"] == "user"
    assert owner.client.delete(url).status_code == 204
    assert owner.client.get(url).status_code == 404


def test_i9_arxiv_id_edits_are_normalized_and_unique(owner, project, upstream):
    first = do_import(owner, project["id"], arxiv_id="1706.03762").json()
    second = do_import(owner, project["id"], doi="10.1038/nature14539").json()
    url = paper_url(project["id"], second["id"])
    clash = owner.client.patch(url, json={"arxiv_id": "arXiv:1706.03762v2"})
    assert clash.status_code == 409
    error = clash.json()["error"]
    assert error["code"] == "duplicate_arxiv_id" and error["details"]["paper_id"] == first["id"]
    clash_url = owner.client.patch(url, json={"arxiv_id": "https://arxiv.org/abs/1706.03762"})
    assert clash_url.status_code == 409
    assert owner.client.get(url).json()["arxiv_id"] is None

    ok = owner.client.patch(url, json={"arxiv_id": "ARXIV:2301.00001V3"})
    assert ok.status_code == 200 and ok.json()["arxiv_id"] == "2301.00001"
    assert owner.client.patch(url, json={"arxiv_id": "garbage"}).status_code == 422
    assert owner.client.patch(url, json={"arxiv_id": ""}).json()["arxiv_id"] is None
    # Duplicate DOI still reports the DOI code.
    dup = owner.client.patch(url, json={"doi": first["doi"] or "10.5555/x"})
    assert dup.status_code == 200
    other = sign_up()
    assert other.client.patch(url, json={"year": 2000}).status_code == 404


def test_import_counts_against_the_user_limit_and_ledger(owner, project, upstream, settings):
    settings.set(search_user_per_minute=1)
    assert do_import(owner, project["id"], arxiv_id="1706.03762").status_code == 201
    limited = do_import(owner, project["id"], doi="10.1038/nature14539")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "search_rate_limited"
