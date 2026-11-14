"""OpenCV calls that must not read outside the image they are given.

The server died twice during the full RD-TableBench run with a segmentation fault inside ``cv2.resize``, called by the
PP-OCR text detector. The macOS crash reports show the faulting address one byte after the end of the heap block that
holds the source image, inside ``kleidicv_resize_generic_stripe_u8`` (the KleidiCV backend OpenCV 5 uses on arm64).
On a slight downscale (706 x 1481 -> 704 x 1472, the detector's "round to a multiple of 32") that routine reads up to
one image row past the end of the source. Nearly always the memory behind an image is readable and nothing happens;
when the image ends exactly at the end of a mapped block the process dies. It does not need two threads: many
requests at once only make such heap layouts common. ``tests/test_localmodels.py`` reproduces it with one call on an
image that ends at a protected page.

``install()`` replaces ``cv2.resize`` (for every caller in the process, RapidOCR included) with a wrapper that hands
OpenCV a copy of the source with readable rows after it. The pixels of the result are unchanged: the extra rows repeat
the last row and lie outside the image. Cost: one copy of the source per call.
"""

from __future__ import annotations

import threading

PAD_ROWS = 2
_LOCK = threading.Lock()
_installed = False


def padded(src):
    """A C-contiguous array equal to ``src`` whose buffer continues for ``PAD_ROWS`` more rows (copies of the last
    row). ``src`` itself when it is not a non-empty 2-D / 3-D numpy array."""
    import numpy as np
    if not isinstance(src, np.ndarray) or src.ndim not in (2, 3) or src.shape[0] == 0 or src.size == 0:
        return src
    h = src.shape[0]
    buf = np.empty((h + PAD_ROWS,) + src.shape[1:], dtype=src.dtype)
    buf[:h] = src
    buf[h:] = src[h - 1]
    return buf[:h]


def install() -> bool:
    """Make ``cv2.resize`` safe for the whole process. Idempotent; False when OpenCV is not installed."""
    global _installed
    if _installed:
        return True
    with _LOCK:
        if _installed:
            return True
        try:
            import cv2
        except Exception:
            return False
        raw = getattr(cv2.resize, "__readanydoc_raw__", None) or cv2.resize

        def resize(src, *args, **kwargs):
            return raw(padded(src), *args, **kwargs)

        resize.__readanydoc_raw__ = raw
        resize.__doc__ = getattr(raw, "__doc__", None)
        cv2.resize = resize
        _installed = True
        return True
