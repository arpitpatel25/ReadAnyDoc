"""Layout detections of a page (plain dicts) and geometry between them and the page's blocks."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

from ...model import BBox, Document, Page, Region

Box = tuple[float, float, float, float]
CONTAINER_LABELS = {"form", "key_value_region", "picture", "image", "chart", "table", "document_index"}
MARK_LABELS = {"checkbox_selected", "checkbox_unselected"}
FORM_LABELS = {"form", "key_value_region"}
TABLE_LABELS = {"table"}
TEXT_LABELS = {"text", "list_item", "section_header", "title", "caption", "footnote", "code", "formula",
               "page_header", "page_footer",
               # PP-DocLayout names
               "paragraph_title", "doc_title", "abstract", "content", "reference", "reference_content", "aside_text",
               "figure_title", "header", "footer", "number", "algorithm"}


def box_of(b: BBox) -> Box:
    return (b.left, b.top, b.left + b.width, b.top + b.height)


def to_bbox(box: Box, page: int) -> BBox:
    return BBox(left=box[0], top=box[1], width=box[2] - box[0], height=box[3] - box[1], page=page)


def area(a: Box) -> float:
    return max(a[2] - a[0], 0.0) * max(a[3] - a[1], 0.0)


def inter(a: Box, b: Box) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    return iw * ih if iw > 0 and ih > 0 else 0.0


def iou(a: Box, b: Box) -> float:
    i = inter(a, b)
    u = area(a) + area(b) - i
    return i / u if u > 0 else 0.0


def inside(a: Box, b: Box) -> float:
    """Share of ``a`` that lies in ``b``."""
    A = area(a)
    return inter(a, b) / A if A > 0 else 0.0


# ---------------------------------------------------------------------------------------------- detection
def _layout_image(doc: Document, page: Page) -> Optional[bytes]:
    """A raster of the page for the detector, about 1100 px on the long edge (the models take 640-800 px)."""
    import io
    if doc.file_type == "pdf" and doc.meta.get("path"):
        import pypdfium2 as pdfium

        from ...readers.sizing import _PDFIUM_LOCK
        with _PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(doc.meta["path"]), password=doc.meta.get("password"))
            try:
                pg = pdf[page.original_page - 1]
                w, h = pg.get_size()
                im = pg.render(scale=1100.0 / max(w, h, 1.0)).to_pil()
            finally:
                pdf.close()
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    from ..grounding.sources import page_image
    p = page_image(doc, page)
    return Path(p).read_bytes() if p else None


def detect_pages(doc: Document, ctx) -> None:
    """Run the configured layout detector on every page; results in ``page.properties["layout_detections"]``.
    ``READANYDOC_LAYOUT_CACHE`` (a directory) keeps detections on disk by image hash, with the model seconds."""
    cache_dir = os.environ.get("READANYDOC_LAYOUT_CACHE")
    for page in doc.pages:
        try:
            data = _layout_image(doc, page)
            if data is None:
                raise RuntimeError("no page image")
            det = ctx.layout
            f = None
            if cache_dir:
                key = hashlib.sha1(data + f"|{det.name}|{getattr(det, 'threshold', '')}".encode()).hexdigest()[:24]
                f = Path(cache_dir) / f"{key}.json"
            if f is not None and f.exists():
                saved = json.loads(f.read_text())
                dets, secs = saved["detections"], saved.get("seconds")
            else:
                t0 = time.time()
                from ...readers.base import ReadContext
                blocks = det.detect(data, ReadContext(stem=doc.stem, page_index=page.index))
                secs = round(time.time() - t0, 3)
                dets = [{"id": f"d{i}", "label": (b.native_label or b.type.value).lower(), "type": b.type.value,
                         "score": b.score if b.score is not None else 1.0,
                         "box": [round(v, 5) for v in box_of(b.bbox)], "order": b.order} for i, b in enumerate(blocks)]
                _nest(dets)
                if f is not None:
                    f.parent.mkdir(parents=True, exist_ok=True)
                    tmp = f.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")  # another request may read f now
                    tmp.write_text(json.dumps({"detections": dets, "seconds": secs, "detector": det.name}))
                    tmp.replace(f)
            page.properties["layout_detections"] = dets
            page.properties["layout_detector"] = det.name
            page.properties["layout_seconds"] = secs
        except Exception as e:
            doc.note("layout", f"page {page.index}: layout unavailable ({str(e)[:160]})", "warning")


def _nest(dets: list[dict]) -> None:
    for d in dets:
        d["inside"] = None
        best = None
        for c in dets:
            if c is d or c["label"] not in CONTAINER_LABELS:
                continue
            if inside(tuple(d["box"]), tuple(c["box"])) >= 0.8 and area(tuple(c["box"])) > area(tuple(d["box"])):
                if best is None or area(tuple(c["box"])) < area(tuple(best["box"])):
                    best = c
        if best is not None:
            d["inside"] = best["id"]


# ---------------------------------------------------------------------------------------------- access
def detections(page: Page, labels: Optional[Iterable[str]] = None, min_score: float = 0.3) -> list[dict]:
    want = set(labels) if labels is not None else None
    return [d for d in page.properties.get("layout_detections") or []
            if d["score"] >= min_score and (want is None or d["label"] in want)]


def form_regions(page: Page, min_score: float = 0.5) -> list[dict]:
    """Form-like regions (``form``, ``key_value_region``), outermost only."""
    regs = detections(page, FORM_LABELS, min_score)
    ids = {d["id"] for d in regs}
    return [d for d in regs if d.get("inside") not in ids]


def checkbox_marks(page: Page, box: Optional[Box] = None) -> list[dict]:
    marks = detections(page, MARK_LABELS, 0.3)
    return [m for m in marks if box is None or inside(tuple(m["box"]), box) >= 0.5]


def blocks_in(page: Page, box: Box, min_inside: float = 0.6) -> list[Region]:
    """Page blocks (located by Ground) whose box lies in ``box``."""
    return [r for r in page.regions if r.bbox is not None and inside(box_of(r.bbox), box) >= min_inside]


def top_level(dets: list[dict]) -> list[dict]:
    """Detections that are not inside a container and are not checkbox marks, in reading order
    (an XY cut over these boxes only, so nested boxes cannot disturb the column structure)."""
    from ...layout.onnx import reading_order
    top = [d for d in dets if not d.get("inside") and d["label"] not in MARK_LABELS]
    return [top[i] for i in reading_order([tuple(d["box"]) for d in top])]
