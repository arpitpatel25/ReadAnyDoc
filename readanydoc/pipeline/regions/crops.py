"""Full-resolution crops of layout regions and model reads of them (one call per crop, in parallel)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from ...model import Document, Page
from ...readers import make_reader
from ...readers.base import ReadContext, Reader, ReadResult
from ...readers.prompts import FORM_RULE, TABLE_CROP_STRICT_FILE
from ..reading.robust import detect_problems
from .match import Box

TABLE_PROMPT = TABLE_CROP_STRICT_FILE
_EXACT = ("Output every character exactly as printed (do not summarize, translate, fix spelling, or reformat numbers "
          "and dates); transcribe handwriting as written and write [illegible] where it cannot be read; never guess "
          "or add text that is not visible. ")
REGION_PROMPT = (
    "Read the image at {img}. It is a crop of one block of a document page (a paragraph, heading, list, caption, "
    "header or footer). Transcribe it to Markdown in natural reading order. " + _EXACT +
    "Start a heading with '#' marks only when the block is a heading; write math as LaTeX; render a table as an HTML "
    "<table>. Lines that are cut off at the edge of the crop belong to a neighbouring block: leave them out. "
    "If the crop shows no text, output nothing. Output only the Markdown, with no preamble and no code fences.")
FORM_PROMPT = (
    "Read the image at {img}. It is a crop of a fill-in form area of a document page. Transcribe it to Markdown in "
    "natural reading order. " + FORM_RULE + _EXACT +
    "Show a checkbox as [x] when it is checked and [ ] when it is not. Text that is cut off at the edge of the crop "
    "must be left out. Output only the Markdown, with no preamble and no code fences.")
FIGURE_PROMPT = (
    "Read the image at {img}. It is a crop of one figure of a document page (a photo, chart, diagram, logo or stamp). "
    "Describe it in one short line. If it contains printed text that a reader needs (labels, values, captions), "
    "add that text exactly as printed on the following lines. "
    "Output only the Markdown, with no preamble and no code fences.")

_readers: dict[str, Reader] = {}

# two steps of one page (the guarded form crops and the form evidence read) ask for
# the same crop with the same prompt. Inside ``shared_reads(page)`` such a read is made once and used by both, so a
# region is never paid twice and the two steps cannot see two different reads of it.
_SHARED: dict[int, dict] = {}
_SHARED_LOCK = threading.Lock()


@contextmanager
def shared_reads(page: Page):
    with _SHARED_LOCK:
        _SHARED[id(page)] = {}
    try:
        yield
    finally:
        with _SHARED_LOCK:
            _SHARED.pop(id(page), None)


def reader_for(spec: str) -> Reader:
    if spec not in _readers:
        _readers[spec] = make_reader(spec)
    return _readers[spec]


def crop_png(doc: Document, page: Page, box: Box, pad: float = 0.012, zoom: Optional[float] = None,
             max_dpi: float = 400.0) -> bytes:
    """PNG of a region at the largest size the model keeps (``zoom`` limits it to that multiple of the whole-page
    scale). A PDF is rendered again at the crop's own resolution; a raster is cut and enlarged up to 2x."""
    from ...readers.sizing import pad_box, render_pdf_native, resize_image_native
    b = pad_box(tuple(box), pad)
    if doc.file_type == "pdf":
        return render_pdf_native(doc.meta["path"], page.original_page, b, crop_zoom=zoom, max_dpi=max_dpi,
                                 password=doc.meta.get("password"))
    src = None
    for key, p in page.renders.items():
        if key != "native" and Path(p).exists():
            src = p
            break
    src = src or doc.meta.get("path")
    if not src:
        raise RuntimeError("no page image to crop")
    return resize_image_native(src, b, max_scale=2.0)


def _budget_step(tag: str) -> str:
    return ("table" if tag.startswith("tread") else "form" if tag.startswith("form:")
            else "formula" if tag.startswith("formula") else "region")


def _account(doc: Document, page: Page, reader: Reader, res: ReadResult, tag: str, **more) -> Optional[ReadResult]:
    """Records a crop read in ``doc.usage`` and drops a broken answer (refusal, loop, cut off)."""
    doc.usage.append({"page": page.index, "reader": reader.name, "escalation": "region", "reason": tag, **more,
                      **res.usage.model_dump(exclude_none=True)})
    if res.refused or res.truncated or any(p.fatal for p in detect_problems(res.text, res.stop_reason, res.refused,
                                                                            res.truncated) if p.kind != "empty"):
        doc.note("regions", f"page {page.index}: crop read ({tag}) is broken; ignored", "warning")
        return None
    return res


def read_crop(doc: Document, page: Page, box: Box, prompt: str, reader_spec: str, tag: str,
              pad: float = 0.012, zoom: Optional[float] = None) -> Optional[ReadResult]:
    """One model call on the crop. None when the call fails or the answer is broken (refusal, loop, cut off).
    The call is recorded in ``doc.usage`` (replayed calls keep their original tokens, flagged ``cached``).
    a read that was started with the page read (``prefetch``) is waited for instead of made again; with
    a per-page spending cap (``reading/budget.py``) a read that no longer fits is skipped."""
    reader = reader_for(reader_spec)
    fut = _take_prefetched(doc, page, box, prompt, reader_spec, pad) if zoom is None else None
    if fut is not None:
        try:
            res = fut.result()
        except Exception as e:
            doc.note("regions", f"page {page.index}: prefetched crop read ({tag}) unavailable ({str(e)[:120]})", "warning")
            return None
        return _account(doc, page, reader, res, tag, prefetched=True)
    from ..reading import budget
    if not budget.allow(doc, page, _budget_step(tag), tag=tag):
        return None
    try:
        image = crop_png(doc, page, box, pad=pad, zoom=zoom)
        res = reader.read(image, None, ReadContext(stem=f"ws1:{tag}:{doc.stem or doc.id}", page_index=page.index,
                                                   prompt=prompt))
    except Exception as e:
        doc.note("regions", f"page {page.index}: crop read ({tag}) unavailable ({str(e)[:120]})", "warning")
        return None
    return _account(doc, page, reader, res, tag, prefetched=False)


# Crop reads started before the page read (reading/form_prefetch.py). Same crop, same prompt,
# same reader as the step that later asks for it, so the answer is the one that step would have got.
_PREFETCHED: dict[tuple, tuple] = {}
_PREFETCH_LOCK = threading.Lock()
_PREFETCH_POOL: Optional[ThreadPoolExecutor] = None


def _pkey(doc: Document, page: Page, box: Box, prompt: str, reader_spec: str, pad: float) -> tuple:
    return (id(doc), page.index, tuple(round(float(v), 5) for v in box), prompt, reader_spec, pad)


def prefetch(doc: Document, page: Page, jobs: list[tuple[Box, str, str]], reader_spec: str, pad: float = 0.012) -> int:
    """Starts the crop reads of ``jobs`` (box, prompt, tag) in the background. Returns how many were started."""
    global _PREFETCH_POOL
    import os
    n = 0
    for box, prompt, tag in jobs:
        k = _pkey(doc, page, box, prompt, reader_spec, pad)
        with _PREFETCH_LOCK:
            if k in _PREFETCHED:
                continue
            if _PREFETCH_POOL is None:
                _PREFETCH_POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("READANYDOC_REGION_WORKERS", "16")),
                                                    thread_name_prefix="regionpre")

            def call(box=box, prompt=prompt, tag=tag):
                image = crop_png(doc, page, box, pad=pad)
                return reader_for(reader_spec).read(image, None, ReadContext(
                    stem=f"ws1:{tag}:{doc.stem or doc.id}", page_index=page.index, prompt=prompt))
            _PREFETCHED[k] = (_PREFETCH_POOL.submit(call), reader_spec, tag)
        n += 1
    return n


def _take_prefetched(doc: Document, page: Page, box: Box, prompt: str, reader_spec: str, pad: float):
    with _PREFETCH_LOCK:
        hit = _PREFETCHED.pop(_pkey(doc, page, box, prompt, reader_spec, pad), None)
    return hit[0] if hit else None


def settle_prefetched(doc: Document) -> int:
    """Reads that were started and never asked for: wait for them and bill them (``form_prefetch_unused``)."""
    with _PREFETCH_LOCK:
        mine = [k for k in _PREFETCHED if k[0] == id(doc)]
        left = [(k, _PREFETCHED.pop(k)) for k in mine]
    n = 0
    for k, (fut, reader_spec, tag) in left:
        try:
            res = fut.result()
        except Exception:
            continue
        doc.usage.append({"page": k[1], "reader": reader_for(reader_spec).name, "escalation": "region",
                          "reason": f"form_prefetch_unused:{tag}", **res.usage.model_dump(exclude_none=True)})
        n += 1
        for page in doc.pages:
            if page.index == k[1]:
                page.properties["form_prefetch_unused"] = page.properties.get("form_prefetch_unused", 0) + 1
    return n


def read_many(doc: Document, page: Page, jobs: list[tuple[Box, str, str]], reader_spec: str,
              workers: int = 8, pad: float = 0.012) -> list[Optional[ReadResult]]:
    """``jobs``: (box, prompt, tag). All crops of a page are read together. Inside ``shared_reads(page)`` a crop
    that was already read with the same prompt is not read (or billed) again."""
    if not jobs:
        return []
    memo = _SHARED.get(id(page))
    keys = [(tuple(round(float(v), 5) for v in j[0]), j[1], reader_spec, pad) for j in jobs]
    todo = [i for i, k in enumerate(keys) if memo is None or k not in memo]
    out: dict[int, Optional[ReadResult]] = {}
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as ex:
            for i, res in zip(todo, ex.map(lambda i: read_crop(doc, page, jobs[i][0], jobs[i][1], reader_spec,
                                                               jobs[i][2], pad=pad), todo)):
                out[i] = res
                if memo is not None:
                    memo[keys[i]] = res
    reused = len(jobs) - len(todo)
    if reused:
        page.properties["region_reads_shared"] = page.properties.get("region_reads_shared", 0) + reused
    return [out[i] if i in out else memo[keys[i]] for i in range(len(jobs))]
