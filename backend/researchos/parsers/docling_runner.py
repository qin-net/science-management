"""Docling adapter. Runs inside the separate Docling environment (DOCLING_PYTHON), so it
imports only the stdlib-only parser contract from this package.

Docling's provenance boxes are already in the displayed page frame: rotation and crop are
applied, unlike PDFium's. Measured against rendered ink, see specs/spikes.
"""

import json
import sys
import time
from importlib.metadata import version
from pathlib import Path

from docling.datamodel.base_models import ConversionStatus
from docling.document_converter import DocumentConverter

from researchos.parsers.base import ParsedBlock, ParseResult

PARSER_NAME = "docling"
MARGIN_LABELS = {"page_header", "page_footer"}


def _bbox(box, width: float, height: float) -> list[float] | None:
    top_left = str(box.coord_origin).endswith("TOPLEFT")
    y0 = box.t if top_left else height - box.t
    y1 = box.b if top_left else height - box.b
    shown = [box.l / width, y0 / height, box.r / width, y1 / height]
    if shown[2] <= shown[0] or shown[3] <= shown[1]:
        return None
    return [round(min(max(value, 0.0), 1.0), 5) for value in shown]


def _blocks(doc) -> list[ParsedBlock]:
    blocks = []
    for item, _level in doc.iterate_items():
        label = str(getattr(item.label, "value", item.label))
        if label in MARGIN_LABELS:
            continue
        text = (getattr(item, "text", "") or "").strip()
        if label == "table":
            text = item.export_to_markdown(doc=doc).strip()
        for prov in getattr(item, "prov", None) or []:
            size = doc.pages[prov.page_no].size
            bbox = _bbox(prov.bbox, size.width, size.height)
            blocks.append(
                ParsedBlock(
                    page_no=prov.page_no,
                    kind=label,
                    text=text,
                    bbox=bbox,
                    granularity="block" if bbox else "page",
                )
            )
    return blocks


def parse(path: str) -> ParseResult:
    started = time.monotonic()
    result = DocumentConverter().convert(path, raises_on_error=False)
    if result.status not in {ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS}:
        return None
    doc = result.document
    warnings = []
    if result.status == ConversionStatus.PARTIAL_SUCCESS:
        warnings.append("docling converted this document only partially")
    return ParseResult(
        parser=PARSER_NAME,
        parser_version=f"docling {version('docling')}",
        page_count=len(doc.pages),
        blocks=_blocks(doc),
        warnings=warnings,
        duration_seconds=round(time.monotonic() - started, 2),
    )


def main(argv: list[str]) -> int:
    pdf_path, out_path = argv
    result = parse(pdf_path)
    if result is None:
        payload = {
            "error": {
                "code": "pdf_unreadable",
                "message": "docling could not convert this file",
                "retryable": False,
            }
        }
    else:
        payload = {"result": result.to_dict()}
    Path(out_path).write_text(json.dumps(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
