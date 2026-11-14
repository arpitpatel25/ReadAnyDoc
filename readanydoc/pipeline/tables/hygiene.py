"""Cell hygiene and re-joining of split tables. Deterministic: no model call.

``clean``   inside table cells: descriptions of pictures the reader wrote where the page shows an icon, logo or
            photo are removed (the cell is empty on the page: a description is text that is not printed);
            ``<ul>/<ol>/<li>`` and ``<p>`` become plain lines with a bullet, separated by ``<br>``.
``merge``   two table blocks that follow each other are one table when the layout model shows one table region
            around both (``layout``), or also (``cont``) when the second has the same columns, sits directly
            under the first and has no column header of its own.
Nothing outside ``<table>`` elements is touched.
"""

from __future__ import annotations

import re
from typing import Optional

from ...model import BBox, Page, Region, RegionType
from ...textnorm import plain_text
from ..reading.tables import find_tables, parse_grid
from ..regions.match import TABLE_LABELS, box_of, detections, inside

_TABLE = re.compile(r"<table\b.*?</table\s*>", re.I | re.S)
_CELL = re.compile(r"(<t[dh]\b[^>]*>)(.*?)(</t[dh]\s*>)", re.I | re.S)
_KINDS = (r"icon|image|figure|logo|photo|photograph|picture|symbol|diagram|illustration|graphic|pictogram|drawing|"
          r"emblem|img")
# [Icon: green car], [Figure: FDA logo], *[Image: ...]*, ![alt](src); not [x], [illegible], [signature], [1]
_DESC = re.compile(r"[*_]{0,2}\[\s*(?:" + _KINDS + r")\s*(?:[:\-–—][^\]]*|of\b[^\]]*)?\][*_]{0,2}|!\[[^\]]*\]\([^)]*\)",
                   re.I)
_LI = re.compile(r"<li\b[^>]*>(.*?)(?:</li\s*>|(?=<li\b)|(?=</[uo]l))", re.I | re.S)
_LIST = re.compile(r"<(ul|ol)\b[^>]*>(.*?)</\1\s*>", re.I | re.S)
_P = re.compile(r"\s*<p\b[^>]*>(.*?)</p\s*>\s*", re.I | re.S)
_BR_RUN = re.compile(r"(?:\s*<br\s*/?>\s*){2,}", re.I)
_BR_EDGE = re.compile(r"^(?:\s*<br\s*/?>\s*)+|(?:\s*<br\s*/?>\s*)+$", re.I)


def _list_to_lines(m: re.Match) -> str:
    ordered = m.group(1).lower() == "ol"
    items = [re.sub(r"\s+", " ", x).strip() for x in _LI.findall(m.group(2))]
    items = [x for x in items if x]
    if not items:
        return ""
    return "<br>" + "<br>".join((f"{i + 1}. {x}" if ordered else f"• {x}") for i, x in enumerate(items)) + "<br>"


def clean_cell(inner: str) -> str:
    out = inner
    if "[" in out:
        out = _DESC.sub("", out)
    low = out.lower()
    if "<li" in low:
        for _ in range(3):  # nested lists, innermost first
            new = _LIST.sub(_list_to_lines, out)
            if new == out:
                break
            out = new
    if "<p" in low:
        out = _P.sub(lambda m: "<br>" + m.group(1).strip() + "<br>", out)
    if out != inner:
        out = _BR_EDGE.sub("", _BR_RUN.sub("<br>", out)).strip()
    return out


def clean_table(table_html: str) -> str:
    return _CELL.sub(lambda m: m.group(1) + clean_cell(m.group(2)) + m.group(3), table_html)


def clean_content(content: str) -> str:
    if "<table" not in content.lower():
        return content
    return _TABLE.sub(lambda m: clean_table(m.group(0)), content)


def clean_page(page: Page) -> int:
    """Cleans every table of the page; returns the number of blocks changed."""
    n = 0
    for r in page.regions:
        new = clean_content(r.content)
        if new != r.content:
            r.meta["cells_cleaned"] = True
            r.content, r.text = new, plain_text(new)
            n += 1
    return n


# ---------------------------------------------------------------------------------------------- merge
_TR = re.compile(r"<tr\b[^>]*>.*?</tr\s*>", re.I | re.S)
_TH = re.compile(r"<th\b[^>]*>(.*?)</th\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")


def _single_table(r: Region) -> Optional[str]:
    if r.type != RegionType.TABLE:
        return None
    ts = find_tables(r.content)
    if len(ts) != 1 or r.content.strip() != ts[0].strip():
        return None  # text around the table (a caption) stays with its own table
    return ts[0]


def has_own_header(table_html: str) -> bool:
    """The first row is a column header: two or more non-empty <th> cells."""
    rows = _TR.findall(table_html)
    if not rows:
        return False
    return sum(1 for c in _TH.findall(rows[0]) if _TAG.sub("", c).strip()) >= 2


def join_tables(first: str, second: str) -> str:
    """Rows of ``second`` appended to ``first`` (its header cells become ordinary cells)."""
    rows = "\n".join(re.sub(r"<(/?)th\b", r"<\1td", x, flags=re.I) for x in _TR.findall(second))
    cut = re.search(r"(</tbody\s*>\s*)?</table\s*>\s*$", first, re.I)
    head, tail = first[:cut.start()].rstrip(), first[cut.start():]
    return head + "\n" + rows + "\n" + tail.lstrip()


def merge_page(page: Page, mode: str = "layout", max_gap: float = 0.06) -> list[dict]:
    """Joins consecutive table blocks that are one table. Returns one record per join."""
    report: list[dict] = []
    dets = detections(page, TABLE_LABELS, 0.5)
    i = 0
    while i + 1 < len(page.regions):
        a, b = page.regions[i], page.regions[i + 1]
        ta, tb = _single_table(a), _single_table(b)
        why = None
        if ta and tb and a.bbox is not None and b.bbox is not None:
            ga, gb = parse_grid(ta), parse_grid(tb)
            same_cols = ga.n_cols == gb.n_cols and ga.n_cols >= 2 and not ga.ragged and not gb.ragged
            ba, bb = box_of(a.bbox), box_of(b.bbox)
            if same_cols:
                one = [d for d in dets if inside(ba, tuple(d["box"])) >= 0.8 and inside(bb, tuple(d["box"])) >= 0.8]
                own = [d for d in dets if (inside(ba, tuple(d["box"])) >= 0.8) != (inside(bb, tuple(d["box"])) >= 0.8)]
                if one and not own:
                    why = "one table region of the layout holds both"
                elif mode == "cont" and not has_own_header(tb):
                    gap = bb[1] - ba[3]
                    aligned = abs(ba[0] - bb[0]) <= 0.03 and abs(ba[2] - bb[2]) <= 0.03
                    if -0.02 <= gap <= max_gap and aligned:
                        why = "same columns directly below, no column header of its own"
        if why is None:
            i += 1
            continue
        a.content = join_tables(ta, tb)
        a.text = plain_text(a.content)
        a.bbox = BBox.union([a.bbox, b.bbox])
        a.meta["table_joined"] = why
        del page.regions[i + 1]
        report.append({"kept": a.id, "joined": b.id, "why": why})
    if report:
        for k, r in enumerate(page.regions):
            r.order = k
    return report
