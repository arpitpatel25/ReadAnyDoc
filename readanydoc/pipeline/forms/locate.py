"""Where a piece of the page read is on the page, from the page's word boxes (PDF text layer or OCR).

The read's words and the page's words are aligned as two sequences (difflib); every read word that
pairs with a page word takes that word's box. No model call. Used to cut crops for re-reads until
layout regions are available; see ``regions_from_layout``.
"""

from __future__ import annotations

import difflib
from typing import Optional

from ...model import BBox, Page, Region
from ..common import pick_word_source
from .fillable import _TOKEN, norm_map

Box = tuple[float, float, float, float]


class Located:
    """Read tokens of every region with the page box each one was paired with (or None)."""

    def __init__(self, regions: list[Region], page: Page):
        self.tokens: list[tuple[int, int, int, str]] = []  # (region index, start, end, word)
        for ri, r in enumerate(regions):
            text, spans = norm_map(r.content)
            for w, (s, e) in zip(text.split(" ") if text else [], spans):
                self.tokens.append((ri, s, e, w))
        self.boxes: list[Optional[Box]] = [None] * len(self.tokens)
        src = pick_word_source(page)
        words = page.words.get(src) or [] if src else []
        pw: list[tuple[str, Box]] = []
        for w in words:
            if w.bbox is None:
                continue
            b = (w.bbox.left, w.bbox.top, w.bbox.right, w.bbox.bottom)
            for m in _TOKEN.finditer(w.text or ""):
                t = m.group(0)
                if not t.startswith(("<", "&")):
                    pw.append((t.lower(), b))
        self.source = src
        seen: dict[str, int] = {}
        for t, _ in pw:
            seen[t] = seen.get(t, 0) + 1
        self.once: dict[str, Box] = {t: b for t, b in pw if seen[t] == 1 and len(t) >= 3}
        self.read_count: dict[str, int] = {}
        for t in self.tokens:
            self.read_count[t[3]] = self.read_count.get(t[3], 0) + 1
        if not pw or not self.tokens:
            return
        sm = difflib.SequenceMatcher(None, [t[3] for t in self.tokens], [t for t, _ in pw], autojunk=False)
        for a, b, n in sm.get_matching_blocks():
            for k in range(n):
                self.boxes[a + k] = pw[b + k][1]

    def coverage(self) -> float:
        return sum(b is not None for b in self.boxes) / max(len(self.boxes), 1)

    def span_box(self, ri: int, start: int, end: int, before: int = 0, after: int = 0) -> Optional[Box]:
        """Union of the boxes of the read words of region ``ri`` between two offsets, widened by some
        words on either side (same region only)."""
        idx = [k for k, t in enumerate(self.tokens) if t[0] == ri]
        inside = [k for k in idx if self.tokens[k][2] > start and self.tokens[k][1] < end]
        if not inside:
            near = [k for k in idx if self.tokens[k][1] >= end][:1] or [k for k in idx if self.tokens[k][2] <= start][-1:]
            if not near:
                return None
            inside = near
        lo = max(idx[0], inside[0] - before)
        hi = min(idx[-1], inside[-1] + after)
        return union([self.boxes[k] for k in range(lo, hi + 1) if self.boxes[k] is not None])

    def region_box(self, ri: int, min_share: float = 0.5) -> Optional[Box]:
        ks = [k for k, t in enumerate(self.tokens) if t[0] == ri]
        got = [self.boxes[k] for k in ks if self.boxes[k] is not None]
        if ks and len(got) >= min_share * len(ks):
            return union(trim(got))
        # the two reading orders differ too much to align: use the words that occur once on the page
        anchors = [self.once[self.tokens[k][3]] for k in ks
                   if self.tokens[k][3] in self.once and self.read_count[self.tokens[k][3]] == 1]
        if len(anchors) >= max(4, 0.2 * len(ks)):
            return union(trim(anchors))
        return None


def union(boxes: list[Box]) -> Optional[Box]:
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def trim(boxes: list[Box], share: float = 0.04) -> list[Box]:
    """Drop the few words paired far from the rest (a repeated word matched elsewhere on the page)."""
    if len(boxes) < 12:
        return boxes
    ys = sorted((b[1] + b[3]) / 2 for b in boxes)
    k = int(len(ys) * share)
    lo, hi = ys[k], ys[-k - 1]
    return [b for b in boxes if lo - 0.03 <= (b[1] + b[3]) / 2 <= hi + 0.03]


def pad(box: Box, x: float = 0.02, y: float = 0.012) -> Box:
    return (max(box[0] - x, 0.0), max(box[1] - y, 0.0), min(box[2] + x, 1.0), min(box[3] + y, 1.0))


def regions_from_layout(page: Page, labels: tuple[str, ...] = ("form", "key_value_region"),
                        min_score: float = 0.5) -> list[Box]:
    """When its Layout stage ran with
    ``detect_only``, ``page.properties["layout_detections"]`` holds plain dicts ``{label, score, box,
    inside}``. The form-like regions are the outermost ``form`` / ``key_value_region`` boxes. Empty when
    no layout ran: the caller then derives regions from the read's own blocks and the word boxes."""
    dets = [d for d in (page.properties.get("layout_detections") or [])
            if d.get("label") in labels and (d.get("score") or 0) >= min_score and d.get("box")]
    ids = {d.get("id") for d in dets}
    return [tuple(d["box"]) for d in dets if d.get("inside") not in ids]


def inside(inner: Optional[Box], outer: Box, share: float = 0.6) -> bool:
    if inner is None:
        return False
    w = max(min(inner[2], outer[2]) - max(inner[0], outer[0]), 0.0)
    h = max(min(inner[3], outer[3]) - max(inner[1], outer[1]), 0.0)
    area = max((inner[2] - inner[0]) * (inner[3] - inner[1]), 1e-9)
    return w * h / area >= share


def crop_png(doc, page: Page, box: Box, zoom: Optional[float] = None) -> Optional[bytes]:
    """The crop as PNG at the largest size the model keeps (``zoom`` limits it to that multiple of the
    page-read scale)."""
    from ...readers.sizing import render_pdf_native, resize_image_native
    if doc.file_type == "pdf" and doc.meta.get("path"):
        return render_pdf_native(doc.meta["path"], page.original_page, box, crop_zoom=zoom,
                                 password=doc.meta.get("password"))
    src = next(iter(page.renders.values()), None) or doc.meta.get("path")
    return resize_image_native(src, box, max_scale=zoom or 3.0) if src else None
