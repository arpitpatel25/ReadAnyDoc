"""Image sizing for vision models.

Rule (measured): always send an image at the largest size the model keeps,
and never let the provider downscale. Claude's high-resolution tier keeps images up to 2576 px on
the long edge and 4784 visual tokens of 28x28 px patches; the same page sent as an oversized
150-dpi PNG scored 84% on tiny text against 95% at the native size.
"""

from __future__ import annotations

import io
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


@dataclass(frozen=True)
class ModelLimits:
    max_edge: int = 2576
    max_tokens: int = 4784
    patch: int = 28


CLAUDE_HIRES = ModelLimits()
LIMITS = {"claude": CLAUDE_HIRES, "haiku": CLAUDE_HIRES, "sonnet": CLAUDE_HIRES}


def visual_tokens(width: float, height: float, limits: ModelLimits = CLAUDE_HIRES) -> int:
    return math.ceil(width / limits.patch) * math.ceil(height / limits.patch)


def fits(width: float, height: float, limits: ModelLimits = CLAUDE_HIRES) -> bool:
    p = limits.patch
    return (math.ceil(width / p) * p <= limits.max_edge and math.ceil(height / p) * p <= limits.max_edge
            and visual_tokens(width, height, limits) <= limits.max_tokens)


def native_scale(width: float, height: float, limits: ModelLimits = CLAUDE_HIRES,
                 max_scale: Optional[float] = None) -> float:
    """Largest factor s such that (width*s, height*s) still fits the model's limits.
    ``max_scale`` caps upsampling (rendering a PDF beyond ~400 dpi adds no information)."""
    if width <= 0 or height <= 0:
        raise ValueError("image size must be positive")
    hi = min(limits.max_edge / max(width, height),
             math.sqrt(limits.max_tokens * limits.patch ** 2 / (width * height)))
    if max_scale is not None:
        hi = min(hi, max_scale)
    s = hi
    while s > 0 and not fits(int(width * s), int(height * s), limits):
        s -= max(hi * 0.002, 1e-4)
    return max(s, 0.0)


def native_size(width: float, height: float, limits: ModelLimits = CLAUDE_HIRES,
                max_scale: Optional[float] = None) -> tuple[int, int]:
    s = native_scale(width, height, limits, max_scale)
    return max(int(width * s), 1), max(int(height * s), 1)


def area_fraction(box: tuple[float, float, float, float]) -> float:
    """Share of the page a normalised crop box (x0, y0, x1, y1) covers; drives the crop cost."""
    x0, y0, x1, y1 = box
    return max(min(x1, 1.0) - max(x0, 0.0), 0.0) * max(min(y1, 1.0) - max(y0, 0.0), 0.0)


def pad_box(box: tuple[float, float, float, float], pad: float = 0.012) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    return max(x0 - pad, 0.0), max(y0 - pad, 0.0), min(x1 + pad, 1.0), min(y1 + pad, 1.0)


_PDFIUM_LOCK = threading.RLock()  # pdfium is not thread-safe: concurrent renders crash the process


def _png(image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def render_pdf_native(pdf_path: Union[str, Path], page_number: int = 1,
                      box: Optional[tuple[float, float, float, float]] = None,
                      limits: ModelLimits = CLAUDE_HIRES, max_dpi: float = 400.0,
                      password: Optional[str] = None, crop_zoom: Optional[float] = None) -> bytes:
    """PNG of a PDF page, or of a normalised crop of it, rendered directly at the native size
    (no resampling of a lower-resolution raster).

    ``crop_zoom`` limits a crop to that multiple of the scale the whole page would get: at 1.0 the
    crop has exactly the pixels it has inside a native-size page image, so its visual tokens, and
    its cost, are proportional to its area. ``None`` renders the crop as large as the limits allow."""
    import pypdfium2 as pdfium
    # Speed: the strips of one page are crops of the same raster, so the page is rendered once per
    # scale and kept for a moment; only pdfium itself runs under the process-wide lock, cropping and PNG encoding
    # do not. The bytes are the same as before (same render, same crop).
    x0, y0, x1, y1 = box or (0.0, 0.0, 1.0, 1.0)
    W, H = _page_size(pdfium, pdf_path, page_number, password)
    s = native_scale(W * (x1 - x0), H * (y1 - y0), limits, max_scale=max_dpi / 72.0)
    if box is not None and crop_zoom is not None:
        s = min(s, native_scale(W, H, limits, max_scale=max_dpi / 72.0) * crop_zoom)
    img = _page_raster(pdfium, pdf_path, page_number, s, password, keep=box is not None)
    if box is not None:
        w, h = img.size
        img = img.crop((int(x0 * w), int(y0 * h), max(int(x1 * w), int(x0 * w) + 1),
                        max(int(y1 * h), int(y0 * h) + 1)))
    return _png(img)


_RASTERS: "dict[tuple, object]" = {}  # (path, mtime, page, scale, password) -> PIL image; insertion order = age
_SIZES: "dict[tuple, tuple[float, float]]" = {}
_RASTER_LOCK = threading.Lock()


def _file_key(pdf_path, page_number, password) -> tuple:
    p = Path(str(pdf_path))
    try:
        m = p.stat().st_mtime_ns
    except OSError:
        m = 0
    return (str(p), m, page_number, password)


def _page_size(pdfium, pdf_path, page_number, password) -> tuple[float, float]:
    key = _file_key(pdf_path, page_number, password)
    with _RASTER_LOCK:
        if key in _SIZES:
            return _SIZES[key]
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path), password=password)
        try:
            size = pdf[page_number - 1].get_size()
        finally:
            pdf.close()
    with _RASTER_LOCK:
        if len(_SIZES) > 4096:
            _SIZES.clear()
        _SIZES[key] = size
    return size


def _page_raster(pdfium, pdf_path, page_number, scale: float, password, keep: bool):
    """The whole page at ``scale`` as a PIL image. Rasters asked for a crop are kept (the next strips of the
    same page want the same one); at most READANYDOC_RASTER_CACHE of them (default 8, 0 = none)."""
    import os
    limit = int(os.environ.get("READANYDOC_RASTER_CACHE", "8") or 0)
    key = (*_file_key(pdf_path, page_number, password), round(scale, 9))
    if keep and limit > 0:
        with _RASTER_LOCK:
            if key in _RASTERS:
                return _RASTERS[key]
    with _PDFIUM_LOCK:
        if keep and limit > 0:
            with _RASTER_LOCK:  # another thread rendered it while this one waited for pdfium
                if key in _RASTERS:
                    return _RASTERS[key]
        pdf = pdfium.PdfDocument(str(pdf_path), password=password)
        try:
            img = pdf[page_number - 1].render(scale=scale).to_pil()
        finally:
            pdf.close()
        if keep and limit > 0:
            with _RASTER_LOCK:
                _RASTERS[key] = img
                while len(_RASTERS) > limit:
                    _RASTERS.pop(next(iter(_RASTERS)))
    return img


def resize_image_native(image_path: Union[str, Path, bytes],
                        box: Optional[tuple[float, float, float, float]] = None,
                        limits: ModelLimits = CLAUDE_HIRES, max_scale: float = 2.0) -> bytes:
    """PNG of a raster image (or a crop of it) scaled to the native size. Rasters are scaled down
    when too large and up to ``max_scale`` when small (small crops read better enlarged)."""
    from PIL import Image
    img = Image.open(io.BytesIO(image_path)) if isinstance(image_path, bytes) else Image.open(image_path)
    if box is not None:
        w, h = img.size
        x0, y0, x1, y1 = box
        img = img.crop((int(x0 * w), int(y0 * h), max(int(x1 * w), int(x0 * w) + 1),
                        max(int(y1 * h), int(y0 * h) + 1)))
    w, h = img.size
    nw, nh = native_size(w, h, limits, max_scale=max_scale)
    if (nw, nh) != (w, h):
        img = img.resize((nw, nh), Image.LANCZOS)
    return _png(img)


def pdfium_locked(fn):
    """Every use of pdfium in the process goes through one lock: the library is not thread-safe, and the
    API server parses several documents at once (found by the end-to-end harness: concurrent requests
    failed with "Data format error")."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _PDFIUM_LOCK:
            return fn(*args, **kwargs)
    return wrapper
