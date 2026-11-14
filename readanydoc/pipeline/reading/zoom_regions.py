"""Zoomed digit check restricted to table regions.

The zoom vote reads the whole page again as enlarged strips, twice. Its verified gains are digits in printed
tables, so with ``zoom_regions="tables"`` only the table regions of the layout model are cut into strips: text
outside tables is not transcribed again (output tokens are what a strip costs). The two sets must be different
images of the same content, or the second "vote" would be the first read again: set ``za`` cuts a table into k
strips, set ``zb`` into k + 1. A page without a table detection keeps the whole-page strips.
"""

from __future__ import annotations

import math
from typing import Optional

from ...model import Page
from . import zoom

Box = tuple[float, float, float, float]


def table_boxes(page: Page, min_score: float = 0.4, pad_x: float = 0.02, pad_y: float = 0.015) -> list[Box]:
    """Padded table regions, top to bottom; regions that overlap vertically and horizontally are joined."""
    dets = [d for d in page.properties.get("layout_detections") or []
            if d.get("label") == "table" and d.get("score", 0) >= min_score]
    boxes = sorted(((max(0.0, d["box"][0] - pad_x), max(0.0, d["box"][1] - pad_y),
                     min(1.0, d["box"][2] + pad_x), min(1.0, d["box"][3] + pad_y)) for d in dets), key=lambda b: b[1])
    out: list[Box] = []
    for b in boxes:
        if out and b[1] < out[-1][3] and b[0] < out[-1][2] and out[-1][0] < b[2]:
            a = out[-1]
            out[-1] = (min(a[0], b[0]), a[1], max(a[2], b[2]), max(a[3], b[3]))
        else:
            out.append(b)
    return out


def has_table_or_form(page: Page, table_score: float = 0.4, form_score: float = 0.5) -> bool:
    dets = page.properties.get("layout_detections")
    if dets is None:
        return True  # no layout model ran: nothing is known, the page keeps its strips
    return any((d.get("label") == "table" and d.get("score", 0) >= table_score) or
               (d.get("label") in ("form", "key_value_region") and d.get("score", 0) >= form_score) for d in dets)


def _cut(box: Box, k: int, overlap: float = 0.12) -> list[Box]:
    x0, y0, x1, y1 = box
    return [(x0, y0 + s[1] * (y1 - y0), x1, y0 + s[3] * (y1 - y0)) for s in zoom.strip_boxes(k, overlap)] if k > 1 else [box]


def boxes(page: Page, n: int, mode: Optional[str], base_n: int = 3) -> list[Box]:
    """The strip boxes of one set. ``n`` is the whole-page strip count of the set (3 for za, 4 for zb)."""
    if mode == "gate":
        # the whole-page strips as before, but none on a page where the layout model shows neither a table nor a
        # form region: with ``zoom_prefetch`` those strips were read before the page read and then not used
        return zoom.strip_boxes(n) if has_table_or_form(page) else []
    if mode not in ("tables", "tables_only"):
        return zoom.strip_boxes(n)
    tables = table_boxes(page)
    if not tables:
        # ``tables``: a page without a table region keeps the whole-page strips; ``tables_only``: it gets none
        # (the vote only ever runs for a page whose read has a numeric table, see zoom_gate ``table_legible``)
        return zoom.strip_boxes(n) if mode == "tables" else []
    out: list[Box] = []
    for b in tables:
        k = max(1, math.ceil((b[3] - b[1]) * base_n - 0.15)) + (n - base_n)  # as tall as a whole-page strip, or less
        out += _cut(b, max(k, 1))
    return out


def coverage(page: Page) -> float:
    """Share of the page area inside the table boxes (what the restricted strips read)."""
    return min(1.0, sum((b[2] - b[0]) * (b[3] - b[1]) for b in table_boxes(page)))
