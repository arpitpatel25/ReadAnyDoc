"""Figure blocks for pictures the reader did not describe.

The reader writes a Figure only for a drawing it chose to describe; logos, stamps, seals and photographs
often get no block at all. The layout model (Docling heron, ``layout.detect_only``) finds them as
``picture`` detections. ``add`` turns each such detection that no existing block accounts for into a Figure
block with the detection's box, placed in reading order. Off by default (shape option ``layout_figures``).

The new block's content is a one-sentence description when a live model is configured and
``layout_figures_describe`` is on (one image call per added figure), otherwise it is empty: the block then
carries only its box (and its crop when ``return_images`` asks) and adds nothing to the chunk text.
"""

from __future__ import annotations

from typing import Any, Optional

from ...model import BBox, Confidence, Page, Region, RegionType

LABELS = ("picture", "image", "chart", "figure", "seal")
DESCRIBE_PROMPT = ("Describe this figure in one sentence: say what it is (logo, seal, stamp, photograph, chart, "
                   "diagram, signature, barcode) and what it shows, including any words in it.")


def _area(b) -> float:
    return max(b[2], 0.0) * max(b[3], 0.0)


def _inter(a, b) -> float:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(w, 0.0) * max(h, 0.0)


def _covered(box, others, share: float) -> bool:
    """True when ``share`` of ``box`` lies inside one of ``others``, or it holds most of one of them."""
    a = _area(box) or 1e-9
    for o in others:
        i = _inter(box, o)
        if i / a >= share or i / (_area(o) or 1e-9) >= 0.6:
            return True
    return False


RUNNING_LABELS = ("page_header", "page_footer")
RUNNING_MAX_AREA = 0.01


def on_running_line(box, dets: list[dict], share: float) -> bool:
    """a small picture (under 1% of the page) of which ``share`` of the height lies on the
    line of a detected running head or foot is part of that head or foot (an icon next to the chapter name, a
    publisher's mark beside the page number), not a figure of the page. On the OmniDocBench dev pages 8 of the 16
    added figures that its labels do not call a figure were such icons (the labels say "header"; r-1 has a Figure at
    2 of them); no added figure that an answer key calls a figure is on such a line at 0.5 (the closest has 0.36)."""
    if share <= 0 or _area(box) >= RUNNING_MAX_AREA or box[3] <= 0:
        return False
    for d in dets:
        if str(d.get("label", "")).lower() in RUNNING_LABELS and float(d.get("score") or 0) >= 0.5:
            y0, y1 = float(d["box"][1]), float(d["box"][3])
            if (min(box[1] + box[3], y1) - max(box[1], y0)) / box[3] >= share:
                return True
    return False


def candidates(page: Page, min_score: float = 0.5, min_area: float = 0.0006, max_area: float = 0.5,
               running: float = 0.5) -> list[dict]:
    """Picture detections of the page worth a block: confident, not a speck, not most of the page, not an icon on
    the line of a running head or foot (``running`` = share of its height on that line; 0 switches the rule off)."""
    out = []
    dets = page.properties.get("layout_detections") or []
    for d in dets:
        if str(d.get("label", "")).lower() not in LABELS or float(d.get("score") or 0) < min_score:
            continue
        x0, y0, x1, y1 = (float(v) for v in d["box"])  # detections are corner boxes
        box = (x0, y0, x1 - x0, y1 - y0)
        if not (min_area <= _area(box) <= max_area) or box[2] < 0.012 or box[3] < 0.008:
            continue
        if on_running_line(box, dets, running):
            continue
        out.append({"box": box, "score": float(d.get("score") or 0)})
    out.sort(key=lambda c: -c["score"])
    kept: list[dict] = []
    for c in out:  # nested or doubled detections: the more confident one stays
        if not _covered(c["box"], [k["box"] for k in kept], 0.5):
            kept.append(c)
    return kept


def add(page: Page, figure_boxes: list[tuple], min_score: float = 0.5,
        unlocated: Optional[list[Region]] = None, running: float = 0.5) -> list[Region]:
    """Append a Figure block for every picture detection not already covered. Returns the new blocks.

    ``figure_boxes``: boxes (left, top, width, height) of the figures the page already has and whose place on
    the page is known. A detection inside a table is left alone (a cell with a logo or a tick is the table's
    business). ``unlocated``: figure blocks the reader described but that could not be found on the page
    image; each takes the box of the nearest free picture instead of a second block being made for it.
    """
    tables = [(r.bbox.left, r.bbox.top, r.bbox.width, r.bbox.height) for r in page.regions
              if r.type == RegionType.TABLE and r.bbox is not None and not (r.bbox.width > 0.95 and r.bbox.height > 0.95)]
    have = list(figure_boxes)
    new: list[Region] = []
    free = [c for c in candidates(page, min_score=min_score, running=running)
            if not _covered(c["box"], have, 0.3)
            and not any(_inter(c["box"], t) / (_area(c["box"]) or 1e-9) >= 0.7 for t in tables)]
    for r in unlocated or []:  # the reader's description + the layout model's box = one block, not two
        if not free:
            break
        b = r.bbox
        cx, cy = (b.left + b.width / 2, b.top + b.height / 2) if b is not None else (0.5, 0.5)
        best = min(free, key=lambda c: (c["box"][0] + c["box"][2] / 2 - cx) ** 2 + (c["box"][1] + c["box"][3] / 2 - cy) ** 2)
        free.remove(best)
        box = best["box"]
        r.meta["bbox_before_layout"] = [round(v, 4) for v in (b.left, b.top, b.width, b.height)] if b is not None else None
        r.bbox = BBox(left=box[0], top=box[1], width=box[2], height=box[3], page=page.index)
        r.meta.update(figure_box=[round(v, 4) for v in box], bbox_source="layout", layout_score=round(best["score"], 4))
        have.append(box)
    for c in free:
        box = c["box"]
        if _covered(box, have, 0.3):
            continue
        r = Region(type=RegionType.FIGURE, content="", content_format="markdown", source="layout:picture",
                   bbox=BBox(left=box[0], top=box[1], width=box[2], height=box[3], page=page.index),
                   confidence=Confidence.of(c["score"]),
                   meta={"from_layout": True, "layout_score": round(c["score"], 4), "bbox_source": "layout",
                         "figure_box": [round(v, 4) for v in box]})
        have.append(box)
        new.append(r)
    if not new:
        return new
    # reading order: before the first block that starts below the picture's top edge (same column or not:
    # a logo above the title must come first, a stamp at the bottom last)
    for r in sorted(new, key=lambda r: (r.bbox.top, r.bbox.left)):
        at = len(page.regions)
        for i, other in enumerate(page.regions):
            ob = other.bbox
            if ob is None or (ob.width > 0.95 and ob.height > 0.95):
                continue
            if ob.top > r.bbox.top + r.bbox.height * 0.5:
                at = i
                break
        page.regions.insert(at, r)
    for i, r in enumerate(page.regions):
        r.order = i
    return new


def describe(llm, image_path: str) -> tuple[Optional[str], dict[str, Any]]:
    """One sentence about a figure crop. (text | None, usage)."""
    from pathlib import Path
    res = llm.complete("You look at one figure cut from a document page. Reply with one plain sentence, no preamble.",
                       DESCRIBE_PROMPT, images=[Path(image_path).read_bytes()], max_tokens=400)
    usage = {"model": res.model, **{k: res.usage.get(k) for k in ("input_tokens", "output_tokens", "cost_usd")}}
    text = " ".join((res.text or "").split())
    return (text or None), usage
