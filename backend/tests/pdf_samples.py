"""Generates real PDF files for tests and reads back where their ink actually lands."""

import io
import uuid
from pathlib import Path

import pypdfium2 as pdfium
from fpdf import FPDF
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PAPER = REPO_ROOT / "samples" / "pdfs" / "1706.03762.pdf"
PAGE_SIZE = (400, 600)


def text_pdf(
    marker: str = "Marker text",
    *,
    pages: int = 1,
    title: str | None = None,
    author: str | None = None,
) -> bytes:
    """A unique file on each call, so a test only hits dedupe when it means to."""
    pdf = FPDF(unit="pt", format=PAGE_SIZE)
    pdf.set_font("helvetica", size=14)
    if title:
        pdf.set_title(title)
    if author:
        pdf.set_author(author)
    for number in range(pages):
        pdf.add_page()
        pdf.text(60, 100, f"{marker} {number + 1}" if pages > 1 else marker)
    pdf.add_page()
    pdf.set_font("helvetica", size=6)
    pdf.text(60, 100, uuid.uuid4().hex)
    return bytes(pdf.output())


def single_page_pdf(marker: str = "Marker text") -> bytes:
    pdf = FPDF(unit="pt", format=PAGE_SIZE)
    pdf.set_font("helvetica", size=14)
    pdf.add_page()
    pdf.text(60, 100, marker)
    return bytes(pdf.output())


def transformed_pdf(rotate: int = 0, crop: tuple[int, int, int, int] | None = None) -> bytes:
    reader = PdfReader(io.BytesIO(single_page_pdf()))
    writer = PdfWriter()
    page = reader.pages[0]
    if crop:
        page.cropbox = RectangleObject(crop)
    if rotate:
        page.rotate(rotate)
    writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def scanned_pdf() -> bytes:
    """Pixels only: no text layer, like a scanner's output."""
    image = Image.new("RGB", (400, 600), "white")
    image.paste((0, 0, 0), (60, 90, 300, 110))
    pdf = FPDF(unit="pt", format=PAGE_SIZE)
    pdf.add_page()
    pdf.image(image, 0, 0, 400, 600)
    return bytes(pdf.output())


def partly_scanned_pdf() -> bytes:
    reader = PdfWriter()
    for source in (single_page_pdf(), scanned_pdf()):
        reader.add_page(PdfReader(io.BytesIO(source)).pages[0])
    out = io.BytesIO()
    reader.write(out)
    return out.getvalue()


def encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(single_page_pdf())).pages[0])
    writer.encrypt("open-sesame")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def corrupt_pdf() -> bytes:
    return b"%PDF-1.7\n" + b"this is not a real pdf body\n" * 50


def ink_box(data: bytes, page_index: int = 0) -> list[float]:
    """Bounds of everything dark on the rendered page, normalized like the parser's bbox."""
    document = pdfium.PdfDocument(data)
    image = document[page_index].render(scale=3).to_pil().convert("L")
    width, height = image.size
    left, top, right, bottom = image.point(lambda v: 255 if v < 200 else 0).getbbox()
    document.close()
    return [left / width, top / height, right / width, bottom / height]
