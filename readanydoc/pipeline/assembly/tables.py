"""Cross-page table continuation and ``merge_tables``.

A table that ends page N and a table that starts page N+1 are one table when:
- they have the same number of columns (required);
- and either the second repeats the first one's header rows (the repeat is dropped on merge),
- or the second has no header of its own and its first row looks like data: its cell types
  (number / date / text) fit the column types of the first table's body, and when the columns carry
  no type signal (all text) the two tables must also line up on the page.
A second table that starts with a different header is a new table, whatever its width.

The merged table is written to ``meta["merged_content"]`` of the first block together with
``meta["merged_tables"]`` (the original fragments with their boxes, the shape of Reducto's
``ParseBlock.merged_tables``); later fragments get ``meta["merged_into"]``. Region content is not
touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

from ...model import Confidence, CrossPageLink, Document, Page, Region, RegionType
from ...textnorm import plain_text
from ..shaping.tables import Cell, TableGrid, parse_table, to_html
from .furniture import FURNITURE

_CONTINUED = re.compile(r"\b(continued|cont['’]?d|cont\.|continuation|fortsetzung|suite)\b", re.I)
_NUM = re.compile(r"^[\s(\[-]*[$€£¥₹]?\s*[+-]?\d[\d,.\s]*\s*(?:%|[kKmMbB]|[a-zA-Z]{1,3})?[)\]]*$")
_DATE = re.compile(r"^\s*(?:\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{2,4}|"
                   r"[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{2,4})\s*$")
_SKIP = set(FURNITURE) | {RegionType.COMMENT, RegionType.SIGNATURE}


def cell_type(text: str) -> str:
    t = text.strip()
    if not t or t in ("-", "–", "—", "n/a", "N/A"):
        return "empty"
    if _DATE.match(t):
        return "date"
    if _NUM.match(t):
        return "number"
    return "text"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _row_texts(g: TableGrid, r: int) -> list[str]:
    return [_norm(t) for t in g.matrix(fill_merged=True)[r]]


def header_row_count(g: TableGrid) -> int:
    return min(g.header_rows, max(g.n_rows - 1, 0)) if g.header_rows < g.n_rows else 0


def column_types(g: TableGrid, skip_rows: int) -> list[Optional[str]]:
    """Dominant non-empty type per column over the body rows (None when a column is empty or mixed)."""
    rows = g.matrix(fill_merged=True)[skip_rows:]
    out: list[Optional[str]] = []
    for k in range(g.n_cols):
        types = [cell_type(r[k]) for r in rows]
        types = [t for t in types if t != "empty"]
        if not types:
            out.append(None)
            continue
        top = max(set(types), key=types.count)
        out.append(top if types.count(top) >= 0.7 * len(types) else None)
    return out


def _fits(row: list[str], types: list[Optional[str]]) -> tuple[int, int]:
    """(columns whose type matches, columns compared) for one row against column types."""
    ok = seen = 0
    for text, want in zip(row, types):
        got = cell_type(text)
        if want is None or got == "empty":
            continue
        seen += 1
        ok += got == want
    return ok, seen


@dataclass
class Judgement:
    score: float
    reason: str
    drop_rows: int = 0  # leading rows of the second table that repeat the header


def judge(a: TableGrid, b: TableGrid, ra: Optional[Region] = None, rb: Optional[Region] = None) -> Judgement:
    if a.n_cols != b.n_cols:
        return Judgement(0.0, f"column count differs ({a.n_cols} vs {b.n_cols})")
    if a.n_cols < 2:
        return Judgement(0.3, "single column: no structure to compare")
    ha, hb = header_row_count(a), header_row_count(b)
    a_head = [_row_texts(a, r) for r in range(ha)] or ([_row_texts(a, 0)] if a.n_rows > 1 else [])
    # 1. repeated header
    k = len(a_head)
    if k and b.n_rows > k:
        same = all(_same_header(a_head[r], _row_texts(b, r)) for r in range(k))
        if same and any(t for t in a_head[0]):
            return Judgement(0.95, "header repeated", drop_rows=k)
    types = column_types(a, k)
    typed = [t for t in types if t in ("number", "date")]
    first = b.matrix(fill_merged=True)[0]
    ok, seen = _fits(first, types)
    b_first_all_text = all(cell_type(t) in ("text", "empty") for t in first)
    # 2. the second table starts with its own, different header
    if hb and (not typed or b_first_all_text):
        return Judgement(0.1, "second table has its own header")
    if typed and b_first_all_text and seen:
        return Judgement(0.15, "first row of the second table looks like a new header")
    # 3. missing header: the first row must look like data of the same columns
    aligned = _aligned(ra, rb)
    if typed:
        if seen and ok / seen >= 0.8:
            body_ok, body_seen = 0, 0
            for row in b.matrix(fill_merged=True)[:6]:
                o, s = _fits(row, types)
                body_ok, body_seen = body_ok + o, body_seen + s
            if body_seen and body_ok / body_seen >= 0.8:
                return Judgement(0.9 if aligned is not False else 0.6,
                                 "no header on the second table, column types match"
                                 + ("" if aligned is not False else ", tables not aligned"))
        return Judgement(0.3, "column types differ")
    if aligned:
        return Judgement(0.85, "no header on the second table, text columns, tables aligned")
    return Judgement(0.5, "same width, text columns, no positional evidence")


def _same_header(x: list[str], y: list[str]) -> bool:
    """Every cell matches (a little OCR noise allowed per cell); one different column is a different header."""
    return len(x) == len(y) and all(a == b or (len(a) >= 6 and fuzz.ratio(a, b) >= 90) for a, b in zip(x, y))


def _aligned(ra: Optional[Region], rb: Optional[Region]) -> Optional[bool]:
    """Do the two tables share left edge and width, with the first low on its page and the second high?"""
    if ra is None or rb is None or ra.bbox is None or rb.bbox is None:
        return None
    a, b = ra.bbox, rb.bbox
    return abs(a.left - b.left) <= 0.03 and abs(a.width - b.width) <= 0.06 and a.bottom >= 0.6 and b.top <= 0.4


def merge_grids(grids: list[TableGrid], drops: list[int]) -> TableGrid:
    """Stack tables; ``drops[i]`` leading rows of table i are left out (repeated header)."""
    out = TableGrid(n_cols=grids[0].n_cols, caption=grids[0].caption)
    offset = 0
    for g, drop in zip(grids, drops):
        for c in g.cells:
            if c.row < drop:
                continue
            out.cells.append(Cell(row=c.row - drop + offset, col=c.col, rowspan=c.rowspan, colspan=c.colspan,
                                  text=c.text, html=c.html, header=c.header and offset == 0, attrs=dict(c.attrs)))
        offset += g.n_rows - drop
    out.n_rows = offset
    return out


def _body(page: Page) -> list[Region]:
    return [r for r in page.regions if r.type not in _SKIP and r.content.strip()]


def _boundary_tables(a: Page, b: Page) -> tuple[Optional[Region], Optional[Region]]:
    """(last table of page a, first table of page b) when nothing but a 'continued' note separates them."""
    last, first, _ = _boundary(a, b)
    return last, first


_CAPTION_ID = re.compile(r"^\W*(?:table|tableau|tabelle|tabla|exhibit|schedule)\s+([0-9]+(?:[.\-–][0-9]+)*[a-z]?|[ivxlc]+|[a-z])\b",
                         re.I)
MAX_GAP_BLOCKS = 3    # caption, unit line, one note
MAX_GAP_CHARS = 240


def caption_id(r: Region) -> Optional[str]:
    """'Table 5. Outlays ...—Continued' -> '5'; None when the block is not a table caption."""
    m = _CAPTION_ID.match(plain_text(r.content).strip())
    return m.group(1).lower().replace("–", "-") if m else None


@dataclass
class Gap:
    """What stands between the two tables besides 'continued' notes."""
    blocks: int = 0
    continued: bool = False       # one of the blocks in between says "continued"
    before_id: Optional[str] = None   # caption number of the table that ends page a
    after_id: Optional[str] = None    # caption number above the table that starts page b


_NOTE = re.compile(r"^\W*(?:foot)?notes?\b|^\W*sources?\b|^\s*(?:[*†‡§¹²³]|\(?[0-9a-z][.)]\s|\[\d+\])", re.I)


def _table_note(r: Region) -> bool:
    """A short line under a table that belongs to it: a 'continued' note, a footnote, a source line."""
    t = plain_text(r.content).strip()
    return _short(r) and (_is_continued_note(r) or bool(_NOTE.match(t)))


def _short(r: Region) -> bool:
    return r.type not in (RegionType.TABLE, RegionType.FIGURE) and len(plain_text(r.content)) <= MAX_GAP_CHARS


def _boundary(a: Page, b: Page) -> tuple[Optional[Region], Optional[Region], Gap]:
    """Last table of page a and first table of page b. Real documents repeat the caption and a unit line
    above every part of a long table ("Table 5. Outlays ...—Continued", "[$ millions]") and put a note
    under it ("Footnotes appear on the last page."), so up to MAX_GAP_BLOCKS short blocks on either side
    are stepped over; ``Gap`` says what was stepped over and ``judge_gap`` decides what that allows."""
    body_a, body_b = _body(a), _body(b)
    gap = Gap()
    if not body_a or not body_b:
        return None, None, gap
    i = len(body_a) - 1
    while i > 0 and body_a[i].type != RegionType.TABLE and _table_note(body_a[i]) and len(body_a) - 1 - i < MAX_GAP_BLOCKS:
        i -= 1
    k = 0
    while k < len(body_b) - 1 and body_b[k].type != RegionType.TABLE and _short(body_b[k]) and k < MAX_GAP_BLOCKS:
        k += 1
    last, first = body_a[i], body_b[k]
    if last.type != RegionType.TABLE or first.type != RegionType.TABLE:
        return None, None, gap
    between = body_a[i + 1:] + body_b[:k]
    gap.blocks = sum(1 for r in between if not _is_continued_note(r))
    gap.continued = any(_CONTINUED.search(plain_text(r.content)) for r in between)
    gap.after_id = next((c for c in map(caption_id, body_b[:k]) if c), None)
    gap.before_id = next((c for c in map(caption_id, reversed(body_a[:i])) if c), None) or last.meta.get("caption_id")
    return last, first, gap


def judge_gap(j: Judgement, gap: Gap) -> Optional[Judgement]:
    """A join over blocks that are not 'continued' notes needs more than matching columns: the caption
    numbers must not differ (Table 2 on one page, Table 3 on the next = two tables, even with the same
    columns), and either the caption says 'continued', or it repeats the number, or the header repeats."""
    if gap.before_id and gap.after_id and gap.before_id != gap.after_id:
        return None
    if not gap.blocks:
        return j
    same_caption = bool(gap.before_id and gap.before_id == gap.after_id)
    if gap.after_id and not same_caption and not gap.continued:
        return None  # a new numbered caption with no sign of continuation starts a new table
    if j.reason == "header repeated" and (gap.continued or same_caption):
        return Judgement(j.score, j.reason + ", caption repeated" + (" (continued)" if gap.continued else ""),
                         j.drop_rows)
    if j.score >= 0.85 and (gap.continued or same_caption):
        return Judgement(0.85, j.reason + ", caption says the table continues", j.drop_rows)
    return Judgement(min(j.score, 0.6), j.reason + f", {gap.blocks} block(s) between the tables", j.drop_rows)


def _is_continued_note(r: Region) -> bool:
    t = plain_text(r.content)
    return len(t) <= 100 and bool(_CONTINUED.search(t))


def _box(region: Region, page: Page) -> dict:
    b = region.bbox
    if b is None:
        left, top, width, height = 0.0, 0.0, 1.0, 1.0
    else:
        left, top, width, height = b.left, b.top, b.width, b.height
    return {"left": round(left, 4), "top": round(top, 4), "width": round(width, 4), "height": round(height, 4),
            "page": page.index + 1, "original_page": page.original_page}


def find_table_continuations(doc: Document) -> list[tuple[CrossPageLink, Judgement]]:
    out = []
    for a, b in zip(doc.pages, doc.pages[1:]):
        if b.original_page != a.original_page + 1:
            continue
        last, first, gap = _boundary(a, b)
        if last is None or first is None:
            continue
        first.meta.pop("caption_id", None)
        ga, gb = parse_table(last.content), parse_table(first.content)
        if ga is None or gb is None:
            continue
        j = judge_gap(judge(ga, gb, last, first), gap)
        if j is None or j.score < 0.5:
            continue
        if gap.after_id or gap.before_id:  # a part without its own caption carries the number on
            first.meta["caption_id"] = gap.after_id or gap.before_id
        link = CrossPageLink(kind="table_continuation", from_region=last.id, to_region=first.id,
                             confidence=Confidence.of(j.score))
        first.meta["continues"] = {"region": last.id, "score": j.score, "reason": j.reason, "drop_rows": j.drop_rows}
        out.append((link, j))
    return out


def clear_merges(doc: Document) -> None:
    for r in doc.iter_regions():
        for k in ("merged_content", "merged_tables", "merged_into"):
            r.meta.pop(k, None)


def merge_tables(doc: Document, joins: list[tuple[CrossPageLink, Judgement]]) -> int:
    """Apply accepted joins (chains across several pages become one table). Returns merged table count."""
    index = {r.id: (r, p) for p in doc.pages for r in p.regions}
    nxt = {l.from_region: (l.to_region, j) for l, j in joins if l.confidence.label == "high"}
    targets = {to for to, _ in nxt.values()}
    merged = 0
    for start in [rid for rid in nxt if rid not in targets]:
        chain, drops, cur = [start], [0], start
        while cur in nxt:
            to, j = nxt[cur]
            chain.append(to)
            drops.append(j.drop_rows)
            cur = to
        grids = [parse_table(index[rid][0].content) for rid in chain]
        if any(g is None for g in grids):
            continue
        head = index[start][0]
        head.meta["merged_content"] = to_html(merge_grids(grids, drops))  # type: ignore[arg-type]
        head.meta["merged_tables"] = [{"content": index[rid][0].content, "bbox": _box(*index[rid]),
                                       "image_url": None} for rid in chain]
        for rid in chain[1:]:
            index[rid][0].meta["merged_into"] = start
        merged += 1
    return merged
