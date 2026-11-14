"""Layout detector interface: page image -> typed, boxed blocks in reading order."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel

from ..model import BBox, RegionType
from ..readers.base import ImageInput, ReadContext


class LayoutBlock(BaseModel):
    bbox: BBox
    type: RegionType
    native_label: Optional[str] = None
    order: int = 0
    score: Optional[float] = None
    text: Optional[str] = None  # the detector pipeline's own read of the block, if it has one


class LayoutError(RuntimeError):
    pass


class LayoutDetector(ABC):
    name: str = "layout"

    @abstractmethod
    def detect(self, image: ImageInput, context: Optional[ReadContext] = None) -> list[LayoutBlock]: ...


# PP-DocLayout / GLM native labels -> Reducto block types
LABEL_MAP: dict[str, RegionType] = {
    "text": RegionType.TEXT, "content": RegionType.TEXT, "abstract": RegionType.TEXT,
    "reference_content": RegionType.TEXT, "reference": RegionType.TEXT, "aside_text": RegionType.TEXT,
    "footnote": RegionType.TEXT, "vision_footnote": RegionType.TEXT, "figure_title": RegionType.TEXT,
    "display_formula": RegionType.TEXT, "formula": RegionType.TEXT, "formula_number": RegionType.TEXT,
    "algorithm": RegionType.TEXT,
    "paragraph_title": RegionType.SECTION_HEADER, "doc_title": RegionType.TITLE,
    "header": RegionType.HEADER, "header_image": RegionType.HEADER,
    "footer": RegionType.FOOTER, "footer_image": RegionType.FOOTER,
    "number": RegionType.PAGE_NUMBER,
    "image": RegionType.FIGURE, "chart": RegionType.FIGURE, "seal": RegionType.FIGURE,
    "table": RegionType.TABLE,
}


def map_label(label: str | None) -> RegionType:
    return LABEL_MAP.get((label or "").lower(), RegionType.TEXT)
