"""Document -> Page[] -> Region[] -> Line[] -> Word[] (pydantic v2, JSON-serialisable).

Conventions
- All boxes are normalised 0-1 with a top-left origin; ``BBox.page`` is the 0-based index
  into ``Document.pages``. The 1-based page numbers Reducto wants are produced by the serializer.
- ``content`` is what is shown to the user (markdown; tables as HTML). ``text`` is plain text.
- Nothing is overwritten: every model output stays in ``Region.candidates``; independent reads
  that agree or disagree are recorded in ``evidence``.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Iterable, Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class RegionType(str, Enum):
    """Reducto's 12 block types."""

    HEADER = "Header"
    FOOTER = "Footer"
    TITLE = "Title"
    SECTION_HEADER = "Section Header"
    PAGE_NUMBER = "Page Number"
    LIST_ITEM = "List Item"
    FIGURE = "Figure"
    TABLE = "Table"
    KEY_VALUE = "Key Value"
    TEXT = "Text"
    COMMENT = "Comment"
    SIGNATURE = "Signature"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=False)


class BBox(_Model):
    left: float
    top: float
    width: float
    height: float
    page: int = 0  # 0-based index into Document.pages

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def area(self) -> float:
        return max(self.width, 0.0) * max(self.height, 0.0)

    @property
    def center(self) -> tuple[float, float]:
        return (self.left + self.width / 2, self.top + self.height / 2)

    @classmethod
    def from_xyxy(cls, x0: float, y0: float, x1: float, y1: float, page: int = 0,
                  page_width: float = 1.0, page_height: float = 1.0) -> "BBox":
        """Build from absolute corner coordinates; divides by the page size and clamps to 0-1."""
        pw, ph = (page_width or 1.0), (page_height or 1.0)
        l, t = min(x0, x1) / pw, min(y0, y1) / ph
        r, b = max(x0, x1) / pw, max(y0, y1) / ph
        l, t, r, b = (min(max(v, 0.0), 1.0) for v in (l, t, r, b))
        return cls(left=l, top=t, width=r - l, height=b - t, page=page)

    def contains_point(self, x: float, y: float, pad: float = 0.0) -> bool:
        return (self.left - pad <= x <= self.right + pad) and (self.top - pad <= y <= self.bottom + pad)

    def intersection_area(self, other: "BBox") -> float:
        if other.page != self.page:
            return 0.0
        w = min(self.right, other.right) - max(self.left, other.left)
        h = min(self.bottom, other.bottom) - max(self.top, other.top)
        return w * h if w > 0 and h > 0 else 0.0

    def iou(self, other: "BBox") -> float:
        inter = self.intersection_area(other)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    @classmethod
    def union(cls, boxes: Iterable["BBox"]) -> Optional["BBox"]:
        boxes = [b for b in boxes if b is not None]
        if not boxes:
            return None
        l = min(b.left for b in boxes)
        t = min(b.top for b in boxes)
        r = max(b.right for b in boxes)
        bt = max(b.bottom for b in boxes)
        return cls(left=l, top=t, width=r - l, height=bt - t, page=boxes[0].page)


class Confidence(_Model):
    """0-1 score plus Reducto's high/low label. Default is "low": unverified is not trusted."""

    score: Optional[float] = None
    label: Literal["high", "low"] = "low"

    @classmethod
    def of(cls, score: float, high_at: float = 0.85) -> "Confidence":
        score = min(max(float(score), 0.0), 1.0)
        return cls(score=round(score, 4), label="high" if score >= high_at else "low")


class Evidence(_Model):
    """One independent read compared with this node (PDF layer, OCR, second model, ...)."""

    source: str
    verdict: Literal["agree", "disagree", "info"] = "info"
    score: Optional[float] = None  # agreement 0-1 when measurable
    text: Optional[str] = None
    note: Optional[str] = None


class Candidate(_Model):
    """A model output for a region. Candidates are appended, never replaced."""

    source: str  # e.g. "cached:haiku", "layout:pvl"
    content: str
    format: Literal["markdown", "html", "text"] = "markdown"
    usage: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class _Node(_Model):
    text: str = ""
    bbox: Optional[BBox] = None
    source: Optional[str] = None  # which reader/stage produced the text
    confidence: Confidence = Field(default_factory=Confidence)
    evidence: list[Evidence] = Field(default_factory=list)


class Word(_Node):
    line: Optional[int] = None  # line index within the word source (page-level word lists)
    char_start: Optional[int] = None  # offsets into Region.content (grounded words)
    char_end: Optional[int] = None
    bbox_source: Optional[str] = None  # which word source supplied the box


class Line(_Node):
    words: list[Word] = Field(default_factory=list)


class TableCell(_Model):
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    text: str = ""
    is_header: bool = False
    bbox: Optional[BBox] = None
    char_start: Optional[int] = None  # span of the cell's inner HTML in Region.content
    char_end: Optional[int] = None
    confidence: Confidence = Field(default_factory=Confidence)


class Region(_Node):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: RegionType = RegionType.TEXT
    order: int = 0  # reading-order index within the page
    content: str = ""  # markdown; tables as HTML with rowspan/colspan
    content_format: Literal["markdown", "html", "text"] = "markdown"
    heading_level: Optional[int] = None
    native_label: Optional[str] = None  # the layout detector's own label
    lines: list[Line] = Field(default_factory=list)
    cells: list[TableCell] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    def iter_words(self) -> Iterator[Word]:
        for line in self.lines:
            yield from line.words


class Page(_Model):
    index: int  # 0-based position in Document.pages
    original_page: int = 1  # 1-based page number in the source file
    width: float = 0.0
    height: float = 0.0
    unit: Literal["pt", "px"] = "pt"
    renders: dict[str, str] = Field(default_factory=dict)  # dpi (as str) -> image path
    kind: Optional[Literal["digital", "scanned", "photo"]] = None
    rotation: int = 0  # 0/90/180/270
    language: Optional[str] = None
    script: Optional[str] = None
    text_layer_quality: Optional[float] = None  # 0-1 agreement of the PDF text layer with OCR
    text_layer_usable: Optional[bool] = None
    properties: dict[str, Any] = Field(default_factory=dict)  # density, word counts, coverage...
    words: dict[str, list[Word]] = Field(default_factory=dict)  # word source -> page words
    regions: list[Region] = Field(default_factory=list)
    layout_regions: list[Region] = Field(default_factory=list)  # raw detector output (provenance)

    def page_bbox(self) -> BBox:
        return BBox(left=0.0, top=0.0, width=1.0, height=1.0, page=self.index)


class HeadingNode(_Model):
    title: str
    level: int
    region_id: Optional[str] = None
    page: int = 0
    children: list["HeadingNode"] = Field(default_factory=list)


class CrossPageLink(_Model):
    kind: Literal["table_continuation", "paragraph_continuation"]
    from_region: str
    to_region: str
    confidence: Confidence = Field(default_factory=Confidence)


class Chunk(_Model):
    """Shaped view over regions; written by the Shape stage, read by the serializer."""

    content: str = ""
    embed: str = ""
    enriched: Optional[str] = None
    region_ids: list[str] = Field(default_factory=list)


class StageNote(_Model):
    stage: str
    message: str
    level: Literal["info", "warning", "error"] = "info"


class Document(_Model):
    schema_version: int = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: Optional[str] = None  # path / url / cached stem as given
    stem: Optional[str] = None  # cache key for the cached adapters
    file_type: Optional[str] = None
    pages: list[Page] = Field(default_factory=list)
    heading_tree: list[HeadingNode] = Field(default_factory=list)
    links: list[CrossPageLink] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)  # title, author, ... (PDF metadata)
    chunks: list[Chunk] = Field(default_factory=list)
    usage: list[dict[str, Any]] = Field(default_factory=list)  # one entry per model call
    notes: list[StageNote] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)  # profile, model tier, duration, ...

    # ---- helpers (stable API) ----
    def iter_regions(self) -> Iterator[Region]:
        for page in self.pages:
            yield from page.regions

    def region_by_id(self, region_id: str) -> Optional[Region]:
        for region in self.iter_regions():
            if region.id == region_id:
                return region
        return None

    def page_of(self, region: Region) -> Optional[Page]:
        for page in self.pages:
            if any(r is region or r.id == region.id for r in page.regions):
                return page
        return None

    def note(self, stage: str, message: str, level: str = "info") -> None:
        self.notes.append(StageNote(stage=stage, message=message, level=level))  # type: ignore[arg-type]

    def to_json(self, **kwargs: Any) -> str:
        return self.model_dump_json(**kwargs)

    @classmethod
    def from_json(cls, data: str | bytes) -> "Document":
        return cls.model_validate_json(data)


HeadingNode.model_rebuild()
