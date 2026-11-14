"""Running headers, footers and page numbers, found by repetition across pages.

A block is page furniture when blocks with nearly the same text (digits masked) sit at nearly the
same height in the top or bottom band of enough pages. One page alone cannot tell a running header
from a first line, which is why the per-page version of this lowered the benchmark score; this one
only acts on documents with 3 or more pages and keeps the type it replaced in
``region.meta["type_before_assemble"]``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz

from ...model import Document, Region, RegionType
from ...textnorm import plain_text

FURNITURE = (RegionType.HEADER, RegionType.FOOTER, RegionType.PAGE_NUMBER)
_CANDIDATE_TYPES = (RegionType.TEXT, RegionType.SECTION_HEADER, RegionType.TITLE, RegionType.HEADER,
                    RegionType.FOOTER, RegionType.PAGE_NUMBER, RegionType.KEY_VALUE, RegionType.LIST_ITEM)
_HEADINGS = (RegionType.TITLE, RegionType.SECTION_HEADER)
# keys are masked (digits -> #): "table #. outlays of the u.s. government ...", "figure #-#", "note # - leases"
_CAPTION = re.compile(r"^(?:table|tableau|tabelle|tabla|figure|fig\.?|exhibit|schedule|chart|appendix|annex|note|chapter|part|"
                      r"section|article|kapitel|chapitre|cap[ií]tulo)\s+"
                      r"(?:#|[a-z]\b|[ivxlc]+\b)", re.I)
_FOOTNOTE = re.compile(r"^(?:\\\*|\*\s+\S|\*\\\*|[†‡§¹²³]|\[\d+\]\s|notes?\b|sources?\b)", re.I)
_CONTINUED = re.compile(r"\b(?:continued|cont['’]?d|cont\.|fortsetzung|suite)", re.I)
# rules of the per-page typing (pipeline/typing/layout.py) that turn a block into furniture from its position alone
_BAND_RULES = ("top_band", "top_band_2", "top_band_heading", "bottom_band", "bottom_run")
_PAGE_NUMBER = re.compile(r"^(?:[-–—\s]*#[-–—\s]*|(?:page|p\.?|pg\.?|seite|página|pagina)\s*#(?:\s*(?:of|/|von|de)\s*#)?"
                          r"|#\s*(?:of|/)\s*#)$", re.I)


def mask(text: str) -> str:
    t = re.sub(r"\s+", " ", plain_text(text)).strip().lower()
    return re.sub(r"\d+", "#", t)


@dataclass
class _Cluster:
    band: str
    key: str
    y: float
    members: list[tuple[int, Region]] = field(default_factory=list)  # (page index, region)

    @property
    def pages(self) -> set[int]:
        return {p for p, _ in self.members}


def _band(region: Region, order: int, count: int, top: float, bottom: float, edge_blocks: int = 3,
          edge_top: float = 0.25, edge_bottom: float = 0.75) -> tuple[Optional[str], float, bool]:
    """(band, y centre, positioned by box). Without a box only the first/last block of a page counts.
    A page with wide margins (a court slip opinion starts at 15% of the page height) has its running head
    below the fixed band, so the first and last ``edge_blocks`` blocks of a page also count while they
    sit in the outer quarter of the page."""
    if region.bbox is not None:
        if region.bbox.bottom <= top or (order < edge_blocks and region.bbox.bottom <= edge_top):
            return "top", region.bbox.center[1], True
        if region.bbox.top >= bottom or (order >= count - edge_blocks and region.bbox.top >= edge_bottom):
            return "bottom", region.bbox.center[1], True
        return None, 0.0, True
    if order == 0:
        return "top", 0.0, False
    if order == count - 1:
        return "bottom", 1.0, False
    return None, 0.0, False


def detect_furniture(doc: Document, *, min_pages: int = 3, min_share: float = 0.3, top: float = 0.12,
                     bottom: float = 0.88, similarity: float = 90.0, y_tol: float = 0.03,
                     max_chars: int = 160) -> list[dict]:
    """Retype repeated edge blocks. Returns one record per retyped region."""
    n = len(doc.pages)
    if n < min_pages:
        return []
    clusters: list[_Cluster] = []
    inner: dict[str, list[Region]] = {}  # masked text of the blocks that are not at a page edge
    first_heading: dict[str, RegionType] = {}  # masked text -> type of its first occurrence typed as a heading
    follows: dict[str, tuple[int, Optional[Region]]] = {}  # region id -> (position on its page, next block)
    for page in doc.pages:
        body = [r for r in page.regions if r.content.strip()]
        for i, r in enumerate(body):
            follows[r.id] = (i, body[i + 1] if i + 1 < len(body) else None)
            if r.type not in _CANDIDATE_TYPES:
                continue
            band, y, boxed = _band(r, i, len(body), top, bottom)
            key = mask(r.content)
            if key and not band:
                inner.setdefault(_base(key), []).append(r)
            if key and r.type in _HEADINGS:
                first_heading.setdefault(_base(key), r.type)
            if not band or not key or len(key) > max_chars:
                continue
            need = similarity if boxed else 100.0  # no box: only exact repeats
            best = None
            for c in clusters:
                if c.band != band or abs(c.y - y) > y_tol:
                    continue
                if (c.key == key) or (need < 100 and fuzz.ratio(c.key, key) >= need
                                      and bool(_CAPTION.match(c.key)) == bool(_CAPTION.match(key))):
                    best = c
                    break
            if best is None:
                best = _Cluster(band=band, key=key, y=y)
                clusters.append(best)
            best.members.append((page.index, r))
    changed = []
    accepted: list[tuple[_Cluster, RegionType]] = []
    for c in clusters:
        if len(c.pages) < max(min_pages, math.ceil(min_share * n)):
            continue
        is_number = bool(_PAGE_NUMBER.match(c.key))
        if is_number and not _counts_up(c):
            is_number = False
        if c.key.strip("#-–— ") == "" and not is_number:
            continue  # bare numbers that do not count up are data, not furniture
        why = None if is_number else _content_reason(c, inner, follows)
        if why:  # repeated because the content continues, not because the page design repeats
            changed.extend(_keep_as_content(c, why, first_heading.get(_base(c.key))))
            continue
        new = RegionType.PAGE_NUMBER if is_number else (RegionType.HEADER if c.band == "top" else RegionType.FOOTER)
        for page_index, r in c.members:
            if r.type in FURNITURE:
                continue  # already typed as furniture by the reader or the layout: leave its type
            r.meta["type_before_assemble"] = r.type.value
            r.meta["furniture"] = {"band": c.band, "pages": len(c.pages), "key": c.key}
            r.type = new
            changed.append({"region": r.id, "page": page_index, "type": new.value, "key": c.key})
        if not is_number:
            accepted.append((c, new))
    # a running line that carries one more line on some pages ("LOPER BRIGHT v. RAIMONDO / Opinion of the Court")
    done = {x["region"] for x in changed}
    for c in clusters:
        if len(c.pages) >= max(min_pages, math.ceil(min_share * n)) or len(c.key) < 12 or _CAPTION.match(c.key):
            continue
        for big, new in accepted:
            if big.band == c.band and abs(big.y - c.y) <= y_tol and len(big.key) >= 12 \
                    and len(c.key) <= len(big.key) + 60 and big.key.strip("# ") in c.key:
                for page_index, r in c.members:
                    if r.type in FURNITURE or r.id in done:
                        continue
                    r.meta["type_before_assemble"] = r.type.value
                    r.meta["furniture"] = {"band": c.band, "pages": len(big.pages), "key": big.key, "extended": True}
                    r.type = new
                    changed.append({"region": r.id, "page": page_index, "type": new.value, "key": big.key})
                break
    return changed


def _base(key: str) -> str:
    """Masked text without a 'continued' mark: '(#) unpaid losses (continued)' -> '(#) unpaid losses'."""
    return re.sub(r"[\s(\[—–-]*" + _CONTINUED.pattern + r"[\s)\].:]*", " ", key, flags=re.I).strip()


def _content_reason(c: _Cluster, inner: dict[str, list[Region]], follows: dict) -> Optional[str]:
    """Why a repeated line at the top of pages is content and not a running header (None = it is furniture).
    Measured on real documents against Reducto r-1, which keeps all three as Title / Section Header / Text:
    - the caption of a table or figure repeated on each page it runs over ("Table 5. Outlays ... —Continued");
    - a section heading repeated because the section continues ("(16) Unpaid losses ..." under the running
      header of an annual report): the same heading also stands inside a page;
    - a unit line directly above the table on every page ("[$ millions]", "Millions of dollars")."""
    if c.band == "bottom":
        # a footnote printed under the table on every page ("* Unaudited required supplemental information")
        marked = sum(1 for _, r in c.members if _FOOTNOTE.match(re.sub(r"^(?:<[^>]+>|[\s_])+", "", r.content)))
        return "repeated footnote" if marked >= 0.5 * len(c.members) else None
    keys = {mask(r.content) for _, r in c.members if r.type in _HEADINGS}
    if len(keys) >= 2 and sum(r.type in _HEADINGS for _, r in c.members) >= 0.5 * len(c.members):
        return "different headings at the same place"  # "CHAPTER IV", "CHAPTER V", "CHAPTER VI"
    if _CAPTION.match(c.key):
        return "repeated caption"
    if _CONTINUED.search(c.key) and len(_base(c.key).split()) >= 2:
        return "continued heading"
    same = inner.get(_base(c.key)) or []
    if any(r.type in _HEADINGS or r.meta.get("type_before_layout") in ("Title", "Section Header") for r in same):
        return "heading repeated while its section continues"
    above_table = sum(1 for _, r in c.members if follows[r.id][0] > 0 and follows[r.id][1] is not None
                      and follows[r.id][1].type == RegionType.TABLE)
    if len(c.key) <= 60 and above_table >= 0.6 * len(c.members):
        return "line directly above the table on each page"
    return None


def _keep_as_content(c: _Cluster, why: str, first: Optional[RegionType] = None) -> list[dict]:
    """Members of a repeated-content cluster: undo a furniture type given from position alone, and give a
    repeated heading or caption one type on every page (the type of its first occurrence in the document)."""
    out = []
    votes = [r.type for _, r in c.members if r.type in _HEADINGS]
    heading = first or (max(votes, key=votes.count) if votes else None)
    if why in ("line directly above the table on each page", "repeated footnote", "different headings at the same place"):
        heading = None
    for page_index, r in c.members:
        before = r.type
        if r.type in FURNITURE and r.meta.get("type_rule") in _BAND_RULES and r.meta.get("type_before_layout"):
            r.type = RegionType(r.meta["type_before_layout"])
        if heading is not None and r.type in (RegionType.TEXT, RegionType.LIST_ITEM, RegionType.KEY_VALUE, *_HEADINGS):
            r.type = heading
        r.meta["furniture_kept"] = why
        if r.type != before:
            r.meta["type_before_assemble"] = before.value
            if r.type in _HEADINGS and r.heading_level is None:
                r.heading_level = 1 if r.type == RegionType.TITLE else 2
            elif r.type not in _HEADINGS:
                r.heading_level = None
            out.append({"region": r.id, "page": page_index, "type": r.type.value, "key": c.key, "kept": why})
    return out


_LOWER_START = re.compile(r"^[\s*_>]*[a-zà-ÿ]")
_BULLET_START = re.compile(r"^\s*(?:[-*+•▪◦]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)])\s+\S")


def restore_body(doc: Document, changed: list[dict]) -> list[dict]:
    """Per-page typing calls every short block in the top band a Header. With the whole document in hand,
    a block that is not repeated on other pages goes back to its own type when it is
    - the rest of a sentence from the page before (starts in lower case), or a list item, or
    - the caption of the table right under it ("Table A. What You Need To Know ...")."""
    repeated = {c["region"] for c in changed}
    out = []
    for page in doc.pages:
        body = [r for r in page.regions if r.content.strip()]
        for i, r in enumerate(body):
            if r.type != RegionType.HEADER or r.id in repeated or r.meta.get("furniture") \
                    or r.meta.get("type_rule") not in _BAND_RULES or not r.meta.get("type_before_layout"):
                continue
            plain = plain_text(r.content).strip()
            why = None
            if len(plain.split()) >= 6 and (_LOWER_START.match(plain) or _BULLET_START.match(r.content)):
                why = "sentence or list continues at the top of the page"
            elif _CAPTION.match(mask(r.content)) and i + 1 < len(body) and body[i + 1].type == RegionType.TABLE:
                why = "caption of the table under it"
            if why:
                r.type = RegionType(r.meta["type_before_layout"])
                r.meta["type_before_assemble"] = RegionType.HEADER.value
                r.meta["furniture_kept"] = why
                if r.type in _HEADINGS and r.heading_level is None:
                    r.heading_level = 1 if r.type == RegionType.TITLE else 2
                out.append({"region": r.id, "page": page.index, "type": r.type.value, "kept": why})
    return out


def _counts_up(c: _Cluster) -> bool:
    """Page numbers grow with the page index (first number of each member)."""
    seq = []
    for page_index, r in sorted(c.members, key=lambda m: m[0]):
        m = re.search(r"\d+", plain_text(r.content))
        if not m:
            return False
        seq.append((page_index, int(m.group(0))))
    steps = [(b[1] - a[1]) == (b[0] - a[0]) for a, b in zip(seq, seq[1:])]
    return bool(steps) and sum(steps) >= 0.8 * len(steps)
