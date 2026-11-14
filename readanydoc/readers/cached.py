"""Replay cached model outputs from ``spike/`` (offline dev, deterministic tests)."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Optional

from .. import paths
from ..model import BBox, Confidence, RegionType, Word
from .base import ImageInput, ReadContext, Reader, ReaderError, ReadResult, Usage, WordBoxProvider

#: systems with page-level markdown under spike/out/<system>/<stem>.md
SYSTEMS = ("haiku", "glm_pdf", "glm_gpu", "pvl16", "pvl16_pdf", "reducto_r1", "reducto_r1mini")


class CachedReader(Reader):
    """Page-level markdown exactly as the model produced it during the spike."""

    granularity = "page"
    needs_image = False

    def __init__(self, system: str = "haiku"):
        self.system = system
        self.name = f"cached:{system}"
        self.dir = paths.spike_dir() / "out" / system
        if not self.dir.is_dir():
            raise ReaderError(f"no cached outputs for system {system!r} at {self.dir}")

    def has(self, stem: str) -> bool:
        return (self.dir / f"{stem}.md").exists()

    def read(self, image: ImageInput = None, hint: Optional[RegionType] = None,
             context: Optional[ReadContext] = None) -> ReadResult:
        if context is None or not context.stem:
            raise ReaderError("CachedReader needs context.stem")
        if context.bbox is not None:
            raise ReaderError("CachedReader only has page-level outputs (no region crops)")
        f = self.dir / f"{context.stem}.md"
        if not f.exists():
            raise ReaderError(f"no cached {self.system} output for {context.stem!r}")
        usage = Usage(model=self.system, cached=True)
        meta = self.dir / f"{context.stem}.md.meta.json"
        if meta.exists():
            m = json.loads(meta.read_text())
            usage = Usage(model=self.system, cached=True, input_tokens=m.get("in"),
                          output_tokens=m.get("out"), cost_usd=m.get("cost"), seconds=m.get("secs"))
        return ReadResult(text=f.read_text(), format="markdown", usage=usage, stop_reason="end_turn")


@lru_cache(maxsize=512)
def _load_ppocr(stem: str) -> tuple[dict, ...] | None:
    f = paths.spike_dir() / "ocr_pp" / f"{stem}_res.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text())
    out: list[dict] = []
    n = len(d.get("rec_texts", []))
    for i in range(n):
        line_text = d["rec_texts"][i]
        score = float(d["rec_scores"][i]) if i < len(d.get("rec_scores", [])) else None
        wtexts = d.get("text_word", [[]] * n)[i] if i < len(d.get("text_word", [])) else []
        wboxes = d.get("text_word_boxes", [[]] * n)[i] if i < len(d.get("text_word_boxes", [])) else []
        pairs = [(t, b) for t, b in zip(wtexts, wboxes) if t and t.strip()]
        if not pairs and line_text.strip():  # scripts without word segmentation: the line is one word
            pairs = [(line_text, d["rec_boxes"][i])]
        for t, b in pairs:
            out.append({"text": t, "box": [float(v) for v in b], "line": i, "score": score})
    return tuple(out)


@lru_cache(maxsize=512)
def _image_size(stem: str) -> tuple[int, int] | None:
    """Pixel size of the 150-dpi page image the OCR ran on."""
    f = paths.pod_boxes_dir() / "out_pvl" / "json" / f"{stem}_res.json"
    if f.exists():
        d = json.loads(f.read_text())
        return int(d["width"]), int(d["height"])
    img = paths.bench_image(stem)
    if img:
        from PIL import Image
        with Image.open(img) as im:
            return im.size
    return None


class CachedPPOCR(WordBoxProvider):
    """PP-OCRv5 word boxes from ``spike/ocr_pp`` (150-dpi pixel coords -> normalised)."""

    name = "ppocr"

    def has(self, stem: str) -> bool:
        return _load_ppocr(stem) is not None

    def words(self, image: ImageInput = None, context: Optional[ReadContext] = None) -> list[Word]:
        if context is None or not context.stem:
            raise ReaderError("CachedPPOCR needs context.stem")
        raw = _load_ppocr(context.stem)
        size = _image_size(context.stem)
        if raw is None or size is None:
            return []
        w, h = size
        words = []
        for r in raw:
            x0, y0, x1, y1 = r["box"]
            conf = Confidence.of(r["score"]) if r["score"] is not None else Confidence()
            words.append(Word(text=r["text"], bbox=BBox.from_xyxy(x0, y0, x1, y1, context.page_index, w, h),
                              source=self.name, line=r["line"], confidence=conf))
        return words
