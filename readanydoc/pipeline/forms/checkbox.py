"""``checkbox_crops``: every group of checkboxes is read again from enlarged crops.

A page read sees a checkbox at a few pixels; whether it holds a mark is the detail it gets wrong most
easily. The marks the read wrote are grouped by where they are on the page (word boxes), each group is
cut out twice (a tight crop and a wider one) and read with a prompt that asks only for the state of
the transcription with every box in place. Each crop read is aligned with the page read word by word, so a
box is identified by the words around it, not by a guess about which label belongs to it. A mark in the
page read changes only when both crop reads show that box and both disagree with the page read.
Text is never changed.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Optional

from ...model import Candidate, Document, Page
from ...readers.base import ReadContext
from ...textnorm import plain_text
from . import locate
from .fillable import MARK, _is_checked, _mark

CROP_PROMPT = (
    "Read the image at {img}. It is an enlarged crop of part of a form. Transcribe the crop exactly as printed, "
    "in reading order, keeping every checkbox or radio button at the place where it is printed: write [x] for a "
    "box that holds a mark (a cross, tick, dot or fill, typed or handwritten) and [ ] for a box that is empty. "
    "Look at each box on its own before deciding. Do not add, drop or reorder anything. "
    "Output only the transcription, with no preamble and no code fences.")
_MARK_RE = re.compile(MARK)
_SEQ = re.compile(r"(" + MARK + r")|<[^>]+>|&[a-zA-Z#0-9]+;|[^\W_]+", re.U)
BOX = "\u25a1"  # every mark is the same token when two reads are aligned


def sequence(content: str) -> list[tuple[str, int, int, Optional[bool]]]:
    """(token, start, end, state) for the words and marks of a text; state is None for a word."""
    out = []
    for m in _SEQ.finditer(content or ""):
        t = m.group(0)
        if m.group(1):
            out.append((BOX, m.start(), m.end(), _is_checked(t)))
        elif not t.startswith(("<", "&")):
            out.append((t.lower(), m.start(), m.end(), None))
    return out


def marks_in(content: str) -> list[dict]:
    return [{"start": s, "end": e, "mark": content[s:e]} for t, s, e, st in sequence(content) if t == BOX]


def crop_states(content: str, crop_text: str) -> dict[int, bool]:
    """start offset of a mark in ``content`` -> the state the crop read shows for it. The two texts are
    aligned word by word; a mark counts only when it is aligned together with a word next to it."""
    a, b = sequence(content), sequence(crop_text)
    out: dict[int, bool] = {}
    sm = difflib.SequenceMatcher(None, [t[0] for t in a], [t[0] for t in b], autojunk=False)
    for i, j, n in sm.get_matching_blocks():
        if n < 2:
            continue
        for k in range(n):
            if a[i + k][0] == BOX:
                out[a[i + k][1]] = bool(b[j + k][3])
    return out


def groups(page: Page, loc: locate.Located, max_gap: float = 0.05, max_height: float = 0.30) -> list[dict]:
    """Marks grouped by position: a new group starts when the next mark is further down than
    ``max_gap`` or the group would get taller than ``max_height``."""
    items = []
    for ri, r in enumerate(page.regions):
        for k, m in enumerate(marks_in(r.content)):
            box = loc.span_box(ri, m["start"], m["end"], before=3, after=5)
            if box is not None:
                items.append({"region": ri, "index": k, "box": box, **m})
    items.sort(key=lambda x: (x["box"][1], x["box"][0]))
    out: list[dict] = []
    for it in items:
        g = out[-1] if out else None
        if g and it["box"][1] - g["box"][3] <= max_gap and max(it["box"][3], g["box"][3]) - g["box"][1] <= max_height:
            g["marks"].append(it)
            g["box"] = locate.union([g["box"], it["box"]])
        else:
            out.append({"marks": [it], "box": it["box"]})
    return out


def apply(doc: Document, page: Page, stage: Any, max_groups: int = 12) -> dict:
    if not any(_MARK_RE.search(r.content) for r in page.regions):
        return {}
    loc = locate.Located(page.regions, page)
    gs = groups(page, loc)
    if not gs:
        return {"marks": sum(len(marks_in(r.content)) for r in page.regions), "groups": 0}
    reader = stage._reader(stage.opt("checkbox_reader") or stage.ctx_reader_spec)
    stats = {"marks": sum(len(g["marks"]) for g in gs), "groups": len(gs), "crop_reads": 0, "paired_twice": 0,
             "changed": 0}
    edits: dict[int, list[tuple[int, int, str]]] = {}
    for gi, g in enumerate(gs[:max_groups]):
        votes: list[dict[tuple[int, int], bool]] = []
        in_regions = sorted({m["region"] for m in g["marks"]})
        for tag, (px, py) in (("a", (0.03, 0.012)), ("b", (0.08, 0.03))):
            box = locate.pad((0.0, g["box"][1], 1.0, g["box"][3]) if tag == "b" else g["box"], px, py)
            try:
                png = locate.crop_png(doc, page, box)
                res = reader.read(png, None, ReadContext(stem=f"{doc.stem}#cb{gi}{tag}", page_index=page.index,
                                                         prompt=CROP_PROMPT, max_tokens=4000))
            except Exception as e:
                doc.note("read", f"page {page.index}: checkbox crop failed ({str(e)[:100]})", "warning")
                break
            doc.usage.append({"page": page.index, "reader": reader.name, "escalation": "checkbox_crop",
                              **res.usage.model_dump(exclude_none=True)})
            stats["crop_reads"] += 1
            vote = {}
            for ri in in_regions:
                for start, state in crop_states(page.regions[ri].content, res.text).items():
                    vote[(ri, start)] = state
            votes.append(vote)
        if len(votes) < 2:
            continue
        for m in g["marks"]:
            k = (m["region"], m["start"])
            if k in votes[0] and k in votes[1]:
                stats["paired_twice"] += 1
                if votes[0][k] == votes[1][k] != _is_checked(m["mark"]):
                    edits.setdefault(m["region"], []).append((m["start"], m["end"], _mark(votes[0][k], m["mark"])))
    for ri, es in edits.items():
        r = page.regions[ri]
        c = r.content
        for s, e, rep in sorted(es, reverse=True):
            c = c[:s] + rep + c[e:]
        r.candidates.append(Candidate(source=r.source or "reader", content=r.content,
                                      meta={"replaced": True, "by": "forms.checkbox_crops"}))
        r.content, r.text = c, plain_text(c)
        r.meta.setdefault("forms", {})["marks_changed"] = len(es)
        stats["changed"] += len(es)
    return stats
