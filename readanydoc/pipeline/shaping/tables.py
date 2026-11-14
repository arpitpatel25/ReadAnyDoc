"""Table model and deterministic format conversion (html, md, json, csv, jsonbbox, dynamic).
``dynamic`` is stricter than Reducto's rule: markdown only when the conversion is lossless.

The canonical table form inside a Document is HTML with rowspan/colspan. Everything here is a pure
function of that HTML (or of a markdown pipe table, which some readers emit): no model calls.

Flattening rule for md / json / csv, which cannot express merged cells: the text of a merged cell is
repeated in every grid slot it covers, so each row and each column stays self-describing.
"""

from __future__ import annotations

import csv
import html as _html
import io
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Optional

DYNAMIC_MAX_CELLS = 30
DYNAMIC_MAX_MERGED = 0  # Reducto states 4; see dynamic_format
FORMATS = ("dynamic", "html", "md", "json", "csv", "jsonbbox")

_TABLE_RE = re.compile(r"<table\b.*?</table\s*>", re.I | re.S)
_WS = re.compile(r"[ \t\r\f\v]+")
_MD_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


@dataclass
class Cell:
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    text: str = ""  # plain text, line breaks as "\n"
    html: str = ""  # inner HTML as written
    header: bool = False
    attrs: dict[str, str] = field(default_factory=dict)

    @property
    def merged(self) -> bool:
        return self.rowspan > 1 or self.colspan > 1


@dataclass
class TableGrid:
    cells: list[Cell] = field(default_factory=list)
    n_rows: int = 0
    n_cols: int = 0
    caption: str = ""

    @property
    def cell_count(self) -> int:
        return len(self.cells)

    @property
    def merged_count(self) -> int:
        return sum(1 for c in self.cells if c.merged)

    @property
    def header_rows(self) -> int:
        """Number of leading rows made only of header cells."""
        n = 0
        for r in range(self.n_rows):
            row = [c for c in self.cells if c.row <= r < c.row + c.rowspan]
            if row and all(c.header for c in row):
                n += 1
            else:
                break
        return n

    def slots(self) -> list[list[Optional[Cell]]]:
        """n_rows x n_cols matrix; a merged cell appears in every slot it covers."""
        m: list[list[Optional[Cell]]] = [[None] * self.n_cols for _ in range(self.n_rows)]
        for c in self.cells:
            for r in range(c.row, min(c.row + c.rowspan, self.n_rows)):
                for k in range(c.col, min(c.col + c.colspan, self.n_cols)):
                    if m[r][k] is None:
                        m[r][k] = c
        return m

    def matrix(self, fill_merged: bool = True) -> list[list[str]]:
        """Flattened text grid. ``fill_merged`` repeats merged text; otherwise only the top-left slot."""
        out = []
        for r, row in enumerate(self.slots()):
            line = []
            for k, c in enumerate(row):
                if c is None:
                    line.append("")
                elif fill_merged or (c.row == r and c.col == k):
                    line.append(c.text)
                else:
                    line.append("")
            out.append(line)
        return out

    def row_cells(self, r: int) -> list[Cell]:
        return sorted((c for c in self.cells if c.row == r), key=lambda c: c.col)


class _TableParser(HTMLParser):
    """First <table> in the input -> TableGrid. A nested table is flattened into its cell's text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.grid = TableGrid()
        self.depth = 0
        self.done = False
        self.row = -1
        self.occupied: set[tuple[int, int]] = set()
        self.cur: Optional[Cell] = None
        self.text: list[str] = []
        self.raw: list[str] = []
        self.in_caption = False
        self.caption: list[str] = []
        self.in_thead = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if self.done:
            return
        if tag == "table":
            self.depth += 1
            if self.depth > 1 and self.cur is not None:
                self.raw.append(self.get_starttag_text() or "")
            return
        if self.depth == 0:
            return
        if self.depth > 1:
            if self.cur is not None:
                self.raw.append(self.get_starttag_text() or "")
                if tag in ("tr", "br", "p"):
                    self.text.append("\n")
                elif tag in ("td", "th"):
                    self.text.append(" ")
            return
        if tag == "caption":
            self.in_caption = True
        elif tag == "thead":
            self.in_thead = True
        elif tag == "tr":
            self._close_cell()
            self.row += 1
        elif tag in ("td", "th"):
            self._close_cell()
            if self.row < 0:
                self.row = 0
            a = {k: (v or "") for k, v in attrs}
            col = 0
            while (self.row, col) in self.occupied:
                col += 1
            rs, cs = _span(a.get("rowspan")), _span(a.get("colspan"))
            self.cur = Cell(row=self.row, col=col, rowspan=rs, colspan=cs, header=(tag == "th" or self.in_thead),
                            attrs={k: v for k, v in a.items() if k not in ("rowspan", "colspan")})
            for r in range(self.row, self.row + rs):
                for k in range(col, col + cs):
                    self.occupied.add((r, k))
            self.text, self.raw = [], []
        elif self.cur is not None:
            self.raw.append(self.get_starttag_text() or "")
            if tag in ("br", "p", "div", "li"):
                self.text.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if self.done or self.depth == 0:
            return
        if self.cur is not None:
            self.raw.append(self.get_starttag_text() or "")
            if tag == "br":
                self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.done or self.depth == 0:
            return
        if tag == "table":
            self.depth -= 1
            if self.depth == 0:
                self._close_cell()
                self.done = True
            elif self.cur is not None:
                self.raw.append("</table>")
            return
        if self.depth > 1:
            if self.cur is not None:
                self.raw.append(f"</{tag}>")
            return
        if tag == "caption":
            self.in_caption = False
        elif tag == "thead":
            self.in_thead = False
        elif tag in ("td", "th", "tr"):
            self._close_cell()
        elif self.cur is not None:
            self.raw.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.done or self.depth == 0:
            return
        if self.in_caption and self.depth == 1:
            self.caption.append(data)
        elif self.cur is not None:
            self.text.append(data)
            self.raw.append(_html.escape(data, quote=False))

    def _close_cell(self) -> None:
        if self.cur is None:
            return
        self.cur.text = _clean("".join(self.text))
        self.cur.html = "".join(self.raw).strip()
        self.grid.cells.append(self.cur)
        self.cur = None

    def result(self) -> Optional[TableGrid]:
        self._close_cell()
        g = self.grid
        if not g.cells:
            return None
        g.n_rows = max(c.row + c.rowspan for c in g.cells)
        g.n_cols = max(c.col + c.colspan for c in g.cells)
        # a rowspan that runs past the last real row is clipped
        last_row = max(c.row for c in g.cells)
        for c in g.cells:
            c.rowspan = min(c.rowspan, last_row - c.row + 1)
        g.n_rows = last_row + 1
        g.caption = _clean("".join(self.caption))
        return g


def _span(v: Optional[str]) -> int:
    try:
        return max(1, min(int(str(v).strip()), 1000))
    except (TypeError, ValueError):
        return 1


def _clean(text: str) -> str:
    lines = [_WS.sub(" ", ln).strip() for ln in text.replace("\xa0", " ").split("\n")]
    return "\n".join(ln for ln in lines if ln)


def is_html_table(content: str) -> bool:
    return bool(content and re.search(r"<table\b", content, re.I))


def is_md_table(content: str) -> bool:
    lines = [ln for ln in (content or "").strip().split("\n") if ln.strip()]
    return len(lines) >= 2 and all(ln.lstrip().startswith("|") for ln in lines) and bool(_MD_SEP.match(lines[1]))


def parse_html_table(content: str) -> Optional[TableGrid]:
    p = _TableParser()
    try:
        p.feed(content)
        p.close()
    except Exception:  # malformed markup: report "not a table" rather than guess
        return None
    return p.result()


def _split_md_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells = re.split(r"(?<!\\)\|", s)
    return [c.replace("\\|", "|").strip() for c in cells]


def parse_md_table(content: str) -> Optional[TableGrid]:
    if not is_md_table(content):
        return None
    lines = [ln for ln in content.strip().split("\n") if ln.strip()]
    rows = [_split_md_row(lines[0])] + [_split_md_row(ln) for ln in lines[2:]]
    n_cols = max(len(r) for r in rows)
    g = TableGrid(n_rows=len(rows), n_cols=n_cols)
    for r, row in enumerate(rows):
        for k in range(n_cols):
            raw = row[k] if k < len(row) else ""
            text = _clean(re.sub(r"<br\s*/?>", "\n", raw, flags=re.I))
            g.cells.append(Cell(row=r, col=k, text=text, header=(r == 0)))
    return g


def parse_table(content: str) -> Optional[TableGrid]:
    """HTML table or markdown pipe table -> TableGrid; None when the content is neither."""
    if is_html_table(content):
        return parse_html_table(content)
    return parse_md_table(content)


# ---------------------------------------------------------------- writers

def to_html(g: TableGrid, cell_attrs: bool = True) -> str:
    out = ["<table>"]
    if g.caption:
        out.append(f"<caption>{_html.escape(g.caption, quote=False)}</caption>")
    for r in range(g.n_rows):
        out.append("<tr>")
        for c in g.row_cells(r):
            tag = "th" if c.header else "td"
            attrs = ""
            if c.rowspan > 1:
                attrs += f' rowspan="{c.rowspan}"'
            if c.colspan > 1:
                attrs += f' colspan="{c.colspan}"'
            if cell_attrs:
                for k, v in c.attrs.items():
                    attrs += f' {k}="{_html.escape(v, quote=True)}"'
            inner = c.html if c.html else _html.escape(c.text, quote=False).replace("\n", "<br>")
            out.append(f"<{tag}{attrs}>{inner}</{tag}>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _md_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def to_md(g: TableGrid) -> str:
    rows = g.matrix(fill_merged=True)
    if not rows:
        return ""
    lines = ["| " + " | ".join(_md_cell(c) for c in rows[0]) + " |",
             "| " + " | ".join("---" for _ in rows[0]) + " |"]
    lines += ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in rows[1:]]
    return "\n".join(lines)


def to_csv(g: TableGrid) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(g.matrix(fill_merged=True))
    return buf.getvalue().rstrip("\n")


def to_json(g: TableGrid) -> str:
    """A JSON array of rows, each an array of cell strings (merged cells flattened)."""
    return json.dumps(g.matrix(fill_merged=True), ensure_ascii=False)


def to_jsonbbox(g: TableGrid, boxes: dict[tuple[int, int], Any]) -> str:
    """Rows of ``{"text", "bbox"}``; ``boxes`` maps a cell's (row, col) origin to a bbox dict or None."""
    out = []
    for row in g.slots():
        out.append([{"text": c.text if c else "", "bbox": boxes.get((c.row, c.col)) if c else None} for c in row])
    return json.dumps(out, ensure_ascii=False)


def md_is_lossless(g: TableGrid) -> bool:
    """Markdown keeps this table exactly: no spans, and re-parsing the markdown gives the same text grid."""
    if g.merged_count or any(c.attrs for c in g.cells) or g.caption:
        return False
    if g.cell_count != g.n_rows * g.n_cols:
        return False  # ragged rows: markdown would invent empty cells
    try:
        back = parse_md_table(to_md(g))
    except Exception:
        return False
    return back is not None and (back.n_rows, back.n_cols) == (g.n_rows, g.n_cols) and back.matrix() == g.matrix()


def dynamic_format(g: TableGrid) -> str:
    """Markdown only for small tables (<= 30 cells) with no rowspan/colspan that survive the conversion
    unchanged; everything else stays HTML.

    Stricter than Reducto's stated rule (<= 30 cells and <= 4 merged cells): flattening merged cells
    duplicates their text, which a judge on real business documents counted as errors.
    """
    if g.cell_count > DYNAMIC_MAX_CELLS or g.n_rows < 2:
        return "html"
    return "md" if md_is_lossless(g) else "html"


def convert_table(content: str, fmt: str, boxes: Optional[dict[tuple[int, int], Any]] = None,
                  grid: Optional[TableGrid] = None) -> tuple[str, str]:
    """Convert one table block. Returns (content, format actually used).

    Text around the table inside the block (a caption line the reader kept with it) is preserved.
    Content that cannot be parsed as a table is returned unchanged with format ``"unchanged"``.
    """
    g = grid or parse_table(content)
    if g is None:
        return content, "unchanged"
    if fmt == "dynamic":
        fmt = dynamic_format(g)
    if fmt == "jsonbbox" and not (boxes and any(v for v in boxes.values())):
        fmt = "json"  # no cell boxes: the closest honest output
    source_html = is_html_table(content)
    if fmt == "html":
        if source_html and grid is None:
            return content, "html"  # already canonical: never rewrite it
        body = to_html(g)
    elif fmt == "md":
        if not source_html and grid is None:
            return content, "md"
        body = to_md(g)
    elif fmt == "csv":
        body = to_csv(g)
    elif fmt == "json":
        body = to_json(g)
    elif fmt == "jsonbbox":
        body = to_jsonbbox(g, boxes or {})
    else:
        raise ValueError(f"unknown table_output_format {fmt!r}")
    if source_html and grid is None:
        m = _TABLE_RE.search(content)
        if m:
            before, after = content[:m.start()].strip(), content[m.end():].strip()
            body = "\n\n".join(p for p in (before, body, after) if p)
    if g.caption and fmt != "html":
        body = g.caption + "\n\n" + body
    return body, fmt
