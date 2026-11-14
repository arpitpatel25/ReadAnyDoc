"""Form fields and block structure on business pages. Every step is a switch, off by
default (``READANYDOC_REGIONS`` / ground option ``regions``, see ``__init__.py``). No model call, and no character
of the page read changes: a step only decides where a block ends, what type it has and where its box is.

    hfsplit     a one-line block in the top or bottom band whose words stand in groups far apart on the line
                ("436   MIRANDA v. ARIZONA   Syllabus.") becomes one block per group (r-1 and the answer key
                have one Header / Footer / Page Number block per group)
    kvsplit     a Key Value block inside a detected form region that runs over several layout regions is cut at
                the line breaks between them, only where the next line starts a new field (a label is never cut
                from its value); the pieces stay Key Value
    kv=form     a short block without a sentence inside a detected form / key-value region is typed Key Value
                (only where the page read itself shows a field in that region; +any: everywhere)
                +heads: also a Header / Section Header there that a position or type-size rule made from Text
                +page:  on a page without any form region or checkbox, the reader's Key Value lines are Text
                        (``kv=page``: this rule alone)
    figures     a picture detection that no block covers (logo, seal, stamp) becomes a Figure block (box only)
    boxfill     a block whose box is only an estimate takes the box of the free layout region with the same words
    formread    form regions are read from a crop with the form prompt as additional evidence for Key Value
                typing and for joining a label with its value (formevidence.py; the only step with a model call)
"""

from __future__ import annotations

import re
import statistics
from typing import Optional

from ...model import Candidate, Page, Region, RegionType
from ...textnorm import norm_tokens, plain_text
from .match import CONTAINER_LABELS, FORM_LABELS, MARK_LABELS, area, box_of, inside, inter, to_bbox

from . import furniture as _furniture  # structure rules without model calls (all off by default)

SWITCHES = ("hfsplit", "kvsplit", "kv", "figures", "boxfill", "formread") + _furniture.SWITCHES
CONTENT_TYPES = {RegionType.TABLE, RegionType.FIGURE, RegionType.SIGNATURE, RegionType.COMMENT}
PICTURE_LABELS = {"picture", "image", "chart"}

TOP_BAND = 0.12          # a one-line block that ends above this line, or starts below BOTTOM_BAND, may be furniture
BOTTOM_BAND = 0.86
LINE_GAP = 0.07          # page widths between two word groups of one furniture line
_CHECKBOX = re.compile(r"\[[ xX✓✔]?\]|[☐☑☒□■▢]")
_BLANK = re.compile(r"_{3,}|\.{6,}|<empty\s*/?>")
_SENTENCE = re.compile(r"[.!?][\"”’)]?\s+[A-Z]")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
_LABEL_START = re.compile(
    r"\s*(?:[-*]\s+)?(?:"
    r"\*\*[^*\n]{1,80}\*\*"                       # **Label** / **Label:**
    r"|<b>[^<\n]{1,80}</b>"
    r"|\[[ xX✓✔]?\]|[☐☑☒□■▢]"                     # a checkbox opens the line
    r"|[^:\n|<>*]{1,60}:(?!\d)"                   # Label:
    r"|\(?\d{1,2}[a-z]?[.)]\s+\S"                 # 12a. numbered field
    r")")


def _words(r: Region):
    return sorted((w for l in r.lines for w in l.words if w.bbox is not None and w.char_start is not None),
                  key=lambda w: w.char_start)


def _piece(r: Region, text: str, box, page: Page, k: int, rule: str) -> Region:
    meta = {"split_from": r.id, "tier": r.meta.get("tier"), "split_rule": rule}
    if box is not None:
        meta["search_bbox"] = to_bbox(tuple(box), page.index).model_dump()
    nr = Region(type=r.type, heading_level=r.heading_level if k == 0 else None, content=text, text=plain_text(text),
                source=r.source, native_label=r.native_label,
                candidates=[Candidate(source=r.source or "reader", content=text)], meta=meta)
    if k == 0:
        nr.candidates.append(Candidate(source=r.source or "reader", content=r.content, meta={"before_split": True}))
    return nr


def _renumber(page: Page, out: list[Region]) -> None:
    page.regions = out
    for i, r in enumerate(page.regions):
        r.order = i


# ------------------------------------------------------------------------------------------- furniture lines
def _balanced(piece: str) -> bool:
    """Emphasis marks and tags open and close inside the piece (a cut never falls inside a bold span)."""
    if piece.count("**") % 2 or piece.replace("**", "").count("*") % 2 or piece.count("`") % 2:
        return False
    return piece.count("<") == piece.count(">") and len(re.findall(r"<[a-z]+\b", piece)) == len(re.findall(r"</", piece))


def _emphasis(content: str) -> Optional[tuple[str, str]]:
    """(opening mark, closing mark) when the whole line is one emphasis span or one <p>, else ("", "");
    None for a heading or a table row, which are never cut."""
    c = content.strip()
    if c.startswith(("#", "|", ">")):
        return None
    if c.startswith("<p>") and c.endswith("</p>") and "<p" not in c[3:]:
        return "<p>", "</p>"
    for m in ("**", "*"):
        if c.startswith(m) and c.endswith(m) and len(c) > 2 * len(m) and m[0] not in c[len(m):-len(m)]:
            return m, m
    return "", ""


_EDGE = re.compile(r"^(?:\s|\||&nbsp;)+|(?:\s|\||&nbsp;)+$")


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _line_words(r: Region, page: Optional[Page]) -> list[tuple[int, int, tuple]]:
    """(char start, char end, box) of the block's placed words, plus tokens at either end of the text that the
    grounding left out when the page's words have them on the same printed line, clear of the placed words
    (a page number printed at the far edge of a running head)."""
    ws = [(w.char_start, w.char_end if w.char_end is not None else w.char_start + len(w.text),
           (w.bbox.left, w.bbox.top, w.bbox.left + w.bbox.width, w.bbox.top + w.bbox.height)) for w in _words(r)]
    if not ws or page is None:
        return ws
    words = None
    from ..grounding.sources import source_priority
    for name, _trusted in source_priority(page):
        if page.words.get(name):
            words = page.words[name]
            break
    if not words:
        return ws
    placed = {a for a, _, _ in ws}
    toks = [(m.start(), m.end(), m.group(0).lower()) for m in _TOKEN.finditer(r.content)]
    first, last = min(placed), max(placed)
    lead = [t for t in toks if t[0] < first and t[0] not in placed]
    trail = [t for t in toks if t[0] > last and t[0] not in placed]
    if not lead and not trail or len(lead) > 3 or len(trail) > 3:
        return ws
    cy = statistics.median((b[1] + b[3]) / 2 for _, _, b in ws)
    h = statistics.median(b[3] - b[1] for _, _, b in ws)
    x0, x1 = min(b[0] for _, _, b in ws), max(b[2] for _, _, b in ws)
    on_line = [w for w in words if w.bbox is not None and abs(w.bbox.top + w.bbox.height / 2 - cy) <= max(0.7 * h, 0.005)]
    out = list(ws)
    for side, group in (("lead", lead), ("trail", trail)):
        for a, e, t in group:
            hits = [w for w in on_line if "".join(_TOKEN.findall(w.text.lower())) == t
                    and (w.bbox.left + w.bbox.width <= x0 + 0.005 if side == "lead" else w.bbox.left >= x1 - 0.005)]
            if len(hits) == 1:
                w = hits[0]
                out.append((a, e, (w.bbox.left, w.bbox.top, w.bbox.left + w.bbox.width, w.bbox.top + w.bbox.height)))
    return sorted(out)


def furniture_groups(r: Region, page: Optional[Page] = None, gap: float = LINE_GAP) -> Optional[list[tuple[int, int, tuple]]]:
    """Word groups of a block printed on one line: [(first char, end char, box)], or None when the block is one
    group, is not one printed line, or too few of its words are placed."""
    c = r.content
    if r.bbox is None:
        return None
    ws = _line_words(r, page)
    n_tokens = len(plain_text(c).split())
    if not 2 <= len(ws) <= 30 or len(ws) < 0.8 * n_tokens:
        return None
    line = statistics.median(b[3] - b[1] for _, _, b in ws)
    cys = [(b[1] + b[3]) / 2 for _, _, b in ws]
    if max(cys) - min(cys) > max(1.2 * line, 0.008):      # not one printed line
        return None
    groups = [[ws[0]]]
    for a, b in zip(ws, ws[1:]):
        if b[2][0] < a[2][0] - 0.002:                     # the text order is not the order on the line
            return None
        if b[2][0] - a[2][2] >= gap:
            groups.append([b])
        else:
            groups[-1].append(b)
    if len(groups) < 2:
        return None
    def cut(k: int) -> int:
        """The cut before group k: back over marks glued to its first word, never into the word before."""
        a, floor = groups[k][0][0], groups[k - 1][-1][1]
        while a > floor and not c[a - 1].isspace():
            a -= 1
        return a

    out = []
    for k, g in enumerate(groups):
        a = 0 if k == 0 else cut(k)
        e = len(c) if k == len(groups) - 1 else cut(k + 1)
        box = (min(w[2][0] for w in g), min(w[2][1] for w in g), max(w[2][2] for w in g), max(w[2][3] for w in g))
        out.append((a, e, box))
    return out


def split_furniture(page: Page, dets: list[dict]) -> int:
    """``hfsplit``. Returns the number of blocks that were cut; the caller grounds the page again."""
    out: list[Region] = []
    n = 0
    for r in page.regions:
        b = r.bbox
        if r.type in CONTENT_TYPES or b is None or not (b.top + b.height <= TOP_BAND or b.top >= BOTTOM_BAND) \
                or _CHECKBOX.search(r.content) or _BLANK.search(r.content):
            out.append(r)
            continue
        em = _emphasis(r.content)
        groups = furniture_groups(r, page) if em is not None else None
        if not groups:
            out.append(r)
            continue
        opening, closing = em
        texts = []
        for a, e, box in groups:
            t = r.content[a:e].strip()
            if opening:
                t = t[len(opening):] if t.startswith(opening) else t
                t = t[:-len(closing)] if t.endswith(closing) else t
            t = _EDGE.sub("", _EDGE.sub("", t))     # separators the reader wrote between the groups
            texts.append((f"{opening}{t}{closing}" if opening and t else t, box))
        if any(not plain_text(t).strip() or not _balanced(t) for t, _ in texts):
            out.append(r)
            continue
        n += 1
        for k, (t, box) in enumerate(texts):
            out.append(_piece(r, t, box, page, k, "hfsplit"))
    if n:
        _renumber(page, out)
    return n


# ------------------------------------------------------------------------------------------- key value blocks
def starts_field(line: str) -> bool:
    return bool(_LABEL_START.match(line))


def _open_label(line: str) -> bool:
    p = plain_text(line).rstrip()
    return p.endswith(":") or p.endswith(("-", "–", "—", ",", "&", "/"))


def split_key_values(page: Page, dets: list[dict], min_score: float = 0.5) -> int:
    """``kvsplit``. Lines of a Key Value block are grouped by the layout region their words lie in; a new block
    starts at a line break where the region changes, the regions do not touch, and the next line starts a field."""
    cands = [d for d in dets if d["score"] >= min_score and d["label"] not in MARK_LABELS
             and d["label"] not in CONTAINER_LABELS]
    by_id = {d["id"]: d for d in cands}
    forms = [d for d in dets if d["label"] in FORM_LABELS and d["score"] >= min_score]
    out: list[Region] = []
    n = 0
    for r in page.regions:
        # only inside a detected form region: there the layout model agrees that the block holds fields
        if r.type != RegionType.KEY_VALUE or "<table" in r.content.lower() or r.content.count("\n") < 1 \
                or form_container(r, forms, 0.5) is None:
            out.append(r)
            continue
        ws = _words(r)
        lines = []          # [start char, end char, owner detection id or None]
        pos = 0
        for raw in r.content.split("\n"):
            a, e = pos, pos + len(raw)
            pos = e + 1
            if not raw.strip():
                continue
            own: dict = {}
            for w in ws:
                if a <= w.char_start < e:
                    cx, cy = w.bbox.center
                    ins = [d for d in cands if d["box"][0] - 0.004 <= cx <= d["box"][2] + 0.004
                           and d["box"][1] - 0.003 <= cy <= d["box"][3] + 0.003]
                    if ins:
                        k = min(ins, key=lambda d: area(tuple(d["box"])))["id"]
                        own[k] = own.get(k, 0) + 1
            lines.append([a, e, max(own, key=own.get) if own else None])
        if len(lines) < 2:
            out.append(r)
            continue
        starts = [0]
        for i in range(1, len(lines)):
            prev, cur = lines[i - 1][2], lines[i][2]
            if prev is None or cur is None or prev == cur:
                continue
            pb, cb = tuple(by_id[prev]["box"]), tuple(by_id[cur]["box"])
            if inter(pb, cb) > 0:                         # nested or touching boxes: one field group
                continue
            if not starts_field(r.content[lines[i][0]:lines[i][1]]) \
                    or _open_label(r.content[lines[i - 1][0]:lines[i - 1][1]]):
                continue
            starts.append(i)
        if len(starts) < 2:
            out.append(r)
            continue
        n += 1
        for k, i in enumerate(starts):
            j = starts[k + 1] if k + 1 < len(starts) else len(lines)
            text = r.content[lines[i][0]:lines[j - 1][1]].strip("\n")
            ids = [l[2] for l in lines[i:j] if l[2] is not None]
            boxes = [tuple(by_id[x]["box"]) for x in dict.fromkeys(ids)]
            box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                   max(b[3] for b in boxes)) if boxes else None
            out.append(_piece(r, text, box, page, k, "kvsplit"))
    if n:
        _renumber(page, out)
    return n


# ------------------------------------------------------------------------------------------- field typing
def field_like(content: str) -> bool:
    """A label, a label with its value, or a few of them: short lines without a sentence."""
    p = plain_text(content).strip()
    lines = [l.strip() for l in p.split("\n") if l.strip()]
    if not lines or len(lines) > 4:
        return False
    toks = p.split()
    if len(toks) > 40 or not any(ch.isalnum() for ch in p):
        return False
    for l in lines:
        n = len(l.split())
        if n > 16 or _URL.search(l):
            return False
        if n > 7 and _SENTENCE.search(l):
            return False
        if n >= 5 and l.endswith((".", "!", "?")) and ":" not in l and not l.endswith(("No.", "etc.", "Inc.", "Co.")):
            return False          # a note ("See attached statement for additional information."), not a field
    return True


def form_container(r: Region, forms: list[dict], min_inside: float = 0.6) -> Optional[dict]:
    if r.bbox is None or r.bbox.width <= 0 or r.bbox.height <= 0 or (r.bbox.width > 0.95 and r.bbox.height > 0.95):
        return None
    rb = box_of(r.bbox)
    best = [d for d in forms if inside(rb, tuple(d["box"])) >= min_inside]
    return min(best, key=lambda d: area(tuple(d["box"]))) if best else None


def shows_fields(page: Page, d: dict) -> bool:
    """The page read itself shows a field inside the form region: a block the reader typed Key Value, a checkbox
    or a blank. Without it the region is the layout model's opinion alone (on OmniDocBench dev it calls a page of
    exercises, a page of notes and the source lines under tables form regions: 66 text blocks)."""
    key = "_shows_fields"
    if key not in d:
        db = tuple(d["box"])
        d[key] = any(r.bbox is not None and inside(box_of(r.bbox), db) >= 0.6 and r.bbox.height <= 0.95
                     and (r.type == RegionType.KEY_VALUE and r.meta.get("type_rule", "").split(":")[0] != "form_field"
                          or r.type != RegionType.TABLE and (_CHECKBOX.search(r.content) or _BLANK.search(r.content)))
                     for r in page.regions)
    return d[key]


def type_fields(page: Page, dets: list[dict], mode: str = "form", min_score: float = 0.5) -> dict:
    """``kv=form``: Text blocks inside a detected form region that look like fields become Key Value, when the
    page read itself shows a field in that region (``+any``: without that condition).
    ``kv=form+heads``: also Header / Section Header blocks there that the reader did not mark as headings."""
    for d in dets:
        d.pop("_shows_fields", None)
    forms = [d for d in dets if d["label"] in FORM_LABELS and d["score"] >= min_score]
    parts = set(mode.split("+"))
    if not forms:
        return untype_page(page, dets) if "page" in parts else {}
    if "form" not in parts:          # ``kv=page``: only the rule for pages without a form region
        return {}
    n = 0
    for r in page.regions:
        if r.type == RegionType.TEXT:
            pass
        elif "heads" in parts and r.type in (RegionType.HEADER, RegionType.SECTION_HEADER) \
                and not r.content.lstrip().startswith("#") \
                and (r.meta.get("type_before_regions") == "Text" or r.meta.get("type_before_layout") == "Text"):
            pass
        else:
            continue
        d = form_container(r, forms)
        if d is None or not field_like(r.content) or r.meta.get("form_plain"):
            continue          # form_plain: the form read of the region (formread) wrote this text as a plain line
        if "any" not in parts and not shows_fields(page, d):
            continue
        if r.bbox.top >= 0.945:                       # the form number and page number under a form are footers
            continue
        r.meta.setdefault("type_before_fields", r.type.value)
        r.meta["type_rule"] = f"form_field:{d['label']}"
        r.type = RegionType.KEY_VALUE
        r.heading_level = None
        n += 1
    return {"typed_key_value": n} if n else {}


def untyped_blocks(page: Page, dets: list[dict]) -> list[Region]:
    """The Key Value blocks that ``kv=page`` makes Text (the rule of ``untype_page``, without applying it)."""
    if any(d["label"] in FORM_LABELS and d["score"] >= 0.3 for d in dets) or any(d["label"] in MARK_LABELS for d in dets):
        return []
    return [r for r in page.regions
            if r.type == RegionType.KEY_VALUE and not (_CHECKBOX.search(r.content) or _BLANK.search(r.content)
                                                       or "<table" in r.content.lower() or r.meta.get("forms")
                                                       or r.meta.get("form_evidence"))]


def mark_text_like(page: Page, dets: list[dict], on: dict) -> int:
    """The table step runs before ``kv=page``; its missed-table rule leaves a region alone
    when the page read typed a block in it Key Value (a form is not a table). On a page without any form region
    ``kv=page`` will make such a block Text afterwards, so the table step must see it as text already: a menu
    written as "**Dish** M: $16.00 NM: $17.50" lines was typed Key Value by one page read and Text by another, and
    only the second was recovered as a table. Marks the blocks (``meta["kv_is_text"]``); no type changes here."""
    if "page" not in str(on.get("kv", "")).split("+"):
        return 0
    blocks = untyped_blocks(page, dets)
    for r in blocks:
        r.meta["kv_is_text"] = True
    return len(blocks)


def untype_page(page: Page, dets: list[dict]) -> dict:
    """``kv=form+page``: on a page where the layout model sees no form region at all (not even an unsure one) and
    no checkbox, a line the reader typed Key Value because of its colon ("Keywords: ...", "Log P: 4.43") is text.
    A block that shows a blank or a checkbox keeps its type."""
    n = 0
    for r in untyped_blocks(page, dets):
        r.meta.setdefault("type_before_fields", r.type.value)
        r.meta["type_rule"] = "no_form_on_page"
        r.type = RegionType.TEXT
        n += 1
    return {"untyped_key_value": n} if n else {}


# ------------------------------------------------------------------------------------------- figures
def add_figures(page: Page, dets: list[dict], min_score: float = 0.6, min_area: float = 0.005) -> int:
    """``figures``: picture detections that no block covers. The block has a box and no content (no model call).
    ``min_area`` (share of the page): on the dev pages 7 of the 10 added figures that the OmniDocBench labels do
    not call a figure were icons and ornaments under 0.5% of the page (they belong to a title or header there)."""
    pics = [d for d in dets if d["label"] in PICTURE_LABELS and d["score"] >= min_score
            and area(tuple(d["box"])) >= min_area and area(tuple(d["box"])) <= 0.6]
    if not pics:
        return 0
    located = [r for r in page.regions if r.bbox is not None and r.bbox.width > 0 and r.bbox.height > 0
               and not (r.bbox.width > 0.95 and r.bbox.height > 0.95)]
    n = 0
    for d in sorted(pics, key=lambda d: (d["box"][1], d["box"][0])):
        db = tuple(d["box"])
        if any(r.type in (RegionType.FIGURE, RegionType.TABLE, RegionType.SIGNATURE)
               and inter(db, box_of(r.bbox)) > 0.2 * min(area(db), area(box_of(r.bbox))) for r in located):
            continue
        # text the reader transcribed from inside the picture (a letterhead read as text) stays as it is
        covered = sum(inter(db, box_of(r.bbox)) for r in located if inside(box_of(r.bbox), db) >= 0.5)
        if covered > 0.5 * area(db):
            continue
        # no content: nothing was read there and no description is made up (a "[Figure]" placeholder inside the
        # text cost 3 olmOCR old_scans tests on the held half); the block says where the picture is
        fig = Region(type=RegionType.FIGURE, content="", text="", source="layout",
                     native_label=d["label"], bbox=to_bbox(db, page.index),
                     meta={"bbox_source": "layout", "type_rule": f"layout_picture:{d['label']}", "added_by": "figures",
                           "layout_detection": {"id": d["id"], "label": d["label"], "how": "added", "iou": 1.0}})
        k = next((i for i, r in enumerate(page.regions) if r.bbox is not None and r in located
                  and (r.bbox.top + r.bbox.height / 2 > db[1] + (db[3] - db[1]) / 2
                       and r.bbox.top >= db[1] - 0.01)), len(page.regions))
        page.regions.insert(k, fig)
        located.append(fig)
        n += 1
    if n:
        for i, r in enumerate(page.regions):
            r.order = i
    return n


# ------------------------------------------------------------------------------------------- boxes
def fill_boxes(page: Page, dets: list[dict], min_score: float = 0.5) -> int:
    """``boxfill``: a block with an estimated (or no) box takes the box of the one free text detection whose
    words are the block's words."""
    from ..common import words_in
    from ..grounding.sources import source_priority
    need = [r for r in page.regions if r.type not in CONTENT_TYPES and plain_text(r.content).strip()
            and (r.bbox is None or str(r.meta.get("bbox_source", "")).startswith("estimate")
                 or (r.bbox.width > 0.95 and r.bbox.height > 0.95))]
    if not need:
        return 0
    words = None
    for name, _trusted in source_priority(page):
        if page.words.get(name):
            words = page.words[name]
            break
    if not words:
        return 0
    taken = [box_of(r.bbox) for r in page.regions if r.bbox is not None and r not in need
             and not (r.bbox.width > 0.95 and r.bbox.height > 0.95)]
    free = []
    for d in dets:
        if d["score"] < min_score or d["label"] in MARK_LABELS or d["label"] in CONTAINER_LABELS:
            continue
        db = tuple(d["box"])
        # taken: a located block sits in the detection or matches it (a large block that merely covers it does not)
        if any(inside(t, db) >= 0.6 or inter(db, t) >= 0.3 * (area(db) + area(t) - inter(db, t)) for t in taken):
            continue
        toks = norm_tokens(" ".join(words[i].text for i in words_in(words, to_bbox(db, page.index))))
        if toks:
            free.append((d, toks))
    def fits(rt: list, toks: list) -> bool:
        if len(rt) <= 2:
            return toks == rt
        common = sum(min(rt.count(t), toks.count(t)) for t in set(rt))
        return common >= 0.8 * len(rt) and common >= 0.7 * len(toks)

    def anchor(r: Region) -> Optional[tuple[float, float]]:
        """Where the block should be: between its located neighbours in reading order."""
        i = next(k for k, x in enumerate(page.regions) if x is r)
        pts = []
        for step in (-1, 1):
            k = i + step
            while 0 <= k < len(page.regions):
                x = page.regions[k]
                if x.bbox is not None and not any(x is y for y in need) and not (x.bbox.width > 0.95 and x.bbox.height > 0.95):
                    pts.append(x.bbox.center)
                    break
                k += step
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else None

    def dist(d: dict, a: Optional[tuple[float, float]]) -> float:
        if a is None:
            return 1.0
        return abs((d["box"][0] + d["box"][2]) / 2 - a[0]) + abs((d["box"][1] + d["box"][3]) / 2 - a[1])

    claims: dict[str, list] = {}
    for r in need:
        rt = norm_tokens(plain_text(r.content))
        hits = [d for d, toks in free if rt and fits(rt, toks)]
        if not hits:
            continue
        a = anchor(r)
        hits.sort(key=lambda d: dist(d, a))
        # several regions with the same words (a form printed twice): the one clearly nearest to the neighbours
        if len(hits) == 1 or (a is not None and dist(hits[0], a) < 0.5 * dist(hits[1], a)):
            claims.setdefault(hits[0]["id"], []).append((dist(hits[0], a), r, hits[0]))
    n = 0
    for got in claims.values():
        got.sort(key=lambda g: g[0])
        if len(got) > 1 and not got[0][0] < 0.5 * got[1][0]:
            continue                # two blocks want the detection and neither is clearly nearer
        _, r, d = got[0]
        if r.bbox is not None:
            r.meta["bbox_before_fill"] = r.bbox.model_dump()
        r.bbox = to_bbox(tuple(d["box"]), page.index)
        r.meta["bbox_source"] = "layout_text"
        r.meta["layout_detection"] = {"id": d["id"], "label": d["label"], "how": "text", "iou": 1.0}
        n += 1
    return n


# ------------------------------------------------------------------------------------------- stage hooks
def before_types(stage, doc, page: Page, dets: list[dict], on: dict, reader_spec: str) -> None:
    """Steps that change where blocks end; the page is grounded again when one of them did."""
    from . import assemble as _assemble
    info: dict = {}
    if any(k in on for k in _furniture.SWITCHES):      # cuts by layout detections and word positions
        r3 = _furniture.before_types(stage, doc, page, dets, on)
        if r3:
            page.properties["round3"] = r3
            info["round3"] = sum(r3.values())
    if "hfsplit" in on:
        info["hfsplit"] = split_furniture(page, dets)
    if "kvsplit" in on:
        info["kvsplit"] = split_key_values(page, dets)
    regrounded = any(info.values())
    if "formread" in on:
        from . import formevidence
        info["formread"] = formevidence.run(doc, page, reader_spec)
        regrounded = regrounded or bool(info["formread"].get("joined"))
    if regrounded:
        _furniture.release_boxes(page)
        stage._page(doc, page)
        _assemble.restore_types(page)
    else:
        _furniture.release_boxes(page)
    info.pop("round3", None)
    page.properties["region_fields"] = {k: v for k, v in info.items() if v}


def after_types(doc, page: Page, dets: list[dict], on: dict) -> None:
    """Steps on the located and typed blocks."""
    info = page.properties.setdefault("region_fields", {})
    for r in page.regions:      # a piece of a Key Value block is Key Value (the layout calls each piece "text")
        if r.meta.get("split_rule") == "kvsplit" and r.type != RegionType.KEY_VALUE:
            r.type = RegionType.KEY_VALUE
            r.heading_level = None
    if "boxfill" in on:
        info["boxfill"] = fill_boxes(page, dets)
    if "kv" in on:
        info["kv"] = type_fields(page, dets, on["kv"])
    if "formread" in on:
        from . import formevidence
        info["formread_typed"] = formevidence.retype(page)
    if "figures" in on:
        info["figures"] = add_figures(page, dets)
    if any(k in on for k in _furniture.SWITCHES):      # label rules
        r3 = _furniture.after_types(doc, page, dets, on)
        if r3:
            page.properties.setdefault("round3", {}).update(r3)
