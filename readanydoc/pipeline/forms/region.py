"""``region_reread``: only the form-like region of a page is read again, with the form prompt.

Trigger: the page read wrote a form as an HTML table (``grid.classify`` says form grid, or cannot say
and at least two cells are fields by their own marks). The blocks of such a run are located on the page
from the word boxes (or come from layout regions, see ``locate.regions_from_layout``), cut out as one
crop and read with a prompt that asks for `label: value` lines and keeps real grids as tables.
The crop read replaces those blocks, and nothing else, only when it carries the same words: at least
``min_recall`` of the page read's words of the region are in it and at most ``max_extra`` of its own
words are new. Otherwise the page read stays. Every replaced block is kept as a candidate.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ...model import Candidate, Document, Page, Region, RegionType
from ...readers import prompts
from ...readers.base import ReadContext
from ...textnorm import plain_text
from ..mdblocks import split_markdown
from . import grid, locate
from .fillable import norm_map

REGION_PROMPT = ("Read the image at {img}. It is a crop of one section of a document page that holds a form. "
                 "Transcribe the crop to Markdown, in natural reading order. " + prompts.FORM_RULE +
                 prompts.PAGE_RULES_FULL)


def _formish(r: Region, loose: bool) -> bool:
    if r.type != RegionType.TABLE:
        return False
    ts = [p for kind, p in grid.split_content(r.content) if kind == "table"]
    if not ts:
        return False
    for t in ts:
        v = grid.classify(t)
        if v.kind == "form" or (loose and v.kind == "unclear" and v.stats.get("strong", 0) >= 2):
            continue
        return False
    return True


def runs(regions: list[Region], loose: bool = True, bridge_words: int = 15) -> list[list[int]]:
    """Maximal runs of form-grid tables; a short text or Key Value block between two of them is bridged."""
    out: list[list[int]] = []
    cur: list[int] = []
    pending: list[int] = []
    for i, r in enumerate(regions):
        if _formish(r, loose):
            cur.extend(pending + [i])
            pending = []
        elif cur and r.type in (RegionType.TEXT, RegionType.KEY_VALUE) and len(plain_text(r.content).split()) <= bridge_words \
                and len(pending) < 2:
            pending.append(i)
        else:
            if cur:
                out.append(cur)
            cur, pending = [], []
    if cur:
        out.append(cur)
    return out


def form_blocks(md: str, max_chars: int = 2500) -> list[tuple[RegionType, object, str]]:
    """Blocks of a form-region read: the field lines between two tables or headings are one Key Value
    block (the region is a form section; Reducto returns such a section as one block)."""
    out: list[tuple[RegionType, object, str]] = []
    for b in split_markdown(md):
        field = b.type in (RegionType.TEXT, RegionType.KEY_VALUE, RegionType.LIST_ITEM)
        if field and out and out[-1][0] == RegionType.KEY_VALUE and len(out[-1][2]) + len(b.text) <= max_chars:
            out[-1] = (RegionType.KEY_VALUE, None, out[-1][2] + "\n" + b.text.replace("\n\n", "\n"))
        elif field:
            out.append((RegionType.KEY_VALUE, None, b.text.replace("\n\n", "\n")))
        else:
            out.append((b.type, b.heading_level, b.text))
    return out


def _words(s: str) -> Counter:
    return Counter(w for w in norm_map(s)[0].split(" ") if w)


def accept(old: str, new: str, min_recall: float = 0.92, max_extra: float = 0.15) -> tuple[bool, float, float]:
    a, b = _words(old), _words(new)
    if not a or not b:
        return False, 0.0, 0.0
    common = sum((a & b).values())
    recall = common / sum(a.values())
    extra = 1 - common / sum(b.values())
    return recall >= min_recall and extra <= max_extra, round(recall, 3), round(extra, 3)


def apply(doc: Document, page: Page, stage: Any, max_regions: int = 3) -> dict:
    loose = bool(stage.opt("region_loose", True))
    rs = runs(page.regions, loose=loose)
    if not rs:
        return {}
    loc = locate.Located(page.regions, page)
    layout = locate.regions_from_layout(page)
    if layout:  # layout regions: a form region that holds a form-grid table is re-read as a whole
        boxes = [loc.region_box(i) for i in range(len(page.regions))]
        by_layout = []
        for lb in layout:
            idx = [i for i, b in enumerate(boxes) if locate.inside(b, lb)]
            if idx and any(_formish(page.regions[i], loose) for i in idx):
                by_layout.append(list(range(idx[0], idx[-1] + 1)))
        taken = {i for run in by_layout for i in run}
        rs = by_layout + [run for run in rs if not taken & set(run)]
        rs.sort()
    reader = stage._reader(stage.opt("region_reader") or stage.ctx_reader_spec)
    info = {"regions": len(rs), "from_layout": bool(layout), "read": 0, "replaced": 0, "kept": []}
    replace: dict[int, tuple[list[int], list[Region]]] = {}
    for k, run in enumerate(rs[:max_regions]):
        boxes = [loc.region_box(i) for i in run]
        if any(b is None for b in boxes):
            info["kept"].append("not located")
            continue
        box = locate.pad(locate.union(boxes), 0.012, 0.008)
        old = "\n\n".join(page.regions[i].content for i in run)
        try:
            res = reader.read(locate.crop_png(doc, page, box), None,
                              ReadContext(stem=f"{doc.stem}#form{k}", page_index=page.index, prompt=REGION_PROMPT))
        except Exception as e:
            info["kept"].append(f"crop read failed: {str(e)[:80]}")
            continue
        doc.usage.append({"page": page.index, "reader": reader.name, "escalation": "form_region",
                          "area": round((box[2] - box[0]) * (box[3] - box[1]), 4),
                          **res.usage.model_dump(exclude_none=True)})
        info["read"] += 1
        if res.refused or res.truncated or not res.text.strip():
            info["kept"].append("crop read unusable")
            continue
        ok, recall, extra = accept(old, res.text)
        if not ok:
            info["kept"].append(f"words differ (recall {recall}, new {extra})")
            continue
        source = f"{reader.name}#form_region"
        new = [Region(type=t, heading_level=level, content=text, text=plain_text(text), source=source,
                      candidates=[Candidate(source=source, content=text)],
                      meta={"forms": {"rule": "region_reread", "recall": recall, "new_words": extra}})
               for t, level, text in form_blocks(res.text)]
        if not new:
            continue
        new[0].candidates.extend(Candidate(source=page.regions[i].source or "reader", content=page.regions[i].content,
                                           meta={"replaced": True, "by": "forms.region_reread"}) for i in run)
        replace[run[0]] = (run, new)
        info["replaced"] += 1
    if replace:
        out: list[Region] = []
        skip: set[int] = set()
        for i, r in enumerate(page.regions):
            if i in replace:
                out.extend(replace[i][1])
                skip.update(replace[i][0])
            elif i not in skip:
                out.append(r)
        page.regions = out
    return info
