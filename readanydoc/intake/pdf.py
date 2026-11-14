"""PDF extras read with pikepdf: document properties, link annotations, comments."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

_DATE = re.compile(r"^D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?\s*([Zz+\-])?(\d{2})?'?:?(\d{2})?'?")
_KEYS = {"title": "title", "author": "author", "subject": "subject", "keywords": "keywords", "creator": "creator",
         "producer": "producer", "creationdate": "created_at", "moddate": "modified_at"}
_COMMENT_TYPES = ("/Text", "/FreeText", "/Highlight", "/Underline", "/StrikeOut", "/Squiggly", "/Caret")


def pdf_date(value: Any) -> Optional[str]:
    """``D:20240131120000+01'00'`` -> ISO 8601. No offset means UTC (Reducto's rule)."""
    m = _DATE.match(str(value or "").strip())
    if not m:
        return None
    y, mo, d, h, mi, s, sign, oh, om = m.groups()
    out = f"{y}-{mo or '01'}-{d or '01'}T{h or '00'}:{mi or '00'}:{s or '00'}"
    if sign in ("+", "-") and oh:
        return f"{out}{sign}{oh}:{om or '00'}"
    return out + "+00:00"


def normalise_properties(raw: dict[str, Any]) -> dict[str, Any]:
    """PDF Info keys (any case, with or without '/') -> Reducto's DocumentProperties keys."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        key = _KEYS.get(str(k).lstrip("/").lower())
        if not key or v in (None, ""):
            continue
        out[key] = pdf_date(v) if key.endswith("_at") else str(v)
    return out


def read_properties(path: Path, password: Optional[str] = None) -> dict[str, Any]:
    import pikepdf
    with pikepdf.open(str(path), password=password or "") as pdf:
        return normalise_properties({str(k): str(v) for k, v in pdf.docinfo.items()})


def _norm_rect(rect, box, rotation: int = 0) -> Optional[dict[str, float]]:
    try:
        x0, y0, x1, y1 = (float(v) for v in rect)
        bx0, by0, bx1, by1 = (float(v) for v in box)
    except Exception:
        return None
    w, h = bx1 - bx0, by1 - by0
    if w <= 0 or h <= 0:
        return None
    left, right = (min(x0, x1) - bx0) / w, (max(x0, x1) - bx0) / w
    top, bottom = (by1 - max(y0, y1)) / h, (by1 - min(y0, y1)) / h
    if rotation % 360 == 90:
        left, top, right, bottom = 1 - bottom, left, 1 - top, right
    elif rotation % 360 == 180:
        left, top, right, bottom = 1 - right, 1 - bottom, 1 - left, 1 - top
    elif rotation % 360 == 270:
        left, top, right, bottom = top, 1 - right, bottom, 1 - left
    clamp = lambda v: min(max(v, 0.0), 1.0)
    left, top, right, bottom = clamp(left), clamp(top), clamp(right), clamp(bottom)
    return {"left": round(left, 5), "top": round(top, 5), "width": round(right - left, 5),
            "height": round(bottom - top, 5)}


def read_annotations(path: Path, page_numbers: list[int], password: Optional[str] = None) -> dict[int, dict]:
    """1-based page number -> {"links": [...], "comments": [...]} with page-normalised boxes."""
    import pikepdf
    out: dict[int, dict] = {}
    with pikepdf.open(str(path), password=password or "") as pdf:
        for n in page_numbers:
            if not 1 <= n <= len(pdf.pages):
                continue
            page = pdf.pages[n - 1]
            annots = page.obj.get("/Annots")
            if annots is None:
                continue
            box = page.obj.get("/CropBox") or page.mediabox
            rotation = int(page.obj.get("/Rotate", 0) or 0)
            links, comments = [], []
            for a in annots:
                try:
                    sub = str(a.get("/Subtype"))
                    rect = _norm_rect(a.get("/Rect"), box, rotation)
                    if sub == "/Link":
                        action = a.get("/A")
                        uri = action.get("/URI") if action is not None else None
                        if uri is not None and rect:
                            links.append({"bbox": rect, "url": str(uri)})
                    elif sub in _COMMENT_TYPES:
                        text = str(a.get("/Contents") or "").strip()
                        if text:
                            comments.append({"bbox": rect, "text": text, "author": str(a.get("/T") or "") or None,
                                             "subtype": sub.lstrip("/")})
                except Exception:
                    continue
            if links or comments:
                out[n] = {"links": links, "comments": comments}
    return out
