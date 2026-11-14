"""Stage 2, Triage: per-page properties that drive routing.

Loads the page word sources (PDF text layer via pdfplumber, cached PP-OCR words), scores the text
layer against OCR, classifies digital/scanned, guesses script and density.

Page signals added for reading (all in ``Page.properties``; ``text_layer_usable`` keeps its meaning):
- ``text_layer_class``: ``none`` | ``trustworthy`` (born-digital layer that agrees with OCR) |
  ``ocr_layer`` (a scan with an OCR text layer that agrees with our OCR: usable as words, but its
  characters are another OCR's guesses) | ``stale_ocr`` (a scan whose layer disagrees) |
  ``garbage`` (digital layer that disagrees: glyph codes, wrong encoding) | ``unchecked``.
- ``text_layer_exact``: True only for ``trustworthy``; only then may characters be copied from it.
- ``ocr_confidence`` (mean PP-OCR word score), ``handwriting_likely``, ``rotation_suspect``
  (90 when most OCR words are taller than wide; 180 cannot be seen from boxes), ``words_per_page``.
Stubbed: photo detection, language id, pixel-based rotation.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path

from .. import paths
from ..model import BBox, Document, Page, Word
from ..readers.base import ReadContext
from ..textnorm import agreement, guess_script, norm_tokens
from .base import PipelineContext, Stage, register_stage


def pdf_words(path: str, page_number: int, page_index: int) -> tuple[list[Word], dict]:
    """Words of the PDF text layer, normalised. Disk-cached (pdfplumber is the slow part)."""
    p = Path(path)
    key = hashlib.sha1(f"{p.resolve()}|{p.stat().st_mtime_ns}|{page_number}".encode()).hexdigest()[:20]
    cache = paths.cache_dir() / "pdfwords" / f"{key}.json"
    if cache.exists():
        d = json.loads(cache.read_text())
    else:
        import logging

        import pdfplumber
        logging.getLogger("pdfminer").setLevel(logging.ERROR)
        d = {"words": [], "big_image": False, "n_chars": 0}
        try:
            with pdfplumber.open(str(p)) as pdf:
                pg = pdf.pages[page_number - 1]
                W, H = float(pg.width), float(pg.height)
                d["n_chars"] = len(pg.chars)
                area = max((float(im["width"]) * float(im["height"]) for im in pg.images), default=0.0)
                d["big_image"] = bool(W and H and area / (W * H) >= 0.7)
                line, prev_top = -1, None
                for w in pg.extract_words(keep_blank_chars=False, use_text_flow=False):
                    if prev_top is None or abs(w["top"] - prev_top) > 3:
                        line += 1
                    prev_top = w["top"]
                    d["words"].append([w["text"], w["x0"] / W, w["top"] / H, w["x1"] / W, w["bottom"] / H, line])
        except Exception as e:  # damaged text layer: treat as no layer
            d["error"] = str(e)[:200]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(d))
    words = [Word(text=t, bbox=BBox.from_xyxy(x0, y0, x1, y1, page_index), source="pdf", line=ln)
             for t, x0, y0, x1, y1, ln in d["words"]]
    return words, d


@register_stage("triage")
class Triage(Stage):
    def run(self, doc: Document, ctx: PipelineContext) -> Document:
        usable_at = float(self.opt("usable_at", 0.8))
        prep = self.opt("prepare", False)  # off | "rotate" (or True) | "deskew" | "rotate+deskew"
        if prep:
            try:
                from ..intake.prepare import prepare_document
                mode = "rotate" if prep is True else str(prep)
                rep = prepare_document(doc, rotate="rotate" in mode, deskew="deskew" in mode,
                                       method=str(self.opt("prepare_method", "auto")),
                                       deskew_min=float(self.opt("deskew_min", 1.0)))
                doc.meta["prepare"] = rep
                for n, r in rep["pages"].items():
                    if r.get("turn") or r.get("skew"):
                        doc.note(self.name, f"page {n}: turned {r.get('turn', 0)} degrees, skew {r.get('skew', 0)} "
                                            f"corrected (by {r.get('by')})")
            except Exception as e:
                doc.note(self.name, f"page preparation failed: {e}", "warning")
        for page in doc.pages:
            info: dict = {}
            if doc.file_type == "pdf":
                words, info = pdf_words(doc.meta["path"], page.original_page, page.index)
                if words:
                    page.words["pdf"] = words
            img = None
            if getattr(ctx.words, "needs_image", False):  # live OCR (Tesseract): any document
                ocr = []
                try:
                    from .grounding.sources import page_image
                    img = page_image(doc, page) or self._render(doc, page)
                    ocr = ctx.words.words(img, ReadContext(stem=doc.stem, page_index=page.index)) if img else []
                except Exception as e:
                    doc.note(self.name, f"word provider failed: {e}", "warning")
                if ocr:
                    page.words[ctx.words.name] = ocr
            elif doc.stem and len(doc.pages) == 1:  # cached OCR is keyed by single-page stems
                try:
                    ocr = ctx.words.words(None, ReadContext(stem=doc.stem, page_index=page.index))
                except Exception as e:
                    ocr = []
                    doc.note(self.name, f"word provider failed: {e}", "warning")
                if ocr:
                    page.words["ppocr"] = ocr
            self._classify(page, info, usable_at)
            # a second OCR that only Ground uses; skipped where the PDF text layer is trusted:
            # its word positions are exact and the second OCR costs seconds of CPU per page
            more = getattr(ctx.words, "extra_words", None) if getattr(ctx.words, "needs_image", False) else None
            if more is not None and img and not page.text_layer_usable:
                try:
                    for name, ws in more(img, ReadContext(stem=doc.stem, page_index=page.index)).items():
                        if ws:
                            page.words[name] = ws
                except Exception as e:
                    doc.note(self.name, f"extra word provider failed: {e}", "warning")
        return doc

    def _render(self, doc: Document, page: Page) -> str | None:
        """A raster of a PDF page for live OCR (disk-cached by file, mtime and page)."""
        if doc.file_type != "pdf":
            return None
        dpi = int(self.opt("ocr_dpi", 200))
        p = Path(doc.meta["path"])
        key = hashlib.sha1(f"{p.resolve()}|{p.stat().st_mtime_ns}|{page.original_page}|{dpi}".encode()).hexdigest()[:20]
        out = paths.cache_dir() / "renders" / f"ocr_{key}.png"
        if not out.exists():
            from .intake import render_page
            out.parent.mkdir(parents=True, exist_ok=True)
            render_page(p, page.original_page, dpi).convert("RGB").save(out)
        page.renders.setdefault(str(dpi), str(out))
        return str(out)

    def _classify(self, page: Page, info: dict, usable_at: float) -> None:
        pdf = page.words.get("pdf", [])
        ocr = page.words.get("ppocr") or page.words.get("tesseract") or []
        pdf_tokens = norm_tokens(" ".join(w.text for w in pdf))
        ocr_tokens = norm_tokens(" ".join(w.text for w in ocr))
        if pdf_tokens and ocr_tokens:
            page.text_layer_quality = round(agreement(pdf_tokens, ocr_tokens), 4)
            page.text_layer_usable = page.text_layer_quality >= usable_at
        elif pdf_tokens:
            page.text_layer_quality = None  # nothing to check it against: do not trust blindly
            page.text_layer_usable = False
        else:
            page.text_layer_quality = 0.0 if page.unit == "pt" else None
            page.text_layer_usable = False
        if page.unit == "px" or info.get("big_image") or not pdf_tokens:
            page.kind = "scanned"
        else:
            page.kind = "digital"
        sample = pdf if page.text_layer_usable else (ocr or pdf)
        page.script = guess_script(" ".join(w.text for w in sample[:400]))
        heights = [w.bbox.height for w in sample if w.bbox and w.bbox.height > 0]
        med = statistics.median(heights) if heights else None
        conf = [w.confidence.score for w in ocr if w.confidence.score is not None]
        mean_conf = round(statistics.mean(conf), 4) if conf else None
        has_layer = bool(pdf_tokens)
        if not has_layer:
            layer_class = "none"
        elif page.text_layer_quality is None:
            layer_class = "unchecked"
        elif page.kind == "scanned":
            layer_class = "ocr_layer" if page.text_layer_usable else "stale_ocr"
        else:
            layer_class = "trustworthy" if page.text_layer_usable else "garbage"
        long_words = [w for w in ocr if w.bbox and len(w.text) >= 4 and w.bbox.width > 0]
        ratio = (page.width / page.height) if page.width and page.height else 1.0
        tall = sum(1 for w in long_words if w.bbox.height / ratio > 1.5 * w.bbox.width)
        rotated = len(long_words) >= 10 and tall / len(long_words) > 0.6
        page.properties.update({
            "text_layer_class": layer_class,
            "text_layer_exact": layer_class == "trustworthy",
            "ocr_confidence": mean_conf,
            "handwriting_likely": bool(page.kind == "scanned" and mean_conf is not None and len(ocr) >= 5
                                       and mean_conf < float(self.opt("handwriting_below", 0.85))),
            "rotation_suspect": 90 if rotated else 0,
            "words_per_page": len(sample),
            "n_pdf_words": len(pdf), "n_ocr_words": len(ocr),
            "has_text_layer": bool(pdf_tokens), "big_image": bool(info.get("big_image")),
            "median_word_height": round(med, 5) if med else None,
            "tiny_text": bool(med and med < 0.008),
            "dense": len(sample) > 900,
        })
