"""The document for one-stage extraction (``extract(..., parse=False)``), built with no model call.

The model reads the file itself (a PDF as a document, an image as images). For citations, code needs every word
of every page with its position. They come from the free sources two-stage parsing also uses:

- a PDF page with a usable text layer: the PDF's own words (exact positions);
- a scanned PDF page, an image or a photo: local OCR (Tesseract, then PP-OCR for what it misses);
- Office files, text, email, spreadsheets: read as they are (their text needs no model either), and the model is
  given that text.

Handwriting read by local OCR is often wrong or missing: such values are extracted (the model reads the page)
but may get no citation box.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any, Optional

from .model import BBox, Confidence, Document, Line, Page, Region, RegionType, Word

OCR_SPEC = "tesseract>rapidocr"   # the same word sources as the default (engine) profile
_OCR_ORDER = ("ppocr", "rapidocr", "tesseract", "rapidocr_fill")
MIN_TEXT_WORDS = 5  # a PDF page with fewer words in its text layer is treated as a scan


def page_count(path: str) -> int:
    import pypdfium2 as pdfium
    return len(pdfium.PdfDocument(path))


def _lines(words: list[Word]) -> list[list[Word]]:
    """Words grouped into printed lines: by the source's own line number, else by vertical position."""
    words = [w for w in words if w.text and w.bbox is not None]
    if words and all(w.line is not None for w in words):
        by: dict[int, list[Word]] = {}
        for w in words:
            by.setdefault(w.line, []).append(w)
        return [sorted(by[k], key=lambda w: w.bbox.left) for k in sorted(by)]
    out: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.bbox.top + w.bbox.height / 2, w.bbox.left)):
        cy = w.bbox.top + w.bbox.height / 2
        if out:
            last = out[-1][-1].bbox
            if abs((last.top + last.height / 2) - cy) <= max(last.height, w.bbox.height) * 0.5:
                out[-1].append(w)
                continue
        out.append([w])
    return [sorted(ln, key=lambda w: w.bbox.left) for ln in out]


def _regions(page: Page, words: list[Word], source: str) -> None:
    for order, ln in enumerate(_lines(words)):
        text, placed, pos = [], [], 0
        for w in ln:
            if text:
                pos += 1
            placed.append(Word(text=w.text, bbox=w.bbox, source=source, char_start=pos, char_end=pos + len(w.text)))
            text.append(w.text)
            pos += len(w.text)
        content = " ".join(text)
        page.regions.append(Region(type=RegionType.TEXT, order=order, content=content, text=content,
                                   bbox=BBox.union([w.bbox for w in ln]), lines=[Line(text=content, words=placed)],
                                   confidence=Confidence.of(0.9 if source == "pdf" else 0.7)))


def _ocr(image: Any, index: int) -> list[Word]:
    from .readers import make_word_provider
    from .readers.base import ReadContext
    provider = make_word_provider(OCR_SPEC)
    words = list(provider.words(image, ReadContext(page_index=index)) or [])
    extra = getattr(provider, "extra_words", None)
    if extra is not None:
        try:
            for ws in (extra(image, ReadContext(page_index=index)) or {}).values():
                placed = [w.bbox for w in words if w.bbox is not None]
                words += [w for w in ws if w.bbox is not None and not any(_overlap(w.bbox, b) for b in placed)]
        except Exception:  # noqa: BLE001 - a second OCR only fills gaps
            pass
    return words


def _overlap(a: BBox, b: BBox) -> bool:
    x = min(a.left + a.width, b.left + b.width) - max(a.left, b.left)
    y = min(a.top + a.height, b.top + b.height) - max(a.top, b.top)
    return x > 0 and y > 0 and x * y > 0.3 * min(a.width * a.height, b.width * b.height)


def _render(path: str, number: int, dpi: int = 200) -> str:
    """A PNG of one PDF page (disk-cached), for OCR of a scanned page."""
    import pypdfium2 as pdfium
    from . import paths
    p = Path(path)
    key = hashlib.sha1(f"{p.resolve()}|{p.stat().st_mtime_ns}|{number}|{dpi}".encode()).hexdigest()[:20]
    out = paths.cache_dir() / "renders" / f"onestage_{key}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        pdfium.PdfDocument(str(p))[number - 1].render(scale=dpi / 72).to_pil().convert("RGB").save(out)
    return str(out)


def one_stage_document(path: str, pages: Optional[list[int]] = None) -> tuple[Document, dict]:
    """(document with every word placed, what the model is given: {"pdf": path} | {"images": {page: file}} |
    {} for text the model reads as rendered text)."""
    from .intake.sniff import IMAGE_KINDS, sniff
    from .pipeline.triage import pdf_words

    kind = sniff(Path(path))
    if kind == "pdf":
        total = page_count(path)
        wanted = [n for n in (pages or range(1, total + 1)) if 1 <= n <= total]
        doc = Document(source=str(path), file_type="pdf")
        doc.meta["path"] = str(path)
        for idx, number in enumerate(wanted):
            words, info = pdf_words(str(path), number, idx)
            scanned = len(words) < MIN_TEXT_WORDS or bool(info.get("big_image") and len(words) < 40)
            page = Page(index=idx, original_page=number, width=612, height=792, kind="scanned" if scanned else "digital")
            if scanned:
                words = _ocr(_render(path, number), idx)
            _regions(page, words, "ocr" if scanned else "pdf")
            doc.pages.append(page)
        doc.meta["one_stage"] = {"pages": len(wanted), "ocr_pages": sum(p.kind == "scanned" for p in doc.pages)}
        return doc, {"pdf": str(path)}
    # anything else: the pipeline's own intake (conversions, image frames, Office files); no model call
    from .pipeline import Pipeline
    from .profiles import get_profile
    cfg = get_profile("engine")
    for name in ("triage", "layout", "read", "verify", "ground", "assemble", "shape"):
        cfg = cfg.with_stage(name, enabled=False)
    if pages:
        from .pipeline.request import apply_parse_request
        cfg = apply_parse_request(cfg, {"settings": {"page_range": list(pages)}})
    doc = Pipeline(cfg).run(str(path))
    if doc.file_type in IMAGE_KINDS or doc.file_type == "png":
        images = {}
        for page in doc.pages:
            render = next(iter(page.renders.values()), None)
            if render is None:
                continue
            page.regions = []
            _regions(page, _ocr(render, page.index), "ocr")
            page.kind = "scanned"
            images[page.original_page] = render
        doc.meta["one_stage"] = {"pages": len(doc.pages), "ocr_pages": len(images)}
        return doc, {"images": images}
    doc.meta["one_stage"] = {"pages": len(doc.pages), "ocr_pages": 0}
    return doc, {}  # native text (Word, Excel, email...): the model reads it as text; citations use its blocks


def document_from_text_layer(path: str, pages: Optional[list[int]] = None) -> Document:
    """The document of a PDF from its text layer alone (kept for callers of the first version)."""
    return one_stage_document(path, pages)[0]


def pdf_subset(path: str, numbers: list[int]) -> bytes:
    """A PDF of only these pages (1-based), for sending one part of a long document to the model."""
    import pypdfium2 as pdfium
    src = pdfium.PdfDocument(path)
    out = pdfium.PdfDocument.new()
    out.import_pages(src, [n - 1 for n in numbers])
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()
