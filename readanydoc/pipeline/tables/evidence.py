"""Evidence for choosing between two readings of one table. Nothing here calls a model.

Three independent things can be measured:
- ``content``: do the two readings hold the same numbers and words (multisets)? When they do, replacing one by the
  other changes no character that was read, only the grid.
- ``words``: agreement of a reading's tokens and numbers with the independent words in the table's box (exact PDF
  text layer, else local OCR).
- ``fit``: after Ground has located the cells of a reading on the page, do the cells of one column lie in one
  vertical band and the cells of one row in one horizontal band? A grid that puts a value under the wrong header,
  or merges two printed columns, breaks this.
"""

from __future__ import annotations

import html as _html
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from statistics import median
from typing import Optional

from ...model import Page, TableCell
from ...textnorm import norm_tokens
from ..checks import htmlgrid
from ..common import words_in
from ..reading.tables import number_tokens, parse_grid
from ..regions.match import Box, to_bbox

_TAG = re.compile(r"<[^>]+>")
_LATEX_CMD = re.compile(r"\\[a-zA-Z]+|[{}$^_\\]")


def cell_text(table_html: str) -> str:
    return _html.unescape(_TAG.sub(" ", table_html))


def tokens(table_html: str) -> list[str]:
    """Word tokens of the cells; LaTeX commands and markup are not words."""
    return norm_tokens(_LATEX_CMD.sub(" ", cell_text(table_html)))


def numbers(table_html: str) -> Counter:
    return Counter(n for n in number_tokens(cell_text(table_html)))


def share(a: Counter, b: Counter) -> float:
    n = sum(a.values())
    return sum((a & b).values()) / n if n else 1.0


@dataclass
class Content:
    number_recall: float  # numbers of the page read found in the candidate
    number_precision: float
    word_recall: float
    word_precision: float

    @property
    def lowest(self) -> float:
        return min(self.number_recall, self.number_precision, self.word_recall, self.word_precision)


def content(mine: str, cand: str) -> Content:
    mn, cn = numbers(mine), numbers(cand)
    mw, cw = Counter(tokens(mine)), Counter(tokens(cand))
    return Content(share(mn, cn), share(cn, mn), share(mw, cw), share(cw, mw))


def box_evidence(page: Page, box: Box) -> tuple[list[str], str]:
    """Tokens of the independent words in ``box``: the exact text layer, else local OCR (confident words)."""
    bb = to_bbox(tuple(box), page.index)
    if page.properties.get("text_layer_exact") and page.words.get("pdf"):
        ws = page.words["pdf"]
        return [t for i in words_in(ws, bb) for t in norm_tokens(ws[i].text)], "pdf"
    for src in ("ppocr", "rapidocr", "tesseract"):
        ws = page.words.get(src) or []
        if ws:
            return [t for i in words_in(ws, bb) if (ws[i].confidence.score or 0) >= 0.8
                    for t in norm_tokens(ws[i].text)], src
    return [], "none"


def word_agreement(table_html: str, evidence: list[str]) -> tuple[float, float]:
    """(recall of the evidence tokens, precision of the reading's tokens)."""
    mine, ev = Counter(tokens(table_html)), Counter(evidence)
    hit = sum((mine & ev).values())
    return (hit / sum(ev.values()) if ev else 0.0), (hit / sum(mine.values()) if mine else 0.0)


# ---------------------------------------------------------------------------------------------- grid fit
@dataclass
class Fit:
    cells: int = 0  # cells with text
    located: float = 0.0  # share of them that Ground found on the page
    cols: float = 1.0  # located single-column cells that lie in their own column's band
    rows: float = 1.0
    checked: int = 0  # cells the column test could judge

    @property
    def score(self) -> float:
        return min(self.cols, self.rows)

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _band_test(items: list[tuple[int, float, float]]) -> tuple[int, int]:
    """``items``: (index along the axis, low, high) of cells that span one index. A cell is in place when it overlaps
    the band of its own index (median low .. median high of that index) at least as much as any other band."""
    by: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for k, lo, hi in items:
        by[k].append((lo, hi))
    bands = {k: (median(x[0] for x in v), median(x[1] for x in v)) for k, v in by.items() if len(v) >= 2}
    if len(bands) < 2:
        return 0, 0
    ok = n = 0
    for k, lo, hi in items:
        if k not in bands:
            continue
        n += 1
        own = min(hi, bands[k][1]) - max(lo, bands[k][0])
        other = max((min(hi, b[1]) - max(lo, b[0])) for j, b in bands.items() if j != k)
        if own > 0 and own >= other:
            ok += 1
    return ok, n


def fit(cells: list[TableCell]) -> Fit:
    with_text = [c for c in cells if c.text.strip()]
    loc = [c for c in with_text if c.bbox is not None]
    f = Fit(cells=len(with_text), located=(len(loc) / len(with_text) if with_text else 0.0))
    okc, nc = _band_test([(c.col, c.bbox.left, c.bbox.left + c.bbox.width) for c in loc if c.colspan == 1])
    okr, nr = _band_test([(c.row, c.bbox.top, c.bbox.top + c.bbox.height) for c in loc if c.rowspan == 1])
    f.cols = okc / nc if nc else 1.0
    f.rows = okr / nr if nr else 1.0
    f.checked = nc
    return f


# ---------------------------------------------------------------------------------------------- shape
@dataclass
class Shape:
    rows: int
    cols: int
    consistent: Optional[bool]
    filled_cols: int  # columns with text in at least half of the rows
    median_cell_words: float
    longest_cell_words: int


def shape(table_html: str) -> Shape:
    t = htmlgrid.parse(table_html)
    g = parse_grid(table_html)
    exp = t.expanded() if t.rows else []
    n_cols = max((len(r) for r in exp), default=0)
    filled = 0
    for c in range(n_cols):
        have = sum(1 for r in exp if c < len(r) and r[c] is not None and r[c].text.strip())
        if exp and have >= 0.5 * len(exp):
            filled += 1
    lens = [len(x.split()) for x in g.cells if x.strip()]
    return Shape(rows=len(exp), cols=n_cols, consistent=t.consistent, filled_cols=filled,
                 median_cell_words=(median(lens) if lens else 0.0), longest_cell_words=max(lens, default=0))
