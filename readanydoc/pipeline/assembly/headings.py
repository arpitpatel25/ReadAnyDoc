"""Document-level heading hierarchy.

Each page is read on its own, so the reader's markdown levels are only locally consistent. This
module assigns one level per heading for the whole document from, in order of trust:
1. numbering patterns: "2", "2.3", "2.3.1" (depth = number of components), "Chapter 4", "Part II",
   "Appendix B" (top level);
2. font size, when the heading's words were grounded on the PDF text layer (word-box height is the
   size signal; OCR boxes are too noisy to use);
3. the reader's markdown level.
A level changes only when rule 1 or 2 gives evidence; the old level is kept in
``region.meta["heading_level_before"]``. With no evidence the reader's levels are returned as they are.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional

from ...model import Document, HeadingNode, Page, Region, RegionType
from ...textnorm import plain_text

_MD = re.compile(r"^\s{0,3}(#{1,6})\s+")
_DECIMAL = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})(\.?)\s+(?=\S)")
_KEYWORD = re.compile(r"^(chapter|part|appendix|annex|kapitel|chapitre|capítulo)\s+([0-9]{1,3}|[ivxlc]{1,7}|[a-z])\b",
                      re.I)
HEADING_TYPES = (RegionType.TITLE, RegionType.SECTION_HEADER)


@dataclass
class Heading:
    region: Region
    page: Page
    title: str
    md_level: int
    depth: Optional[int] = None  # numbering depth (1 = "2", 2 = "2.3")
    keyword: bool = False
    size: Optional[float] = None
    level: int = 0
    source: str = "markdown"


def markdown_level(region: Region) -> int:
    m = _MD.match(region.content)
    if m:
        return len(m.group(1))
    return region.heading_level or (1 if region.type == RegionType.TITLE else 2)


def numbering_depth(title: str) -> Optional[int]:
    """Depth of a decimal section number at the start of ``title``; None when there is none."""
    m = _DECIMAL.match(title)
    if not m:
        return None
    number, dot = m.group(1), m.group(2)
    parts = number.split(".")
    rest = title[m.end():]
    if len(parts) == 1:
        if int(parts[0]) == 0 or not re.match(r"[^\W\d_]", rest):
            return None  # "2024 results", "3 000 units": not a section number
        if not dot and (len(rest.split()) > 12 or rest[:1].islower()):
            return None  # a sentence that starts with a number
    return len(parts)


def font_size(region: Region, page: Page) -> Optional[float]:
    """Median grounded word height in page units, from PDF-layer boxes only."""
    hs = [w.bbox.height for w in region.iter_words()
          if w.bbox is not None and (w.bbox_source or "").startswith("pdf") and w.bbox.height > 0]
    if len(hs) < 1:
        fs = region.meta.get("font_size")
        return float(fs) if fs else None
    return statistics.median(hs) * (page.height or 1.0)


def collect(doc: Document, use_font_size: bool = True) -> list[Heading]:
    out = []
    for page in doc.pages:
        for r in page.regions:
            if r.type not in HEADING_TYPES or not r.content.strip():
                continue
            title = plain_text(r.content)
            h = Heading(region=r, page=page, title=title, md_level=markdown_level(r))
            h.depth = numbering_depth(title)
            h.keyword = bool(_KEYWORD.match(title))
            h.size = font_size(r, page) if use_font_size else None
            out.append(h)
    return out


def _size_clusters(sizes: list[float], tol: float = 0.06) -> list[float]:
    """Cluster centres, largest first; sizes within ``tol`` (relative) are one cluster."""
    centres: list[list[float]] = []
    for s in sorted(sizes, reverse=True):
        if centres and abs(statistics.mean(centres[-1]) - s) <= tol * s:
            centres[-1].append(s)
        else:
            centres.append([s])
    return [statistics.mean(c) for c in centres]


def _cluster_of(size: float, centres: list[float]) -> int:
    return min(range(len(centres)), key=lambda i: abs(centres[i] - size))


def assign_levels(headings: list[Heading]) -> list[Heading]:
    for h in headings:
        h.level, h.source = h.md_level, "markdown"
    numbered = [h for h in headings if h.depth is not None]
    # ---- 1. numbering ----
    depths = Counter(h.depth for h in numbered)
    use_numbering = len(numbered) >= 2 and 1 in depths and (len(depths) >= 2 or depths[1] >= 2)
    if use_numbering and not _sequence_ok([h for h in numbered if h.depth == 1]):
        use_numbering = False
    if use_numbering:
        tops = [h.md_level for h in numbered if h.depth == 1]
        base = Counter(tops).most_common(1)[0][0]
        has_title = any(h.region.type == RegionType.TITLE and h.depth is None and h.md_level == 1 for h in headings)
        if base == 1 and has_title:
            base = 2  # numbered sections sit under the document title
        for h in numbered:
            h.level, h.source = min(base + h.depth - 1, 6), "numbering"
        for h in headings:
            if h.keyword and h.depth is None:
                h.level, h.source = base, "numbering"
    # ---- 2. font size ----
    sized = [h for h in headings if h.size]
    if len(sized) >= 2:
        centres = _size_clusters([h.size for h in sized])
        if len(centres) >= 2 or use_numbering:
            known: dict[int, Counter] = defaultdict(Counter)
            for h in sized:
                if h.source == "numbering":
                    known[_cluster_of(h.size, centres)][h.level] += 1
            if known:  # sizes of numbered headings teach the level of unnumbered ones
                for h in sized:
                    if h.source == "numbering":
                        continue
                    votes = known.get(_cluster_of(h.size, centres))
                    if votes and len(votes) == 1:
                        h.level, h.source = next(iter(votes)), "font_size"
            elif len(sized) == len(headings):
                md_by_cluster: dict[int, Counter] = defaultdict(Counter)
                for h in sized:
                    md_by_cluster[_cluster_of(h.size, centres)][h.md_level] += 1
                order = [md_by_cluster[i].most_common(1)[0][0] for i in range(len(centres))]
                consistent = all(len(c) == 1 for c in md_by_cluster.values()) and order == sorted(order) \
                    and len(set(order)) == len(order)
                if not consistent:  # markdown levels disagree with the type sizes: sizes win
                    top = min(h.md_level for h in sized)
                    for h in sized:
                        h.level, h.source = min(top + _cluster_of(h.size, centres), 6), "font_size"
    return headings


def _retype(h: Heading, new: RegionType, why: str) -> None:
    r = h.region
    if r.type == new:
        return
    r.meta.setdefault("type_before_assemble", r.type.value)
    r.meta["heading_type_rule"] = why
    r.type = new


def unify_types(headings: list[Heading], small: float = 0.85) -> int:
    """Title or Section Header, decided for the whole document. Each page is read on its own, and the
    largest heading of a page tends to come back as a title ("# Income Tax" on page 5 of a booklet).
    Measured on 13 real multi-page documents against Reducto r-1, a Title becomes a Section Header when
    - its page has three or more titles (a page has one title; five "Schedule A/B/C" lines are sections);
    - it carries a section number ("5. Consolidated Statement ...") and most numbered headings of the
      same depth are section headers;
    - its type size (PDF text layer only) is clearly below the largest title of the document.
    Nothing is promoted to Title. Returns the number of headings retyped."""
    before = sum(h.region.type == RegionType.TITLE for h in headings)
    by_page: dict[int, list[Heading]] = defaultdict(list)
    for h in headings:
        if h.region.type == RegionType.TITLE:
            by_page[h.page.index].append(h)
    sized = [h for h in headings if h.size and h.region.type == RegionType.TITLE and len(h.title) > 3]
    top = max((h.size for h in sized), default=None) if len(sized) >= 2 else None
    for hs in by_page.values():
        if len(hs) >= 3:
            for h in hs:
                if not (top and h.size):  # with type sizes the size rule below decides
                    _retype(h, RegionType.SECTION_HEADER, "three or more titles on one page")
    by_depth: dict[int, list[Heading]] = defaultdict(list)
    for h in headings:
        if h.depth is not None:
            by_depth[h.depth].append(h)
    for hs in by_depth.values():
        titles = [h for h in hs if h.region.type == RegionType.TITLE]
        if titles and len(hs) >= 2 and len(titles) <= len(hs) / 2:
            for h in titles:
                _retype(h, RegionType.SECTION_HEADER, "numbered like the section headers")
    if top:
        for h in headings:
            if h.size and h.region.type == RegionType.TITLE and h.size < small * top:
                _retype(h, RegionType.SECTION_HEADER, "smaller type than the document's title")
    return before - sum(h.region.type == RegionType.TITLE for h in headings)


def finalise_levels(headings: list[Heading]) -> None:
    """Title = level 1; section headers use levels 2, 3, ... without gaps (the levels found from numbering
    or type size keep their order). A level changed here is marked so that Shape prints it."""
    section = [h for h in headings if h.region.type != RegionType.TITLE]
    rank = {lv: min(2 + i, 6) for i, lv in enumerate(sorted({h.level for h in section}))}
    if section and min(rank) >= 2 and max(rank) <= 6 and all(h.source == "markdown" for h in section):
        rank = {lv: lv for lv in rank}  # the reader's own levels, already below the title: leave them
    for h in headings:
        new = 1 if h.region.type == RegionType.TITLE else rank[h.level]
        if new != h.level:
            h.level = new
            if h.source == "markdown":
                h.source = "document"


def _sequence_ok(tops: list[Heading]) -> bool:
    """Top-level numbers mostly go up through the document (guards against numbered list items)."""
    nums = [int(_DECIMAL.match(h.title).group(1).split(".")[0]) for h in tops]  # type: ignore[union-attr]
    if len(nums) < 2:
        return True
    ups = sum(b >= a for a, b in zip(nums, nums[1:]))
    return ups >= 0.8 * (len(nums) - 1)


def build_tree(headings: list[Heading]) -> list[HeadingNode]:
    roots: list[HeadingNode] = []
    stack: list[HeadingNode] = []
    for h in headings:
        node = HeadingNode(title=h.title, level=h.level, region_id=h.region.id, page=h.page.index)
        while stack and stack[-1].level >= h.level:
            stack.pop()
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)
    return roots


def apply(doc: Document, normalise: bool = True, use_font_size: bool = True, unify: bool = True) -> list[Heading]:
    """Set ``doc.heading_tree``; with ``normalise`` also write evidence-based levels to the regions."""
    headings = collect(doc, use_font_size)
    if normalise:
        if unify and len(doc.pages) >= 2:
            unify_types(headings)
        assign_levels(headings)
        if unify:
            finalise_levels(headings)
    else:
        for h in headings:
            h.level = h.md_level
    for h in headings:
        r = h.region
        if h.source != "markdown" and h.level != h.md_level:
            r.meta.setdefault("heading_level_before", r.heading_level if r.heading_level else h.md_level)
            r.meta["heading_level_source"] = h.source
            r.heading_level = h.level
        elif r.heading_level is None:
            r.heading_level = h.level
    doc.heading_tree = build_tree(headings)
    return headings
