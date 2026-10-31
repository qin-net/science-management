"""The Docling adapter, run the way the worker runs it: a separate interpreter, `<pdf> <out.json>`.

Docling lives in its own environment (D-002), so these tests need TEST_DOCLING_PYTHON, the
python of that environment. Without it they are skipped, and the skip says so: a green run
without Docling proves nothing about the adapter. Models must already be cached; the tests
run offline so they never download anything.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from researchos.parsers.base import ParseResult
from researchos.parsers.registry import DOCLING_RUNNER, parser_env
from tests.pdf_samples import REAL_PAPER, corrupt_pdf, ink_box, transformed_pdf

DOCLING_PYTHON = os.environ.get("TEST_DOCLING_PYTHON", "")

pytestmark = pytest.mark.skipif(
    not (DOCLING_PYTHON and os.access(DOCLING_PYTHON, os.X_OK)),
    reason="TEST_DOCLING_PYTHON is not set to a Docling environment's python; "
    "the Docling adapter was NOT tested",
)

# Measured on the same samples in specs/spikes: the largest gap was 0.004.
INK_TOLERANCE = 0.012


def run_runner(pdf: Path, tmp_path: Path) -> dict:
    out = tmp_path / "out.json"
    # Same argv and environment the worker builds in parsers/registry.py, plus offline mode.
    completed = subprocess.run(
        [DOCLING_PYTHON, str(DOCLING_RUNNER), str(pdf), str(out)],
        cwd=tmp_path,
        env={**parser_env(), "HF_HUB_OFFLINE": "1"},
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.mark.parametrize("rotate", [0, 90, 180, 270], ids=lambda angle: f"rotate-{angle}")
@pytest.mark.parametrize("crop", [None, (30, 300, 330, 590)], ids=["full-page", "cropped"])
def test_docling_boxes_match_rendered_ink(tmp_path, rotate, crop):
    data = transformed_pdf(rotate, crop)
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(data)

    payload = run_runner(pdf, tmp_path)

    assert "error" not in payload, payload
    blocks = ParseResult.from_dict(payload["result"]).blocks
    marker = [block for block in blocks if block.text == "Marker text"]
    assert len(marker) == 1 and marker[0].granularity == "block"
    assert marker[0].bbox == pytest.approx(ink_box(data), abs=INK_TOLERANCE)


def test_docling_reads_the_real_paper(tmp_path):
    if not REAL_PAPER.exists():
        pytest.skip(f"{REAL_PAPER.name} is not downloaded; see samples/README.md")

    payload = run_runner(REAL_PAPER, tmp_path)

    result = ParseResult.from_dict(payload["result"])
    assert result.parser == "docling" and result.parser_version.startswith("docling ")
    assert result.page_count == 15
    assert {block.kind for block in result.blocks} >= {"table", "picture", "section_header"}
    assert all(1 <= block.page_no <= result.page_count for block in result.blocks)
    located = [block for block in result.blocks if block.bbox]
    assert located, "no block carries a location"
    for block in located:
        x0, y0, x1, y1 = block.bbox
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1


def test_docling_unreadable_pdf_is_an_error_never_an_empty_result(tmp_path):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(corrupt_pdf())

    payload = run_runner(pdf, tmp_path)

    assert "result" not in payload
    assert payload["error"]["code"] == "pdf_unreadable"
    assert payload["error"]["retryable"] is False
