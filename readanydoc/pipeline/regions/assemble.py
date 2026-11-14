"""Variant d, page assembled from region reads: every top-level layout region is cropped at full resolution
and read on its own, all in parallel; the page is the region reads in the layout's reading order. Types and
boxes come from the layout. The page read is not used (it is kept in ``page.properties`` for comparison)."""

from __future__ import annotations

import time

from ...model import Candidate, Document, Page, Region, RegionType
from ...textnorm import plain_text
from ..mdblocks import split_markdown
from ..reading.tables import has_table
from . import crops
from .match import FORM_LABELS, TABLE_LABELS, detections, to_bbox, top_level

FIGURE_LABELS = {"picture", "image", "chart", "seal"}


def prompt_for(label: str) -> str:
    if label in TABLE_LABELS or label == "document_index":
        return crops.TABLE_PROMPT if label in TABLE_LABELS else crops.REGION_PROMPT
    if label in FORM_LABELS:
        return crops.FORM_PROMPT
    if label in FIGURE_LABELS:
        return crops.FIGURE_PROMPT
    return crops.REGION_PROMPT


def run(doc: Document, page: Page, reader_spec: str, workers: int = 12, min_score: float = 0.4) -> bool:
    dets = top_level(detections(page, min_score=min_score))
    if not dets:
        page.properties["region_assemble"] = {"used": False, "why": "no layout regions"}
        return False
    t0 = time.time()
    results = crops.read_many(doc, page, [(tuple(d["box"]), prompt_for(d["label"]), f"asm:{d['label']}:{d['id']}")
                                          for d in dets], reader_spec, workers=workers)
    seconds = round(time.time() - t0, 2)
    regions: list[Region] = []
    failed = 0
    for d, res in zip(dets, results):
        if res is None:
            failed += 1
            continue
        text = res.text.strip()
        if not text:
            continue
        bbox = to_bbox(tuple(d["box"]), page.index)
        meta = {"layout_label": d["label"], "layout_type": d["type"], "tier": "strong"}
        if d["label"] in FORM_LABELS:
            for b in split_markdown(text):
                regions.append(Region(type=b.type, heading_level=b.heading_level, content=b.text, text=plain_text(b.text),
                                      source="regions:assemble", candidates=[Candidate(source="regions:assemble", content=b.text)],
                                      meta={**meta, "search_bbox": bbox.model_dump(), "layout_type": None}))
            continue
        t = RegionType(d["type"])
        if t == RegionType.TABLE and not has_table(text):
            t = RegionType.TEXT
        elif t != RegionType.TABLE and has_table(text):
            t = RegionType.TABLE
        meta["layout_type"] = t.value
        regions.append(Region(type=t, heading_level=(1 if t == RegionType.TITLE else 2 if t == RegionType.SECTION_HEADER else None),
                              content=text, text=plain_text(text), bbox=bbox, source="regions:assemble",
                              candidates=[Candidate(source="regions:assemble", content=text)],
                              meta={**meta, "bbox_source": "layout"}))
    page.properties["region_assemble"] = {"used": bool(regions), "regions": len(dets), "failed": failed,
                                          "seconds": seconds, "calls": len(dets)}
    if not regions or failed > 0.3 * len(dets):
        return False
    page.properties["page_read_content"] = "\n\n".join(r.content for r in page.regions)
    page.regions = regions
    for i, r in enumerate(page.regions):
        r.order = i
    return True


def restore_types(page: Page) -> None:
    """After Ground ran again its position rules may have retyped a block: the layout's type stands."""
    for r in page.regions:
        t = r.meta.get("layout_type")
        if t and r.type.value != t:
            r.type = RegionType(t)
            r.heading_level = 1 if t == "Title" else 2 if t == "Section Header" else None


# ---------------------------------------------------------------------------------------------- routing
def routed_share(page: Page, labels: set[str], min_score: float = 0.5) -> float:
    """Share of the page area inside the top-level layout regions with one of ``labels`` (known before the read)."""
    from .match import area
    return min(1.0, sum(area(tuple(d["box"])) for d in top_level(detections(page, labels, min_score))))


def run_routed(doc: Document, page: Page, reader_spec: str, what: str = "tables", min_share: float = 0.0,
               guard: bool = False, workers: int = 12, min_score: float = 0.5) -> bool:
    """The page read stays for text; only table regions (``what`` = "tables") or table and
    form regions ("tables+forms") are read from their crops, in parallel, and take the place of the page-read
    blocks that lie in them. ``min_share``: only on pages where those regions cover at least this share of the page
    (a signal that exists before the read). ``guard``: a crop read must keep 90% of the words of the blocks it
    replaces and must not be much longer; without it the crop read is taken as it is (as ``assemble`` does).
    A form region that holds filled form fields of the file is never read from the render (as forms.py)."""
    from collections import Counter

    from ...textnorm import norm_tokens
    from .match import blocks_in
    labels = set(TABLE_LABELS) | (set(FORM_LABELS) if "forms" in what else set())
    share = routed_share(page, labels, min_score)
    report = {"what": what, "share": round(share, 3), "min_share": min_share, "regions": []}
    page.properties["region_route"] = report
    if share < min_share:
        report["skipped"] = "regions cover too little of the page"
        return False
    filled = [f for f in page.properties.get("form_fields") or []
              if f.get("bbox") and (str(f.get("value") or "").strip() or f.get("checked"))]
    todo = []
    for d in top_level(detections(page, labels, min_score)):
        x0, y0, x1, y1 = d["box"]
        entry = {"detection": d["id"], "label": d["label"]}
        report["regions"].append(entry)
        if d["label"] in FORM_LABELS and any(x0 <= (f["bbox"][0] + f["bbox"][2]) / 2 <= x1 and
                                             y0 <= (f["bbox"][1] + f["bbox"][3]) / 2 <= y1 for f in filled):
            entry["skipped"] = "holds filled form fields of the file"
            continue
        blocks = blocks_in(page, tuple(d["box"]), 0.5)
        idx = [i for i, r in enumerate(page.regions) if any(r is b for b in blocks)]
        if not idx or idx != list(range(idx[0], idx[-1] + 1)):
            entry["skipped"] = "no page-read blocks located there, or not one run of the reading order"
            continue
        todo.append((d, [page.regions[i] for i in idx], entry))
    if not todo:
        return False
    t0 = time.time()
    results = crops.read_many(doc, page, [(tuple(d["box"]), prompt_for(d["label"]), f"route:{d['label']}:{d['id']}")
                                          for d, _, _ in todo], reader_spec, workers=workers)
    report["seconds"] = round(time.time() - t0, 2)
    changed = False
    for (d, old_blocks, entry), res in zip(todo, results):
        if res is None or not res.text.strip():
            entry["skipped"] = "crop read unavailable"
            continue
        text = res.text.strip()
        old = "\n\n".join(r.content for r in old_blocks)
        is_table = d["label"] in TABLE_LABELS
        if is_table and not has_table(text):
            entry["skipped"] = "crop read has no table"
            continue
        ot, nt = norm_tokens(old), norm_tokens(text)
        keep = sum((Counter(ot) & Counter(nt)).values()) / max(len(ot), 1)
        entry.update({"words_kept": round(keep, 3), "tokens": [len(ot), len(nt)]})
        if guard and (keep < 0.9 or len(nt) > 1.3 * len(ot) + 10):
            entry["skipped"] = "crop read does not hold the page read's words"
            continue
        bbox = to_bbox(tuple(d["box"]), page.index)
        if is_table:
            new = [Region(type=RegionType.TABLE, content=text, text=plain_text(text), bbox=bbox, source="regions:route",
                          candidates=[Candidate(source="regions:route", content=text)],
                          meta={"layout_label": d["label"], "layout_type": "Table", "bbox_source": "layout", "tier": "strong"})]
        else:
            new = [Region(type=b.type, heading_level=b.heading_level, content=b.text, text=plain_text(b.text),
                          source="regions:route", candidates=[Candidate(source="regions:route", content=b.text)],
                          meta={"search_bbox": bbox.model_dump(), "tier": "strong"}) for b in split_markdown(text)]
        if not new:
            continue
        new[0].candidates.append(Candidate(source="reader", content=old, meta={"replaced": True}))
        i0 = next(k for k, r in enumerate(page.regions) if r is old_blocks[0])
        page.regions[i0:i0 + len(old_blocks)] = new
        entry["replaced"] = True
        changed = True
    for i, r in enumerate(page.regions):
        r.order = i
    return changed
