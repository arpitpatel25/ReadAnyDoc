"""Word citations, cell citations and OCR data for the Reducto serializer.

Everything is derived from the Document alone. Offsets are checked against the block content that
is actually serialised: if a later stage rewrote the content, a citation is re-anchored by
searching its text forward from the previous one, and dropped when it cannot be found.
"""

from __future__ import annotations

from typing import Any, Optional

from ..model import BBox, Document, Page, Region
from ..pipeline.grounding.sources import Source
from ..pipeline.grounding.tokens import scan


def bbox_dict(box: BBox, page: Page) -> dict[str, Any]:
    return {"left": round(box.left, 4), "top": round(box.top, 4), "width": round(box.width, 4),
            "height": round(box.height, 4), "page": page.index + 1, "original_page": page.original_page}


def word_citations(region: Region, page: Page, content: Optional[str] = None) -> list[dict[str, Any]]:
    """One entry per content word that has at least one grounded token: ``text`` is exactly
    ``content[char_start:char_end]``; the box is the union of its tokens' source-word boxes."""
    content = region.content if content is None else content
    grounded = [w for w in region.iter_words() if w.bbox is not None and w.char_start is not None]
    if not grounded or not content:
        return []
    by_start: dict[int, BBox] = {}
    cursor = 0
    for w in grounded:
        s, e = w.char_start, w.char_end
        if content[s:e] != w.text:  # content changed after grounding: re-anchor or drop
            s = content.find(w.text, cursor)
            if s < 0:
                continue
            e = s + len(w.text)
        cursor = e
        by_start[s] = w.bbox
    toks, words = scan(content)
    boxes: dict[int, list[BBox]] = {}
    for t in toks:
        b = by_start.get(t.start)
        if b is not None:
            boxes.setdefault(t.word, []).append(b)
    out = []
    for wi in sorted(boxes):
        cw = words[wi]
        box = BBox.union(boxes[wi])
        out.append({"text": content[cw.start:cw.end], "char_start": cw.start, "char_end": cw.end,
                    "bbox": bbox_dict(box, page)})
    return out


def cell_citations(region: Region, page: Page) -> list[dict[str, Any]]:
    """Table cells that have a word-evidence box; row/col spans expanded to index lists."""
    out = []
    for c in region.cells:
        if c.bbox is None:
            continue
        out.append({"text": c.text, "row_idx": list(range(c.row, c.row + c.rowspan)),
                    "col_idx": list(range(c.col, c.col + c.colspan)), "bbox": bbox_dict(c.bbox, page)})
    return out


def ocr_source(page: Page) -> Optional[str]:
    if page.text_layer_usable and page.words.get("pdf"):
        return "pdf"
    for name in ("ppocr", "tesseract"):
        if page.words.get(name):
            return name
    return next((k for k, v in page.words.items() if v and k != "pdf"), None)


def ocr_result(doc: Document, chunk_of_region: dict[str, int]) -> dict[str, Any]:
    """``result.ocr``: words and lines of the best word source of each page. ``confidence`` is the
    engine's score (null for PDF text-layer words), ``rotation`` the page rotation, ``chunk_index``
    the chunk of the block whose box holds the word's centre (null when no block does)."""
    words_out: list[dict[str, Any]] = []
    lines_out: list[dict[str, Any]] = []
    for page in doc.pages:
        name = ocr_source(page)
        if not name:
            continue
        regions = [(r.bbox, chunk_of_region.get(r.id)) for r in page.regions if r.bbox is not None]
        regions.sort(key=lambda rb: rb[0].area)

        def chunk_at(box: BBox) -> Optional[int]:
            cx, cy = box.center
            for rb, ci in regions:
                if rb.contains_point(cx, cy, 0.003):
                    return ci
            return None

        lines: dict[Any, list] = {}
        geo: dict[int, int] = {}
        if name == "pdf" or any(w.line is None for w in page.words[name]):
            # no engine lines (or pdfplumber's, which run across columns): group by geometry
            aspect = (page.height / page.width) if page.width and page.height else 1.0
            src = Source(name, page.words[name], aspect)
            geo = {id(w): src.segment_of[k] for k, w in enumerate(src.words)}
        for i, w in enumerate(page.words[name]):
            if w.bbox is None or not w.text.strip():
                continue
            ci = chunk_at(w.bbox)
            words_out.append({"text": w.text, "bbox": bbox_dict(w.bbox, page), "confidence": w.confidence.score,
                              "chunk_index": ci, "rotation": int(page.rotation or 0)})
            lines.setdefault(geo[id(w)] if geo else w.line, []).append((w, ci))
        for members in lines.values():
            members.sort(key=lambda m: m[0].bbox.left)
            box = BBox.union(m[0].bbox for m in members)
            scores = [m[0].confidence.score for m in members if m[0].confidence.score is not None]
            cis = [m[1] for m in members if m[1] is not None]
            lines_out.append({"text": _join_line([m[0].text.strip() for m in members], name), "bbox": bbox_dict(box, page),
                              "confidence": round(sum(scores) / len(scores), 4) if scores else None,
                              "chunk_index": max(set(cis), key=cis.count) if cis else None,
                              "rotation": int(page.rotation or 0)})
    return {"words": words_out, "lines": lines_out}


_CLOSE = set(".,;:!?)]}%'\u2019\u201d")
_OPEN = set("([{\u2018\u201c")


def _join_line(parts: list[str], source: str) -> str:
    """PP-OCR reports punctuation as separate pieces with the spaces removed: glue them back."""
    if source != "ppocr":
        return " ".join(parts)
    out = ""
    for p in parts:
        if out and not (set(p) <= _CLOSE or out[-1] in _OPEN or p[0] in "-\u2010" or out[-1] in "-\u2010"):
            out += " "
        out += p
    return out
