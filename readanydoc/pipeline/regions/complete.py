"""Variant e, completeness: every detected text region must have matching text in the output.

A text detection counts as present when a located block of the page read overlaps it, or when the independent
words inside its box (PDF text layer, else OCR) are found in the page read. Otherwise the region is read from
its own crop and the result is inserted at its place in the reading order, but only when that text is really
absent from the page read (and, where a trusted text layer exists, agrees with it)."""

from __future__ import annotations

from collections import Counter

from ...model import Candidate, Document, Page, Region, RegionType
from ...textnorm import containment, norm_tokens, plain_text
from . import crops
from .forms import box_words
from .match import TEXT_LABELS, area, box_of, detections, inter, to_bbox


def missing(page: Page, min_score: float = 0.5) -> list[dict]:
    dets = detections(page, TEXT_LABELS, min_score)
    by_id = {d["id"]: d for d in page.properties.get("layout_detections") or []}
    located = [box_of(r.bbox) for r in page.regions
               if r.bbox is not None and not (r.bbox.width > 0.95 and r.bbox.height > 0.95)]
    page_tokens = norm_tokens("\n".join(r.content for r in page.regions))
    out = []
    for d in dets:
        parent = by_id.get(d.get("inside") or "")
        if parent is not None and parent["label"] in ("table", "picture", "image", "chart", "document_index"):
            continue  # text inside a table or a figure belongs to that block
        db = tuple(d["box"])
        covered = sum(inter(db, b) for b in located) / max(area(db), 1e-9)
        if covered >= 0.3:
            continue
        ev, src = box_words(page, db)
        if len(ev) >= 3 and containment(ev, page_tokens) >= 0.6:
            continue
        if len(ev) < 3 and d["score"] < 0.6:
            continue
        out.append({**d, "_evidence": ev, "_source": src})
    return out


def _position(page: Page, d: dict) -> int:
    if d["label"] in ("page_header", "header"):
        return 0
    if d["label"] in ("page_footer", "footer", "number"):
        return len(page.regions)
    x0, y0, x1, y1 = d["box"]
    for i, r in enumerate(page.regions):
        if r.bbox is None:
            continue
        b = box_of(r.bbox)
        overlap_x = min(b[2], x1) - max(b[0], x0)
        if b[1] >= y1 - 0.005 and overlap_x > 0.3 * min(b[2] - b[0], x1 - x0):
            return i
    return len(page.regions)


def run(doc: Document, page: Page, reader_spec: str, max_crops: int = 6) -> bool:
    todo = missing(page)[:max_crops]
    report = []
    page.properties["region_complete"] = report
    if not todo:
        return False
    results = crops.read_many(doc, page, [(tuple(d["box"]), crops.REGION_PROMPT, f"miss:{d['id']}") for d in todo],
                              reader_spec)
    trusted = bool(page.properties.get("text_layer_exact"))
    changed = False
    for d, res in zip(todo, results):
        entry = {"detection": d["id"], "label": d["label"], "evidence": d["_source"], "evidence_tokens": len(d["_evidence"])}
        report.append(entry)
        if res is None or not res.text.strip():
            entry["skipped"] = "crop read empty or unavailable"
            continue
        text = res.text.strip()
        nt = norm_tokens(text)
        page_tokens = norm_tokens("\n".join(r.content for r in page.regions))
        entry["tokens"] = len(nt)
        if not nt or "[illegible" in text:
            entry["skipped"] = "nothing readable"
            continue
        if containment(nt, page_tokens) >= 0.6 and len(nt) >= 2:
            entry["skipped"] = "text is already in the page read"
            continue
        if len(nt) < 2 and Counter(page_tokens)[nt[0]] > 0:
            entry["skipped"] = "text is already in the page read"
            continue
        if trusted and len(d["_evidence"]) >= 3 and containment(nt, d["_evidence"]) < 0.6:
            entry["skipped"] = "crop read disagrees with the text layer"
            continue
        t = RegionType(d["type"])
        r = Region(type=t, heading_level=(1 if t == RegionType.TITLE else 2 if t == RegionType.SECTION_HEADER else None),
                   content=text, text=plain_text(text), bbox=to_bbox(tuple(d["box"]), page.index),
                   source="regions:complete", candidates=[Candidate(source="regions:complete", content=text)],
                   meta={"bbox_source": "layout", "layout_label": d["label"], "layout_type": t.value,
                         "inserted_by": "completeness", "tier": "strong"})
        page.regions.insert(_position(page, d), r)
        entry["inserted"] = True
        changed = True
        doc.note("regions", f"page {page.index}: text region {d['id']} ({d['label']}) was missing; read from its crop")
    for i, r in enumerate(page.regions):
        r.order = i
    return changed
