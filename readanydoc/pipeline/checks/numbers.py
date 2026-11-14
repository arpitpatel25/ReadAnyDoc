"""Check 3, printed totals. A column whose printed total is not the sum of the numbers above it (or
a row whose "Total" column is not the sum of the row) holds a misread digit, a shifted cell, or a
total the rule does not understand. The cells involved are read again from a zoomed crop; a cell
changes only when the re-read differs AND the change makes the arithmetic reconcile without breaking
a total that reconciled before. Anything else is recorded as unresolved.

The totals rule is the one the development set scores with (``totals_in``):
- column total: a row whose first or second cell contains the word "total"; each numeric cell of it is
  compared with three sums of the numbers above (all non-total rows; the rows since the previous total or
  subtotal row; the subtotal rows since the previous total row);
- row total: a column whose own header cell starts with "total"; the numeric cells to its left, or to
  its right, are summed;
- a total counts when at least two addends exist and reconciles within one unit of its last digit.
"""

from __future__ import annotations

import difflib
import itertools
import re
from dataclasses import dataclass, field
from typing import Optional

from ...model import RegionType
from ...readers import prompts
from . import htmlgrid, reread
from .common import PageChecks

_NUM = re.compile(r"^\(?\s*[-+−–]?\s*[$€£]?\s*\(?\s*[-+−–]?\s*\d[\d,]*(\.\d+)?\s*\)?\s*%?\s*\)?$")
_TOTAL = re.compile(r"\btotals?\b", re.I)
_SUB = re.compile(r"\bsub[\s-]?totals?\b", re.I)
Coord = tuple[int, int]


def number(s: Optional[str]) -> Optional[tuple[float, int]]:
    """(value, decimals) of a cell that holds exactly one number, else None."""
    s = (s or "").strip().replace(" ", " ")
    if not _NUM.match(s):
        return None
    neg = "(" in s or s.rstrip().endswith("-") or bool(re.match(r"^\s*[$€£]?\s*[-−–]", s))
    digits = re.sub(r"[^\d.]", "", s)
    if not digits or digits.count(".") > 1:
        return None
    v = float(digits)
    dec = len(digits.split(".")[1]) if "." in digits else 0
    return (-v if neg else v, dec)


@dataclass
class Total:
    kind: str  # "column" | "row"
    row: int
    col: int
    printed: float
    decimals: int
    ok: bool
    addends: list[list[Coord]] = field(default_factory=list)  # the candidate sums (cells of each)
    best_diff: float = 0.0  # smallest |sum - printed| over the candidates
    best_n: int = 0  # number of addends of that candidate
    drop_one: bool = False  # leaving out one addend makes a candidate reconcile (a year in the header, a nested item)

    @property
    def cells(self) -> list[Coord]:
        seen: list[Coord] = []
        for a in self.addends:
            for x in a:
                if x not in seen:
                    seen.append(x)
        return seen + [(self.row, self.col)]

    @property
    def near(self) -> bool:
        """The miss is at most two digit positions wide and small against the total: the pattern of a
        misread digit rather than of a total the rule does not understand. A miss of up to half a unit
        per addend is rounding (a total of rounded figures), not a misread."""
        d = round(abs(self.best_diff) * 10 ** self.decimals)
        if d <= max(1, self.best_n // 2) or self.drop_one:
            return False
        digits = [ch for ch in str(d) if ch != "0"]
        return 0 < len(digits) <= 2 and abs(self.best_diff) <= max(abs(self.printed), 1.0)


def _drop_one(cands: list[list[tuple[int, tuple[float, int]]]], T: tuple[float, int], tol: float) -> bool:
    """One addend equals the miss: the rule summed a cell that is not part of this total. Not a misread."""
    for a in cands:
        miss = sum(v[0] for _, v in a) - T[0]
        if abs(miss) > tol and len(a) >= 3 and any(abs(v[0] - miss) <= tol for _, v in a):
            return True
    return False


def totals(g: list[list[str]]) -> list[Total]:
    out: list[Total] = []
    nums = [[number(c) for c in row] for row in g]
    kind = []
    for row in g:
        lab = " ".join(c for c in row[:2] if number(c) is None)[:80]
        kind.append("sub" if _SUB.search(lab) else "total" if _TOTAL.search(lab) else "data")
    for r, row in enumerate(g):
        if kind[r] == "data":
            continue
        for c in range(1, len(row)):
            T = nums[r][c]
            if T is None:
                continue
            col = [(i, nums[i][c]) for i in range(r) if c < len(nums[i]) and nums[i][c] is not None]
            prev = max([i for i in range(r) if kind[i] != "data"], default=-1)
            prev_total = max([i for i in range(r) if kind[i] == "total"], default=-1)
            cands = [[(i, v) for i, v in col if kind[i] == "data"], [(i, v) for i, v in col if i > prev],
                     [(i, v) for i, v in col if kind[i] == "sub" and i > prev_total]]
            cands = [a for a in cands if len(a) >= 2]
            if not cands:
                continue
            tol = 10 ** (-T[1]) * 1.0001
            diffs = [abs(sum(v[0] for _, v in a) - T[0]) for a in cands]
            out.append(Total("column", r, c, T[0], T[1], min(diffs) <= tol,
                             [[(i, c) for i, _ in a] for a in cands], min(diffs), len(cands[diffs.index(min(diffs))]),
                             _drop_one(cands, T, tol)))
    width = max((len(x) for x in g), default=0)
    hrows = [i for i in range(min(2, len(g))) if not any(n is not None for n in nums[i][1:])]

    def total_col(c: int) -> bool:
        for i in hrows:
            if c >= len(g[i]):
                continue
            h = g[i][c]
            alone = (c == 0 or g[i][c - 1] != h) and (c + 1 >= len(g[i]) or g[i][c + 1] != h)
            if alone and re.match(r"^\W*(grand\s+)?totals?\b", h, re.I):
                return True
        return False
    tcols = {c for c in range(1, width) if total_col(c)}
    for c in sorted(tcols):
        for r in range(len(hrows), len(g)):
            if kind[r] != "data" or c >= len(g[r]) or nums[r][c] is None:
                continue
            T = nums[r][c]
            sides = [[(k, nums[r][k]) for k in range(1, c) if k not in tcols and nums[r][k] is not None],
                     [(k, nums[r][k]) for k in range(c + 1, len(g[r])) if k not in tcols and nums[r][k] is not None]]
            sides = [a for a in sides if len(a) >= 2]
            if not sides:
                continue
            tol = 10 ** (-T[1]) * 1.0001
            diffs = [abs(sum(v[0] for _, v in a) - T[0]) for a in sides]
            out.append(Total("row", r, c, T[0], T[1], min(diffs) <= tol, [[(r, k) for k, _ in a] for a in sides],
                             min(diffs), len(sides[diffs.index(min(diffs))]), _drop_one(sides, T, tol)))
    return out


def text_grid(t: htmlgrid.Table) -> list[list[str]]:
    return [[(c.text if c is not None else "") for c in row] for row in t.expanded()]


def table_totals(table_html: str) -> list[Total]:
    return totals(text_grid(htmlgrid.parse(table_html)))


# ---------------------------------------------------------------------------------- re-read alignment
def _label(row: list[str]) -> str:
    lab = " ".join(dict.fromkeys(c for c in row if c.strip() and number(c) is None))  # every text cell: labels repeat
    return re.sub(r"[\W_]+", " ", lab.lower()).strip()[:120] or "\u2205"


def align(draft: list[list[str]], crop: list[list[str]]) -> dict[Coord, str]:
    """Draft cell (row, column) -> the text the crop re-read has for the same printed cell.
    Rows are matched in order by their label; inside a matched row cells are matched by column when the
    two rows have the same width, else by position among the row's numeric cells when both rows have
    equally many. Cells that cannot be matched this way are left out (no evidence)."""
    out: dict[Coord, str] = {}
    a, b = [_label(r) for r in draft], [_label(r) for r in crop]
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            i, j = blk.a + k, blk.b + k
            dr, cr = draft[i], crop[j]
            if len(dr) == len(cr):
                for c, txt in enumerate(cr):
                    out[(i, c)] = txt
            else:
                dn = [c for c, x in enumerate(dr) if number(x) is not None]
                cn = [x for x in cr if number(x) is not None]
                if len(dn) == len(cn):
                    for c, txt in zip(dn, cn):
                        out[(i, c)] = txt
    return out


def compare(table_html: str, crop_html: str, fail: Total) -> tuple[int, int]:
    """(cells of the failing total that the re-read shows, how many of them it reads differently)."""
    g = text_grid(htmlgrid.parse(table_html))
    m = align(g, text_grid(htmlgrid.parse(crop_html)))
    seen = [c for c in fail.cells if c in m and number(m[c]) is not None]
    return len(seen), sum(1 for c in seen if number(m[c]) != number(g[c[0]][c[1]]))


def repair(table_html: str, crop_html: str, max_subset: int = 3) -> tuple[str, list[dict], list[Total]]:
    """Apply the smallest set of re-read cell values that makes a failing total reconcile.
    Returns (table HTML, accepted changes, totals that still fail)."""
    changes: list[dict] = []
    crop_grid = text_grid(htmlgrid.parse(crop_html))
    for _ in range(40):
        t = htmlgrid.parse(table_html)
        ex = t.expanded()
        g = [[(c.text if c is not None else "") for c in row] for row in ex]
        tt = totals(g)
        good_before = {(x.kind, x.row, x.col) for x in tt if x.ok}
        seen = alt = None
        mapping = align(g, crop_grid)
        for fail in [x for x in tt if not x.ok]:
            diffs = []
            for (r, c) in fail.cells:
                new = mapping.get((r, c))
                if new is None or ex[r][c] is None:
                    continue
                nv, ov = number(new), number(g[r][c])
                if nv is not None and ov is not None and nv != ov:
                    diffs.append(((r, c), new))
            for size in range(1, min(len(diffs), max_subset) + 1):
                for sub in itertools.combinations(diffs, size):
                    g2 = [row[:] for row in g]
                    for (r, c), new in sub:
                        g2[r][c] = new
                    t2 = totals(g2)
                    good_after = {(x.kind, x.row, x.col) for x in t2 if x.ok}
                    if (fail.kind, fail.row, fail.col) in good_after and good_before <= good_after:
                        seen, alt = fail, sub
                        break
                if seen:
                    break
            if seen:
                break
        if not seen:
            return table_html, changes, [x for x in tt if not x.ok]
        cells = {}
        for (r, c), new in alt:
            cell = ex[r][c]
            cells[id(cell)] = (cell, new, g[r][c])
        for cell, new, old in sorted(cells.values(), key=lambda x: -x[0].inner_start):
            inner = cell.inner.replace(old, new, 1) if old and old in cell.inner else new
            table_html = table_html[:cell.inner_start] + inner + table_html[cell.inner_end:]
            changes.append({"row": cell.row, "col": cell.col, "old": old, "new": new,
                            "total": [seen.kind, seen.row, seen.col]})
    return table_html, changes, []


# ---------------------------------------------------------------------------------- the check
def _bands(pc: PageChecks, region, fails: list[Total], table_box: reread.Box, max_height: float) -> list[reread.Box]:
    """Crops for the cells of the failing totals: the table's full width over their rows, cut into
    pieces no taller than ``max_height`` of the page so that every piece is enlarged."""
    g = pc.located()
    k = next((i for i, r in enumerate(pc.page.regions) if r is region), None)
    rows = sorted({r for f in fails for r, _ in f.cells})
    ys = []
    if g is not None and k is not None and k < len(g.regions):
        for cell in g.regions[k].cells:
            if cell.bbox is not None and rows and rows[0] <= cell.row <= rows[-1]:
                ys += [cell.bbox.top, cell.bbox.top + cell.bbox.height]
    y0, y1 = (min(ys), max(ys)) if ys else (table_box[1], table_box[3])
    y0, y1 = max(table_box[1], y0 - 0.02), min(table_box[3], y1 + 0.01)
    if y1 - y0 < 0.02:
        y0, y1 = table_box[1], table_box[3]
    n = max(1, int((y1 - y0) / max_height + 0.999))
    h = (y1 - y0) / n
    return [reread.pad((table_box[0], max(y0, y0 + i * h - 0.015), table_box[2], min(y1, y0 + (i + 1) * h + 0.015)))
            for i in range(n)]


def run(pc: PageChecks, reread_cells: bool = True) -> None:
    max_tables = int(pc.opt("numbers_max_tables", 3))
    max_height = float(pc.opt("numbers_band_height", 0.34))
    max_crops = int(pc.opt("numbers_max_crops", 3))
    only_near = bool(pc.opt("numbers_only_near", True))  # far misses are totals the rule does not understand
    done = 0
    for region in list(pc.page.regions):
        if region.type != RegionType.TABLE:
            continue
        for m in htmlgrid.find_tables(region.content):
            draft = m.group()
            tt = table_totals(draft)
            fails = [x for x in tt if not x.ok]
            if not tt:
                continue
            region.meta["totals"] = {"found": len(tt), "reconciled": len(tt) - len(fails)}
            if not fails:
                continue
            new, changes, left = draft, [], fails
            crops: list[str] = []
            targets = [x for x in fails if x.near] if only_near else fails
            if reread_cells and targets and done < max_tables:
                done += 1
                box = pc.box_of(region)
                if box is not None:
                    for band in _bands(pc, region, targets, box, max_height)[:max_crops]:
                        text = pc.ask(band, prompts.TABLE_CROP_STRICT_FILE, "totals", zoom=float(pc.opt("numbers_zoom", 3.0)))
                        crop = max((x.group() for x in htmlgrid.find_tables(text or "")), key=len, default=None)
                        if crop:
                            crops.append(crop)
                            new, ch, left = repair(new, crop)
                            changes += ch
            if changes:
                pc.replace(region, region.content.replace(draft, new, 1),
                           f"{len(changes)} table cell(s) changed: printed totals now reconcile")
                pc.record(region, "totals", "repaired", f"{len(changes)} cell(s) re-read; the printed total reconciles",
                          changes=changes)
                region.meta["totals"]["reconciled"] = len(tt) - len(left)
            near = [x for x in left if x.near]
            confirmed = []
            for x in near:  # a zoomed re-read that shows the same cells with the same values confirms the page read
                seen = [compare(new, crop, x) for crop in crops]
                if sum(n for n, _ in seen) >= len(x.cells) and not any(d for _, d in seen):
                    confirmed.append(x)
            if confirmed:
                near = [x for x in near if x not in confirmed]
                region.meta["totals"]["confirmed_by_reread"] = len(confirmed)
            if near:
                pc.record(region, "totals", "unresolved",
                          f"{len(near)} printed total(s) miss the sum of their cells by a digit-sized amount",
                          cells=[[x.kind, x.row, x.col, x.printed, round(x.best_diff, 4)] for x in near[:20]])
            elif left:
                region.meta["totals"]["not_understood"] = len(left)
