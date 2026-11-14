"""Table-only reads started together with the page read.

A table re-read needs the page read only to decide whether it is used; the crop and the prompt depend on the layout
alone. With ``prefetch=1`` in the tables spec the Read stage starts the table-only read of every layout table region
before the page read, so a table page takes max(page read, table read) instead of their sum. The reads are the
same calls ``reread.run`` makes (same crop, same prompt), so the output does not change.

``prefetch_score`` / ``prefetch_area``: only regions with at least this layout score and this
share of the page are started early; the others are read by the table step when it wants them (same output).
On the development set 51 of 295 started reads were not used; of the regions under score 0.7 or under 5% of the page 26 of 58.

Cost: a region whose read turns out not to be needed (no page-read table matched it, or the page read already has
no fault there) is paid anyway; ``settle`` adds those to the usage as ``table_prefetch_unused``.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Optional

from ...model import Document, Page
from ...readers.base import ReadContext
from ..regions import crops
from ..regions.match import area, detections

_POOL: Optional[ThreadPoolExecutor] = None
_LOCK = threading.Lock()
_PENDING: dict[tuple, Future] = {}


def _pool() -> ThreadPoolExecutor:
    global _POOL
    with _LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("READANYDOC_TABLE_WORKERS", "16")),
                                       thread_name_prefix="table")
        return _POOL


def _key(doc: Document, page: Page, box, prompt: str) -> tuple:
    return (id(doc), page.index, tuple(round(float(v), 5) for v in box), hash(prompt))


def _read(doc: Document, page: Page, box, prompt: str, reader_spec: str, tag: str, pad: float):
    image = crops.crop_png(doc, page, box, pad=pad)
    return crops.reader_for(reader_spec).read(image, None, ReadContext(stem=f"ws1:{tag}:{doc.stem or doc.id}",
                                                                      page_index=page.index, prompt=prompt))


def start(doc: Document, page: Page, spec: dict[str, Any], reader_spec: str) -> int:
    """Called by Read before the page read. Returns the number of reads started."""
    from . import reread
    modes = set(spec.get("reread") or [])
    if not spec.get("prefetch") or not modes:
        return 0
    prompt = reread.PROMPTS[str(spec.get("prompt", "clean"))]
    want = {"table": reread.TABLE_LIKE["table"]}
    if spec.get("all_if"):  # which tables are re-read is decided on the page read, so none is started now
        want = {}
    if "missed" in modes and spec.get("missed_prompt") == "force":
        want["document_index"] = reread.TABLE_LIKE["document_index"]  # with the way-out prompt it is another call
    if not modes & {"all", "broken"} and spec.get("missed_prompt") != "force":
        return 0
    if not want:
        return 0
    whole_at, pad = float(spec.get("whole_at", 0.5)), float(spec.get("pad", 0.012))
    n = 0
    for d in detections(page, want, 0.0):
        if d["score"] < want[d["label"]] or n >= int(spec.get("max_tables", 6)):
            continue
        if d["score"] < float(spec.get("prefetch_score", 0.0)) or area(tuple(d["box"])) < float(spec.get("prefetch_area", 0.0)):
            continue  # a weak or small region is often not re-read at all; if it is, it is read then
        box = reread.WHOLE if area(tuple(d["box"])) >= whole_at else tuple(d["box"])
        k = _key(doc, page, box, prompt)
        pool = _pool()  # takes the lock itself: not inside the block below
        with _LOCK:
            if k in _PENDING:
                continue
            _PENDING[k] = pool.submit(_read, doc, page, box, prompt, reader_spec, f"tread:pre:{d['id']}", pad)
        n += 1
    if n:
        page.properties["table_prefetch"] = n
    return n


def take(doc: Document, page: Page, box, prompt: str) -> Optional[Future]:
    with _LOCK:
        return _PENDING.pop(_key(doc, page, box, prompt), None)


def peek(doc: Document, page: Page, box, prompt: str) -> Optional[Future]:
    """The started read, left in place (Verify's grid repair looks at it; the table step still takes
    and bills it)."""
    with _LOCK:
        return _PENDING.get(_key(doc, page, box, prompt))


def settle(doc: Document, page: Page) -> int:
    """Reads that were started and not used: wait for them and bill them. Called after the table step of the page."""
    with _LOCK:
        mine = [k for k in _PENDING if k[0] == id(doc) and k[1] == page.index]
        futures = [_PENDING.pop(k) for k in mine]
    n = 0
    for fut in futures:
        try:
            res = fut.result()
        except Exception:
            continue
        doc.usage.append({"page": page.index, "reader": getattr(res.usage, "model", None) or "table", "escalation": "region",
                          "reason": "table_prefetch_unused", **res.usage.model_dump(exclude_none=True)})
        n += 1
    if n:
        page.properties["table_prefetch_unused"] = n
    return n
