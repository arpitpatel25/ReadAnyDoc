"""Zoomed strip reads started together with the page read (speed).

The zoom vote of a scanned page needs the page read first only to decide whether to run at all and
what to compare; the strip images depend on the page alone. ``start`` launches the strip reads in
the background before the page read, so the wall time of a scan is max(page read, strips) instead
of their sum. ``zoom_prefetch``: 1 = the first set (3 strips), 2 = both sets (3 + 4 strips).

Cost: strips that turn out not to be needed (handwriting route, 3+ ``[illegible]`` marks, a second
set when nothing differed) are paid anyway; ``settle`` adds them to the usage as ``zoom_strip_unused``.
The reads use the same images, prompt and cache keys as ``Read._zoom_vote``.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

from ...model import Document, Page
from ...readers.base import ReadContext

_POOL: Optional[ThreadPoolExecutor] = None
_POOL_LOCK = threading.Lock()


def pool() -> ThreadPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("READANYDOC_STRIP_WORKERS", "32")),
                                       thread_name_prefix="strip")
        return _POOL


def reader_spec(stage) -> str:
    """The reader of the strip reads: ``zoom_reader`` when set; else the page reader, at ``zoom_effort`` when that
    option is set (the strips only vote on characters, the page read keeps its own effort). The disk
    cache of test servers does not key on effort, so a spec with a sample gets the effort added to the sample."""
    if stage.opt("zoom_reader"):
        return str(stage.opt("zoom_reader"))
    spec, effort = stage.ctx_reader_spec, stage.opt("zoom_effort")
    if not effort or not spec or "#" not in spec:
        return spec
    head, _, rest = spec.partition("#")
    old, at, sample = rest.partition("@")
    if old == str(effort):
        return spec
    return f"{head}#{effort}" + (f"@{sample}_{effort}" if at else "")


def eligible(stage, doc: Document, page: Page) -> Optional[dict]:
    """What ``_zoom_vote`` checks before it looks at the page read; None when the page gets no strips."""
    from ...readers import sizing
    from . import zoom
    if page.properties.get("text_layer_exact"):
        return None
    if doc.file_type == "pdf" and page.kind != "scanned":
        return None
    src = doc.meta.get("path")
    image_path = None
    try:
        if doc.file_type == "pdf":
            z = zoom.source_zoom(src, page.original_page, doc.meta.get("password"))
        else:
            from PIL import Image
            image_path = next(iter(page.renders.values()), None) or src
            with Image.open(image_path) as im:
                z = 1.0 / max(sizing.native_scale(*im.size), 1e-6)
    except Exception:
        return None
    if z < float(stage.opt("zoom_min", 0.0)):
        return None
    return {"src": src, "image_path": image_path, "cap": float(stage.opt("zoom_max", 3.0))}


def read_set(stage, doc: Document, page: Page, n: int, tag: str, info: dict) -> list:
    """The ``n`` strip reads of one set, in strip order (same calls as ``_zoom_vote``)."""
    from ...readers import prompts, sizing
    from . import zoom
    reader = stage._reader(reader_spec(stage))

    def one(item):
        i, box = item
        if doc.file_type == "pdf":
            data = sizing.render_pdf_native(info["src"], page.original_page, box=box, crop_zoom=info["cap"],
                                            password=doc.meta.get("password"))
        else:
            data = sizing.resize_image_native(info["image_path"], box=box, max_scale=info["cap"])
        return reader.read(data, None, ReadContext(stem=f"{doc.stem}#{tag}{i}", page_index=page.index,
                                                   prompt=prompts.STRIP_PROMPT_FILE))
    from . import zoom_regions  # the same cut as ``_zoom_vote`` (table regions only when switched on)
    cut = zoom_regions.boxes(page, n, stage.opt("zoom_regions"), int(stage.opt("zoom_strips", 3)))
    futures = [pool().submit(one, item) for item in enumerate(cut)]
    return [f.result() for f in futures]


def start(stage, doc: Document, page: Page) -> dict[str, Future]:
    """Launch the strip sets in the background. {tag: future of the list of results}."""
    level = int(stage.opt("zoom_prefetch", 0) or 0)
    if level <= 0:
        return {}
    info = eligible(stage, doc, page)
    if info is None:
        return {}
    n = int(stage.opt("zoom_strips", 3))
    out: dict[str, Future] = {}
    runner = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stripset")
    out["za"] = runner.submit(read_set, stage, doc, page, n, "za", info)
    if level >= 2:
        out["zb"] = runner.submit(read_set, stage, doc, page, n + 1, "zb", info)
    runner.shutdown(wait=False)
    page.properties["zoom_prefetch"] = sorted(out)
    return out


def settle(stage, doc: Document, page: Page, pending: dict[str, Future]) -> None:
    """Strip sets that were started and not consumed by the zoom vote: wait, and bill them."""
    for tag, fut in list(pending.items()):
        pending.pop(tag, None)
        try:
            results = fut.result()
        except Exception:
            continue
        for res in results:
            doc.usage.append({"page": page.index, "reader": getattr(res.usage, "model", None) or "zoom",
                              "escalation": "zoom_strip_unused", "reason": tag,
                              **res.usage.model_dump(exclude_none=True)})
        page.properties.setdefault("zoom_prefetch_unused", []).append(tag)
