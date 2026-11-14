"""Page preparation before reading: turn pages upright (90 / 180 / 270 degrees) and remove skew.

No model call. Three sources of evidence, cheapest first:
1. PDF text layer: the direction in which the page's characters are written (exact, free).
2. Image EXIF orientation (photos).
3. Pixels: text-line boxes of the OCR detector say "upright or sideways"; recognising a few lines in
   both directions says which way up (``method="ocr"``, needs the optional OCR packages), or
   Tesseract's orientation detection (``method="osd"``).
Skew (a few degrees) is measured on pixels alone with a projection profile (needs NumPy) and is
corrected only on pages without a text layer (scans, images).

``prepare_document(doc, ...)`` writes a corrected working copy (PDF: ``/Rotate`` set on turned pages,
skewed scans replaced by a straightened raster; image: a new PNG), points ``doc.meta["path"]`` and the
page renders to it and records ``rotation_applied`` / ``skew_applied`` in ``Page.properties``. An upright
document is not touched at all (same bytes, same reads). After grounding, ``restore_original_frame`` maps
every box back onto the page as it was given and hands the original file back to the later stages.

A "turn" is always the clockwise angle that makes the page upright.
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from .. import paths


# ---------------------------------------------------------------- text layer
def text_layer_turns(pdf_path: str | Path, page_numbers: list[int], password: Optional[str] = None,
                     min_chars: int = 40, share: float = 0.7) -> dict[int, Optional[int]]:
    """Per page: the turn the text layer asks for (0 / 90 / 180 / 270), or None when the page has too
    few characters to decide. A direction must hold ``share`` of the characters to count."""
    out: dict[int, Optional[int]] = {n: None for n in page_numbers}
    try:
        import logging

        import pdfplumber
        logging.getLogger("pdfminer").setLevel(logging.ERROR)
        with pdfplumber.open(str(pdf_path), password=password or None) as pdf:
            for n in page_numbers:
                try:
                    chars = pdf.pages[n - 1].chars
                except Exception:
                    continue
                counts = {0: 0, 90: 0, 180: 0, 270: 0}
                for c in chars:
                    m = c.get("matrix")
                    if not m or not (c.get("text") or "").strip():
                        continue
                    ang = math.degrees(math.atan2(m[1], m[0])) % 360
                    q = int(((ang + 45) // 90) % 4) * 90
                    counts[q] += 1
                total = sum(counts.values())
                if total < min_chars:
                    continue
                best = max(counts, key=lambda k: counts[k])
                out[n] = best if counts[best] >= share * total else 0
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- pixels: which way up
def _np_rgb(im, max_side: int):
    import numpy as np
    im = im.convert("RGB")
    w, h = im.size
    s = min(1.0, max_side / max(w, h))
    if s < 1.0:
        from PIL import Image
        im = im.resize((max(int(w * s), 1), max(int(h * s), 1)), Image.BILINEAR)
    return np.ascontiguousarray(np.asarray(im)[:, :, ::-1])  # BGR, as OpenCV images are


def _extent(q) -> tuple[float, float, float, float]:
    xs = [p[0] for p in q]
    ys = [p[1] for p in q]
    return min(xs), min(ys), max(xs), max(ys)


def image_turn_ocr(im, max_side: int = 1600, lines: int = 10, margin: float = 0.25,
                   min_score: float = 0.4) -> tuple[int, dict]:
    """Turn from the OCR detector and recogniser. (turn, details). 0 when there is too little text or
    no direction reads clearly better. Thresholds from 59 turned / upright test images (business scans,
    FUNSD forms): the better-reading direction was the right one on all 59; on faint scans its score
    is as low as 0.55 against 0.15 the other way, so the decision needs a clear margin, not a high score."""
    import numpy as np

    from ..pipeline.grounding import ocr
    info: dict[str, Any] = {"method": "ocr"}
    if not ocr.available():
        info["reason"] = "ocr packages missing"
        return 0, info
    arr = _np_rgb(im, max_side)
    quads = ocr.detect_lines(arr)

    def long_boxes(qs):
        out = []
        for q in qs:
            x0, y0, x1, y1 = _extent(q)
            w, h = x1 - x0, y1 - y0
            if max(w, h) >= 3 * max(min(w, h), 1):
                out.append((x0, y0, x1, y1, w, h))
        return out
    lb = long_boxes(quads)
    info["lines"] = len(lb)
    if len(lb) < 3:
        info["reason"] = "too little text"
        return 0, info
    tall = sum(1 for b in lb if b[5] > b[4])
    sideways = tall > 0.6 * len(lb)
    info["sideways_share"] = round(tall / len(lb), 3)
    if sideways:
        arr = np.ascontiguousarray(np.rot90(arr, k=-1))  # a quarter turn clockwise
        lb = long_boxes(ocr.detect_lines(arr))
        if len(lb) < 3:
            info["reason"] = "too little text after a quarter turn"
            return 0, info
    lb = [b for b in lb if b[4] > b[5]]
    lb.sort(key=lambda b: -b[4])
    H, W = arr.shape[:2]
    crops = []
    for x0, y0, x1, y1, w, h in lb[:lines]:
        pad = int(0.15 * h) + 1
        c = arr[max(int(y0) - pad, 0):min(int(y1) + pad, H), max(int(x0) - pad, 0):min(int(x1) + pad, W)]
        if c.size:
            crops.append(np.ascontiguousarray(c))
    if not crops:
        return 0, info
    up = ocr.recognise(crops)
    down = ocr.recognise([np.ascontiguousarray(np.rot90(c, 2)) for c in crops])

    def quality(res) -> float:
        # a line read the right way up gives long text with a high score; upside down, short junk
        vals = [s * min(len(re.sub(r"\W", "", t)), 12) / 12 for t, s in res]
        return sum(vals) / len(vals) if vals else 0.0
    s_up, s_down = quality(up), quality(down)
    info.update(score_upright=round(s_up, 3), score_flipped=round(s_down, 3))
    if max(s_up, s_down) < min_score:
        info["reason"] = "no direction reads clearly"
        return 0, info
    if sideways:
        if abs(s_up - s_down) < margin:
            info["reason"] = "sideways text, direction unclear"
            return 0, info
        return (90 if s_up > s_down else 270), info
    if s_down - s_up >= margin:
        return 180, info
    if s_up - s_down < margin:
        info["reason"] = "direction unclear"  # upright stays upright; "auto" may still ask Tesseract
    return 0, info


def image_turn_osd(image_path: str | Path, min_confidence: float = 2.0) -> tuple[int, dict]:
    """Turn from Tesseract's orientation and script detection (``--psm 0``)."""
    info: dict[str, Any] = {"method": "osd"}
    exe = shutil.which("tesseract")
    if exe is None:
        info["reason"] = "tesseract missing"
        return 0, info
    try:
        out = subprocess.run([exe, str(Path(image_path).resolve()), "-", "--psm", "0"], capture_output=True,
                             timeout=120).stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return 0, info
    rot = re.search(r"Rotate:\s*(\d+)", out)
    conf = re.search(r"Orientation confidence:\s*([\d.]+)", out)
    if not rot or not conf:
        info["reason"] = "no orientation reported"
        return 0, info
    info["confidence"] = float(conf.group(1))
    if float(conf.group(1)) < min_confidence:
        info["reason"] = "low confidence"
        return 0, info
    return int(rot.group(1)) % 360, info


def image_turn(im, method: str = "auto", image_path: Optional[str | Path] = None) -> tuple[int, dict]:
    """``ocr`` | ``osd`` | ``auto`` (OCR; where it cannot decide, Tesseract's orientation detection is asked:
    on the turned test pages OCR alone was right on 26 of 32, OSD alone on 20, together on 29, none wrong)."""
    if method == "safe":
        return image_turn_safe(im, image_path)
    if method == "auto":
        turn, info = image_turn_ocr(im)
        if not info.get("reason"):
            return turn, info
        turn2, info2 = image_turn(im, "osd", image_path)
        if turn2:
            return turn2, {**info2, "method": "osd", "ocr_reason": info.get("reason")}
        return 0, info
    if method == "osd":
        if image_path is None:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", dir=str(_out_dir())) as tf:
                im.convert("RGB").save(tf.name)
                return image_turn_osd(tf.name)
        return image_turn_osd(image_path)
    return image_turn_ocr(im)


def image_turn_safe(im, image_path: Optional[str | Path] = None) -> tuple[int, dict]:
    """``auto`` with two guards. ``auto`` turned five upright development-set pages:
    four handwritten letters (the OCR check leaned upright but could not decide, and Tesseract's orientation
    detection said "upside down" with confidence 2.4 to 10.3) and a page of vertical Chinese handwriting (line
    boxes taller than wide look like a sideways page). Here Tesseract may only decide what the OCR evidence leaves
    open: it is asked alone when OCR found too little text; it may confirm "upside down" only when the flipped
    reading scored higher; it may choose between the two quarter turns only when the lines are sideways; and a
    quarter turn that OCR is sure of is dropped when Tesseract is sure the page is upright or upside down
    (vertical writing). The price is a few more turned pages left as they are."""
    turn, info = image_turn_ocr(im)
    reason = info.get("reason")
    if reason == "ocr packages missing":
        return image_turn(im, "osd", image_path)
    turn2, info2 = image_turn(im, "osd", image_path)
    osd_sure = not info2.get("reason")
    info = {**info, "method": "safe", "osd_turn": turn2 if osd_sure else None, "osd_confidence": info2.get("confidence")}
    if not reason:                                   # OCR decided
        if turn in (90, 270) and osd_sure and turn2 in (0, 180):
            return 0, {**info, "reason": "sideways lines, but orientation detection reads the page upright (vertical writing)"}
        return turn, info
    if not osd_sure:
        return 0, info
    if reason.startswith("too little text"):
        return turn2, ({**info, "ocr_reason": reason} if turn2 else info)
    sideways = info.get("sideways_share", 0.0) > 0.6
    if sideways:
        return (turn2, {**info, "ocr_reason": reason}) if turn2 in (90, 270) else (0, info)
    if turn2 == 180 and info.get("score_flipped", 0.0) > info.get("score_upright", 0.0):
        return 180, {**info, "ocr_reason": reason}
    return 0, info


# ---------------------------------------------------------------- pixels: skew
def skew_angle(im, max_angle: float = 10.0, max_side: int = 900) -> tuple[float, float]:
    """(angle, gain). Rotating the image by ``angle`` degrees counter-clockwise (``Image.rotate(angle)``)
    makes its text lines horizontal. ``gain`` = sharpness of the row profile at that angle over the
    sharpness at 0 (1.0 = no better than leaving the page as it is)."""
    import numpy as np
    from PIL import Image, ImageOps
    g = ImageOps.autocontrast(im.convert("L"))
    w, h = g.size
    s = min(1.0, max_side / max(w, h))
    if s < 1.0:
        g = g.resize((max(int(w * s), 1), max(int(h * s), 1)), Image.BILINEAR)
    ink = ImageOps.invert(g).point(lambda v: 255 if v > 96 else 0)

    def sharp(a: float) -> float:
        r = ink.rotate(a, resample=Image.BILINEAR, fillcolor=0) if a else ink
        prof = np.asarray(r, dtype=np.float32).sum(axis=1)
        d = prof[1:] - prof[:-1]
        return float((d * d).sum())
    base = sharp(0.0)
    if base <= 0:
        return 0.0, 1.0
    best, best_s = 0.0, base
    a = -max_angle
    while a <= max_angle + 1e-9:
        v = sharp(a)
        if v > best_s:
            best, best_s = a, v
        a += 1.0
    centre = best
    for k in range(-9, 10):
        a = centre + k / 10.0
        v = sharp(a)
        if v > best_s:
            best, best_s = a, v
    return round(best, 2), round(best_s / base, 3)


# ---------------------------------------------------------------- applying
def _out_dir() -> Path:
    d = paths.cache_dir() / "prepared"
    d.mkdir(parents=True, exist_ok=True)
    return d


def turn_image(im, turn: int = 0, skew: float = 0.0):
    """The image turned clockwise by ``turn`` and then straightened by ``skew`` (see ``skew_angle``)."""
    from PIL import Image
    if turn % 360:
        im = im.transpose({90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}[turn % 360])
    if skew:
        im = im.convert("RGB").rotate(skew, resample=Image.BICUBIC, expand=False, fillcolor=(255, 255, 255))
    return im


def prepare_document(doc, rotate: bool = True, deskew: bool = False, method: str = "auto",
                     deskew_min: float = 1.0, deskew_gain: float = 1.3, detect_dpi: int = 110,
                     raster_dpi: int = 300, max_pixel_pages: int = 60) -> dict:
    """Detects and corrects turned / skewed pages of ``doc`` (after Intake). Returns
    ``{"pages": {original_page: {"turn", "skew", "by", ...}}, "changed": bool}``; pages listed are those
    that were examined. Nothing is written when no page needs a change."""
    report: dict[str, Any] = {"pages": {}, "changed": False}
    path = doc.meta.get("path")
    if not path or not Path(str(path)).exists() or not doc.pages:
        return report
    if doc.file_type == "pdf":
        _prepare_pdf(doc, Path(str(path)), report, rotate, deskew, method, deskew_min, deskew_gain, detect_dpi,
                     raster_dpi, max_pixel_pages)
    elif doc.pages and all(p.unit == "px" and p.renders for p in doc.pages):  # raster inputs (png, jpg, tiff, heic, ...)
        _prepare_image(doc, report, rotate, deskew, method, deskew_min, deskew_gain)
    return report


def _measure_skew(im, deskew_min: float, deskew_gain: float) -> tuple[float, float]:
    try:
        angle, gain = skew_angle(im)
    except Exception:
        return 0.0, 1.0
    if abs(angle) < deskew_min or gain < deskew_gain:
        return 0.0, gain
    return angle, gain


def _prepare_pdf(doc, path: Path, report: dict, rotate: bool, deskew: bool, method: str, deskew_min: float,
                 deskew_gain: float, detect_dpi: int, raster_dpi: int, max_pixel_pages: int) -> None:
    from ..pipeline.intake import render_page
    password = doc.meta.get("password")
    numbers = [p.original_page for p in doc.pages]
    layer = text_layer_turns(path, numbers, password)
    plan: dict[int, tuple[int, float]] = {}
    pixel_pages = 0
    for page in doc.pages:
        n = page.original_page
        turn, skew, info = 0, 0.0, {}
        t = layer.get(n)
        if t is not None:
            turn, info = (t if rotate else 0), {"by": "text_layer"}
        elif pixel_pages < max_pixel_pages:
            pixel_pages += 1
            try:
                im = render_page(path, n, detect_dpi, password)
            except Exception:
                continue
            if rotate:
                turn, info = image_turn(im, method)
                info["by"] = info.pop("method", method)
            if deskew:
                skew, gain = _measure_skew(turn_image(im, turn), deskew_min, deskew_gain)
                info["skew_gain"] = gain
        report["pages"][n] = {"turn": turn, "skew": skew, **info}
        if turn or skew:
            plan[n] = (turn, skew)
    if not plan:
        return
    import pikepdf
    h = hashlib.sha1(path.read_bytes())
    h.update(repr(sorted(plan.items())).encode())
    out = _out_dir() / f"{h.hexdigest()[:20]}.pdf"
    if not out.exists():
        with pikepdf.open(str(path), password=password or "") as pdf:
            for n, (turn, skew) in plan.items():
                pg = pdf.pages[n - 1]
                if skew:
                    # a skewed scan: the page becomes its own straightened raster (it has no text layer to lose)
                    im = turn_image(render_page(path, n, raster_dpi, password), turn, skew)
                    one = _out_dir() / f"{out.stem}_p{n}.pdf"
                    im.convert("RGB").save(one, "PDF", resolution=float(raster_dpi))
                    with pikepdf.open(str(one)) as src:
                        pdf.pages[n - 1] = src.pages[0]
                    one.unlink(missing_ok=True)
                else:
                    pg.obj["/Rotate"] = (int(pg.obj.get("/Rotate", 0) or 0) + turn) % 360
            tmp = out.with_suffix(".tmp.pdf")
            pdf.save(str(tmp))
            tmp.replace(out)
    doc.meta.setdefault("original_path", str(path))
    doc.meta["path"] = str(out)
    for page in doc.pages:
        if page.original_page in plan:
            turn, skew = plan[page.original_page]
            if turn in (90, 270):
                page.width, page.height = page.height, page.width
            page.rotation = (int(page.rotation or 0) + turn) % 360
            page.properties["renders_before_prepare"] = dict(page.renders)
            page.renders = {}
            page.properties["rotation_applied"] = turn
            page.properties["skew_applied"] = skew
    report["changed"] = True


def _prepare_image(doc, report: dict, rotate: bool, deskew: bool, method: str, deskew_min: float,
                   deskew_gain: float) -> None:
    from PIL import Image
    for page in doc.pages:
        key, src = next(iter(page.renders.items()), (None, None))
        if not src or not Path(src).exists():
            continue
        try:
            with Image.open(src) as im0:
                im0.load()
                exif = 1
                try:
                    exif = int(im0.getexif().get(0x0112, 1) or 1)
                except Exception:
                    pass
                im = im0.copy()
        except Exception:
            continue
        turn, skew, info = 0, 0.0, {}
        exif_turn = {3: 180, 6: 90, 8: 270}.get(exif, 0)
        if rotate and exif_turn:
            turn, info = exif_turn, {"by": "exif"}
        elif rotate:
            turn, info = image_turn(im, method, src)
            info["by"] = info.pop("method", method)
        if deskew:
            skew, gain = _measure_skew(turn_image(im, turn), deskew_min, deskew_gain)
            info["skew_gain"] = gain
        report["pages"][page.original_page] = {"turn": turn, "skew": skew, **info}
        if not (turn or skew):
            continue
        fixed = turn_image(im, turn, skew).convert("RGB")
        h = hashlib.sha1(Path(src).read_bytes())
        h.update(f"|{turn}|{skew}".encode())
        out = _out_dir() / f"{h.hexdigest()[:20]}.png"
        if not out.exists():
            fixed.save(out)
        page.width, page.height = float(fixed.size[0]), float(fixed.size[1])
        page.properties["renders_before_prepare"] = dict(page.renders)
        page.renders = {key: str(out)}
        page.properties["rotation_applied"] = turn
        page.properties["skew_applied"] = skew
        if len(doc.pages) == 1:
            doc.meta.setdefault("original_path", str(doc.meta.get("path")))
            doc.meta["path"] = str(out)
        report["changed"] = True


# ---------------------------------------------------------------- boxes back into the original page
def original_point(x: float, y: float, turn: int = 0, skew: float = 0.0, aspect: float = 1.0) -> tuple[float, float]:
    """A point of the corrected page (fractions, origin top left) -> the same point on the page as it was given.
    ``aspect`` = height / width of the corrected page (only needed to undo a skew)."""
    if skew:
        a = math.radians(skew)
        dx, dy = (x - 0.5), (y - 0.5) * aspect  # in units of the page width
        dx, dy = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
        x, y = 0.5 + dx, 0.5 + dy / aspect
    t = turn % 360
    if t == 90:
        x, y = y, 1.0 - x
    elif t == 180:
        x, y = 1.0 - x, 1.0 - y
    elif t == 270:
        x, y = 1.0 - y, x
    return x, y


def _boxes_in(obj: Any, seen: set) -> list:
    """Every BBox reachable from ``obj`` (models, lists, dicts), each once."""
    from pydantic import BaseModel

    from ..model import BBox
    out = []
    if isinstance(obj, BBox):
        if id(obj) not in seen:
            seen.add(id(obj))
            out.append(obj)
    elif isinstance(obj, BaseModel):
        if id(obj) in seen:
            return out
        seen.add(id(obj))
        for name in type(obj).model_fields:
            out.extend(_boxes_in(getattr(obj, name, None), seen))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_boxes_in(v, seen))
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_boxes_in(v, seen))
    return out


def restore_original_frame(doc) -> int:
    """After grounding: every box of a turned / straightened page is mapped back to the page as it was given
    (Reducto reports boxes on the original page), the page gets its size back and the document its original
    file. ``rotation_applied`` / ``skew_applied`` stay in ``Page.properties``. Returns the number of pages restored.
    A straightened page's boxes become the upright rectangles around the tilted ones."""
    done = 0
    for page in doc.pages:
        turn = int(page.properties.get("rotation_applied") or 0)
        skew = float(page.properties.get("skew_applied") or 0.0)
        if not (turn or skew) or page.properties.get("boxes_frame") == "original":
            continue
        aspect = (page.height / page.width) if page.width and page.height else 1.0
        seen: set = set()
        for b in _boxes_in([page.regions, page.layout_regions, page.words], seen):
            pts = [original_point(x, y, turn, skew, aspect) for x in (b.left, b.left + b.width)
                   for y in (b.top, b.top + b.height)]
            x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
            y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
            x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in (x0, y0, x1, y1))
            b.left, b.top, b.width, b.height = round(x0, 5), round(y0, 5), round(x1 - x0, 5), round(y1 - y0, 5)
        if turn in (90, 270):
            page.width, page.height = page.height, page.width
        page.rotation = (int(page.rotation or 0) - turn) % 360
        page.renders = dict(page.properties.pop("renders_before_prepare", None) or {})
        page.properties["boxes_frame"] = "original"
        done += 1
    if done and doc.meta.get("original_path"):
        doc.meta["prepared_path"] = doc.meta.get("path")
        doc.meta["path"] = doc.meta["original_path"]
    return done
