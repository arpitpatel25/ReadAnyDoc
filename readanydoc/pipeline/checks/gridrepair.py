"""Check 1, table grid repair. A table whose rows differ in width puts values under wrong headers.
Only such a table is touched, and a replacement is accepted only when its grid is consistent and its
cell texts are the page read's cell texts (``htmlgrid.acceptable``).

Modes (stage option ``grid``; several joined by ``+`` are tried in order until one is accepted):
- ``norm``   deterministic normalisation where only one reading is possible (no model call);
- ``pad``    the same plus empty cells at row ends, i.e. the grid a browser already draws;
- ``crop``   the table is read again from a full-resolution crop;
- ``draft``  the crop is read again with the broken draft in the prompt (repair prompt);
- ``pre``    the table-only read of the table's layout region that Read started with the page read
             (tables ``prefetch=1``), when there is one: no call of its own.
A table no mode repairs is recorded as unresolved.
"""

from __future__ import annotations

from typing import Optional

from ...model import RegionType
from ...readers import prompts
from . import htmlgrid, reread
from .common import PageChecks

REPAIR_PROMPT = (
    "The image is a crop of one table from a document page. Below is a draft transcription of that table. "
    "Its rows do not all span the same number of columns, so some values sit under the wrong headers. "
    "Output the corrected table as one HTML <table>: one <tr> per printed row, one cell per printed cell, "
    "rowspan/colspan where cells are merged, empty cells left empty, so that every row spans the same number "
    "of columns and every value sits in the column where it is printed. Keep the text of every cell exactly as "
    "in the draft unless the image clearly shows something else; do not add, drop, merge or reorder rows; do not "
    "compute or correct anything. Text that is visible above or below the table but is not part of it must be "
    "left out. Output only the HTML table, with no preamble and no code fences.\n\nDraft:\n")


def _first_table(text: Optional[str]) -> Optional[str]:
    ms = htmlgrid.find_tables(text or "")
    return max((m.group() for m in ms), key=len) if ms else None


def run(pc: PageChecks, modes: list[str]) -> None:
    numbers_at = float(pc.opt("grid_numbers_at", 1.0))
    words_at = float(pc.opt("grid_words_at", 1.0))
    for region in list(pc.page.regions):
        if region.type != RegionType.TABLE or "<table" not in region.content.lower():
            continue
        for _ in range(8):  # one pass per broken table of the block (content offsets move after a replacement)
            m = next((x for x in htmlgrid.broken_tables(region.content)
                      if x.group() not in region.meta.get("_grid_seen", [])), None)
            if m is None:
                break
            draft = m.group()
            region.meta.setdefault("_grid_seen", []).append(draft)
            widths = htmlgrid.parse(draft).widths
            new, how, tried = None, "", []
            for mode in modes:
                cand, why = _candidate(pc, region, draft, mode)
                ok, reason = (False, why) if cand is None else htmlgrid.acceptable(draft, cand, numbers_at, words_at)
                tried.append(f"{mode}: {reason}")
                if ok:
                    new, how = cand, mode
                    break
            if new is None:
                pc.record(region, "table_grid", "unresolved",
                          f"rows span {min(widths)}-{max(widths)} columns; " + "; ".join(tried))
                continue
            pc.replace(region, region.content[:m.start()] + new + region.content[m.end():],
                       f"table grid repaired ({how})")
            pc.record(region, "table_grid", "repaired", f"rows spanned {min(widths)}-{max(widths)} columns", how=how)
        region.meta.pop("_grid_seen", None)


def _candidate(pc: PageChecks, region, draft: str, mode: str) -> tuple[Optional[str], str]:
    if mode in ("norm", "pad"):
        new, fixes = htmlgrid.normalise(draft, pad=(mode == "pad"))
        return new, "; ".join(fixes[:3])
    if mode == "pre":
        return _prefetched(pc, region)
    if mode not in ("crop", "draft"):
        return None, f"unknown mode {mode}"
    box = pc.box_of(region)
    if box is None:
        if mode == "crop":
            return None, "table not located on the page"
        box = (0.0, 0.0, 1.0, 1.0)  # the draft tells the model which table is meant: the whole page will do
        prompt = REPAIR_PROMPT.replace("a crop of one table from a document page", "a document page that contains a table") + draft
    else:
        prompt = prompts.TABLE_CROP_STRICT_FILE if mode == "crop" else REPAIR_PROMPT + draft
    text = pc.ask(reread.pad(box), prompt, f"grid_{mode}")
    table = _first_table(text)
    return table, ("" if table else "no table in the re-read")


def _prefetched(pc: PageChecks, region) -> tuple[Optional[str], str]:
    """the table-only read of this table's layout region that the Read stage already
    started (tables ``prefetch=1``) is the first model candidate, so a table it repairs needs no repair call.
    The read is only looked at here: the table step still takes and bills it, once. No call is ever made."""
    from .. import tables as _tables
    from ..regions.match import area, detections, inside
    from ..tables import prefetch
    from ..tables import reread as _treread
    spec = _tables.spec_from(pc.ground_options.get("tables"))
    if not spec.get("prefetch") or not set(spec.get("reread") or []) & {"all", "broken"}:
        return None, "no table read was started with the page read"
    box = pc.box_of(region)
    if box is None:
        return None, "table not located on the page"
    best, how = None, 0.0
    for d in detections(pc.page, {"table"}, 0.0):
        if d["score"] < _treread.TABLE_LIKE["table"]:
            continue
        b = tuple(d["box"])
        o = min(inside(box, b), inside(b, box))
        if o > how:
            best, how = b, o
    if best is None or how < 0.5:
        return None, "no layout table region matches the table"
    key = _treread.WHOLE if area(best) >= float(spec.get("whole_at", 0.5)) else best
    fut = prefetch.peek(pc.doc, pc.page, key, _treread.PROMPTS[str(spec.get("prompt", "clean"))])
    if fut is None:
        return None, "no table read was started for the region"
    try:
        res = fut.result()
    except Exception as e:
        return None, f"prefetched table read unavailable ({str(e)[:80]})"
    if res.refused or res.truncated:
        return None, "prefetched table read is broken"
    table = _first_table(res.text)
    return table, ("" if table else "no table in the prefetched read")
