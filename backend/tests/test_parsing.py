"""Acceptance C1-C3 in specs/001: real extraction, and coordinates checked against ink."""

import hashlib
import json
import uuid

import pytest

from researchos.db import session_factory
from researchos.models import Job
from researchos.parsers.base import ParseError, display_bbox
from researchos.parsers.pdfium_parser import parse
from researchos.worker import Worker, scrub
from tests.pdf_samples import (
    REAL_PAPER,
    corrupt_pdf,
    encrypted_pdf,
    ink_box,
    partly_scanned_pdf,
    scanned_pdf,
    single_page_pdf,
    text_pdf,
    transformed_pdf,
)

# Glyph boxes hug the ink, but anti-aliasing and side bearings move the edge by a pixel or so.
INK_TOLERANCE = 0.012


def run_upload(owner, data: bytes) -> tuple[dict, dict]:
    project = owner.new_project()
    paper = owner.upload(project["id"], data).json()
    assert Worker().run_once() is True
    detail = owner.client.get(f"/api/v1/projects/{project['id']}/papers/{paper['id']}").json()
    return project, detail


@pytest.mark.parametrize(
    "rotate", [0, 90, 180, 270], ids=lambda angle: f"rotate-{angle}"
)
@pytest.mark.parametrize(
    "crop", [None, (30, 300, 330, 590)], ids=["full-page", "cropped"]
)
def test_c2_bbox_matches_rendered_ink(tmp_path, rotate, crop):
    data = transformed_pdf(rotate, crop)
    path = tmp_path / "page.pdf"
    path.write_bytes(data)
    result = parse(str(path))
    lines = [block for block in result.blocks if block.kind == "text_line"]
    assert [line.text for line in lines] == ["Marker text"]
    assert lines[0].granularity == "block"
    assert lines[0].bbox == pytest.approx(ink_box(data), abs=INK_TOLERANCE)


def test_c2_text_outside_the_crop_box_is_not_given_a_location(tmp_path):
    data = transformed_pdf(0, (0, 0, 400, 300))
    path = tmp_path / "page.pdf"
    path.write_bytes(data)
    lines = [b for b in parse(str(path)).blocks if b.text == "Marker text"]
    assert len(lines) == 1
    assert lines[0].bbox is None and lines[0].granularity == "page"


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, [0.1, 0.7, 0.3, 0.8]),
        (90, [0.2, 0.1, 0.3, 0.3]),
        (180, [0.7, 0.2, 0.9, 0.3]),
        (270, [0.7, 0.7, 0.8, 0.9]),
    ],
)
def test_display_bbox_on_a_unit_page(rotation, expected):
    assert display_bbox(0.1, 0.2, 0.3, 0.3, (0, 0, 1, 1), rotation) == pytest.approx(expected)


def test_c1_blocks_carry_page_location_and_content_hash(owner):
    project, paper = run_upload(owner, text_pdf("Located words", pages=3))
    version = paper["current_version"]
    assert version["page_count"] == 4
    assert paper["parse_job"]["status"] == "succeeded"
    result = paper["parse_job"]["result"]
    assert result["parser"] == "pdfium" and "pdfium" in result["parser_version"]
    assert any("structure not reconstructed" in warning for warning in result["warnings"])

    url = (
        f"/api/v1/projects/{project['id']}/papers/{paper['id']}"
        f"/versions/{version['id']}/blocks"
    )
    blocks = owner.client.get(url).json()
    assert blocks["parsed"] is True and blocks["total"] == 4
    for block in blocks["items"]:
        digest = hashlib.sha256(block["text"].encode()).hexdigest()
        assert block["content_hash"] == f"sha256:{digest}"
        assert block["granularity"] == "block" and len(block["bbox"]) == 4
    assert [block["page_no"] for block in blocks["items"]] == [1, 2, 3, 4]

    second_page = owner.client.get(url, params={"page": 2}).json()
    assert [block["text"] for block in second_page["items"]] == ["Located words 2"]


@pytest.mark.skipif(not REAL_PAPER.exists(), reason="fetch samples/pdfs first, see samples/README")
def test_c1_real_paper(owner):
    project, paper = run_upload(owner, REAL_PAPER.read_bytes())
    assert paper["parse_job"]["status"] == "succeeded"
    version = paper["current_version"]
    assert version["page_count"] == 15
    url = (
        f"/api/v1/projects/{project['id']}/papers/{paper['id']}"
        f"/versions/{version['id']}/blocks"
    )
    blocks = owner.client.get(url, params={"limit": 2000}).json()
    assert blocks["total"] > 500
    text = " ".join(block["text"] for block in blocks["items"])
    assert "Attention Is All You Need" in text
    assert all(0 <= v <= 1 for block in blocks["items"] for v in block["bbox"] or [])


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (corrupt_pdf, "pdf_unreadable"),
        (encrypted_pdf, "pdf_unreadable"),
        (scanned_pdf, "no_extractable_text"),
    ],
    ids=["corrupt", "encrypted", "scanned"],
)
def test_c3_unusable_files_fail_with_their_own_code(owner, data, code):
    project, paper = run_upload(owner, data())
    job = paper["parse_job"]
    assert job["status"] == "failed"
    assert job["error_code"] == code
    assert job["attempts"] == 1, "a bad file must not be retried"

    version = paper["current_version"]
    base = f"/api/v1/projects/{project['id']}/papers/{paper['id']}/versions/{version['id']}"
    blocks = owner.client.get(f"{base}/blocks").json()
    assert blocks == {"items": [], "total": 0, "offset": 0, "limit": 500, "parsed": False}
    assert owner.client.get(f"{base}/file").status_code == 200

    events = owner.client.get(f"/api/v1/projects/{project['id']}/events").json()
    assert "paper.parse_failed" in {event["event_type"] for event in events}


def test_page_without_text_succeeds_with_a_warning_and_no_box(owner):
    project, paper = run_upload(owner, partly_scanned_pdf())
    assert paper["parse_job"]["status"] == "succeeded"
    warnings = paper["parse_job"]["result"]["warnings"]
    assert any(warning.endswith(": 2") for warning in warnings)
    version = paper["current_version"]["id"]
    blocks = owner.client.get(
        f"/api/v1/projects/{project['id']}/papers/{paper['id']}/versions/{version}/blocks",
        params={"page": 2},
    ).json()
    assert [(b["bbox"], b["granularity"]) for b in blocks["items"]] == [(None, "page")]


def test_scrub_drops_what_postgres_and_utf8_refuse():
    assert scrub("a\x00b\ud83d") == "ab?"


def test_parser_result_is_json_safe(tmp_path):
    path = tmp_path / "page.pdf"
    path.write_bytes(single_page_pdf())
    assert json.loads(json.dumps(parse(str(path)).to_dict()))["page_count"] == 1


def test_parse_error_type_is_stable_for_callers(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(corrupt_pdf())
    with pytest.raises(ParseError) as caught:
        parse(str(path))
    assert caught.value.code == "pdf_unreadable" and caught.value.retryable is False


def test_job_row_keeps_no_lease_after_finishing(owner):
    _, paper = run_upload(owner, text_pdf())
    with session_factory()() as db:
        job = db.get(Job, uuid.UUID(paper["parse_job"]["id"]))
        assert job.lease_owner is None and job.lease_expires_at is None
        assert job.finished_at is not None
