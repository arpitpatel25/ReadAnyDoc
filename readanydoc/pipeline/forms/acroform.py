"""Form fields of a fillable PDF, read from the file: names, labels, values, checkbox and radio states.

Widget enumeration and nearby-label detection are the ones the Edit task uses (``tasks/edit.py``).
Nothing here calls a model; what is returned is what the file stores, so it is exact.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.Lock()
_CACHE: dict[tuple, list[dict]] = {}


def _value(w: Any) -> tuple[Optional[str], Optional[bool]]:
    from ...tasks.edit import _inherited
    v = _inherited(w.annot, "/V")
    if w.type in ("checkbox", "radio"):
        state = w.annot.get("/AS")
        if state is None:
            state = v
        s = str(state) if state is not None else "/Off"
        on = s not in ("/Off", "Off", "")
        if w.type == "radio" and v is not None and w.on_state:
            on = str(v).lstrip("/") == w.on_state
        return None, on
    if v is None:
        return "", None
    try:
        s = str(v)
    except Exception:
        return "", None
    if w.type == "dropdown" and s in w.exports:
        s = w.options[w.exports.index(s)]
    return s.replace("\r\n", "\n").replace("\r", "\n").strip(), None


def all_fields(path: str | Path, password: Optional[str] = None) -> list[dict]:
    """Every field widget of the file: ``{page (1-based), type, name, label, tooltip, bbox [l, t, r, b]
    as page fractions, value, checked, on_state, group}``. Empty when the file has no form or cannot be opened."""
    p = Path(path)
    try:
        key = (str(p), p.stat().st_mtime_ns, p.stat().st_size)
    except OSError:
        return []
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        out: list[dict] = []
        try:
            import pikepdf

            from ...tasks.edit import _enumerate_widgets, _label_widgets
            with pikepdf.open(str(p), password=password or "") as pdf:
                if "/AcroForm" in pdf.Root or any("/Annots" in pg.obj for pg in pdf.pages):
                    widgets = _enumerate_widgets(pdf)
                    _label_widgets(p, widgets)
                    for w in widgets:
                        value, checked = _value(w)
                        b = w.bbox
                        if b["width"] <= 0 or b["height"] <= 0:
                            continue
                        out.append({"page": w.page + 1, "type": w.type, "name": w.name, "label": w.label,
                                    "tooltip": w.tooltip,
                                    "bbox": [b["left"], b["top"], round(b["left"] + b["width"], 4),
                                             round(b["top"] + b["height"], 4)],
                                    "value": value, "checked": checked, "on_state": w.on_state,
                                    "group": str(w.fld_key) if w.type == "radio" else None})
        except Exception:
            out = []
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = out
        return out


def page_fields(path: str | Path, page_number: int, password: Optional[str] = None) -> list[dict]:
    return [f for f in all_fields(path, password) if f["page"] == page_number]
