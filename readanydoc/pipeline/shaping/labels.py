"""Block labels tidied after all reading is done (Shape stage, before figures). Deterministic, no model call.

- A page with fewer than ``kv_min`` Key Value blocks is not a form: its Key Value blocks become Text (prose that
  starts with a bold label, "**Damage:** Slam 1d8+1"). Measured on all 1,651 OmniDocBench pages (+0.8 layout type
  recall) with FUNSD form links unchanged (77.4%). Decision record: docs/decisions.md.
- A page number the page read left out (common: readers skip a bare "12" in the margin) is added as a Page Number
  block when the page's text layer (or, on scans and photos, local OCR) has one in the top or bottom tenth of the
  page that no block covers. OmniDocBench
  page-number recall 72% -> 86%, layout type recall +0.5, precision unchanged.
"""

from __future__ import annotations

import re
from typing import Optional

from ...model import BBox, Confidence, Document, Page, Region, RegionType

PAGE_NUMBER = re.compile(r"^\s*(?:page\s*)?[-–—(\[]?\s*(\d{1,4}|[ivxlcdm]{1,6})\s*[-–—)\]]?\s*(?:(?:/|of)\s*\d{1,4})?\s*$"
                         r"|^\s*第\s*\d{1,4}\s*页\s*$", re.I)


def _overlaps(a: BBox, b: BBox) -> bool:
    return min(a.left + a.width, b.left + b.width) > max(a.left, b.left) and \
        min(a.top + a.height, b.top + b.height) > max(a.top, b.top)


def _page_image(doc: Document, page: Page) -> Optional[str]:
    from ..grounding.sources import page_image
    try:
        return page_image(doc, page)
    except Exception:
        return None


class _Line:
    def __init__(self, text: str, bbox: BBox, score: float):
        self.text, self.bbox, self.score = text, bbox, score


def _layer_lines(page: Page) -> Optional[list]:
    """Lines of a usable PDF text layer (words grouped by their line number), or None when there is none, so a
    digital page never pays for OCR."""
    words = page.words.get("pdf") if page.text_layer_usable else None
    if not words:
        return None
    by: dict = {}
    for w in words:
        if w.bbox is not None:
            by.setdefault(w.line, []).append(w)
    out = []
    for ws in by.values():
        x0 = min(w.bbox.left for w in ws); y0 = min(w.bbox.top for w in ws)
        x1 = max(w.bbox.left + w.bbox.width for w in ws); y1 = max(w.bbox.top + w.bbox.height for w in ws)
        ws.sort(key=lambda w: w.bbox.left)
        out.append(_Line(" ".join(w.text for w in ws), BBox(left=x0, top=y0, width=x1 - x0, height=y1 - y0,
                                                              page=ws[0].bbox.page), 1.0))
    return out


def tidy(doc: Document, kv_min: int = 3, page_numbers: bool = True, margin: float = 0.1) -> dict:
    stats = {"kv_to_text": 0, "page_numbers_added": 0}
    from ...intake.sniff import NATIVE_KINDS
    if doc.file_type in NATIVE_KINDS:  # e-mail, Office, text: blocks come from the file's own structure
        return stats
    for page in doc.pages:
        kv = [r for r in page.regions if r.type == RegionType.KEY_VALUE]
        if 0 < len(kv) < kv_min:
            for r in kv:
                r.meta["type_before_labels"] = r.type.value
                r.type = RegionType.TEXT
            stats["kv_to_text"] += len(kv)
        if not page_numbers or any(r.type == RegionType.PAGE_NUMBER for r in page.regions):
            continue
        lines = _layer_lines(page)
        if lines is None:  # no usable text layer: local OCR (the same cached run the citations use)
            img = _page_image(doc, page)
            if not img:
                continue
            try:
                from ..grounding import ocr
                if not ocr.available():
                    continue
                lines = ocr.ocr_lines(img, page.index)
            except Exception:
                continue
        boxes = [r.bbox for r in page.regions if r.bbox is not None]
        for ln in lines:
            b = ln.bbox
            if not (b.top + b.height <= margin or b.top >= 1 - margin):
                continue
            if ln.score < 0.8 or not PAGE_NUMBER.match(ln.text) or any(_overlaps(b, o) for o in boxes):
                continue
            order = max((r.order for r in page.regions), default=-1) + 1 if b.top >= 0.5 else -1
            reg = Region(type=RegionType.PAGE_NUMBER, content=ln.text.strip(), text=ln.text.strip(),
                         bbox=BBox(left=b.left, top=b.top, width=b.width, height=b.height, page=page.index),
                         source="labels:ocr_page_number", order=order,
                         confidence=Confidence.of(float(ln.score)), meta={"added_by": "page_number_from_ocr"})
            page.regions.append(reg)
            boxes.append(reg.bbox)
            stats["page_numbers_added"] += 1
        if stats["page_numbers_added"]:
            page.regions.sort(key=lambda r: r.order)
            for i, r in enumerate(page.regions):
                r.order = i
    return stats
