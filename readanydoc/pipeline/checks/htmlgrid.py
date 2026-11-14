"""HTML tables as grids: row widths (rowspan/colspan aware), unambiguous normalisation, and the rule
that decides whether a re-read table may replace the page read's table.

Width of a row = cells carried into it by rowspans from above + the colspans of its own cells (the
same count as ``e2e/table_checks.py``). A table is *consistent* when every row has the same width.
"""

from __future__ import annotations

import html as _html
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

TABLE = re.compile(r"<table\b.*?</table\s*>", re.I | re.S)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.I | re.S)
_CELL = re.compile(r"<(t[dh])\b([^>]*)>(.*?)</t[dh]\s*>", re.I | re.S)
_SPAN = re.compile(r"\b(rowspan|colspan)\s*=\s*[\"']?(\d+)[\"']?", re.I)
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>|<!--.*?-->", re.S)  # real tags only
_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_NUMTOK = re.compile(r"\d[\d,.]*\d|\d")
_CHECKED = re.compile(r"\[\s*[xX✓✔]\s*\]|[☑☒✓✔]")
_UNCHECKED = re.compile(r"\[\s?\]|☐")


@dataclass
class Cell:
    tag: str
    attrs: str
    inner: str
    start: int  # span of the whole element in the table HTML
    end: int
    inner_start: int
    inner_end: int
    rowspan: int = 1
    colspan: int = 1
    row: int = 0
    col: int = 0  # first grid column the cell occupies

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", _html.unescape(_TAG.sub(" ", self.inner))).strip()


@dataclass
class Table:
    html: str
    rows: list[list[Cell]] = field(default_factory=list)
    widths: list[int] = field(default_factory=list)
    row_spans: list[tuple[int, int]] = field(default_factory=list)  # (start, end) of each <tr> element

    @property
    def consistent(self) -> Optional[bool]:
        """None for a table with fewer than two rows (nothing to compare)."""
        if len(self.rows) < 2:
            return None
        return len(set(self.widths)) == 1

    @property
    def modal_width(self) -> Optional[int]:
        """The most common row width; None when two widths tie for first place."""
        top = Counter(self.widths).most_common(2)
        if not top or (len(top) == 2 and top[0][1] == top[1][1]):
            return None
        return top[0][0]

    def expanded(self) -> list[list[Optional[Cell]]]:
        """Rows of cells with spans expanded (a spanning cell is repeated), as the totals rule reads them."""
        out: list[list[Optional[Cell]]] = []
        carry: dict[int, tuple[Cell, int]] = {}
        for row in self.rows:
            line: list[Optional[Cell]] = []
            c = 0

            def fill() -> None:
                nonlocal c
                while c in carry:
                    cell, left = carry[c]
                    line.append(cell)
                    if left <= 1:
                        del carry[c]
                    else:
                        carry[c] = (cell, left - 1)
                    c += 1
            for cell in row:
                fill()
                for _ in range(min(cell.colspan, 60)):
                    line.append(cell)
                    if cell.rowspan > 1:
                        carry[c] = (cell, cell.rowspan - 1)
                    c += 1
            fill()
            out.append(line)
        return out


def find_tables(content: str) -> list[re.Match]:
    return list(TABLE.finditer(content or ""))


def parse(table_html: str) -> Table:
    t = Table(html=table_html)
    carry: list[int] = []  # remaining rows of every rowspan still open
    occupied: set[tuple[int, int]] = set()
    for r, rm in enumerate(_ROW.finditer(table_html)):
        width = sum(1 for n in carry if n > 0)
        carry = [n - 1 for n in carry if n > 0]
        row: list[Cell] = []
        c = 0
        for m in _CELL.finditer(rm.group(1)):
            spans = {k.lower(): min(max(int(v), 1), 500) for k, v in _SPAN.findall(m.group(2))}
            rs, cs = spans.get("rowspan", 1), spans.get("colspan", 1)
            while (r, c) in occupied:
                c += 1
            for dr in range(rs):
                for dc in range(cs):
                    occupied.add((r + dr, c + dc))
            base = rm.start(1)
            row.append(Cell(tag=m.group(1).lower(), attrs=m.group(2), inner=m.group(3), start=base + m.start(),
                            end=base + m.end(), inner_start=base + m.start(3), inner_end=base + m.end(3),
                            rowspan=rs, colspan=cs, row=r, col=c))
            width += cs
            if rs > 1:
                carry.extend([rs - 1] * cs)
            c += cs
        t.rows.append(row)
        t.widths.append(width)
        t.row_spans.append((rm.start(), rm.end()))
    return t


def consistent(table_html: str) -> Optional[bool]:
    return parse(table_html).consistent


def broken_tables(content: str) -> list[re.Match]:
    """The tables of a block whose rows differ in width."""
    return [m for m in find_tables(content) if consistent(m.group()) is False]


# ---------------------------------------------------------------------------------- normalisation
def _set_colspan(cell: Cell, n: int) -> str:
    attrs = _SPAN.sub(lambda m: "" if m.group(1).lower() == "colspan" else m.group(), cell.attrs).rstrip()
    attrs = re.sub(r"\s{2,}", " ", attrs)
    if n > 1:
        attrs += f' colspan="{n}"'
    return f"<{cell.tag}{attrs}>{cell.inner}</{cell.tag}>"


def _empty(cell: Cell) -> bool:
    return not cell.text


def normalise(table_html: str, pad: bool = False, max_steps: int = 200) -> tuple[Optional[str], list[str]]:
    """Deterministic repair of an inconsistent table where only one reading is possible.

    Against the most common row width W:
    - a row with a single cell that is narrower than W is a title or section row: its colspan is widened;
    - a row wider than W loses trailing empty cells, or an empty spanning cell is narrowed;
    - with ``pad``: any other short row gets empty cells at its end, and if a wide row remains every other
      row is padded to it. This is how a browser draws the table already, so it changes no reading: it
      makes the grid rectangular for programs that need one, and proves nothing about the values.
    Returns (new HTML, fixes) or (None, reasons) when a row stays ambiguous."""
    html_, fixes = table_html, []
    for _ in range(max_steps):
        t = parse(html_)
        if t.consistent is not False:
            return (html_ if fixes else None), fixes
        W = t.modal_width
        if W is None:
            if not pad:
                return None, ["no most common row width"]
            W = max(t.widths)
        r = next(i for i, w in enumerate(t.widths) if w != W)
        row, w = t.rows[r], t.widths[r]
        if not row:
            return None, [f"row {r}: no cells"]
        if w < W:
            if len(row) == 1:
                c = row[0]
                html_ = html_[:c.start] + _set_colspan(c, c.colspan + W - w) + html_[c.end:]
                fixes.append(f"row {r}: single cell widened to the table width")
            elif pad:
                c = row[-1]
                html_ = html_[:c.end] + "<td></td>" * (W - w) + html_[c.end:]
                fixes.append(f"row {r}: {W - w} empty cell(s) added at the end")
            else:
                return None, [f"row {r}: {W - w} cell(s) short, position unknown"]
            continue
        k = w - W
        trailing = 0
        for c in reversed(row):
            if _empty(c) and c.colspan == 1 and c.rowspan == 1:
                trailing += 1
            else:
                break
        if trailing >= k:
            cut = row[len(row) - k]
            html_ = html_[:cut.start] + html_[row[-1].end:]
            fixes.append(f"row {r}: {k} trailing empty cell(s) removed")
            continue
        wide_empty = [c for c in row if _empty(c) and c.colspan > k and c.rowspan == 1]
        if len(wide_empty) == 1:
            c = wide_empty[0]
            html_ = html_[:c.start] + _set_colspan(c, c.colspan - k) + html_[c.end:]
            fixes.append(f"row {r}: empty spanning cell narrowed by {k}")
            continue
        if pad:  # the wide row defines the width: pad the others up to it
            W2 = max(t.widths)
            r2 = next(i for i, x in enumerate(t.widths) if x < W2)
            if not t.rows[r2]:
                return None, [f"row {r2}: no cells"]
            c = t.rows[r2][-1]
            html_ = html_[:c.end] + "<td></td>" * (W2 - t.widths[r2]) + html_[c.end:]
            fixes.append(f"row {r2}: {W2 - t.widths[r2]} empty cell(s) added at the end")
            continue
        return None, [f"row {r}: {k} cell(s) too wide, no empty cell to remove"]
    return None, ["too many steps"]


# ---------------------------------------------------------------------------------- compatibility
def _number_key(tok: str) -> str:
    return re.sub(r"[,.](?=\d{3}(?:\D|$))", "", tok).strip(".,")


def text_bags(table_html: str) -> tuple[Counter, Counter]:
    """(numbers, words) of a table's cell texts as multisets. Thousands separators are ignored."""
    text = _html.unescape(_TAG.sub(" ", table_html))
    nums = Counter(_number_key(m) for m in _NUMTOK.findall(text))
    words = Counter(w.lower() for w in _WORD.findall(text))
    return nums, words


def _share(a: Counter, b: Counter) -> float:
    n = sum(a.values())
    return sum((a & b).values()) / n if n else 1.0


@dataclass
class Compat:
    ok: bool
    number_recall: float
    number_precision: float
    word_recall: float
    word_precision: float
    reason: str = ""


def marks(table_html: str) -> tuple[int, int]:
    """(checked, unchecked) checkbox marks in a table."""
    text = _html.unescape(_TAG.sub(" ", table_html))
    return len(_CHECKED.findall(text)), len(_UNCHECKED.findall(text))


def compatible(draft_html: str, new_html: str, numbers_at: float = 1.0, words_at: float = 1.0) -> Compat:
    """May ``new_html`` stand in for ``draft_html``? Only when it says the same thing in another grid.

    The evidence against the draft is its shape, not its text, so the replacement must carry the same
    numbers and the same words (as multisets, both ways, at least ``numbers_at`` / ``words_at``; the default
    is all of them: at 0.9 three of 21 repaired development tables had lost a word or two) and the same
    checkbox marks. A re-read that changes digits is rejected: nothing shows that its digits are the right ones."""
    dn, dw = text_bags(draft_html)
    nn, nw = text_bags(new_html)
    c = Compat(True, _share(dn, nn), _share(nn, dn), _share(dw, nw), _share(nw, dw))
    if c.number_recall < numbers_at or c.number_precision < numbers_at:
        c.ok, c.reason = False, "numbers differ from the page read"
    elif c.word_recall < words_at or c.word_precision < words_at:
        c.ok, c.reason = False, "words differ from the page read"
    elif marks(draft_html) != marks(new_html):
        c.ok, c.reason = False, "checkbox marks differ from the page read"
    return c


def acceptable(draft_html: str, new_html: Optional[str], numbers_at: float = 1.0, words_at: float = 1.0
               ) -> tuple[bool, str]:
    """The full acceptance rule for a replacement table: one table, consistent grid, compatible text,
    and not fewer rows than half the draft (a re-read that merged or lost rows)."""
    if not new_html:
        return False, "no table in the re-read"
    t = parse(new_html)
    if t.consistent is not True:
        return False, "re-read grid is not consistent"
    d = parse(draft_html)
    if len(t.rows) * 2 < len(d.rows):
        return False, "re-read has far fewer rows"
    c = compatible(draft_html, new_html, numbers_at, words_at)
    return c.ok, c.reason or "consistent grid, same cell texts"
