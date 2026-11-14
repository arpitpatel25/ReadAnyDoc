"""``formatting.merge_tables`` the way Reducto applies it.

Measured on Reducto's API (2026-11-12): with ``merge_tables`` on, tables that follow one another in the
document and have the same number of columns become one table, whatever lies between them (headings, page
breaks, running headers) and also on a single page. The rows are concatenated as they are, header rows of the
later fragments included. The merged table sits in the first block; the later blocks leave the response; the
first block lists the original fragments in ``merged_tables``.

Tables that Assemble already joined as cross-page continuations are left as they are. Region content is not
rewritten: the merged form goes to ``meta["merged_content"]`` like Assemble's.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ...model import Document, Page, Region, RegionType
from . import tables

_OUTER = re.compile(r"^\s*<table\b[^>]*>(.*)</table\s*>\s*$", re.I | re.S)
_WRAP = re.compile(r"</?(?:thead|tbody|tfoot)\b[^>]*>", re.I)
BY = "shape"


def clear(doc: Document) -> None:
    """Undo merges made here (the stage may run again with other options)."""
    heads = {r.id for r in doc.iter_regions() if r.meta.get("merged_by") == BY}
    for r in doc.iter_regions():
        if r.id in heads:
            for k in ("merged_content", "merged_tables", "merged_by"):
                r.meta.pop(k, None)
        elif r.meta.get("merged_into") in heads:
            r.meta.pop("merged_into", None)


def _rows(content: str) -> Optional[str]:
    """The table's rows as HTML, without the outer tag."""
    html = content if tables.is_html_table(content) else None
    if html is None:
        grid = tables.parse_table(content)
        if grid is None:
            return None
        html = tables.to_html(grid)
    m = _OUTER.match(html.strip())
    return _WRAP.sub("", m.group(1)).strip() if m else None


def _box(region: Region, page: Page) -> dict[str, Any]:
    b = region.bbox
    left, top, width, height = (b.left, b.top, b.width, b.height) if b is not None else (0.0, 0.0, 1.0, 1.0)
    return {"left": round(left, 4), "top": round(top, 4), "width": round(width, 4), "height": round(height, 4),
            "page": page.index + 1, "original_page": page.original_page}


def merge_consecutive(doc: Document) -> int:
    """Merge runs of same-width tables. Returns the number of merged tables written."""
    clear(doc)
    runs: list[list[tuple[Region, Page, str]]] = []
    cur: list[tuple[Region, Page, str]] = []
    width: Optional[int] = None
    for page in doc.pages:
        for r in page.regions:
            if r.type != RegionType.TABLE:
                continue
            taken = bool(r.meta.get("merged_into") or r.meta.get("merged_content"))
            grid = None if taken else tables.parse_table(r.content)
            rows = _rows(r.content) if grid is not None else None
            if grid is None or rows is None or grid.n_cols < 1:
                if len(cur) > 1:
                    runs.append(cur)
                cur, width = [], None
                continue
            if cur and grid.n_cols == width:
                cur.append((r, page, rows))
            else:
                if len(cur) > 1:
                    runs.append(cur)
                cur, width = [(r, page, rows)], grid.n_cols
    if len(cur) > 1:
        runs.append(cur)
    for run in runs:
        head = run[0][0]
        head.meta["merged_content"] = "<table>" + "".join(rows for _, _, rows in run) + "</table>"
        head.meta["merged_tables"] = [{"content": r.content, "bbox": _box(r, p), "image_url": None} for r, p, _ in run]
        head.meta["merged_by"] = BY
        for r, _, _ in run[1:]:
            r.meta["merged_into"] = head.id
    return len(runs)
