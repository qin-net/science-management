"""Parser contract shared by every extractor.

A parser turns a PDF path into located blocks plus a small metadata report. It never
invents coordinates: when a locator is unavailable a block is emitted at page granularity
with bbox=None so downstream code can degrade honestly instead of drawing a fake rectangle.
"""

from dataclasses import asdict, dataclass, field


class ParseError(Exception):
    """Raised by a parser. `retryable` decides whether the job is retried or failed."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class ParsedBlock:
    page_no: int  # 1-based
    kind: str
    text: str
    bbox: list[float] | None  # [x0, y0, x1, y1], normalized, origin top-left of the shown page
    granularity: str  # "block" or "page"


@dataclass
class ParseResult:
    parser: str
    parser_version: str
    page_count: int
    blocks: list[ParsedBlock] = field(default_factory=list)
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    pdf_metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ParseResult":
        blocks = [ParsedBlock(**block) for block in data["blocks"]]
        return cls(**{**data, "blocks": blocks})


def display_bbox(
    left: float,
    bottom: float,
    right: float,
    top: float,
    page_box: tuple[float, float, float, float],
    rotation: int,
) -> list[float]:
    """Map a rectangle in PDF user space to the page as displayed.

    PDFium reports text in unrotated user space, while readers draw the cropped, rotated
    page. Mapping was measured against rendered ink: see specs/spikes.
    """
    box_left, box_bottom, box_right, box_top = page_box
    width = box_right - box_left
    height = box_top - box_bottom
    x0, x1 = (left - box_left) / width, (right - box_left) / width
    y0, y1 = (bottom - box_bottom) / height, (top - box_bottom) / height
    if rotation == 90:
        shown = (y0, x0, y1, x1)
    elif rotation == 180:
        shown = (1 - x1, y0, 1 - x0, y1)
    elif rotation == 270:
        shown = (1 - y1, 1 - x1, 1 - y0, 1 - x0)
    else:
        shown = (x0, 1 - y1, x1, 1 - y0)
    return [round(min(max(value, 0.0), 1.0), 5) for value in shown]
