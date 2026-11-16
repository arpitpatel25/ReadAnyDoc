"""Shared helpers for the task layer: errors, a page/region view over Documents, prompt rendering."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from ..model import Document, Page, Region, RegionType


class TaskError(Exception):
    """A user-facing task failure with an HTTP-style status code."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail

    def to_response(self) -> dict[str, Any]:
        return {"detail": self.detail}


@dataclass
class RegionRef:
    label: str  # short id shown to the model: r1, r2, ...
    region: Region
    page: "PageRef"


@dataclass
class PageRef:
    number: int  # 1-based, continuous across documents
    page: Page
    doc: Document
    regions: list[RegionRef] = field(default_factory=list)


class DocView:
    """Pages and regions of one or more Documents with stable short labels for prompts."""

    def __init__(self, document: Document | Sequence[Document]):
        docs = [document] if isinstance(document, Document) else list(document or [])
        if not docs or not all(isinstance(d, Document) for d in docs):
            raise TaskError(400, "no parsed document was provided")
        self.docs = docs
        self.pages: list[PageRef] = []
        self.by_label: dict[str, RegionRef] = {}
        n = 0
        for d in docs:
            for p in d.pages:
                pref = PageRef(number=len(self.pages) + 1, page=p, doc=d)
                for r in sorted(p.regions, key=lambda r: r.order):
                    n += 1
                    ref = RegionRef(label=f"r{n}", region=r, page=pref)
                    pref.regions.append(ref)
                    self.by_label[ref.label] = ref
                self.pages.append(pref)

    def select(self, page_numbers: Optional[Iterable[int]]) -> list[PageRef]:
        if page_numbers is None:
            return list(self.pages)
        wanted = set(page_numbers)
        return [p for p in self.pages if p.number in wanted]

    def lookup(self, label: Any) -> Optional[RegionRef]:
        if not isinstance(label, str):
            return None
        return self.by_label.get(label.strip().strip("[]").lower())


def resolve_page_range(page_range: Any, total: int) -> Optional[list[int]]:
    """Reducto page_range (1-indexed; object, list of objects, or list of ints) -> sorted page numbers.

    ``None`` means "no restriction". Lists of sheet names (strings) are ignored. Raises 400 when
    the range selects nothing or is malformed.
    """
    if page_range is None or page_range == [] or page_range == {}:
        return None

    def one(obj: dict) -> list[int]:
        start = obj.get("start") or 1
        end = obj.get("end") or total
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            raise TaskError(400, f"invalid page_range: {obj!r}")
        return list(range(start, min(end, total) + 1))

    pages: list[int] = []
    if isinstance(page_range, dict):
        pages = one(page_range)
    elif isinstance(page_range, list):
        if all(isinstance(x, str) for x in page_range):
            return None  # sheet names: not applicable to paged documents
        for x in page_range:
            if isinstance(x, bool):
                raise TaskError(400, f"invalid page_range: {page_range!r}")
            if isinstance(x, int):
                if x < 1:
                    raise TaskError(400, f"invalid page_range: page numbers are 1-indexed, got {x}")
                if x <= total:
                    pages.append(x)
            elif isinstance(x, dict):
                pages.extend(one(x))
            else:
                raise TaskError(400, f"invalid page_range: {page_range!r}")
    else:
        raise TaskError(400, f"invalid page_range: {page_range!r}")
    pages = sorted(set(pages))
    if not pages:
        raise TaskError(400, f"page_range selects no pages (document has {total})")
    return pages


def select_pages(view: DocView, page_range: Any) -> Optional[list[PageRef]]:
    """Pages picked by a Reducto ``page_range``; None when there is no restriction.

    For a single document the range refers to page numbers of the source file (``original_page``),
    so it works both on a full parse and on a Document that was already parsed with the same range.
    For several documents it refers to the continuous numbering.
    """
    if len(view.docs) == 1:
        doc = view.docs[0]
        total = max([int(doc.meta.get("total_pages") or 0), len(view.pages)]
                    + [p.page.original_page for p in view.pages])
        wanted = resolve_page_range(page_range, total)
        if wanted is None:
            return None
        chosen = [p for p in view.pages if p.page.original_page in set(wanted)]
    else:
        wanted = resolve_page_range(page_range, len(view.pages))
        if wanted is None:
            return None
        chosen = view.select(wanted)
    if not chosen:
        raise TaskError(400, "page_range selects no pages of the parsed document")
    return chosen


_ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)


def truncate_table(html: str, keep_rows: int = 4) -> str:
    """Keep the first rows of an HTML table and say how many were dropped."""
    rows = _ROW.findall(html)
    if len(rows) <= keep_rows:
        return html
    return "<table>" + "".join(rows[:keep_rows]) + f"</table> [table truncated: {len(rows) - keep_rows} more rows]"


def _mark(content: str, spans: Sequence[tuple[int, int]]) -> str:
    """``content`` with each (start, end) span written as ~~text~~ (crossed out on the page)."""
    out, last = [], 0
    for a, b in sorted(spans):
        if a < last or b > len(content):
            continue
        out += [content[last:a], "~~", content[a:b], "~~"]
        last = b
    return "".join(out) + content[last:]


def render_pages(pages: Sequence[PageRef], *, labels: bool = True, truncate_tables: bool = False,
                 max_chars_per_page: Optional[int] = None, marks: Optional[dict] = None) -> str:
    """Document text for a prompt: page markers and one line block per region (optionally labelled).

    ``marks``: {region label: [(start, end), ...]} of crossed-out text, written as ~~text~~.
    """
    out: list[str] = []
    for p in pages:
        out.append(f"=== Page {p.number} ===")
        blocks: list[str] = []
        for ref in p.regions:
            r = ref.region
            content = r.content
            if marks and marks.get(ref.label):
                content = _mark(content, marks[ref.label])
            content = content.strip()
            if not content:
                continue
            if truncate_tables and r.type == RegionType.TABLE:
                content = truncate_table(content)
            blocks.append(f"[{ref.label}|{r.type.value}] {content}" if labels else content)
        if max_chars_per_page is not None and sum(len(b) for b in blocks) > max_chars_per_page:
            # over budget: keep the head and the tail of the page (titles are at the top; totals, signatures
            # and holder / addressee blocks are at the bottom) and drop the middle
            head_budget, tail_budget = int(max_chars_per_page * 0.6), int(max_chars_per_page * 0.4)
            head: list[str] = []
            tail: list[str] = []
            used = 0
            i = 0
            while i < len(blocks) and used + len(blocks[i]) <= head_budget:
                head.append(blocks[i]); used += len(blocks[i]); i += 1
            if not head:  # one very long first block
                head.append(blocks[0][:head_budget] + " [...]"); i = 1
            used = 0
            j = len(blocks) - 1
            while j >= i and used + len(blocks[j]) <= tail_budget:
                tail.insert(0, blocks[j]); used += len(blocks[j]); j -= 1
            blocks = head + ([f"[... {j - i + 1} blocks omitted ...]"] if j >= i else []) + tail
        out.extend(blocks)
        if not p.regions:
            out.append("(no text on this page)")
    return "\n".join(out)


def page_images(pages: Sequence[PageRef]) -> list[str]:
    """Rendered page image paths that already exist on the Document (highest dpi per page)."""
    imgs = []
    for p in pages:
        if p.page.renders:
            key = max(p.page.renders, key=lambda k: float(k) if str(k).replace(".", "", 1).isdigit() else 0.0)
            if os.path.isfile(p.page.renders[key]):  # renders of an older job may have been cleaned up
                imgs.append(p.page.renders[key])
    return imgs


def bbox_dict(region: Region, pref: PageRef, box: Any = None) -> dict[str, Any]:
    """Reducto BoundingBox for a region (or for ``box`` inside it). Same fallbacks as the serializer."""
    b = box or region.bbox
    if b is None and region.meta.get("search_bbox"):
        sb = region.meta["search_bbox"]
        left, top, width, height = sb["left"], sb["top"], sb["width"], sb["height"]
    elif b is None:
        left, top, width, height = 0.0, 0.0, 1.0, 1.0
    else:
        left, top, width, height = b.left, b.top, b.width, b.height
    return {"left": round(left, 4), "top": round(top, 4), "width": round(width, 4), "height": round(height, 4),
            "page": pref.number, "original_page": pref.page.original_page}


def sum_usage(results: Iterable[Any]) -> dict[str, Any]:
    results = list(results)
    tot = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for r in results:
        tot["calls"] += 1
        for k in ("input_tokens", "output_tokens", "cost_usd"):
            v = (r.usage or {}).get(k)
            if isinstance(v, (int, float)):
                tot[k] += v
    tot["cost_usd"] = round(tot["cost_usd"], 6)
    models = sorted({str(getattr(r, "model", None)) for r in results if getattr(r, "model", None)})
    if models:  # usage.by_model shows the model, not just the task
        tot["model"] = "+".join(models)
    return tot
