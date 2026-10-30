"""pypdfium2-based extractor: text lines with real, verified page coordinates.

It is the default because it needs no model download and runs in seconds. It does not
reconstruct document structure (headings/sections/tables); Docling does that at much
higher cost. The warning below says so explicitly rather than implying paragraph
structure we did not compute.

Lines come from per-character iteration rather than count_rects/get_text_bounded: bounded
reads duplicated characters on 7 of 15 pages of a real paper, see specs/spikes.
"""

import time

import pypdfium2 as pdfium
import pypdfium2.raw as raw
from pypdfium2 import PdfiumError

from researchos.parsers.base import ParsedBlock, ParseError, ParseResult, display_bbox

PARSER_NAME = "pdfium"
LINE_BREAKS = {"\r", "\n"}
# PDFium's marker for a hyphen at a line end.
HYPHEN_MARKERS = {"\x02", "￾"}
REPLACEMENT = "�"
MAX_CODE_POINT = 0x10FFFF


def _char(code: int) -> str:
    if not code:
        return ""
    # A lone UTF-16 surrogate cannot be encoded as UTF-8, so it would fail the whole parse.
    if 0xD800 <= code <= 0xDFFF or code > MAX_CODE_POINT:
        return REPLACEMENT
    return chr(code)


def _page_lines(textpage) -> list[tuple[str, tuple[float, float, float, float]]]:
    lines = []
    chars: list[str] = []
    boxes: list[tuple[float, float, float, float]] = []

    def flush() -> None:
        text = "".join(chars).strip()
        if text and boxes:
            lines.append(
                (
                    text,
                    (
                        min(b[0] for b in boxes),
                        min(b[1] for b in boxes),
                        max(b[2] for b in boxes),
                        max(b[3] for b in boxes),
                    ),
                )
            )
        chars.clear()
        boxes.clear()

    for index in range(textpage.count_chars()):
        char = _char(raw.FPDFText_GetUnicode(textpage, index))
        if char in LINE_BREAKS:
            flush()
            continue
        chars.append("-" if char in HYPHEN_MARKERS else char)
        # Generated spaces have no ink, so they must not widen the box.
        if char.strip() and not raw.FPDFText_IsGenerated(textpage, index):
            box = textpage.get_charbox(index)
            if box[2] > box[0] and box[3] > box[1]:
                boxes.append(box)
    flush()
    return lines


def parse(path: str) -> ParseResult:
    started = time.monotonic()
    try:
        pdf = pdfium.PdfDocument(path)
    except PdfiumError as exc:
        # A corrupt or encrypted file will not get better by retrying.
        raise ParseError("pdf_unreadable", f"PDF could not be opened: {exc}") from exc

    try:
        blocks: list[ParsedBlock] = []
        no_text_pages: list[int] = []
        page_count = len(pdf)
        for page_index in range(page_count):
            page = pdf[page_index]
            textpage = page.get_textpage()
            try:
                lines = _page_lines(textpage)
                page_box = page.get_bbox()
                rotation = page.get_rotation()
            finally:
                textpage.close()
                page.close()
            if not lines:
                no_text_pages.append(page_index + 1)
                blocks.append(
                    ParsedBlock(
                        page_no=page_index + 1,
                        kind="page_text",
                        text="",
                        bbox=None,
                        granularity="page",
                    )
                )
            for text, box in lines:
                bbox = display_bbox(*box, page_box, rotation)
                # Text clipped away by the crop box collapses to an edge: it has no location.
                located = bbox[2] > bbox[0] and bbox[3] > bbox[1]
                blocks.append(
                    ParsedBlock(
                        page_no=page_index + 1,
                        kind="text_line",
                        text=text,
                        bbox=bbox if located else None,
                        granularity="block" if located else "page",
                    )
                )
        meta = pdf.get_metadata_dict()
    finally:
        pdf.close()

    if len(no_text_pages) == page_count:
        raise ParseError(
            "no_extractable_text",
            "no page has a text layer; this looks like a scan and needs an OCR parser",
        )

    warnings = []
    if no_text_pages:
        warnings.append(
            "pages without extractable text (scanned or image-only): "
            + ", ".join(str(p) for p in no_text_pages[:20])
            + ("..." if len(no_text_pages) > 20 else "")
        )
    warnings.append(
        "structure not reconstructed: blocks are text lines without heading/table labels"
    )

    author_raw = (meta.get("Author") or "").strip()
    return ParseResult(
        parser=PARSER_NAME,
        parser_version=f"pypdfium2 {pdfium.PYPDFIUM_INFO}, pdfium {pdfium.PDFIUM_INFO}",
        page_count=page_count,
        blocks=blocks,
        title=(meta.get("Title") or "").strip() or None,
        authors=[a.strip() for a in author_raw.replace(";", ",").split(",") if a.strip()],
        pdf_metadata={k: v for k, v in meta.items() if isinstance(v, str) and v.strip()},
        warnings=warnings,
        duration_seconds=round(time.monotonic() - started, 2),
    )
