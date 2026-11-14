"""Structure rules that need no model call. Every rule is a switch, off by default (``READANYDOC_REGIONS`` /
ground option ``regions``). No model call, and no character of the page read is lost: a rule only decides where
a block ends, what type it has and where its box is. The evidence is the position of the words on the page, the
layout model's detections and the markup the reader wrote; nothing is specific to a page or a document.

    hfdet       a block whose words lie in two or more page-header / page-footer detections of the layout model
                (one printed line, or several) is cut at the borders between them: one block per detection
                ("H.8 ASSETS AND LIABILITIES ... | Page 2")
    pnsplit     a page number at the start or the end of a running head or foot ("... Bankruptcy   page 1",
                "MIRANDA v. ARIZONA.   485") becomes its own block when the words on the page show it standing
                clear of the rest of the line (or on another printed line, or in its own furniture detection)
    hftable     a table of at most four short cells that the layout model calls a page header / footer (and not a
                table) is furniture: one block per cell ("Form 1065 (1999) | Page 4")
    prose       a one-column table whose cells hold running text, where the layout model sees no table, is text
                the reader wrapped in table markup: one block per printed line of its cells
    lab=...     label rules (``+`` joins them):
                cap    a bold line the reader typed Section Header that the layout model calls a caption is Text
                list   a one-line List Item that the layout model calls a section header / title is a Section Header
                long   a Section Header that is a paragraph (a bold lead-in followed by sentences) is Text
                paren  a Header made from Text by position alone whose whole text is bracketed ("[In millions of
                       dollars]", a unit line over a table) is Text again
                fig    short Text blocks inside one confident picture detection (labels read out of a diagram or
                       a screenshot) become part of the figure: typed Figure
"""

from __future__ import annotations

import re
import statistics
from typing import Optional

from ...model import Candidate, Line, Page, Region, RegionType, Word
from ...textnorm import plain_text
from .match import area, box_of, inside, inter, to_bbox

SWITCHES = ("hfdet", "pnsplit", "hftable", "prose", "lab")
FURNITURE_LABELS = {"page_header", "page_footer", "header", "footer", "number"}
PICTURE_LABELS = {"picture", "image", "chart"}
CONTENT_TYPES = {RegionType.TABLE, RegionType.FIGURE, RegionType.SIGNATURE, RegionType.COMMENT}
HEADINGS = {RegionType.TITLE, RegionType.SECTION_HEADER}
TOP_BAND, BOTTOM_BAND = 0.12, 0.86      # the bands of ``fields.hfsplit``
DET_GAP = 0.05           # page widths between two furniture detections that are cut apart
PN_GAP_WORD = 0.03       # page widths between "Page 2" and the rest of its line
PN_GAP_BARE = 0.06       # ... for a bare number
_CHECKBOX = re.compile(r"\[[ xX✓✔]?\]|[☐☑☒□■▢]")
_BLANK = re.compile(r"_{3,}|\.{6,}|<empty\s*/?>")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_EDGE = re.compile(r"^(?:\s|\||·|•|&nbsp;)+|(?:\s|\||·|•|&nbsp;)+$")
_YEAR = re.compile(r"(?:19|20)\d\d$")
_PAGE_WORD = {"page", "pg", "p", "seite", "página", "pagina"}
_SENTENCE = re.compile(r"[.!?;:][\"”’)]?\s+\S")


def _new(r: Region, text: str, type_: RegionType, box, page: Page, k: int, rule: str, words=None) -> Region:
    """A piece of ``r``. ``words``: [(token text, box)] in text order, the located words of the piece: the steps
    that follow (``hfsplit``, the next rule here) see them before the page is grounded again."""
    meta = {"split_from": r.id, "tier": r.meta.get("tier"), "split_rule": rule}
    if box is not None:
        meta["search_bbox"] = to_bbox(tuple(box), page.index).model_dump()
    level = (r.heading_level if k == 0 else 2) if type_ in HEADINGS else None
    nr = Region(type=type_, heading_level=level, content=text, text=plain_text(text), source=r.source,
                native_label=r.native_label, candidates=[Candidate(source=r.source or "reader", content=text)], meta=meta)
    if k == 0:
        nr.candidates.append(Candidate(source=r.source or "reader", content=r.content, meta={"before_split": True}))
    if box is not None and words:      # until the page is grounded again (``release_boxes``)
        nr.bbox = to_bbox(tuple(box), page.index)
        nr.meta["box_until_grounded"] = True
    if words:
        ws, pos = [], 0
        for t, b in words:
            i = text.lower().find(t.lower(), pos)
            if i < 0 or b is None:
                continue
            ws.append(Word(text=text[i:i + len(t)], bbox=to_bbox(tuple(b), page.index), char_start=i, char_end=i + len(t)))
            pos = i + len(t)
        if ws:
            nr.lines = [Line(words=ws)]
    return nr


def release_boxes(page: Page) -> None:
    """Before the page is grounded again: the provisional boxes of the pieces go, grounding places them itself."""
    for r in page.regions:
        if r.meta.pop("box_until_grounded", None):
            r.bbox = None
            r.lines = []


def _renumber(page: Page, out: list[Region]) -> None:
    page.regions = out
    for i, r in enumerate(page.regions):
        r.order = i


def _page_words(page: Page):
    from ..grounding.sources import source_priority
    for name, _trusted in source_priority(page):
        if page.words.get(name):
            return page.words[name]
    return []


def _wbox(w) -> tuple:
    return (w.bbox.left, w.bbox.top, w.bbox.left + w.bbox.width, w.bbox.top + w.bbox.height)


_MARKUP_SPAN = re.compile(r"<[^<>\n]{1,80}>|&[a-zA-Z]{2,8};|&#\d{1,6};")


def token_spans(content: str) -> list[tuple[int, int]]:
    """(start, end) of the word tokens of a block's content; tag names and character entities are not words."""
    skip = [(m.start(), m.end()) for m in _MARKUP_SPAN.finditer(content)]
    out = []
    for m in _TOKEN.finditer(content):
        if not any(a <= m.start() < e for a, e in skip):
            out.append((m.start(), m.end()))
    return out


def located_tokens(r: Region, page: Optional[Page]) -> list[tuple[int, int, Optional[tuple]]]:
    """(char start, char end, box or None) for every token of the block's content, in text order. A token has the
    box of the word grounding placed on it; a token at either end of the text that grounding left out takes the
    page word with the same text that continues the printed line outwards (the nearest one beyond the placed words)."""
    c = r.content
    placed = {}
    for l in r.lines:
        for w in l.words:
            if w.bbox is not None and w.char_start is not None:
                placed[w.char_start] = _wbox(w)
    toks = [(a, e, c[a:e].lower()) for a, e in token_spans(c)]
    out: list[list] = []
    for a, e, t in toks:
        box = placed.get(a)
        if box is None:      # a word may start at a mark glued to the token ("(Slip", "**Page")
            box = next((b for s, b in placed.items() if a - 3 <= s < e), None)
        out.append([a, e, box, t])
    have = [i for i, x in enumerate(out) if x[2] is not None]
    if not have or page is None or len(have) == len(out):
        return [(a, e, b) for a, e, b, _ in out]
    # every word source of the page: the text layer may lack a stamped page number, or hold "page1" as one word
    words = [(_wbox(w), "".join(_TOKEN.findall(w.text.lower()))) for ws in page.words.values() for w in ws if w.bbox is not None]
    h = statistics.median(x[2][3] - x[2][1] for x in out if x[2] is not None)

    def find(texts: list[str], ref: tuple, side: int) -> Optional[tuple[int, tuple]]:
        """(number of tokens covered, box) of the nearest page word beyond ``ref`` on its printed line that is the
        next token, or the next two or three written as one word."""
        cy = (ref[1] + ref[3]) / 2
        best = None
        for m in (1, 2, 3):
            if m > len(texts):
                break
            t = "".join(texts[:m] if side > 0 else texts[:m][::-1])
            for b, wt in words:
                if wt != t or abs((b[1] + b[3]) / 2 - cy) > max(0.7 * h, 0.005):
                    continue
                d = b[0] - ref[2] if side > 0 else ref[0] - b[2]
                if d >= -0.005 and (best is None or d < best[0]):
                    best = (d, m, b)
            if best is not None:
                break
        return (best[1], best[2]) if best else None

    first, last = have[0], have[-1]
    i = last + 1
    while i < len(out) and i - last <= 5:                  # trailing tokens, outwards to the right
        got = find([x[3] for x in out[i:i + 3]], out[i - 1][2], +1)
        if got is None:
            break
        for j in range(i, i + got[0]):
            out[j][2] = got[1]
        i += got[0]
    i = first - 1
    while i >= 0 and first - i <= 5:                       # leading tokens, outwards to the left
        got = find([x[3] for x in out[max(i - 2, 0):i + 1]][::-1], out[i + 1][2], -1)
        if got is None:
            break
        for j in range(i - got[0] + 1, i + 1):
            out[j][2] = got[1]
        i -= got[0]
    return [(a, e, b) for a, e, b, _ in out]


def _wrapper(content: str) -> Optional[tuple[str, str]]:
    """(opening, closing) marks that wrap the whole block (one <p>, one emphasis span), ("", "") for none;
    None for a table row or a quote, which are never cut."""
    c = content.strip()
    if c.startswith(("|", ">")):
        return None
    if c.startswith("<p>") and c.endswith("</p>") and "<p" not in c[3:]:
        return "<p>", "</p>"
    for m in ("**", "*"):
        if c.startswith(m) and c.endswith(m) and len(c) > 2 * len(m) and m[0] not in c[len(m):-len(m)]:
            return m, m
    return "", ""


def _balanced(piece: str) -> bool:
    if piece.count("**") % 2 or piece.replace("**", "").count("*") % 2 or piece.count("`") % 2 or piece.count("$") % 2:
        return False
    return piece.count("<") == piece.count(">") and len(re.findall(r"<[a-z]+\b", piece)) == len(re.findall(r"</", piece))


def _cut_text(content: str, starts: list[int]) -> Optional[list[str]]:
    """The pieces of ``content`` that begin at the token starts ``starts`` (the first piece begins at 0). A cut
    moves back over marks glued to the token; None when a piece would be empty or would split a markup span."""
    wrap = _wrapper(content)
    if wrap is None:
        return None
    opening, closing = wrap
    body = content.strip()
    off = len(content) - len(content.lstrip())
    if opening:
        body = body[len(opening):len(body) - len(closing)]
        off += len(opening)
    cuts = []
    for s in starts:
        a = s - off
        while a > 0 and not body[a - 1].isspace() and not body[a - 1].isalnum() and body[a - 1] not in ".,;:)]":
            a -= 1
        cuts.append(a)
    if any(a <= 0 for a in cuts) or cuts != sorted(set(cuts)):
        return None
    bounds = [0] + cuts + [len(body)]
    pieces = []
    for a, e in zip(bounds, bounds[1:]):
        t = body[a:e].strip()
        t = _EDGE.sub("", _EDGE.sub("", t))
        if not plain_text(t).strip() or not _balanced(t):
            return None
        pieces.append(f"{opening}{t}{closing}" if opening else t)
    return pieces


def _union(boxes: list[tuple]) -> tuple:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _piece_type(r: Region, k: int) -> RegionType:
    """The first piece keeps the block's type; a later piece of a heading is plain text until the position and
    layout rules type it (it does not carry the heading's markup)."""
    return r.type if k == 0 or r.type not in HEADINGS else RegionType.TEXT


def _furniture_dets(dets: list[dict], min_score: float) -> list[dict]:
    return [d for d in dets if d["label"] in FURNITURE_LABELS and d["score"] >= min_score]


def _owner(box: tuple, furn: list[dict]) -> Optional[str]:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    ins = [d for d in furn if d["box"][0] - 0.004 <= cx <= d["box"][2] + 0.004 and d["box"][1] - 0.003 <= cy <= d["box"][3] + 0.003]
    return min(ins, key=lambda d: area(tuple(d["box"])))["id"] if ins else None


# ------------------------------------------------------------------------------------- cut at furniture detections
def split_by_detections(page: Page, dets: list[dict], min_score: float = 0.5) -> int:
    """``hfdet``. Returns the number of blocks cut; the caller grounds the page again."""
    furn = _furniture_dets(dets, min_score)
    if len(furn) < 2:
        return 0
    out: list[Region] = []
    n = 0
    for r in page.regions:
        pieces = None
        if r.type not in CONTENT_TYPES and r.bbox is not None and not _CHECKBOX.search(r.content) \
                and not _BLANK.search(r.content) and "\n\n" not in r.content.strip():
            pieces = _detection_pieces(r, page, furn)
        if not pieces:
            out.append(r)
            continue
        n += 1
        for k, (t, box, ws) in enumerate(pieces):
            out.append(_new(r, t, _piece_type(r, k), box, page, k, "hfdet", ws))
    if n:
        _renumber(page, out)
    return n


def _detection_pieces(r: Region, page: Page, furn: list[dict]) -> Optional[list[tuple]]:
    toks = located_tokens(r, page)
    if not 2 <= len(toks) <= 40 or any(b is None for _, _, b in toks):
        return None
    owners = [_owner(b, furn) for _, _, b in toks]
    if sum(o is not None for o in owners) < 0.6 * len(owners):
        return None
    # a word outside every detection goes with the neighbour it is printed nearest to
    for i, o in enumerate(owners):
        if o is None:
            b = toks[i][2]
            near = [(abs(toks[j][2][0] - b[2]) if j > i else abs(b[0] - toks[j][2][2]), owners[j])
                    for j in (i - 1, i + 1) if 0 <= j < len(toks) and owners[j] is not None
                    and abs((toks[j][2][1] + toks[j][2][3]) - (b[1] + b[3])) / 2 <= 0.012]
            if not near:
                return None
            owners[i] = min(near)[1]
    groups: list[list[int]] = [[0]]
    for i in range(1, len(toks)):
        if owners[i] == owners[i - 1]:
            groups[-1].append(i)
        else:
            groups.append([i])
    ids = [owners[g[0]] for g in groups]
    if len(groups) < 2 or len(set(ids)) != len(ids):       # one detection, or the text weaves between two
        return None
    by_id = {d["id"]: d["box"] for d in furn}
    for a, b in zip(ids, ids[1:]):                         # only pieces that stand side by side: the lines of one
        xa, xb = by_id[a], by_id[b]                        # footer printed under each other are one block
        if min(xa[2], xb[2]) - max(xa[0], xb[0]) > -DET_GAP:
            return None
    texts = _cut_text(r.content, [toks[g[0]][0] for g in groups[1:]])
    if texts is None:
        return None
    return [(t, _union([toks[i][2] for i in g]), [(r.content[toks[i][0]:toks[i][1]], toks[i][2]) for i in g])
            for t, g in zip(texts, groups)]


# ------------------------------------------------------------------------------------------------- page numbers
def _page_number_run(words: list[str], at_end: bool) -> int:
    """Number of tokens at the end (or the start) of ``words`` that are a page number: "page 2", "page 1 of 6",
    "2 of 6", "485". 0 for none. A year ("1999", "2024") is not a page number unless "page" stands before it."""
    def num(t: str) -> bool:
        return t.isdigit() and 1 <= len(t) <= 4
    w = words if at_end else words[::-1]
    n = len(w)
    if not at_end:                       # leading: only a bare number ("436 MIRANDA v. ARIZONA") or "Page 2 ..."
        if n >= 2 and words[0] in _PAGE_WORD and num(words[1]):
            return 2
        return 1 if n >= 1 and num(words[0]) and not _YEAR.match(words[0]) else 0
    if n >= 4 and num(w[-1]) and w[-2] == "of" and num(w[-3]) and w[-4] in _PAGE_WORD:
        return 4
    if n >= 3 and num(w[-1]) and w[-2] == "of" and num(w[-3]):
        return 3
    if n >= 2 and num(w[-1]) and w[-2] in _PAGE_WORD:
        return 2
    return 1 if n >= 1 and num(w[-1]) and not _YEAR.match(w[-1]) else 0


def split_page_numbers(page: Page, dets: list[dict], min_score: float = 0.5) -> int:
    """``pnsplit``. Returns the number of blocks cut; the caller grounds the page again."""
    furn = _furniture_dets(dets, min_score)
    out: list[Region] = []
    n = 0
    for r in page.regions:
        b = r.bbox
        pieces = None
        if r.type not in CONTENT_TYPES and r.type != RegionType.KEY_VALUE and b is not None \
                and (b.top + b.height <= TOP_BAND or b.top >= BOTTOM_BAND) \
                and not _CHECKBOX.search(r.content) and not _BLANK.search(r.content) and "\n\n" not in r.content.strip():
            pieces = _page_number_pieces(r, page, furn)
        if not pieces:
            out.append(r)
            continue
        n += 1
        for k, (t, box, ws) in enumerate(pieces):
            out.append(_new(r, t, _piece_type(r, k), box, page, k, "pnsplit", ws))
    if n:
        _renumber(page, out)
    return n


def _page_number_pieces(r: Region, page: Page, furn: list[dict]) -> Optional[list[tuple]]:
    toks = located_tokens(r, page)
    if not 2 <= len(toks) <= 30:
        return None
    words = [r.content[a:e].lower() for a, e, _ in toks]
    for at_end in (True, False):
        k = _page_number_run(words, at_end)
        if not k or k >= len(words):
            continue
        pn = list(range(len(words) - k, len(words))) if at_end else list(range(k))
        rest = [i for i in range(len(words)) if i not in pn]
        worded = words[pn[0]] in _PAGE_WORD or k >= 3
        # the rest is a running title: a word, or two for a bare number ("Form   1065" is not a title and a page)
        if sum(sum(ch.isalpha() for ch in words[i]) >= 2 for i in rest) < (1 if worded else 2):
            continue
        if not worded:
            # a bare number: only on a block written as one line, and only a number that stands alone ("190-" is
            # the blank of a date, "1\n2\n3a PAT. CNTL #" are the box numbers of a form)
            a, e = toks[pn[0]][0], toks[pn[-1]][1]
            if "\n" in r.content.strip() or r.content[e:e + 1] in ("-", "–", "—", "/", "%") \
                    or (a > 0 and r.content[a - 1] in "-–—/$#.,"):
                continue
        near = rest[-1] if at_end else rest[0]            # the word of the rest that the number stands next to
        edge = pn[0] if at_end else pn[-1]
        if any(toks[i][2] is None for i in pn) or toks[near][2] is None:
            continue
        nb, eb = toks[near][2], toks[edge][2]
        line = max(statistics.median(toks[i][2][3] - toks[i][2][1] for i in pn), 0.004)
        other_line = abs((nb[1] + nb[3]) - (eb[1] + eb[3])) / 2 > 1.2 * line
        gap = (eb[0] - nb[2]) if at_end else (nb[0] - eb[2])
        own = {_owner(toks[i][2], furn) for i in pn}
        in_own_det = len(own) == 1 and None not in own and _owner(nb, furn) not in own
        if not (in_own_det or (not other_line and gap >= (PN_GAP_WORD if worded else PN_GAP_BARE))
                or (other_line and worded)):
            continue
        start = toks[pn[0]][0] if at_end else toks[rest[0]][0]
        texts = _cut_text(r.content, [start])
        if texts is None:
            continue
        g0, g1 = (rest, pn) if at_end else (pn, rest)
        boxes = [[toks[i][2] for i in g if toks[i][2] is not None] for g in (g0, g1)]
        if not all(boxes):
            continue
        return [(t, _union(bx), [(r.content[toks[i][0]:toks[i][1]], toks[i][2]) for i in g])
                for t, bx, g in zip(texts, boxes, (g0, g1))]
    return None


# ----------------------------------------------------------------------------------------------- tables that are not
def _table_cells(r: Region):
    """The cells of a block that is exactly one HTML table (rows of cells), or None."""
    from ..forms import grid
    c = r.content.strip()
    if not c.lower().startswith("<table") or not c.lower().endswith("</table>") or c.lower().count("<table") != 1:
        return None
    t = grid.parse(c)
    return t.rows or None


def _cell_markdown(line: str) -> str:
    """A printed line of a cell as block text: the table's inline tags as the reader's emphasis marks."""
    s = re.sub(r"</?(?:b|strong)\s*>", "**", line, flags=re.I)
    s = re.sub(r"</?(?:i|em)\s*>", "*", s, flags=re.I)
    return s.strip()


def _table_det(r: Region, dets: list[dict], min_score: float = 0.5) -> bool:
    """The layout model sees a table where the block is."""
    rb = box_of(r.bbox)
    return any(d["label"] in ("table", "document_index") and d["score"] >= min_score
               and inter(rb, tuple(d["box"])) >= 0.5 * min(area(rb), area(tuple(d["box"]))) for d in dets)


def _cells_in_furniture(r: Region, furn: list[dict]) -> bool:
    """Every cell that grounding located lies in a page-header / page-footer detection (at least one cell located):
    a row of form fields at the page foot ("DATE | 08/13/2020 | SIGNATURE ...") has values outside them."""
    boxes = [box_of(c.bbox) for c in r.cells if getattr(c, "bbox", None) is not None]
    return bool(boxes) and all(_owner(b, furn) is not None for b in boxes)


def furniture_tables(page: Page, dets: list[dict], min_score: float = 0.5) -> int:
    """``hftable``. A table of at most four short cells in the top or bottom band whose located cells all lie in
    page-header / page-footer detections, where the layout model sees no table: one block per cell."""
    furn = _furniture_dets(dets, min_score)
    if not furn:
        return 0
    out: list[Region] = []
    n = 0
    for r in page.regions:
        rows = _table_cells(r) if r.type == RegionType.TABLE and r.bbox is not None else None
        b = r.bbox
        ok = bool(rows) and (b.top + b.height <= TOP_BAND or b.top >= BOTTOM_BAND) and not _table_det(r, dets)
        if ok:
            cells = [c for row in rows for c in row if c.text.strip()]
            rb = box_of(b)
            ok = 1 <= len(cells) <= 4 and all(len(c.text.split()) <= 10 and c.rowspan == 1 for c in cells) \
                and not _CHECKBOX.search(r.content) and not _BLANK.search(r.content) \
                and _cells_in_furniture(r, furn)
        if not ok:
            out.append(r)
            continue
        n += 1
        for k, c in enumerate(cells):
            text = "\n".join(_cell_markdown(l) for l in c.lines)
            # no search box: the cells of a running head stand far apart, grounding finds each among the page words
            out.append(_new(r, text, RegionType.TEXT, None, page, k, "hftable"))
    if n:
        _renumber(page, out)
    return n


def is_prose_table(r: Region, dets: list[dict]) -> Optional[list[str]]:
    """The printed lines of a one-column table that holds running text, or None. Conditions: every row has one
    cell; no checkbox or blank (a form box is not prose); at least 40 words, more than half of them in lines of
    eight or more words, and a sentence among them; and the layout model sees no table there."""
    rows = _table_cells(r) if r.type == RegionType.TABLE else None
    if not rows or any(len(row) != 1 for row in rows) or _CHECKBOX.search(r.content) or _BLANK.search(r.content):
        return None
    lines = [l for row in rows for l in row[0].lines]
    counts = [len(plain_text(l).split()) for l in lines]
    total = sum(counts)
    if total < 40 or sum(c for c in counts if c >= 8) < 0.5 * total:
        return None
    if not any(c >= 8 and _SENTENCE.search(plain_text(l) + " x") for l, c in zip(lines, counts)):
        return None
    if r.bbox is not None and _table_det(r, dets):
        return None
    return lines


def unwrap_prose(page: Page, dets: list[dict]) -> int:
    """``prose``. Returns the number of tables turned back into text blocks; the caller grounds the page again."""
    from ..typing.markup import classify_text
    out: list[Region] = []
    n = 0
    for r in page.regions:
        lines = is_prose_table(r, dets)
        if not lines:
            out.append(r)
            continue
        n += 1
        box = box_of(r.bbox) if r.bbox is not None else None
        for k, l in enumerate(lines):
            text = _cell_markdown(l)
            t, level, _rule = classify_text(text)
            nr = _new(r, text, t, box, page, k, "prose")
            nr.heading_level = level
            out.append(nr)
    if n:
        _renumber(page, out)
    return n


# -------------------------------------------------------------------------------------------------- label rules
def _det_of(r: Region, dets: list[dict]) -> Optional[dict]:
    ld = r.meta.get("layout_detection") or {}
    return next((d for d in dets if d["id"] == ld.get("id")), None) if ld.get("how") == "match" else None


def _retype(r: Region, t: RegionType, rule: str) -> None:
    r.meta.setdefault("type_before_round3", r.type.value)
    r.meta["type_rule"] = rule
    r.type = t
    r.heading_level = 2 if t == RegionType.SECTION_HEADER else None


def label_rules(page: Page, dets: list[dict], mode: str) -> dict:
    """``lab=cap+list+long+paren+fig``. Returns counts per rule."""
    parts = set(mode.split("+"))
    if "on" in parts:
        parts = {"cap", "list", "long", "paren", "fig"}
    info: dict = {}

    def count(k: str) -> None:
        info[k] = info.get(k, 0) + 1
    for r in page.regions:
        if r.bbox is None:
            continue
        p = plain_text(r.content).strip()
        nwords = len(p.split())
        d = _det_of(r, dets)
        if "cap" in parts and r.type == RegionType.SECTION_HEADER and not r.content.lstrip().startswith("#") \
                and d is not None and d["label"] == "caption" and d["score"] >= 0.5:
            _retype(r, RegionType.TEXT, "round3:caption")
            count("cap")
        elif "list" in parts and r.type == RegionType.LIST_ITEM and "\n" not in r.content.strip() and nwords <= 12 \
                and d is not None and d["label"] in ("section_header", "title", "paragraph_title") and d["score"] >= 0.6 \
                and not _SENTENCE.search(p) and not p.endswith((".", ";", ",")):
            _retype(r, RegionType.SECTION_HEADER, "round3:list_heading")
            count("list")
        elif "long" in parts and r.type == RegionType.SECTION_HEADER and not r.content.lstrip().startswith("#") \
                and nwords_or_chars(p) >= 40 and _SENTENCE.search(p):
            _retype(r, RegionType.TEXT, "round3:paragraph")
            count("long")
        elif "paren" in parts and r.type == RegionType.HEADER and r.meta.get("type_before_layout") == "Text" \
                and r.meta.get("type_rule") in ("top_band", "top_band_2") \
                and re.fullmatch(r"\[[^\[\]]+\]|\([^()]+\)", p) and any(ch.isalpha() for ch in p):
            _retype(r, RegionType.TEXT, "round3:bracketed")
            count("paren")
    if "fig" in parts:
        k = figure_text(page, dets)
        if k:
            info["fig"] = k
    return info


def nwords_or_chars(p: str) -> int:
    """Word count; for text without spaces between words (Chinese, Japanese) two characters count as one word."""
    n = len(p.split())
    cjk = sum(1 for ch in p if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")
    return max(n, cjk // 2)


def figure_text(page: Page, dets: list[dict], min_score: float = 0.7, min_area: float = 0.03, min_blocks: int = 3) -> int:
    """``lab=fig``. Short Text blocks that lie inside one confident picture detection, at least ``min_blocks`` of
    them and none a sentence: labels the reader transcribed out of a diagram or a screenshot. They are typed
    Figure (their text stays)."""
    n = 0
    for d in dets:
        db = tuple(d["box"])
        if d["label"] not in PICTURE_LABELS or d["score"] < min_score or not min_area <= area(db) <= 0.6:
            continue
        ins = [r for r in page.regions if r.bbox is not None and r.bbox.width > 0 and r.bbox.height > 0
               and inside(box_of(r.bbox), db) >= 0.8]
        texts = [r for r in ins if r.type == RegionType.TEXT]
        if len(texts) < min_blocks or len(texts) != len([r for r in ins if r.type != RegionType.FIGURE]):
            continue          # a heading, a list or a table inside: the "picture" is a framed part of the page
        if any(nwords_or_chars(plain_text(r.content)) > 8 or "\n\n" in r.content.strip() for r in texts):
            continue
        for r in texts:
            _retype(r, RegionType.FIGURE, "round3:figure_text")
            n += 1
    return n


# -------------------------------------------------------------------------------------------------- stage hooks
def before_types(stage, doc, page: Page, dets: list[dict], on: dict) -> dict:
    """The steps that change where blocks end. The caller grounds the page again when one of them cut something
    (once, after its own cuts): a piece carries the words located for it until then."""
    info: dict = {}
    if "prose" in on:
        info["prose"] = unwrap_prose(page, dets)
    if "hftable" in on:
        info["hftable"] = furniture_tables(page, dets)
    if "hfdet" in on:
        info["hfdet"] = split_by_detections(page, dets)
    if "pnsplit" in on:
        info["pnsplit"] = split_page_numbers(page, dets)
    return {k: v for k, v in info.items() if v}


def after_types(doc, page: Page, dets: list[dict], on: dict) -> dict:
    if "lab" in on:
        return {"lab": info} if (info := label_rules(page, dets, on["lab"])) else {}
    return {}
