"""Open OCR on CPU for word and line positions: PP-OCR through ONNX Runtime (RapidOCR).

Licences: RapidOCR code Apache-2.0; PP-OCR weights (Baidu / PaddleOCR) Apache-2.0; ONNX Runtime MIT;
OpenCV Apache-2.0. Optional dependency (``pip install 'readanydoc[engine]'``): everything here returns []
when the package is missing, and callers fall back to Tesseract.

    ocr_lines(image_path, page_index=0)  -> list[OcrLine]   text lines with boxes, each with its words
    ocr_words(image_path, page_index=0)  -> list[Word]      the same words flat, ``Word.line`` = line index

Boxes are normalised to the image (0-1, origin top left), axis-aligned. Results are cached on disk
under ``<cache>/rapidocr`` by image content and model. One engine per process, one
page at a time (the ONNX session already uses every core): every engine call, loading included, runs on the
process's OCR worker thread (``readanydoc.localmodels.ocr``).

Model: ``READANYDOC_OCR_MODEL`` = ``v6-small`` (default; PP-OCRv6 small, multilingual, shipped inside
the rapidocr wheel, no download) | ``v5-mobile`` (PP-OCRv5 mobile detector + Latin recogniser,
downloaded on first use) | ``v6-tiny`` | ``v6-medium``.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ... import localmodels, paths
from ...model import BBox, Confidence, Word

_ENGINES: dict[str, Any] = {}
DEFAULT_MODEL = "v6-small"


@dataclass
class OcrLine:
    text: str
    bbox: BBox
    score: float
    words: list[Word] = field(default_factory=list)


def model_name() -> str:
    return os.environ.get("READANYDOC_OCR_MODEL") or DEFAULT_MODEL


def available() -> bool:
    try:
        import rapidocr  # noqa: F401
        return True
    except Exception:
        return False


def _engine(model: str, cls: bool = True):
    key = f"{model}|{int(cls)}"
    if key not in _ENGINES:
        from ... import cvsafe
        cvsafe.install()  # OpenCV 5's arm64 resize reads past the source image: see readanydoc/cvsafe.py
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
        p: dict[str, Any] = {"Global.log_level": "error", "Global.return_word_box": True, "Global.use_cls": cls}
        if model == "v5-mobile":
            p.update({"Det.ocr_version": OCRVersion.PPOCRV5, "Rec.ocr_version": OCRVersion.PPOCRV5,
                      "Det.model_type": ModelType.MOBILE, "Rec.model_type": ModelType.MOBILE,
                      "Rec.lang_type": LangRec.LATIN})
        elif model in ("v6-tiny", "v6-medium"):
            mt = ModelType.TINY if model == "v6-tiny" else ModelType.MEDIUM
            p.update({"Det.model_type": mt, "Rec.model_type": mt})
        elif model != "v6-small":
            raise ValueError(f"unknown OCR model {model!r}")
        _ENGINES[key] = RapidOCR(params=p)
    return _ENGINES[key]


def _xyxy(quad) -> tuple[float, float, float, float]:
    xs = [float(p[0]) for p in quad]
    ys = [float(p[1]) for p in quad]
    return min(xs), min(ys), max(xs), max(ys)


def run_image(image: Any, model: Optional[str] = None, cls: bool = True) -> tuple[list[list], float]:
    """OCR of one image (path, bytes or numpy array), not cached. Rows in PIXELS of the given image:
    ``[text, score, x0, y0, x1, y1, [[word, score, x0, y0, x1, y1], ...]]``. Second value: seconds."""
    model = model or model_name()
    t0 = time.time()
    # every switch is passed on each call: the engine keeps the switches of its previous call otherwise
    res = localmodels.ocr.call(lambda: _engine(model, cls)(str(image) if isinstance(image, Path) else image, use_det=True,
                                                       use_cls=cls, use_rec=True, return_word_box=True))
    rows: list[list] = []
    txts = res.txts or ()
    words = res.word_results or ()
    for i, text in enumerate(txts):
        if not (text or "").strip():
            continue
        x0, y0, x1, y1 = _xyxy(res.boxes[i])
        ws = []
        for item in (words[i] if i < len(words) else ()):
            wt, wscore, quad = item[0], item[1], item[2]
            if not (wt or "").strip() or quad is None:
                continue
            a, b, c, d = _xyxy(quad)
            ws.append([wt, round(float(wscore), 4), a, b, c, d])
        rows.append([text, round(float(res.scores[i]), 4), x0, y0, x1, y1, ws])
    return rows, time.time() - t0


def detect_lines(image: Any, model: Optional[str] = None) -> list[list[list[float]]]:
    """Text-line quadrilaterals only (detector, no recognition): [[x, y] x 4] in pixels of the image."""
    res = localmodels.ocr.call(lambda: _engine(model or model_name())(image, use_det=True, use_cls=False, use_rec=False))
    boxes = getattr(res, "boxes", None)
    return [[[float(x), float(y)] for x, y in q] for q in boxes] if boxes is not None else []


def recognise(crops: list, model: Optional[str] = None) -> list[tuple[str, float]]:
    """Recognition only, one text-line crop (numpy array) per item: [(text, score)]."""
    def run() -> list[tuple[str, float]]:
        out = []
        eng = _engine(model or model_name())
        for c in crops:
            res = eng(c, use_det=False, use_cls=False, use_rec=True, return_word_box=False)
            txts = getattr(res, "txts", None) or ("",)
            scores = getattr(res, "scores", None) or [0.0]
            out.append((txts[0] or "", float(scores[0] or 0.0)))
        return out
    return localmodels.ocr.call(run)


def _cached_rows(image_path: str | Path, model: str) -> Optional[dict]:
    p = Path(image_path)
    if not p.exists():
        return None
    real = p.resolve()
    h = hashlib.sha1(real.read_bytes())  # by content: the same page uploaded twice (other server, other path) is read once
    h.update(f"|{model}".encode())
    key = h.hexdigest()[:24]
    cache = paths.cache_dir() / "rapidocr" / f"{key}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    if not available():
        return None
    from PIL import Image
    with Image.open(real) as im:
        W, H = im.size
    rows, sec = run_image(str(real), model)
    if not W or not H:
        return None
    for r in rows:
        r[2], r[3], r[4], r[5] = round(r[2] / W, 5), round(r[3] / H, 5), round(r[4] / W, 5), round(r[5] / H, 5)
        for w in r[6]:
            w[2], w[3], w[4], w[5] = round(w[2] / W, 5), round(w[3] / H, 5), round(w[4] / W, 5), round(w[5] / H, 5)
    d = {"model": model, "size": [W, H], "seconds": round(sec, 3), "lines": rows}
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(cache)
    return d


def _clip(v: float) -> float:
    return min(max(v, 0.0), 1.0)


def ocr_lines(image_path: str | Path, page_index: int = 0, model: Optional[str] = None,
              source: str = "rapidocr") -> list[OcrLine]:
    """Text lines of a page image in the engine's order (top-down), each with its words. [] when the
    OCR package is not installed or the image cannot be read."""
    try:
        d = _cached_rows(image_path, model or model_name())
    except Exception:
        return []
    out: list[OcrLine] = []
    for text, score, x0, y0, x1, y1, ws in (d or {}).get("lines", []):
        li = len(out)
        words = [Word(text=wt, bbox=BBox.from_xyxy(_clip(a), _clip(b), _clip(c), _clip(e), page_index), source=source,
                      line=li, confidence=Confidence.of(ws_score))
                 for wt, ws_score, a, b, c, e in ws if c > a and e > b]
        if not words:  # no word split for this line: the line as one word keeps its text usable
            words = [Word(text=text, bbox=BBox.from_xyxy(_clip(x0), _clip(y0), _clip(x1), _clip(y1), page_index),
                          source=source, line=li, confidence=Confidence.of(score))]
        out.append(OcrLine(text=text, bbox=BBox.from_xyxy(_clip(x0), _clip(y0), _clip(x1), _clip(y1), page_index),
                           score=float(score), words=words))
    return out


def ocr_words(image_path: str | Path, page_index: int = 0, model: Optional[str] = None,
              source: str = "rapidocr") -> list[Word]:
    """Words of a page image with boxes, confidence and line index. [] when unavailable."""
    return [w for ln in ocr_lines(image_path, page_index, model, source) for w in ln.words]


def ocr_seconds(image_path: str | Path, model: Optional[str] = None) -> Optional[float]:
    """Seconds the engine took for this image when it was first read (None if never read)."""
    try:
        d = _cached_rows(image_path, model or model_name())
    except Exception:
        return None
    return (d or {}).get("seconds")
