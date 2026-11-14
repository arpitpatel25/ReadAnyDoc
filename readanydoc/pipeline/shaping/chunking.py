"""Chunk modes: disabled, page, block, section, page_sections, variable.

Input is a flat list of ``Piece`` objects in reading order (one per block, already filtered and
format-converted by the Shape stage). Output is a list of ``Chunk``.

The rules follow what Reducto's API returned for the same pages (the option cases under ``e2e/``, 2026-11-12):

- section / page_sections: every Title / Section Header starts a new chunk, also when the chunk
  before it holds nothing but a heading. Blocks in front of the first heading form their own chunk.
- variable: blocks are packed greedily up to the upper bound (target + 25%; target 1000 characters
  when ``chunk_size`` is null). A heading at the end of a full chunk moves to the next chunk, so it
  stays with what it introduces; at a heading the chunk also closes when the whole section that
  starts there does not fit in what is left (unless the chunk is still under a quarter of the
  target). A block larger than the bound is a chunk on its own: tables and figures are never split,
  and a text block is cut (at sentence ends) only above twice the bound. Reducto also uses "visual
  context" (it tends to cut in front of a run of page footers); that part is not reproduced.
- heading context: in every mode except ``disabled`` a chunk that starts below a heading it does not
  contain is prefixed with that heading and `` (cont.)`` (title first, then the section heading).
- ``chunk_overlap``: N characters of the previous chunk are put in front and N characters of the
  next chunk behind, as raw character slices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional

from ...model import Chunk

HEADING_TYPES = ("Title", "Section Header")
ATOMIC_TYPES = ("Table", "Figure")
_SENT_END = re.compile(r"(?<=[.!?。！？])[\"'”’)\]]*\s+")
_SPACE = re.compile(r"\s+")


@dataclass
class Piece:
    region_id: str
    page: int  # 0-based page index
    type: str
    text: str = ""  # contribution to chunk.content ("" when filtered out)
    embed: str = ""
    in_blocks: bool = True  # False for table fragments absorbed by merge_tables
    joins_prev: Optional[str] = None  # region id this piece continues (cross-page paragraph)
    part: int = 0  # index of this part when a long block was cut
    marker: bool = False  # a page marker ([[START OF PAGE n]]), not a block of the document

    @property
    def heading(self) -> bool:
        return self.type in HEADING_TYPES

    @property
    def atomic(self) -> bool:
        return self.type in ATOMIC_TYPES


def marker_text(n: int, start: bool) -> str:
    return f"[[START OF PAGE {n}]]" if start else f"[[END OF PAGE {n}]]"


def marker_id(page: int, start: bool) -> str:
    """Id of the synthetic block a page marker becomes in the response (Reducto: a "Page Number" block)."""
    return f"__page_{'start' if start else 'end'}_{page}"


def with_page_markers(pieces: list[Piece], page_indices: list[int]) -> list[Piece]:
    """Put a START piece in front of each page's blocks and an END piece behind them (empty pages too)."""
    by: dict[int, list[Piece]] = {}
    for p in pieces:
        by.setdefault(p.page, []).append(p)
    out: list[Piece] = []
    for i in sorted(set(page_indices) | set(by)):
        for start in (True, False):
            if not start:
                out.extend(by.get(i, []))
            t = marker_text(i + 1, start)
            out.append(Piece(region_id=marker_id(i, start), page=i, type="Page Number", text=t, embed=t,
                             in_blocks=False, marker=True))
    return out


def render(pieces: list[Piece]) -> Chunk:
    """Join pieces into one chunk."""
    content: list[tuple[str, str]] = []  # (separator before, text)
    embed: list[tuple[str, str]] = []
    ids: list[str] = []
    last: Optional[Piece] = None
    for p in pieces:
        if (p.in_blocks or p.marker) and p.region_id not in ids:
            ids.append(p.region_id)
        if not p.text.strip() and not p.embed.strip():
            continue
        sep = "\n\n"
        if last is not None and p.joins_prev and p.joins_prev == last.region_id:
            sep = "" if last.text.rstrip().endswith("-") else " "
        elif last is not None and last.region_id == p.region_id and p.part:
            sep = " "
        if p.text.strip():
            content.append((sep, p.text))
        if p.embed.strip():
            embed.append((sep, p.embed))
        last = p

    def join(parts: list[tuple[str, str]]) -> str:
        out = ""
        for sep, text in parts:
            out = text if not out else out + sep + text
        return out

    return Chunk(content=join(content), embed=join(embed), region_ids=ids)


# ---------------------------------------------------------------- splitting long text

def split_text(text: str, target: int, hi: int) -> list[str]:
    """Cut ``text`` into parts of at most ``hi`` characters, at sentence ends where possible."""
    text = text.strip()
    if len(text) <= hi:
        return [text]
    cuts = [m.end() for m in _SENT_END.finditer(text)]
    sentences, prev = [], 0
    for c in cuts:
        sentences.append(text[prev:c])
        prev = c
    sentences.append(text[prev:])
    units: list[str] = []
    for s in sentences:  # a single sentence that is too long is cut at spaces
        while len(s) > hi:
            cut = s.rfind(" ", 0, target + 1)
            if cut <= 0:
                cut = s.find(" ", target)
            if cut <= 0:
                cut = min(target, len(s))
            units.append(s[:cut + 1] if s[cut:cut + 1] == " " else s[:cut])
            s = s[len(units[-1]):]
        if s:
            units.append(s)
    parts, cur = [], ""
    for u in units:
        if cur and len(cur) + len(u) > target and len(cur.strip()) >= 1:
            if len(cur) + len(u) <= hi and len(cur) < target * 0.75:
                cur += u
                continue
            parts.append(cur)
            cur = u
        else:
            cur += u
    if cur.strip():
        parts.append(cur)
    if len(parts) >= 2 and len(parts[-1]) < target * 0.5 and len(parts[-2]) + len(parts[-1]) <= hi:
        parts[-2] += parts.pop()
    return [p.strip() for p in parts if p.strip()]


def _split_piece(p: Piece, target: int, hi: int) -> list[Piece]:
    if p.atomic or p.heading or len(p.text) <= 2 * hi:  # Reducto keeps a 708-character block whole at 500
        return [p]
    texts = split_text(p.text, target, hi)
    if len(texts) <= 1:
        return [p]
    same = p.embed == p.text
    embeds = texts if same else split_text(p.embed, target, hi)
    out = []
    for i, t in enumerate(texts):
        e = t if same else (embeds[i] if i < len(embeds) else "")
        out.append(replace(p, text=t, embed=e, part=i, joins_prev=p.joins_prev if i == 0 else None))
    if not same and len(embeds) > len(texts):
        out[-1].embed = " ".join([out[-1].embed, *embeds[len(texts):]]).strip()
    return out


# ---------------------------------------------------------------- modes

def _size(group: list[Piece]) -> int:
    texts = [p.text for p in group if p.text.strip() and not p.marker]
    return sum(len(t) for t in texts) + 2 * max(len(texts) - 1, 0)


def _only_headings(group: list[Piece]) -> bool:
    visible = [p for p in group if p.text.strip() and not p.marker]
    return bool(visible) and all(p.heading for p in visible)


def by_section(pieces: list[Piece]) -> list[list[Piece]]:
    """A visible heading starts a new group (Reducto: also after a group that is only a heading)."""
    groups: list[list[Piece]] = []
    cur: list[Piece] = []
    for p in pieces:
        if p.heading and p.text.strip() and any(q.text.strip() and not q.marker for q in cur):
            groups.append(cur)
            cur = []
        cur.append(p)
    if cur:
        groups.append(cur)
    return groups


def by_page(pieces: list[Piece]) -> dict[int, list[Piece]]:
    out: dict[int, list[Piece]] = {}
    for p in pieces:
        out.setdefault(p.page, []).append(p)
    return out


def variable_groups(pieces: list[Piece], lo: int, target: int, hi: int) -> list[list[Piece]]:
    """Greedy packing up to ``hi`` characters. At a heading the chunk closes when the section that
    starts there does not fit in what is left of it (unless the chunk is still under a quarter of the
    target). ``lo`` is kept for callers; packing does not use it."""
    units: list[Piece] = []
    for p in pieces:
        units.extend(_split_piece(p, target, hi))
    section_size: dict[int, int] = {}  # index of a heading unit -> size of its section
    start = None
    for i, u in enumerate(units + [None]):  # type: ignore[list-item]
        if u is None or (u.heading and u.text.strip()):
            if start is not None:
                section_size[start] = _size(units[start:i])
            start = i
    groups: list[list[Piece]] = []
    cur: list[Piece] = []
    for i, u in enumerate(units):
        if not u.text.strip():
            cur.append(u)
            continue
        size = _size(cur)
        if size and size + len(u.text) + 2 > hi:
            visible = [k for k, q in enumerate(cur) if q.text.strip() and not q.marker] or [0]
            last = visible[-1]
            if cur[last].heading and len(visible) > 1:  # the heading goes with what it introduces
                groups.append(cur[:last])
                cur = cur[last:]
            elif not _only_headings(cur):
                groups.append(cur)
                cur = []
        elif u.heading and size >= target // 4 and size + section_size.get(i, 0) + 2 > hi \
                and not _only_headings(cur):
            groups.append(cur)
            cur = []
        cur.append(u)
    if cur:
        groups.append(cur)
    return groups


def apply_overlap(chunks: list[Chunk], overlap: int) -> list[Chunk]:
    """``overlap`` characters of the previous chunk in front and of the next chunk behind (Reducto: raw
    character slices, joined without a separator)."""
    if overlap <= 0 or len(chunks) < 2:
        return chunks
    out = []
    for i, ch in enumerate(chunks):
        new = ch.model_copy()
        for attr in ("content", "embed"):
            cur = getattr(ch, attr)
            if not cur:
                continue
            before = getattr(chunks[i - 1], attr)[-overlap:] if i > 0 else ""
            after = getattr(chunks[i + 1], attr)[:overlap] if i + 1 < len(chunks) else ""
            setattr(new, attr, before + cur + after)
        out.append(new)
    return out


# ---------------------------------------------------------------- heading context

_HASHES = re.compile(r"^\s{0,3}(#{1,6})\s")


def heading_level(p: Piece) -> int:
    m = _HASHES.match(p.text)
    if m:
        return len(m.group(1))
    return 1 if p.type == "Title" else 2


class HeadingTrail:
    """The headings a block sits under: title first, then the nearest section heading of each level."""

    def __init__(self) -> None:
        self.stack: list[Piece] = []

    def prefix_for(self, group: list[Piece]) -> list[Piece]:
        """Headings to repeat in front of ``group`` (those it sits under and does not contain)."""
        first = next((p for p in group if p.text.strip() and not p.marker), None)
        if first is None:
            return []
        if first.heading:
            return [h for h in self.stack if heading_level(h) < heading_level(first)]
        return list(self.stack)

    def advance(self, group: list[Piece]) -> None:
        for p in group:
            if p.heading and p.text.strip():
                lv = heading_level(p)
                self.stack = [h for h in self.stack if heading_level(h) < lv] + [p]


def _with_context(chunk: Chunk, headings: list[Piece]) -> Chunk:
    if not headings or not chunk.content:
        return chunk
    lead = "\n\n".join(h.text.rstrip() + " (cont.)" for h in headings)
    lead_e = "\n\n".join((h.embed or h.text).rstrip() + " (cont.)" for h in headings)
    return chunk.model_copy(update={"content": lead + "\n\n" + chunk.content,
                                    "embed": (lead_e + "\n\n" + chunk.embed) if chunk.embed else chunk.embed})


def chunk_pieces(pieces: list[Piece], mode: str, page_indices: list[int], add_page_markers: bool = False,
                 band: tuple[int, int, int] = (750, 1000, 1250), overlap: int = 0,
                 heading_context: bool = True) -> list[Chunk]:
    """Build the chunks for one document. ``page_indices`` lists every page, including empty ones."""
    if add_page_markers:
        pieces = with_page_markers(pieces, page_indices)
    if mode == "page":
        pages = by_page(pieces)
        groups = [pages.get(i, []) for i in page_indices]
    elif mode == "block":
        groups = [[p] for p in pieces if p.in_blocks]
    elif mode == "section":
        groups = by_section(pieces)
    elif mode == "page_sections":
        pages = by_page(pieces)
        groups = [g for i in page_indices for g in by_section(pages.get(i, []))]
    elif mode == "variable":
        lo, target, hi = band
        groups = variable_groups(pieces, lo, target, hi)
    else:  # disabled
        return [render(pieces)]
    trail = HeadingTrail()
    chunks = []
    for g in groups:
        ch = render(g)
        if heading_context:
            inside = {p.region_id for p in g}
            ch = _with_context(ch, [h for h in trail.prefix_for(g) if h.region_id not in inside])
            trail.advance(g)
        if mode == "page" or ch.region_ids or ch.content:
            chunks.append(ch)
    return apply_overlap(chunks, overlap)
