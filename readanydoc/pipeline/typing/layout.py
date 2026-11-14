"""Block type from where the block sits on the page. Runs in Ground, after the boxes exist.

Only blocks with a box from word evidence are retyped; an estimated or missing box says nothing.
Bands were measured on Reducto r-1's responses for 147 pages: a short non-title block that ends in the
top 7% of the page is a Header in 70% of cases (the rest are titles, kept by their own rule), a block
that starts in the bottom 5.5% is a Footer in 98%, and a short block in the bottom 14% with nothing but
footers under it in two cases out of three.
"""

from __future__ import annotations

import re
import statistics
from typing import Optional

from ...model import Page, Region, RegionType
from .markup import _BLANK, _CHECKBOX, _SENTENCE_END, _plain, _words

_PAGE_NUMBER = re.compile(
    r"^(?:(?:page|pg\.?|p\.)\s*)?[-–—]?\s*(?:\d{1,4}|[ivxlc]{1,7})\s*[-–—]?(?:\s*(?:of|/)\s*\d{1,4})?$", re.I)
_HEADINGS = (RegionType.TITLE, RegionType.SECTION_HEADER)
_RUNNING_HEAD = re.compile(r"^(?!(?:19|20)\d\d\b)\d{1,4}\s+\S|\S\s+(?!(?:19|20)\d\d$)\d{1,5}$")
_BRACKETED = re.compile(r"^\s*\[[^\[\]]+\]\s*$")
_NUMBERED_HEADING = re.compile(r"^\s*(?:\(?\d+[.)]?|\(?[A-Za-z][.)]|[IVXLC]+\.|§|(?:table|schedule|part|section|note|item|"
                               r"exhibit|appendix|figure|chapter|article)\b)", re.I)

HEADER_END = 0.07        # a block ending above this line is page furniture unless it is the title
HEADER_SIDE_END = 0.11   # short blocks in the left or right third are furniture down to here
FOOTER_START = 0.945
FOOTER_RUN_START = 0.86
MAX_FURNITURE_WORDS = 30


def is_page_number(content: str) -> bool:
    p = _plain(content).strip(" .")
    if re.fullmatch(r"(?:19|20)\d\d", p):
        return False  # a year printed on its own (the tax year of a form), not a page number
    return bool(p) and len(p) <= 14 and bool(_PAGE_NUMBER.match(p)) and any(c.isdigit() or c in "ivxlcIVXLC" for c in p)


def _set(r: Region, t: RegionType, rule: str) -> None:
    if r.type == t:
        return
    r.meta.setdefault("type_before_layout", r.type.value)
    r.meta["type_rule"] = rule
    r.type = t
    if t not in _HEADINGS:
        r.heading_level = None
    elif r.heading_level is None:
        r.heading_level = 1 if t == RegionType.TITLE else 2


def _markdown_heading(r: Region) -> bool:
    return r.content.lstrip().startswith("#")


def _centered(r: Region) -> bool:
    b = r.bbox
    return abs((b.left + b.width / 2) - 0.5) <= 0.12 and b.width >= 0.12


def _runs_into_body(r: Region, located: list[Region], heights: dict) -> bool:
    """The first line of running text at the top of a page is not a header: a header stands clear of what
    follows. True when a long block of body text starts within one line height below ``r`` in its column."""
    b = r.bbox
    line = heights.get(r.id) or min(b.height, 0.015)
    for o in located:
        if o is r or o.type not in (RegionType.TEXT, RegionType.LIST_ITEM) or o.bbox.top < b.top:
            continue
        ob = o.bbox
        overlap = min(b.right, ob.right) - max(b.left, ob.left)
        if overlap > 0.5 * min(b.width, ob.width) and ob.top - b.bottom <= 0.9 * line \
                and len(_words(o.content)) >= 25 and abs(ob.left - b.left) <= 0.05:
            return True
    return False


def _continues_body(r: Region, located: list[Region], heights: dict) -> bool:
    """A footer stands clear of the text above it. True when a Text block with the same left edge ends
    within a line and a half above ``r``: then ``r`` is the next line of that text."""
    b = r.bbox
    line = heights.get(r.id) or min(b.height, 0.015)
    for o in located:
        if o is r or o.type != RegionType.TEXT:
            continue
        ob = o.bbox
        if ob.top < b.top and abs(ob.left - b.left) <= 0.02 and -0.005 <= b.top - ob.bottom <= 1.5 * line:
            return True
    return False


def retype_page(page: Page, heights: Optional[dict[str, float]] = None, *, page_numbers: bool = True,
                font_headings: bool = True) -> int:
    """Retype the located blocks of one page in place. Returns how many types changed.
    ``heights``: region id -> median height of the words cited for it (page fraction), with
    ``heights["__trusted__"]`` true when they come from the PDF text layer (real type sizes)."""
    heights = heights or {}
    located = [r for r in page.regions if r.bbox is not None and r.bbox.width > 0 and r.bbox.height > 0
               and not (r.bbox.width > 0.95 and r.bbox.height > 0.95)]
    before = {r.id: r.type for r in page.regions}
    if not located:
        return 0
    nwords = {r.id: len(_words(r.content)) for r in located}

    # ---- headings from type size (PDF text layer only: OCR box heights follow the letters, not the font)
    if font_headings and heights.get("__trusted__"):
        body = [heights[r.id] for r in located if r.id in heights and r.type in (RegionType.TEXT, RegionType.LIST_ITEM,
                                                                                  RegionType.KEY_VALUE)
                for _ in range(max(nwords[r.id], 1))]
        if len(body) >= 20:
            base = statistics.median(body)
            for r in located:
                h = heights.get(r.id)
                if (r.type == RegionType.TEXT and h and base > 0 and h >= 1.25 * base and "\n" not in r.content.strip()
                        and 1 <= nwords[r.id] <= 14 and not _SENTENCE_END.search(_plain(r.content))
                        and not _CHECKBOX.search(r.content) and not _BLANK.search(r.content)
                        and any(c.isalpha() for c in r.content) and not is_page_number(r.content)):
                    _set(r, RegionType.SECTION_HEADER, "type_size")
                    r.meta["type_size_ratio"] = round(h / base, 2)

    # ---- short centred lines in capitals are headings ("CHAPTER IV", "ROUTING AND RECORD SHEET")
    for r in located:
        if r.type != RegionType.TEXT or not 1 <= nwords[r.id] <= 14 or r.content.strip().count("\n") > 2:
            continue
        p = _plain(r.content)
        letters = [c for c in p if c.isalpha()]
        if len(letters) < 5 or sum(c.isupper() for c in letters) < 0.85 * len(letters):
            continue
        if any(c.isdigit() for c in p) or ":" in p or p.startswith(("(", "[")) or _CHECKBOX.search(r.content) \
                or _BLANK.search(r.content):
            continue
        b = r.bbox
        if abs((b.left + b.width / 2) - 0.5) <= 0.06 and 0.08 <= b.width <= 0.7 and b.top < FOOTER_RUN_START:
            _set(r, RegionType.SECTION_HEADER, "caps_centred")

    # ---- header band
    for r in located:
        b = r.bbox
        short = nwords[r.id] <= MAX_FURNITURE_WORDS
        if r.type in (RegionType.TABLE, RegionType.FIGURE, RegionType.SIGNATURE) or not short:
            continue
        if b.bottom <= HEADER_END:
            if r.type in _HEADINGS:
                plain = _plain(r.content.lstrip("# "))
                # a running head carries the page number
                running = bool(_RUNNING_HEAD.search(plain)) and "\n" not in r.content.strip() and nwords[r.id] <= 10
                side = _markdown_heading(r) and (r.heading_level or 1) >= 2 and not _centered(r)
                if running or side:
                    _set(r, RegionType.HEADER, "top_band_heading")
                continue
            if r.type == RegionType.TEXT and _runs_into_body(r, located, heights):
                continue
            _set(r, RegionType.HEADER, "top_band")
        elif b.bottom <= HEADER_SIDE_END and r.type == RegionType.TEXT and nwords[r.id] <= 12 \
                and r.content.strip().count("\n") <= 2 and not _BRACKETED.match(r.content) \
                and not _runs_into_body(r, located, heights):
            _set(r, RegionType.HEADER, "top_band_2")

    # ---- footer band: bottom-up, a run of short blocks with nothing but footers under them
    below_all_footer = True
    for r in sorted(located, key=lambda x: -x.bbox.top):
        b = r.bbox
        if b.top < FOOTER_RUN_START:
            break
        short = nwords[r.id] <= MAX_FURNITURE_WORDS
        if r.type in (RegionType.TABLE, RegionType.FIGURE, RegionType.SIGNATURE):
            below_all_footer = below_all_footer and b.top >= FOOTER_START
            continue
        if r.type == RegionType.TEXT and _continues_body(r, located, heights):
            below_all_footer = False  # the last lines of a list or ledger that runs to the page edge
            continue
        if b.top >= FOOTER_START and nwords[r.id] <= 45 and not (
                r.type == RegionType.KEY_VALUE and (_CHECKBOX.search(r.content) or _BLANK.search(r.content))):
            _set(r, RegionType.FOOTER, "bottom_band")
        elif below_all_footer and r.type == RegionType.TEXT and "\n\n" not in r.content.strip() \
                and nwords[r.id] <= 22 and not (nwords[r.id] > 8 and _SENTENCE_END.search(_plain(r.content))):
            _set(r, RegionType.FOOTER, "bottom_run")
        else:
            below_all_footer = False

    # ---- page numbers
    if page_numbers:
        for r in located:
            if r.type in (RegionType.HEADER, RegionType.FOOTER) and is_page_number(r.content):
                edge = r.type.value
                _set(r, RegionType.PAGE_NUMBER, "page_number")
                r.meta["page_number_in"] = edge

    # ---- the reader's "#" against the type sizes: a heading set clearly larger than the title is the title
    if heights.get("__trusted__"):
        titles = [r for r in located if r.type == RegionType.TITLE and heights.get(r.id)]
        subs = [r for r in located if r.type == RegionType.SECTION_HEADER and heights.get(r.id) and r.bbox.top < 0.45
                and not _NUMBERED_HEADING.match(_plain(r.content.lstrip("# ")))]
        if titles and subs:
            big = max(subs, key=lambda r: heights[r.id])
            if heights[big.id] >= 1.15 * max(heights[t.id] for t in titles):
                for t in titles:
                    _set(t, RegionType.SECTION_HEADER, "smaller_than_title")
                _set(big, RegionType.TITLE, "largest_heading")

    # ---- one title per page when the reader marked none
    if not any(r.type == RegionType.TITLE for r in page.regions):
        cands = [r for r in located if r.type == RegionType.SECTION_HEADER and r.bbox.top < 0.30
                 and not _NUMBERED_HEADING.match(_plain(r.content.lstrip("# ")))]
        if cands:
            hs = [heights.get(r.id) for r in cands]
            trusted = bool(heights.get("__trusted__")) and all(hs)
            if trusted and len(cands) > 1 and max(hs) >= 1.15 * sorted(hs)[-2]:
                best, why = cands[hs.index(max(hs))], "largest_heading"
            else:
                best, why = min(cands, key=lambda r: r.bbox.top), "first_heading"
            if why == "largest_heading" or _centered(best) or len(cands) == 1 and _markdown_heading(best):
                _set(best, RegionType.TITLE, why)
    if page.properties.get("forms_switches"):  # form switches (off by default)
        from ..forms import retype_located
        retype_located(page, before)
    return sum(before[r.id] != r.type for r in page.regions)
