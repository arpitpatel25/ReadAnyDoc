"""Table verification: parse HTML/markdown tables into a grid and compare with evidence.

Used to (1) detect "no table found" and malformed tables, (2) choose between table candidates of
the same region by cell agreement with independent words, (3) decide whether a fast-tier table
needs escalation (two reads disagree on cells or shape, or the cells are not backed by the words
inside the table's box).
"""

from __future__ import annotations

import html as _html
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from ...textnorm import norm_tokens

_TABLE = re.compile(r"<table\b.*?</table\s*>", re.I | re.S)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)(?=<tr\b|</tr\s*>|</table|</tbody|</thead|$)", re.I | re.S)
_CELL = re.compile(r"<(t[dh])\b([^>]*)>(.*?)(?=<t[dh]\b|</t[dh]\s*>|</tr|<tr\b|$)", re.I | re.S)
_SPAN = re.compile(r"(row|col)span\s*=\s*[\"']?(\d+)", re.I)
_TAG = re.compile(r"<[^>]+>")


@dataclass
class Grid:
    rows: list[list[str]] = field(default_factory=list)  # cell texts, spans not expanded
    widths: list[int] = field(default_factory=list)  # expanded column count per row
    n_cols: int = 0
    ragged: bool = False  # rows do not all span the same number of columns

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def cells(self) -> list[str]:
        return [c for r in self.rows for c in r]

    def cell_keys(self) -> Counter:
        return Counter(k for k in ("".join(norm_tokens(c)) for c in self.cells) if k)

    def tokens(self) -> list[str]:
        return [t for c in self.cells for t in norm_tokens(c)]


def find_tables(content: str) -> list[str]:
    return _TABLE.findall(content or "")


def has_table(content: str) -> bool:
    if _TABLE.search(content or ""):
        return True
    lines = [l for l in (content or "").split("\n") if l.strip().startswith("|")]
    return len(lines) >= 2


def parse_grid(table_html: str) -> Grid:
    """Rows and cells of one HTML table, tolerant of unclosed tags. Spans feed ``widths`` only."""
    g = Grid()
    carry: dict[int, int] = {}  # column -> rows still covered by a rowspan from above
    for rm in _ROW.finditer(table_html):
        cells, width, col = [], 0, 0
        spans = []
        for cm in _CELL.finditer(rm.group(1)):
            attrs = {k.lower(): int(v) for k, v in _SPAN.findall(cm.group(2))}
            cells.append(_html.unescape(_TAG.sub(" ", cm.group(3))).strip())
            spans.append((max(attrs.get("row", 1), 1), max(attrs.get("col", 1), 1)))
        if not cells:
            continue
        new_carry: dict[int, int] = {}
        for rs, cs in spans:
            while carry.get(col, 0) > 0:
                new_carry[col] = carry[col] - 1
                col += 1
            for k in range(cs):
                if rs > 1:
                    new_carry[col + k] = rs - 1
            col += cs
        for c, left in carry.items():
            if left > 0 and c >= col:
                new_carry.setdefault(c, left - 1)
                col = max(col, c + 1)
        width = col
        carry = {c: n for c, n in new_carry.items() if n > 0}
        g.rows.append(cells)
        g.widths.append(width)
    if g.widths:
        g.n_cols = max(g.widths)
        g.ragged = len(set(g.widths)) > 1
    return g


def content_grids(content: str) -> list[Grid]:
    return [parse_grid(t) for t in find_tables(content)]


def cell_agreement(a: Grid, b: Grid) -> float:
    """Share of cells (by normalised text) that two reads of the same table have in common."""
    ka, kb = a.cell_keys(), b.cell_keys()
    if not ka or not kb:
        return 0.0
    return sum((ka & kb).values()) / max(sum(ka.values()), sum(kb.values()))


def cell_support(g: Grid, word_tokens: list[str]) -> float:
    """Share of non-empty cells whose every token occurs among the independent words."""
    bag = Counter(word_tokens)
    cells = [norm_tokens(c) for c in g.cells]
    cells = [c for c in cells if c]
    if not cells:
        return 0.0
    return sum(all(bag[t] > 0 for t in c) for c in cells) / len(cells)


@dataclass
class TableCheck:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    n_rows: int = 0
    n_cols: int = 0
    support: Optional[float] = None
    recall: Optional[float] = None
    agreement: Optional[float] = None


def check_table(content: str, word_tokens: list[str], other: Optional[str] = None,
                min_support: float = 0.8, min_recall: float = 0.8, min_agreement: float = 0.8) -> TableCheck:
    """Verify one table region. ``word_tokens`` = independent words inside the table's box;
    ``other`` = a second read of the same region (any model), if there is one."""
    grids = content_grids(content)
    if not grids:
        return TableCheck(False, ["no table found"])
    g = max(grids, key=lambda g: len(g.cells))
    chk = TableCheck(True, [], g.n_rows, g.n_cols)
    if g.n_rows < 2 or g.n_cols < 2:
        chk.reasons.append("degenerate table (one row or one column)")
    if g.ragged:
        chk.reasons.append("rows span different numbers of columns")
    toks = [t for gr in grids for t in gr.tokens()]
    if word_tokens:
        chk.support = round(sum(cell_support(gr, word_tokens) * len(gr.cells) for gr in grids)
                            / max(sum(len(gr.cells) for gr in grids), 1), 3)
        bag = Counter(toks)
        chk.recall = round(sum(min(n, bag[t]) for t, n in Counter(word_tokens).items()) / len(word_tokens), 3)
        if chk.support < min_support:
            chk.reasons.append(f"cells not backed by words ({chk.support:.2f})")
        if chk.recall < min_recall:
            chk.reasons.append(f"words in the table box missing from the cells ({chk.recall:.2f})")
    if other is not None:
        og = content_grids(other)
        if not og:
            chk.reasons.append("second read has no table")
        else:
            o = max(og, key=lambda g: len(g.cells))
            chk.agreement = round(cell_agreement(g, o), 3)
            if chk.agreement < min_agreement:
                chk.reasons.append(f"second read disagrees on cells ({chk.agreement:.2f})")
            elif (g.n_rows, g.n_cols) != (o.n_rows, o.n_cols):
                chk.reasons.append(f"second read has another shape ({o.n_rows}x{o.n_cols})")
    chk.ok = not chk.reasons
    return chk


def table_score(content: str, word_tokens: list[str]) -> tuple[float, TableCheck]:
    """Evidence score of one table candidate: cells backed by words + words covered by cells
    + numeric cells backed by words."""
    chk = check_table(content, word_tokens)
    if chk.support is None or chk.recall is None:
        return 0.0, chk
    # numbers weigh double: cells holding digits must be backed by the words in the box
    bag = Counter(word_tokens)
    num_cells = [norm_tokens(c) for g in content_grids(content) for c in g.cells if any(ch.isdigit() for ch in c)]
    num_cells = [c for c in num_cells if c]
    num = sum(all(bag[t] > 0 for t in c) for c in num_cells) / len(num_cells) if len(num_cells) >= 5 else chk.support
    return chk.support + chk.recall + num, chk


def choose_table(candidates: list[tuple[str, str]], word_tokens: list[str], margin: float = 0.03,
                 ) -> tuple[int, str]:
    """Pick one table candidate ([(source, content)], ours first) by evidence.

    1. The candidate best supported by the words in the table's box wins if it beats ours by
       ``margin``. 2. Otherwise, if two other candidates agree with each other on shape and cells
       and ours agrees with neither, the first of that pair wins (two reads against one).
    3. Otherwise ours stays."""
    scored = [table_score(c, word_tokens) for _, c in candidates]
    best = max(range(len(candidates)), key=lambda i: (scored[i][0], -i))
    if best != 0 and scored[best][0] > scored[0][0] + margin:
        return best, (f"better backed by the words in the box ({scored[best][0]:.2f} vs {scored[0][0]:.2f})")
    grids = []
    for _, c in candidates:
        g = content_grids(c)
        grids.append(max(g, key=lambda g: len(g.cells)) if g else None)

    def same(a: Optional[Grid], b: Optional[Grid]) -> bool:
        return (a is not None and b is not None and (a.n_rows, a.n_cols) == (b.n_rows, b.n_cols)
                and cell_agreement(a, b) >= 0.8)
    if grids[0] is None or not any(same(grids[0], g) for g in grids[1:]):
        for i in range(1, len(candidates)):
            for k in range(i + 1, len(candidates)):
                if same(grids[i], grids[k]) and scored[i][0] >= scored[0][0] - margin:
                    return i, f"{candidates[i][0]} and {candidates[k][0]} agree on shape and cells"
    return 0, "ours kept"


# ---- numeric evidence (crop re-reads): digits and sums decide, nothing else ----
_NUM = re.compile(r"\d[\d,.]*\d|\d")
_PURE_NUM = re.compile(r"^\(?[-+−–]?\s*[$€£]?\s*\d[\d,]*(?:\.\d+)?\s*%?\)?$")


def number_tokens(text: str) -> list[str]:
    """Numbers as printed, thousands separators removed ("3,016" -> "3016", "1.50" stays)."""
    out = []
    for m in _NUM.findall(text or ""):
        t = re.sub(r",(?=\d{3}(?:\D|$))", "", m).strip(".,")
        if t:
            out.append(t)
    return out


def parse_number(cell: str) -> Optional[float]:
    """Value of a cell that holds one plain number (currency, %, parentheses for negatives), else None."""
    c = (cell or "").strip()
    if not c or not _PURE_NUM.match(c):
        return None
    neg = c.startswith("(") or bool(re.match(r"^\(?\s*[-−–]", c))
    digits = re.sub(r"[^\d.]", "", c)
    try:
        return -float(digits) if neg else float(digits)
    except ValueError:
        return None


def column_sums(g: Grid) -> int:
    """Number of columns whose last number equals the sum of the numbers above it (a totals row
    that adds up is strong evidence that every digit in the column was read correctly)."""
    if not g.rows:
        return 0
    width = Counter(len(r) for r in g.rows).most_common(1)[0][0]
    rows = [r for r in g.rows if len(r) == width]
    ok = 0
    for c in range(width):
        vals = [v for v in (parse_number(r[c]) for r in rows) if v is not None]
        if len(vals) >= 4 and abs(vals[-1]) > 0 and abs(sum(vals[:-1]) - vals[-1]) <= 0.005:
            ok += 1
    return ok


def numeric_agreement(content: str, evidence_numbers: list[str]) -> tuple[float, float, int]:
    """(precision, recall, number count) of a table's numbers against independent numbers (bags)."""
    mine = Counter(n for g in content_grids(content) for c in g.cells for n in number_tokens(c))
    ev = Counter(evidence_numbers)
    n, m = sum(mine.values()), sum(ev.values())
    hit = sum((mine & ev).values())
    return (hit / n if n else 0.0), (hit / m if m else 0.0), n


@dataclass
class NumericChoice:
    replace: bool
    reason: str
    disputed: bool = False  # the two reads differ on numbers and the evidence could not decide
    detail: dict = field(default_factory=dict)


def choose_table_numeric(mine: str, crop: str, evidence_numbers: list[str], margin: float = 0.05,
                         min_numbers: int = 8) -> NumericChoice:
    """Page-read table vs crop-read table. The crop replaces ours only on a clear margin:
    its numbers agree better with the independent numbers (F1 + ``margin``) without losing a
    column sum, or it has a column sum that adds up where ours has none. Otherwise ours stays and
    the table is reported as disputed when the two reads differ on numbers."""
    gm, gc = content_grids(mine), content_grids(crop)
    if not gm:
        return NumericChoice(False, "our read has no table here")
    if not gc:
        return NumericChoice(False, "crop read has no table")
    m, c = max(gm, key=lambda g: len(g.cells)), max(gc, key=lambda g: len(g.cells))
    nums_m = Counter(n for x in m.cells for n in number_tokens(x))
    nums_c = Counter(n for x in c.cells for n in number_tokens(x))
    total = max(sum(nums_m.values()), sum(nums_c.values()))
    same = sum((nums_m & nums_c).values()) / total if total else 1.0
    f1 = lambda p, r: 2 * p * r / (p + r) if p + r else 0.0
    pm, rm, _ = numeric_agreement(mine, evidence_numbers)
    pc, rc, _ = numeric_agreement(crop, evidence_numbers)
    sm, sc = column_sums(m), column_sums(c)
    detail = {"numbers_same": round(same, 3), "f1_ours": round(f1(pm, rm), 3), "f1_crop": round(f1(pc, rc), 3),
              "sums_ours": sm, "sums_crop": sc, "evidence_numbers": len(evidence_numbers),
              "shape_ours": [m.n_rows, m.n_cols], "shape_crop": [c.n_rows, c.n_cols]}
    disputed = same < 1.0
    if "[illegible]" in crop and "[illegible]" not in mine and sum(nums_c.values()) < 0.8 * sum(nums_m.values()):
        return NumericChoice(False, "crop read left cells unread", disputed, detail)
    if len(c.cells) < 0.7 * len(m.cells):
        return NumericChoice(False, "crop read has far fewer cells", disputed, detail)
    if len(c.cells) > 2 * len(m.cells):
        # the box holds more than our table: the rest of it is elsewhere in our read, and putting
        # the crop in would print that content twice
        return NumericChoice(False, "crop covers more than our table", disputed, detail)
    if c.ragged and not m.ragged:
        return NumericChoice(False, "crop read has uneven rows", disputed, detail)
    if not disputed:
        return NumericChoice(False, "both reads have the same numbers", False, detail)
    enough = len(evidence_numbers) >= min_numbers
    if enough and f1(pc, rc) >= f1(pm, rm) + margin and sc >= sm:
        return NumericChoice(True, f"crop numbers agree better with the independent numbers "
                                   f"({f1(pc, rc):.2f} vs {f1(pm, rm):.2f})", True, detail)
    if sc > sm and (not enough or f1(pc, rc) >= f1(pm, rm) - 0.02):
        return NumericChoice(True, f"crop has {sc} column total(s) that add up, ours {sm}", True, detail)
    return NumericChoice(False, "no clear margin: ours kept, table marked low confidence", True, detail)


# ---- cell-level repair of digits when two reads of one table have the same grid ----
def cell_spans(table_html: str) -> list[list[tuple[int, int, str]]]:
    """Rows of (start, end, text) for every cell's inner HTML, offsets into ``table_html``."""
    rows = []
    for rm in _ROW.finditer(table_html):
        cells = [(rm.start(1) + cm.start(3), rm.start(1) + cm.end(3),
                  _html.unescape(_TAG.sub(" ", cm.group(3))).strip()) for cm in _CELL.finditer(rm.group(1))]
        if cells:
            rows.append(cells)
    return rows


def _same_grid(a: list[list], b: list[list]) -> bool:
    return len(a) == len(b) and all(len(x) == len(y) for x, y in zip(a, b))


def vote_number_cells(mine: str, crop: str, other: Optional[str], evidence_numbers: list[str],
                      max_share: float = 0.25) -> tuple[str, list[dict], list[dict]]:
    """Repair digits cell by cell. Needs the same grid (rows and cells per row) in ours and the crop.
    A cell that holds numbers and differs is replaced by the crop's text only when
    (a) the independent numbers contain the crop's numbers and not ours, or
    (b) a second page read with the same grid has exactly the crop's numbers in that cell and the
        independent numbers do not contain ours (two reads, one at higher resolution, against one).
    Returns (new html, edits, unresolved differences). No edit when more than ``max_share`` of the
    number cells differ: then the grids are probably shifted against each other."""
    sm, sc = cell_spans(mine), cell_spans(crop)
    if not sm or not _same_grid(sm, sc):
        return mine, [], []
    so = cell_spans(other) if other else None
    if so is not None and not _same_grid(sm, so):
        so = None
    ev = Counter(evidence_numbers)
    diffs = []
    n_num = 0
    for r, (rm, rc) in enumerate(zip(sm, sc)):
        for c, (a, b) in enumerate(zip(rm, rc)):
            na, nb = number_tokens(a[2]), number_tokens(b[2])
            if na or nb:
                n_num += 1
            if na != nb and na and nb:
                diffs.append((r, c, a, b, na, nb))
    if not diffs or len(diffs) > max_share * max(n_num, 1):
        return mine, [], [{"row": r, "col": c, "ours": a[2], "crop": b[2]} for r, c, a, b, _, _ in diffs]
    edits, open_ = [], []
    for r, c, a, b, na, nb in diffs:
        ours_backed = all(ev[n] > 0 for n in na)
        crop_backed = all(ev[n] > 0 for n in nb)
        third = number_tokens(so[r][c][2]) if so is not None else None
        why = None
        if crop_backed and not ours_backed:
            why = "independent numbers have the crop's value, not ours"
        elif third is not None and third == nb and not ours_backed:
            why = "second page read and crop read agree"
        if why and re.sub(r"[\d,.\s]", "", a[2]) == re.sub(r"[\d,.\s]", "", b[2]):  # only digits differ
            edits.append({"row": r, "col": c, "old": a[2], "new": b[2], "why": why, "span": (a[0], a[1])})
        else:
            open_.append({"row": r, "col": c, "ours": a[2], "crop": b[2]})
    out = mine
    for e in sorted(edits, key=lambda e: -e["span"][0]):
        s, t = e.pop("span")
        out = out[:s] + _html.escape(e["new"], quote=False) + out[t:]
    return out, edits, open_


def vote_cells_majority(current: str, others: list[tuple[str, str, bool]], min_agree: int = 2,
                        ) -> tuple[str, list[dict], int]:
    """Cell-level majority for one table. ``others`` = [(source, table html, is_crop_read)].
    Only reads whose grid (rows, cells per row) equals the current table's take part. A cell changes
    only when at least ``min_agree`` of them, including a crop read, agree on a value that differs
    from the current one. Returns (new html, edits, number of cells still disputed)."""
    cur = cell_spans(current)
    aligned = [(src, cell_spans(t), crop) for src, t, crop in others]
    aligned = [(src, g, crop) for src, g, crop in aligned if g and _same_grid(cur, g)]
    if not cur or len(aligned) < min_agree:
        return current, [], 0
    norm = lambda s: " ".join(s.split())
    edits, open_ = [], 0
    for r, row in enumerate(cur):
        for c, (s, e, text) in enumerate(row):
            votes: dict[str, list[tuple[str, bool]]] = {}
            for src, g, crop in aligned:
                v = norm(g[r][c][2])
                if v != norm(text):
                    votes.setdefault(v, []).append((src, crop))
            if not votes:
                continue
            value, who = max(votes.items(), key=lambda kv: len(kv[1]))
            if value and len(who) >= min_agree and any(crop for _, crop in who):
                edits.append({"row": r, "col": c, "old": text, "new": value,
                              "why": " + ".join(src for src, _ in who) + " agree", "span": (s, e)})
            else:
                open_ += 1
    out = current
    for ed in sorted(edits, key=lambda ed: -ed["span"][0]):
        s, e = ed.pop("span")
        out = out[:s] + _html.escape(ed["new"], quote=False) + out[e:]
    return out, edits, open_
