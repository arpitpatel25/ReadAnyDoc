"""Shared helpers for digital-native readers: regions, flow boxes, text decoding."""

from __future__ import annotations

import datetime as dt
import html
from typing import Any, Optional

from ..model import BBox, Confidence, Page, Region, RegionType

EXACT = Confidence(score=1.0, label="high")  # text copied from the file itself


def make_region(rtype: RegionType, content: str, source: str, *, level: Optional[int] = None,
                fmt: str = "markdown", bbox: Optional[BBox] = None, **meta: Any) -> Region:
    from ..textnorm import plain_text
    return Region(type=rtype, content=content, text=plain_text(content), source=source, heading_level=level,
                  content_format=fmt, bbox=bbox, confidence=EXACT.model_copy(),
                  meta={k: v for k, v in meta.items() if v is not None})


def new_page(index: int, original_page: int, width: float = 612.0, height: float = 792.0, unit: str = "pt",
             **props: Any) -> Page:
    return Page(index=index, original_page=original_page, width=width, height=height, unit=unit,  # type: ignore[arg-type]
                kind="digital", text_layer_usable=True, text_layer_quality=1.0,
                properties={k: v for k, v in props.items() if v is not None})


def flow_boxes(page: Page, margin: float = 0.08) -> None:
    """Formats without geometry (DOCX, TXT, EML): stack blocks top to bottom, height by text length.

    The boxes give reading order and rough position only; they are flagged ``bbox_estimated``.
    """
    regions = [r for r in page.regions if r.bbox is None]
    if not regions:
        return
    weights = []
    for r in regions:
        lines = r.content.count("\n") + 1 + len(r.content) // 90
        if r.type == RegionType.TABLE:
            lines = max(r.content.count("<tr"), 1) + 1
        weights.append(max(lines, 1))
    total = float(sum(weights)) or 1.0
    y = margin
    span = 1.0 - 2 * margin
    for r, w in zip(regions, weights):
        h = span * w / total
        r.bbox = BBox(left=margin, top=round(y, 5), width=round(1 - 2 * margin, 5), height=round(h, 5),
                      page=page.index)
        r.meta["bbox_estimated"] = True
        y += h


def finish_pages(pages: list[Page]) -> list[Page]:
    for i, p in enumerate(pages):
        p.index = i
        for k, r in enumerate(p.regions):
            r.order = k
            if r.bbox is not None:
                r.bbox.page = i
        flow_boxes(p)
    return pages


def decode_text(data: bytes) -> tuple[str, str]:
    """(text, encoding). BOMs first, then UTF-8, then Windows-1252 as the lossless last resort."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(enc, errors="replace"), enc
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def iso(value: Any) -> Optional[str]:
    """Datetime -> ISO 8601 with an offset; naive values are read as UTC (Reducto's rule)."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc).isoformat()
    return str(value)
