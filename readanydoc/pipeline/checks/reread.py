"""Where a block is on the page, and a model re-read of only that part.

Verify runs before Ground, so blocks have no boxes yet. ``locate`` runs the Ground stage on a copy of
the page (word alignment only, no model call) and returns the boxes it finds; the page itself is not
touched. ``ask`` sends one crop with a prompt to the page reader and records the call in ``doc.usage``.
"""

from __future__ import annotations

from typing import Optional

from ...model import BBox, Document, Page
from ...readers import make_reader
from ...readers.base import ReadContext

Box = tuple[float, float, float, float]  # x0, y0, x1, y1, normalised
_READERS: dict = {}


def reader_for(spec: str):
    if spec not in _READERS:
        _READERS[spec] = make_reader(spec)
    return _READERS[spec]


def locate(doc: Document, page: Page, ground_options: Optional[dict] = None, cache: Optional[dict] = None
           ) -> Optional[Page]:
    """A grounded copy of the page (regions in the same order, with ``bbox`` and table ``cells``).
    ``cache`` (a dict the caller keeps for the page) avoids grounding the same content twice."""
    cache = cache if cache is not None else {}
    key = tuple((r.id, hash(r.content)) for r in page.regions)
    if cache.get("key") == key:
        return cache["page"]
    try:
        from ..base import STAGES
        from .. import ground as _ground  # noqa: F401  (registers the stage)
        copy = page.model_copy(deep=True)
        STAGES["ground"](**(ground_options or {}))._page(doc, copy)
    except Exception as e:  # locating is best effort: a check without a box flags instead of repairing
        doc.note("verify", f"page {page.index}: could not locate blocks for checks ({e})", "warning")
        copy = None
    cache["key"], cache["page"] = key, copy
    return copy


def xyxy(b: BBox) -> Box:
    return (b.left, b.top, b.left + b.width, b.top + b.height)


def pad(box: Box, px: float = 0.015, py: float = 0.012) -> Box:
    x0, y0, x1, y1 = box
    return (max(0.0, x0 - px), max(0.0, y0 - py), min(1.0, x1 + px), min(1.0, y1 + py))


def union(boxes: list[Box]) -> Optional[Box]:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def crop_png(doc: Document, page: Page, box: Box, zoom: Optional[float] = None, max_dpi: float = 400.0) -> Optional[bytes]:
    """PNG of a normalised crop, rendered from the source at the largest size the model keeps
    (``zoom`` caps it at that multiple of the whole-page scale)."""
    from ...readers import sizing
    src = doc.meta.get("path")
    try:
        if doc.file_type == "pdf":
            return sizing.render_pdf_native(src, page.original_page, box=box, crop_zoom=zoom, max_dpi=max_dpi,
                                            password=doc.meta.get("password"))
        image = next(iter(page.renders.values()), None) or src
        return sizing.resize_image_native(image, box=box, max_scale=zoom or 3.0)
    except Exception as e:
        doc.note("verify", f"page {page.index}: crop failed ({e})", "warning")
        return None


def ask(doc: Document, page: Page, reader_spec: str, png: bytes, prompt: str, tag: str,
        max_tokens: Optional[int] = None) -> Optional[str]:
    """One crop read. Returns the text, or None on refusal, truncation, error or empty output."""
    try:
        reader = reader_for(reader_spec)
        res = reader.read(png, None, ReadContext(stem=f"{doc.stem}#ws3:{tag}", page_index=page.index, prompt=prompt,
                                                 max_tokens=max_tokens))
    except Exception as e:
        doc.note("verify", f"page {page.index}: re-read {tag} failed ({e})", "warning")
        return None
    doc.usage.append({"page": page.index, "reader": reader.name, "escalation": f"verify:{tag}",
                      **res.usage.model_dump(exclude_none=True)})
    if res.refused or res.truncated or not res.text.strip():
        return None
    return res.text
