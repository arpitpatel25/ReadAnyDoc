"""Helpers shared by read / verify / ground (word lookup on a page)."""

from __future__ import annotations

from typing import Optional

from ..model import BBox, Page, Region, Word


def pick_word_source(page: Page, prefer: Optional[str] = None) -> Optional[str]:
    """PDF words when the text layer passed triage, else OCR words, else whatever exists."""
    if prefer and page.words.get(prefer):
        return prefer
    if page.text_layer_usable and page.words.get("pdf"):
        return "pdf"
    if page.words.get("ppocr"):
        return "ppocr"
    return next((k for k, v in page.words.items() if v), None)


def words_in(words: list[Word], bbox: BBox, pad: float = 0.008) -> list[int]:
    """Indices of words whose centre lies inside ``bbox``."""
    out = []
    for i, w in enumerate(words):
        if w.bbox is None:
            continue
        cx, cy = w.bbox.center
        if bbox.contains_point(cx, cy, pad):
            out.append(i)
    return out


def search_bbox(region: Region) -> Optional[BBox]:
    """Where to look for the region's words: its own box, or the layout box it was matched to."""
    if region.bbox is not None:
        return region.bbox
    sb = region.meta.get("search_bbox")
    return BBox(**sb) if sb else None
