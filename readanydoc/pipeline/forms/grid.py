"""Form grid or data table? Decided from the table the page read wrote, without a model call.

The page read writes a fill-in form as an HTML table whenever the form is drawn with boxes. Reducto r-1
returns such a section as a Key Value block (label and value lines, never a table) and keeps real grids
as tables. The difference used here:

* data table: the columns mean something. Evidence, any one of: a header row of ``<th>`` over two or
  more body rows; a column that holds numbers in two or more rows; three or more rows with the same
  number of cells whose cells are plain values.
* form grid: the cells are self-contained fields. A field cell carries its own label and its own value
  or mark: a checkbox, a blank to fill in, a label line followed by a value line, or a printed box
  number in front of a label ("1a Nonemployee compensation"). A table is a form grid when at least
  half of its filled cells are field cells and no data-table evidence exists.

Anything else stays a table: a data table must never be converted, an unconverted form grid costs nothing.
``to_key_value`` writes the cells of a form grid as lines, in the order the read wrote them; every
character of every cell is kept, only the table markup is dropped.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field

_TABLE = re.compile(r"<table\b.*?</table\s*>", re.S | re.I)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)(?=<tr\b|</tr\s*>|</table|</tbody|</thead|</tfoot|$)", re.I | re.S)
_CELL = re.compile(r"<(t[dh])\b([^>]*)>(.*?)(?=<t[dh]\b|</t[dh]\s*>|</tr|<tr\b|$)", re.I | re.S)
_SPAN = re.compile(r"(row|col)span\s*=\s*[\"']?(\d+)", re.I)
_BR = re.compile(r"<br\s*/?>|</p>\s*<p[^>]*>|</div>\s*<div[^>]*>", re.I)
_BLOCK_TAG = re.compile(r"</?(?:p|div|span|tbody|thead|tfoot|font|center)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
CHECKBOX = re.compile(r"\[[ xX✓✔]?\]|[☐☑☒□■▢]")
BLANK = re.compile(r"_{3,}|\.{6,}|<empty\s*/?>")
_NUMBER = re.compile(r"^\(?\s*[-+−–]?\s*[$€£]?\s*\(?\s*[-+−–]?\s*\d[\d,. ]*\s*\)?\s*%?\s*\)?$")
# a printed box number or letter in front of a label: "1a Wages", "12. Date", "b Employer", "(3) Type"
# the label must start with a capitalised word ("50 mm" and "10 bars" are values); a capital letter with a full
# stop is an initial ("S. Tesh"), so a letter id is lower case, bracketed, or has no full stop
_BOX_ID = re.compile(r"^(?:\(?\d{1,3}[a-zA-Z]?\)?[.)]?|\([a-zA-Z]\)|[a-z][.)]?|[A-Z]\)?)\s+(?=[\"“'‘(]?[A-Z][A-Za-z’']+)")


@dataclass
class Cell:
    inner: str  # the cell's HTML as written
    header: bool = False
    rowspan: int = 1
    colspan: int = 1

    @property
    def lines(self) -> list[str]:
        """The cell's printed lines: ``<br>`` and paragraph breaks split, other markup kept."""
        s = _BR.sub("\n", self.inner)
        s = _BLOCK_TAG.sub("", s)
        s = _html.unescape(s).replace("\xa0", " ")
        return [re.sub(r"[ \t]+", " ", l).strip() for l in s.split("\n") if l.strip()]

    @property
    def text(self) -> str:
        return " ".join(_TAG.sub(" ", l) for l in self.lines).strip()


@dataclass
class Table:
    rows: list[list[Cell]] = field(default_factory=list)
    widths: list[int] = field(default_factory=list)  # columns each row spans, rowspans from above included
    grid: list[list[str]] = field(default_factory=list)  # cell texts with spans expanded


def parse(table_html: str) -> Table:
    t = Table()
    carry: dict[int, tuple[int, str]] = {}  # column -> (rows still covered, text)
    for rm in _ROW.finditer(table_html):
        cells = []
        for cm in _CELL.finditer(rm.group(1)):
            a = {k.lower(): int(v) for k, v in _SPAN.findall(cm.group(2))}
            cells.append(Cell(inner=cm.group(3).strip(), header=cm.group(1).lower() == "th",
                              rowspan=min(max(a.get("row", 1), 1), 200), colspan=min(max(a.get("col", 1), 1), 60)))
        if not cells:
            continue
        row: list[str] = []
        nxt: dict[int, tuple[int, str]] = {}

        def fill() -> None:
            while len(row) in carry:
                left, txt = carry[len(row)]
                if left > 1:
                    nxt[len(row)] = (left - 1, txt)
                row.append(txt)
        for c in cells:
            fill()
            for _ in range(c.colspan):
                if c.rowspan > 1:
                    nxt[len(row)] = (c.rowspan - 1, c.text)
                row.append(c.text)
        fill()
        for col, v in carry.items():  # rowspans to the right of this row's last cell
            if col >= len(row) and v[0] > 1:
                nxt[col] = (v[0] - 1, v[1])
        carry = nxt
        t.rows.append(cells)
        t.widths.append(len(row))
        t.grid.append(row)
    return t


def _words(s: str) -> list[str]:
    return [w for w in s.split() if any(ch.isalnum() for ch in w)]


def is_number(s: str) -> bool:
    return bool(_NUMBER.match(s.strip())) and any(ch.isdigit() for ch in s)


# an amount with its unit or currency after it or before it: "0.00 zł", "550 kg", "USD 12.50", "Rs. 500".
# The unit is one short word (at most three letters) set off by a space; the number has two or more digits, so a
# printed box id ("1 a", "2 b") is not an amount, and "12a" (no space) is not one either.
_UNIT = r"(?:[^\W\d_]{1,3}\.?)"
_AMOUNT_AFTER = re.compile(rf"^(?P<n>.*\d\)?)\s+{_UNIT}$")
_AMOUNT_BEFORE = re.compile(rf"^{_UNIT}\s+(?P<n>[-+−–(]?\s*\d.*)$")


def is_amount(s: str) -> bool:
    """A number, or a number with a short unit / currency word next to it."""
    s = s.strip()
    if is_number(s):
        return True
    for rx in (_AMOUNT_AFTER, _AMOUNT_BEFORE):
        m = rx.match(s)
        if m and is_number(m.group("n")) and sum(ch.isdigit() for ch in m.group("n")) >= 2:
            return True
    return False


def field_strength(c: Cell) -> int:
    """0: not a field. 2: a cell that is a whole field by its own marks (a labelled checkbox, a blank to
    fill in, a printed box number before the label). 1: a label line with something entered under it,
    which a multi-line cell of an ordinary table also looks like."""
    text = c.text
    if not text:
        return 0
    bare = CHECKBOX.sub(" ", text)
    if CHECKBOX.search(text):
        return 2 if any(ch.isalpha() for ch in bare) else 0  # a mark alone is a table cell
    if BLANK.search(c.inner):
        return 2
    lines = c.lines
    first = _TAG.sub(" ", lines[0]).strip() if lines else ""
    if is_number(first):
        return 0
    if _BOX_ID.match(first) and len(_words(first)) >= 2:
        return 2
    if len(lines) >= 2 and any(ch.isalpha() for ch in first) and len(_words(first)) <= 14:
        return 1
    return 0


def is_field_cell(c: Cell) -> bool:
    return field_strength(c) > 0


@dataclass
class Verdict:
    kind: str  # "form" | "data" | "unclear"
    reason: str
    stats: dict = field(default_factory=dict)


def classify(table_html: str, min_fields: int = 3, min_share: float = 0.5, min_strong_share: float = 0.3,
             amounts: bool = False) -> Verdict:
    """``amounts``: a column that holds amounts with a unit or currency word
    ("0.00 zł") in three or more rows is data-table evidence, as a column of bare numbers in two rows already is."""
    t = parse(table_html)
    cells = [c for r in t.rows for c in r if c.text]
    n = len(cells)
    stats = {"rows": len(t.rows), "cells": n}
    if n < 2:
        return Verdict("unclear", "fewer than two filled cells", stats)
    # ---- data-table evidence
    header_rows = [r for r in t.rows if sum(c.header for c in r) >= 2 and all(c.header or not c.text for c in r)]
    body_rows = [r for r in t.rows if not any(c.header for c in r)]
    stats["th_rows"] = len(header_rows)
    if header_rows and len(body_rows) >= 2:
        return Verdict("data", "header row over two or more body rows", stats)
    width = max(t.widths, default=0)
    numeric_cols = 0
    for k in range(width):
        col = [row[k] for row in t.grid if k < len(row)]
        if sum(is_number(x) for x in col) >= 2:
            numeric_cols += 1
    stats["numeric_cols"] = numeric_cols
    if numeric_cols:
        return Verdict("data", "a column holds numbers in two or more rows", stats)
    if amounts:
        amount_cols = sum(1 for k in range(width)
                          if sum(is_amount(row[k]) for row in t.grid if k < len(row)) >= 3)
        stats["amount_cols"] = amount_cols
        if amount_cols:
            return Verdict("data", "a column holds amounts in three or more rows", stats)
    regular = len(t.rows) >= 3 and len(set(t.widths)) == 1
    stats["regular"] = regular
    if pair_rows(t):
        return Verdict("form", "rows of `label:` and value", {**stats, "pairs": True})
    strength = [field_strength(c) for c in cells]
    fields = sum(1 for x in strength if x)
    strong = sum(1 for x in strength if x == 2)
    mark_only = sum(1 for c in cells if CHECKBOX.search(c.text) and not any(ch.isalpha() for ch in CHECKBOX.sub(" ", c.text)))
    share = fields / n
    stats.update(fields=fields, strong=strong, mark_only=mark_only, share=round(share, 3))
    if regular and share < 0.3:
        return Verdict("data", "regular rows of plain values", stats)
    if mark_only >= 2 and mark_only >= strong:
        return Verdict("data" if regular else "unclear", "cells that hold only a mark: columns of a table", stats)
    if regular:
        rows_with = sum(1 for r in t.rows if any(field_strength(c) for c in r if c.text))
        if rows_with * 2 < len(t.rows):
            return Verdict("data", "fields only in the first rows: numbered column headers over a grid", stats)
    if any(c.header for c in cells) and share < 0.8:
        return Verdict("unclear", "header cells and not clearly a form", stats)
    if fields >= min_fields and share >= min_share:
        if strong >= max(2.0, min_strong_share * fields):
            return Verdict("form", f"{fields} of {n} filled cells are fields", stats)
        return Verdict("unclear", "multi-line cells without checkboxes, blanks or box numbers", stats)
    marks = sum(len(CHECKBOX.findall(c.text)) for c in cells)
    if marks >= 2 and strong >= 1 and len(t.rows) <= 2 and not mark_only:
        return Verdict("form", "a row of labelled checkboxes", stats)
    return Verdict("unclear", "not enough field cells", stats)


def pair_rows(t: Table) -> bool:
    """A two-column table without a header whose first column is labels that end in a colon."""
    if len(t.rows) < 2 or any(c.header for r in t.rows for c in r):
        return False
    if any(len(r) != 2 or r[0].colspan != 1 or r[0].rowspan != 1 or r[1].rowspan != 1 for r in t.rows):
        return False
    labels = [r[0].text for r in t.rows]
    if any(is_number(x) or len(_words(x)) > 10 for x in labels if x):
        return False
    colon = sum(1 for x in labels if x.rstrip().endswith(":"))
    return colon >= 2 and colon * 2 >= len(labels)


def to_key_value(table_html: str) -> str:
    """The cells of a form grid as lines: one cell after the other in the written order, a line break
    where the cell had one. Text and inline markup are unchanged. A row of `label:` and value is one line."""
    t = parse(table_html)
    out: list[str] = []
    if pair_rows(t):
        for row in t.rows:
            lab, val = row[0].lines, row[1].lines
            if lab and val:
                out.extend(lab[:-1] + [lab[-1] + " " + val[0]] + val[1:])
            else:
                out.extend(lab + val)
        return "\n".join(out)
    for row in t.rows:
        for c in row:
            out.extend(c.lines)
    return "\n".join(out)


def to_key_value_groups(table_html: str) -> list[str]:
    """``to_key_value`` cut into one piece per table row (rows tied together by a rowspan stay together):
    a row of a form grid is one strip of boxes, which is about the size of the Key Value blocks r-1 returns."""
    t = parse(table_html)
    if pair_rows(t):
        return [to_key_value(table_html)]
    out: list[str] = []
    cur: list[str] = []
    open_rows = 0  # rows below the current one that a rowspan still covers
    for row in t.rows:
        for c in row:
            cur.extend(c.lines)
        open_rows = max(open_rows - 1, max((c.rowspan for c in row), default=1) - 1)
        if open_rows <= 0:
            if cur:
                out.append("\n".join(cur))
            cur = []
    if cur:
        out.append("\n".join(cur))
    return out


def split_content(content: str) -> list[tuple[str, str]]:
    """("table" | "text", piece) for a block's content, in order."""
    parts, pos = [], 0
    for m in _TABLE.finditer(content or ""):
        if content[pos:m.start()].strip():
            parts.append(("text", content[pos:m.start()].strip("\n")))
        parts.append(("table", m.group(0)))
        pos = m.end()
    if (content or "")[pos:].strip():
        parts.append(("text", content[pos:].strip("\n")))
    return parts
