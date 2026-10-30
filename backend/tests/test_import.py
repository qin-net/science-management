"""Acceptance B1-B7 in specs/001: uploading, deduplicating and describing papers."""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import func, select

from researchos.config import get_settings
from researchos.db import session_factory
from researchos.models import Job, Paper, PaperVersion, ResearchEvent
from researchos.worker import Worker
from tests.conftest import MAX_UPLOAD_MB, sign_up
from tests.pdf_samples import text_pdf


def stored_files() -> list[Path]:
    root = get_settings().storage_dir
    return [path for path in root.rglob("*") if path.is_file()] if root.exists() else []


def paper_count() -> int:
    with session_factory()() as db:
        return db.scalar(select(func.count()).select_from(Paper))


def test_b1_upload_keeps_exact_bytes_and_records_the_event(owner):
    project = owner.new_project()
    data = text_pdf()
    response = owner.upload(project["id"], data, "attention.pdf")
    assert response.status_code == 202, response.text
    paper = response.json()
    version = paper["current_version"]
    assert version["sha256"] == hashlib.sha256(data).hexdigest()
    assert version["size_bytes"] == len(data)
    assert version["original_filename"] == "attention.pdf"
    assert paper["parse_job"]["status"] == "queued"

    url = (
        f"/api/v1/projects/{project['id']}/papers/{paper['id']}/versions/{version['id']}/file"
    )
    download = owner.client.get(url)
    assert download.status_code == 200
    assert hashlib.sha256(download.content).hexdigest() == version["sha256"]
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-security-policy"] == "sandbox"

    events = owner.client.get(f"/api/v1/projects/{project['id']}/events").json()
    assert {"paper.imported", "paper.parse_queued"} <= {event["event_type"] for event in events}


@pytest.mark.parametrize(
    ("body", "content_type", "status", "code"),
    [
        (b"%PDF-" + b"x" * (MAX_UPLOAD_MB * 1024 * 1024), "application/pdf", 413, "file_too_large"),
        (b"PK\x03\x04 this is a zip", "application/pdf", 415, "not_a_pdf"),
        (b"", "application/pdf", 415, "not_a_pdf"),
        (b"%PDF-1.7 right bytes wrong type", "text/plain", 415, "not_a_pdf"),
    ],
    ids=["too-large", "wrong-magic", "empty", "wrong-content-type"],
)
def test_b2_rejected_uploads_leave_nothing_behind(owner, body, content_type, status, code):
    project = owner.new_project()
    response = owner.client.post(
        f"/api/v1/projects/{project['id']}/papers",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert paper_count() == 0
    assert stored_files() == []


def test_b2_oversized_chunked_upload_without_length_is_stopped(owner):
    project = owner.new_project()

    def chunks():
        yield b"%PDF-"
        for _ in range(MAX_UPLOAD_MB + 1):
            yield b"x" * (1024 * 1024)

    response = owner.client.post(
        f"/api/v1/projects/{project['id']}/papers",
        content=chunks(),
        headers={"Content-Type": "application/pdf"},
    )
    assert response.status_code == 413
    assert paper_count() == 0
    assert stored_files() == []


def test_b3_duplicates_point_at_the_existing_paper(owner):
    first_project = owner.new_project("First")
    data = text_pdf()
    first = owner.upload(first_project["id"], data).json()

    again = owner.upload(first_project["id"], data)
    assert again.status_code == 409
    error = again.json()["error"]
    assert error["code"] == "duplicate_file"
    assert error["details"]["paper_id"] == first["id"]

    second_project = owner.new_project("Second")
    assert owner.upload(second_project["id"], data).status_code == 202


def test_b3_concurrent_uploads_keep_one_copy(owner):
    project = owner.new_project()
    data = text_pdf()
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = sorted(
            pool.map(lambda _: owner.upload(project["id"], data).status_code, range(4))
        )
    assert codes == [202, 409, 409, 409]
    assert paper_count() == 1
    assert len([path for path in stored_files() if path.suffix == ".pdf"]) == 1


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("..\\..\\windows\\evil.pdf", "evil.pdf"),
        ("bad\x00na\x1fme\r\n.pdf", "badname.pdf"),
        ("", "upload.pdf"),
    ],
)
def test_b4_filenames_never_reach_the_path_or_the_header_raw(owner, raw, shown):
    project = owner.new_project()
    response = owner.upload(project["id"], text_pdf(), raw)
    assert response.status_code == 202, response.text
    paper = response.json()
    version = paper["current_version"]
    assert version["original_filename"] == shown

    with session_factory()() as db:
        row = db.get(PaperVersion, uuid.UUID(version["id"]))
        assert row.storage_key.endswith(f"{version['id']}.pdf")
        assert ".." not in row.storage_key
    root = get_settings().storage_dir.resolve()
    assert all(root in path.resolve().parents for path in stored_files())

    file_url = (
        f"/api/v1/projects/{project['id']}/papers/{paper['id']}/versions/{version['id']}/file"
    )
    disposition = owner.client.get(file_url).headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition and ".." not in disposition


def test_b5_new_version_leaves_the_old_one_and_its_blocks_alone(owner):
    project = owner.new_project()
    base = f"/api/v1/projects/{project['id']}/papers"
    paper = owner.upload(project["id"], text_pdf("Original words")).json()
    Worker().run_once()
    old_id = paper["current_version"]["id"]
    old_blocks = owner.client.get(f"{base}/{paper['id']}/versions/{old_id}/blocks").json()

    response = owner.client.post(
        f"{base}/{paper['id']}/versions",
        content=text_pdf("Revised words"),
        headers={"Content-Type": "application/pdf"},
    )
    assert response.status_code == 202, response.text
    updated = response.json()
    assert updated["current_version"]["version_no"] == 2
    Worker().run_once()

    versions = owner.client.get(f"{base}/{paper['id']}/versions").json()
    assert [v["version_no"] for v in versions] == [2, 1]
    assert owner.client.get(f"{base}/{paper['id']}/versions/{old_id}/blocks").json() == old_blocks
    new_blocks = owner.client.get(
        f"{base}/{paper['id']}/versions/{updated['current_version']['id']}/blocks"
    ).json()
    assert "Revised words" in new_blocks["items"][0]["text"]
    assert "Original words" in old_blocks["items"][0]["text"]


def test_b6_parsing_fills_gaps_but_never_overwrites_the_user(owner):
    project = owner.new_project()
    base = f"/api/v1/projects/{project['id']}/papers"
    paper = owner.upload(
        project["id"], text_pdf(title="Embedded Title", author="Ada Lovelace; Alan Turing")
    ).json()
    patched = owner.client.patch(f"{base}/{paper['id']}", json={"title": "My Title"})
    assert patched.status_code == 200

    Worker().run_once()
    parsed = owner.client.get(f"{base}/{paper['id']}").json()
    assert parsed["title"] == "My Title"
    assert parsed["metadata_provenance"]["title"] == "user"
    assert parsed["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert parsed["metadata_provenance"]["authors"] == "pdf_metadata"


def test_b6_missing_metadata_stays_null_instead_of_using_the_filename(owner):
    project = owner.new_project()
    paper = owner.upload(project["id"], text_pdf(), "my-draft.pdf").json()
    Worker().run_once()
    parsed = owner.client.get(f"/api/v1/projects/{project['id']}/papers/{paper['id']}").json()
    assert parsed["parse_job"]["status"] == "succeeded"
    assert parsed["title"] is None
    assert parsed["authors"] == []
    assert parsed["year"] is None and parsed["doi"] is None


def test_b7_doi_is_normalized_and_unique_within_the_project(owner):
    project = owner.new_project()
    base = f"/api/v1/projects/{project['id']}/papers"
    first = owner.upload(project["id"], text_pdf()).json()
    second = owner.upload(project["id"], text_pdf()).json()

    saved = owner.client.patch(
        f"{base}/{first['id']}", json={"doi": "https://doi.org/10.1234/ABC.def"}
    )
    assert saved.json()["doi"] == "10.1234/abc.def"

    clash = owner.client.patch(f"{base}/{second['id']}", json={"doi": "doi: 10.1234/abc.DEF"})
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "duplicate_doi"
    assert clash.json()["error"]["details"]["paper_id"] == first["id"]

    invalid = owner.client.patch(f"{base}/{second['id']}", json={"doi": "not-a-doi"})
    assert invalid.status_code == 422


def test_listing_filters_by_status_and_reports_worker_absence(owner):
    project = owner.new_project()
    owner.upload(project["id"], text_pdf())
    base = f"/api/v1/projects/{project['id']}/papers"

    page = owner.client.get(base).json()
    assert page["total"] == 1 and page["worker_online"] is False
    assert owner.client.get(base, params={"status": "succeeded"}).json()["total"] == 0

    Worker().run_once()
    page = owner.client.get(base).json()
    assert page["worker_online"] is True
    assert owner.client.get(base, params={"status": "succeeded"}).json()["total"] == 1
    assert owner.client.get(base, params={"status": "queued"}).json()["total"] == 0


def test_delete_refuses_active_jobs_then_removes_rows_and_file(owner):
    project = owner.new_project()
    base = f"/api/v1/projects/{project['id']}/papers"
    paper = owner.upload(project["id"], text_pdf()).json()

    busy = owner.client.delete(f"{base}/{paper['id']}")
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "job_active"

    Worker().run_once()
    assert owner.client.delete(f"{base}/{paper['id']}").status_code == 204
    assert owner.client.get(f"{base}/{paper['id']}").status_code == 404
    assert stored_files() == []
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        deleted = db.scalars(
            select(ResearchEvent).where(ResearchEvent.event_type == "paper.deleted")
        ).all()
    assert len(deleted) == 1


def test_failed_commit_removes_the_stored_file(owner, monkeypatch):
    """Spec 001 section 5: a commit that fails after the move must clean up after itself."""
    from sqlalchemy.orm import Session

    project = owner.new_project()
    original = Session.commit

    def fail_when_file_is_in_place(self):
        if [path for path in stored_files() if path.suffix == ".pdf"]:
            raise RuntimeError("commit failed")
        return original(self)

    monkeypatch.setattr(Session, "commit", fail_when_file_is_in_place)
    response = owner.upload(project["id"], text_pdf())
    monkeypatch.setattr(Session, "commit", original)

    assert response.status_code == 500
    assert stored_files() == []
    assert paper_count() == 0


def test_second_account_sees_an_empty_workspace(owner):
    project = owner.new_project()
    owner.upload(project["id"], text_pdf())
    other = sign_up()
    assert other.client.get(f"/api/v1/workspaces/{other.workspace_id}/projects").json() == []
