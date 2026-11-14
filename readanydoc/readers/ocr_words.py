"""Word-box providers backed by the open CPU OCR in ``pipeline/grounding/ocr.py`` (PP-OCR on ONNX Runtime).

``rapidocr``            PP-OCR words replace Tesseract everywhere: they are stored as ``page.words["ppocr"]``,
                        the name every stage already prefers. (Ground still adds Tesseract on pages it
                        covers badly.)
``tesseract+rapidocr``  Tesseract stays the OCR that triage and reading look at (``page.words["tesseract"]``),
                        so nothing upstream of Ground changes; PP-OCR words are added as
                        ``page.words["rapidocr"]`` and Ground tries them before Tesseract.
``tesseract>rapidocr``  the same, but PP-OCR words are stored as ``page.words["rapidocr_fill"]`` and Ground uses
                        them after Tesseract: only for the blocks Tesseract could not place. Measured best
                        (where both engines place a block, Tesseract's box is the tighter one).
"""

from __future__ import annotations

from typing import Optional

from .base import ReadContext, ReaderError


def _require() -> None:
    from ..pipeline.grounding import ocr
    if not ocr.available():
        raise ReaderError("the rapidocr word provider needs the optional OCR packages: pip install 'readanydoc[engine]'")


class RapidOcrWords:
    name = "ppocr"
    needs_image = True

    def __init__(self, model: str = ""):
        _require()
        self.model = model or None

    def words(self, image, context: Optional[ReadContext] = None):
        from ..pipeline.grounding.ocr import ocr_words
        if image is None:
            return []
        return ocr_words(image, context.page_index if context else 0, self.model, source="ppocr")


class TesseractPlusRapidOcr:
    name = "tesseract"
    needs_image = True

    def __init__(self, model: str = ""):
        _require()
        self.model = model or None

    def words(self, image, context: Optional[ReadContext] = None):
        from ..pipeline.grounding.sources import tesseract_words
        if image is None:
            return []
        return tesseract_words(image, context.page_index if context else 0)

    def extra_words(self, image, context: Optional[ReadContext] = None) -> dict:
        from ..pipeline.grounding.ocr import ocr_words
        if image is None:
            return {}
        return {"rapidocr": ocr_words(image, context.page_index if context else 0, self.model)}


class TesseractThenRapidOcr(TesseractPlusRapidOcr):
    def extra_words(self, image, context: Optional[ReadContext] = None) -> dict:
        from ..pipeline.grounding.ocr import ocr_words
        if image is None:
            return {}
        return {"rapidocr_fill": ocr_words(image, context.page_index if context else 0, self.model, source="rapidocr_fill")}
