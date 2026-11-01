"""Normalized records shared by the adapters, the cache and the merge step."""

from dataclasses import asdict, dataclass, field

SOURCES = ("arxiv", "openalex", "crossref")


@dataclass
class Record:
    source: str
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    abstract: str | None = None
    citations: int | None = None
    open_access_url: str | None = None
    landing_url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Record":
        return cls(**data)


class BadResponse(Exception):
    """The upstream answered, but not with something usable."""


@dataclass
class SourceOutcome:
    """What one source contributed to a request. `status` is one of the spec 2.3 values, or
    `not_found` for identifier lookups that the source answered with 404."""

    source: str
    status: str
    records: list[Record] = field(default_factory=list)
    total: int | None = None
    message: str | None = None
    cached: bool = False
    fetched_at: str | None = None
    duration_ms: int = 0
    extra: dict = field(default_factory=dict)
