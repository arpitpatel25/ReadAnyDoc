"""Variant a, structure only: block types and boxes from the matched layout region. Reading is untouched.

A block (located by Ground from word evidence) is paired with a layout detection in one of three ways:
``match`` (IoU >= 0.5), ``part`` (the block lies inside a larger text detection: the reader split a paragraph)
or ``merge`` (several detections of one type lie inside the block: the reader joined them).
Types: the detection's type replaces the block's type, except that a block never becomes or stops being a
Table / Figure / Signature / Comment (those follow from the content, not from the box).
Boxes: on a mutual one-to-one ``match`` the detection's box replaces the word-union box (not for page
footers and captions, where the word box is the tighter one); a Figure block
(the reader's one-line description) takes the box of the picture detection it overlaps.
Split (``split_blocks``): a text block whose grounded words fall into two or more detections is cut at the
line breaks between them, so that each piece is one layout region. No character inside a piece changes.
"""

from __future__ import annotations

import re
from typing import Optional

from ...model import Page, Region, RegionType
from ...model import Candidate
from ...textnorm import plain_text
from .match import CONTAINER_LABELS, MARK_LABELS, area, box_of, inside, inter, iou, to_bbox

PICTURE_LABELS = {"picture", "image", "chart"}

CONTENT_TYPES = {RegionType.TABLE, RegionType.FIGURE, RegionType.SIGNATURE, RegionType.COMMENT}
HEADINGS = {RegionType.TITLE, RegionType.SECTION_HEADER}


def pair(page: Page, dets: list[dict], min_iou: float = 0.5) -> dict[str, tuple[str, dict, float]]:
    """region id -> (how, detection, IoU)."""
    out: dict[str, tuple[str, dict, float]] = {}
    cands = [d for d in dets if d["label"] not in MARK_LABELS]
    for r in page.regions:
        if r.bbox is None or r.bbox.width <= 0 or r.bbox.height <= 0 or (r.bbox.width > 0.95 and r.bbox.height > 0.95):
            continue
        rb = box_of(r.bbox)
        scored = [(iou(rb, tuple(d["box"])), d) for d in cands]
        scored = [(v, d) for v, d in scored if v > 0]
        if not scored:
            continue
        v, d = max(scored, key=lambda x: x[0])
        if v >= min_iou:
            out[r.id] = ("match", d, v)
            continue
        parts = [x for _, x in scored if x["label"] not in CONTAINER_LABELS and inside(rb, tuple(x["box"])) >= 0.8]
        if parts:
            x = min(parts, key=lambda x: area(tuple(x["box"])))
            out[r.id] = ("part", x, iou(rb, tuple(x["box"])))
            continue
        merged = [x for _, x in scored if x["label"] not in CONTAINER_LABELS and inside(tuple(x["box"]), rb) >= 0.8]
        if merged and len({x["type"] for x in merged}) == 1 \
                and sum(area(tuple(x["box"])) for x in merged) >= 0.5 * area(rb):
            x = max(merged, key=lambda x: area(tuple(x["box"])))
            out[r.id] = ("merge", x, iou(rb, tuple(x["box"])))
    return out


def _set_type(r: Region, t: RegionType, det: dict, how: str) -> None:
    r.meta.setdefault("type_before_regions", r.type.value)
    r.meta["type_rule"] = f"layout:{det['label']}:{how}"
    r.type = t
    if t not in HEADINGS:
        r.heading_level = None
    elif r.heading_level is None:
        r.heading_level = 1 if t == RegionType.TITLE else 2


def _allowed(frm: str, to: str, deny: frozenset, allow: Optional[frozenset]) -> bool:
    keys = (f"{frm}>{to}", f"*>{to}", f"{frm}>*")
    if any(k in deny for k in keys):
        return False
    return allow is None or any(k in allow for k in keys)


_MARKUP = re.compile(r"^(?:[\s>*_#]+|<(?:b|i|em|strong|p)>)+")
_ENUM = re.compile(
    r"(?:[-\u2022\u00b7\u25aa\u25a0\u25a1\u25cb\u25cf\u25e6\u2023\u25bc\u25ba\u25b6\u2013\u2014+]\s"   # bullet
    r"|<sup>|\[\w{1,4}\]|[\u2610\u2611\u2612]"                                                      # footnote mark, [1], box
    r"|\(?\w{1,4}\\?[.)](?:\s|\*|$)"                                                             # 1.  (a)  iv)  3\.
    r"|\w{1,4}(?:[.\-\u2013]\w{1,4})+[.)]?(?:\s|\*|$))")                                         # II.4.6  A-1  1.2.3
_PIPE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)
# one-line furniture and captions: the word-union box is tighter than the detector's (DocLayNet dev: IoU down)
LOOSE_BOX_LABELS = {"page_footer", "caption"}
SAME_TYPE_CUT_SCORE = 0.8
_SENTENCE_END = (".", "!", "?", ":", ";", "\u3002", "\uff01", "\uff1f", "\uff1a", "\uff1b")
TEXT_SCORE = 0.93   # a block the reader typed as something else becomes plain Text only on a confident detection


def starts_like_list_item(text: str) -> bool:
    """The block's own text begins with a bullet or an enumerator (after emphasis marks)."""
    t = text or ""
    if re.match(r"\s*(?:[-*+]|\d{1,3}\\?[.)])\s", t):  # markdown list written by the reader
        return True
    return bool(_ENUM.match(_MARKUP.sub("", t, count=1)))


def guarded(frm: str, to: str, det: dict, how: str, text: str) -> bool:
    """``types=guarded``: the type changes that held on the development half against all three references
    (DocLayNet labels, the business answer key, r-1's types). The layout region alone is not enough where the
    reader's type rests on the content: the content must agree, or the detection must be confident."""
    if frm == "Title":                    # the reader's page title stands (r-1 and the answer key keep it as Title)
        return to == "Header"
    if frm in ("Header", "Footer"):       # furniture the reader recognised stays furniture
        return to == "Text" and frm == "Footer"
    if to in ("Header", "Footer", "Title"):
        return True
    if to == "List Item":                 # only when the text itself starts like a list item
        return frm == "Text" and starts_like_list_item(text)
    if to == "Section Header":
        return frm == "Text"
    if to == "Text":                      # List Item / Key Value / Section Header -> Text
        return how == "match" and det.get("score", 0.0) >= TEXT_SCORE
    return False                          # nothing becomes Key Value from a box


def apply(page: Page, dets: list[dict], types: Optional[str] = None, boxes: bool = False,
          deny: frozenset = frozenset(), min_score: float = 0.5, allow: Optional[frozenset] = None) -> dict:
    """``types``: None | "layout" (every change) | "guarded" (``guarded`` decides). ``deny``: type changes
    "From>To" that are never made; ``allow``: when given, the only type changes that are made ("*" stands for
    any type on one side). Returns counts."""
    dets = [d for d in dets if d["score"] >= min_score]
    pairs = pair(page, dets)
    used: dict[str, int] = {}
    for how, d, _ in pairs.values():
        if how == "match":
            used[d["id"]] = used.get(d["id"], 0) + 1
    n_types = n_boxes = 0
    transitions: dict[str, int] = {}
    for r in page.regions:
        if r.id not in pairs:
            continue
        how, d, v = pairs[r.id]
        r.meta["layout_detection"] = {"id": d["id"], "label": d["label"], "how": how, "iou": round(v, 3)}
        if types and r.type not in CONTENT_TYPES:
            t = RegionType(d["type"])
            ok = t not in CONTENT_TYPES and t != r.type
            if d["label"] in CONTAINER_LABELS and how != "match":
                ok = False
            if ok and types == "guarded" and not guarded(r.type.value, t.value, d, how, r.content):
                ok = False
            if ok and _allowed(r.type.value, t.value, deny, allow):
                key = f"{r.type.value}>{t.value}"
                transitions[key] = transitions.get(key, 0) + 1
                _set_type(r, t, d, how)
                n_types += 1
        if boxes and how == "match" and used.get(d["id"]) == 1 and d["label"] not in LOOSE_BOX_LABELS:
            r.meta["bbox_before_regions"] = r.bbox.model_dump()
            r.bbox = to_bbox(tuple(d["box"]), r.bbox.page)
            r.meta["bbox_source"] = "layout"
            n_boxes += 1
    if boxes:
        n_boxes += _figure_boxes(page, dets, {d["id"] for h, d, _ in pairs.values() if h == "match"})
    return {"paired": len(pairs), "types_changed": n_types, "boxes_changed": n_boxes, "transitions": transitions}


def _figure_boxes(page: Page, dets: list[dict], used: set) -> int:
    """Figure blocks that no detection matched one-to-one: the picture detection they overlap most, or, when
    the page has as many unplaced Figure blocks as free picture detections, the same position in order."""
    pics = [d for d in dets if d["label"] in PICTURE_LABELS and d["id"] not in used]
    figs = [r for r in page.regions if r.type == RegionType.FIGURE and r.meta.get("bbox_source") != "layout"]
    n = 0
    for r in list(figs):
        if r.bbox is None or not pics:
            continue
        rb = box_of(r.bbox)
        best = max(pics, key=lambda d: inter(rb, tuple(d["box"])))
        if inter(rb, tuple(best["box"])) > 0.3 * min(area(rb), area(tuple(best["box"]))):
            r.meta["bbox_before_regions"] = r.bbox.model_dump()
            r.bbox = to_bbox(tuple(best["box"]), r.bbox.page)
            r.meta["bbox_source"] = "layout"
            pics.remove(best)
            figs.remove(r)
            n += 1
    free = [r for r in figs if r.bbox is None]
    if free and len(free) == len(pics):
        for r, d in zip(free, sorted(pics, key=lambda d: d["order"])):
            r.bbox = to_bbox(tuple(d["box"]), page.index)
            r.meta["bbox_source"] = "layout"
            n += 1
    return n


def split_blocks(page: Page, dets: list[dict], min_score: float = 0.5) -> int:
    """Cut text blocks along layout regions. Returns the number of blocks that were cut. The new blocks have
    no box yet (``search_bbox`` = their detection): the caller grounds the page again."""
    by_all = {d["id"]: d for d in dets}
    in_form = lambda d: (by_all.get(d.get("inside") or "") or {}).get("label") in ("form", "key_value_region")
    # fields of a form stay together (a label and the value under it are one block for a reader of the output)
    cands = [d for d in dets if d["score"] >= min_score and d["label"] not in MARK_LABELS
             and d["label"] not in CONTAINER_LABELS and not in_form(d)]
    out: list[Region] = []
    n_cut = 0
    for r in page.regions:
        words = sorted((w for l in r.lines for w in l.words if w.bbox is not None and w.char_start is not None),
                       key=lambda w: w.char_start)
        if r.type in CONTENT_TYPES or r.type == RegionType.KEY_VALUE or len(words) < 2 \
                or "<table" in r.content.lower() or "```" in r.content or _PIPE_ROW.search(r.content):
            out.append(r)
            continue
        owner = []
        for w in words:
            cx, cy = w.bbox.center
            inside_ = [d for d in cands if d["box"][0] - 0.004 <= cx <= d["box"][2] + 0.004
                       and d["box"][1] - 0.003 <= cy <= d["box"][3] + 0.003]
            owner.append(min(inside_, key=lambda d: area(tuple(d["box"])))["id"] if inside_ else None)
        if sum(o is not None for o in owner) < 0.8 * len(owner):
            out.append(r)
            continue
        runs: list[list] = []  # [detection id, first word index]
        for i, o in enumerate(owner):
            if o is None:
                continue
            if not runs or runs[-1][0] != o:
                runs.append([o, i])
        if len(runs) < 2 or len({x[0] for x in runs}) != len(runs):
            out.append(r)  # one region, or the block's words go back and forth between regions
            continue
        cuts = []  # (char position of a line break between two runs, detection id of the piece that starts there)
        by_cand = {d["id"]: d for d in cands}
        for j, (det_id, i) in enumerate(runs[1:], start=1):
            prev, cur = by_cand[runs[j - 1][0]], by_cand[det_id]
            a, b = words[i - 1].char_end, words[i].char_start
            if prev["type"] == cur["type"] and min(prev["score"], cur["score"]) < SAME_TYPE_CUT_SCORE:
                # two boxes of one type inside one block: an unsure box may cut it only where the text itself
                # ends a sentence or starts a new list item (a box around the last line of a paragraph is common)
                k = r.content.rfind("\n", a, b)
                before = r.content[:k].rstrip().rstrip("*_") if k >= 0 else ""
                if not (before.endswith(_SENTENCE_END) or starts_like_list_item(r.content[k + 1:] if k >= 0 else "")):
                    continue
            k = r.content.rfind("\n", a, b)
            if k >= 0:
                cuts.append((k, det_id))
        if not cuts:
            out.append(r)
            continue
        by_id = {d["id"]: d for d in cands}
        starts = [0] + [k for k, _ in cuts]
        ids = [runs[0][0]] + [d for _, d in cuts]
        pieces = []
        for j, a in enumerate(starts):
            b = starts[j + 1] if j + 1 < len(starts) else len(r.content)
            text = r.content[a:b].strip("\n")
            if text.strip():
                pieces.append((text, ids[j]))
        if len(pieces) < 2:
            out.append(r)
            continue
        n_cut += 1
        for k, (text, det_id) in enumerate(pieces):
            nr = Region(type=r.type, heading_level=r.heading_level if k == 0 else None, content=text,
                        text=plain_text(text), source=r.source, native_label=r.native_label,
                        candidates=[Candidate(source=r.source or "reader", content=text)],
                        meta={"split_from": r.id, "tier": r.meta.get("tier"),
                              "search_bbox": to_bbox(tuple(by_id[det_id]["box"]), page.index).model_dump()})
            if k == 0:
                nr.candidates.append(Candidate(source=r.source or "reader", content=r.content, meta={"before_split": True}))
            out.append(nr)
    if n_cut:
        page.regions = out
        for i, r in enumerate(page.regions):
            r.order = i
    return n_cut
