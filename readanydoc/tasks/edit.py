"""Edit: fill forms from natural-language instructions (see tasks/__init__.py for the contract).

PDF with AcroForm widgets: enumerate text / checkbox / radio / dropdown widgets with page-normalised
boxes and nearby label text, let the model map ``edit_instructions`` to fields, write the values,
optionally flatten. Returns the ``form_schema``; an incoming ``form_schema`` skips detection, is
matched to widgets by >= 50% box overlap, and honours ``fill`` / ``value`` / ``font_size``.

Flat or scanned PDF: every box of the ``form_schema`` becomes a real AcroForm field (text or
checkbox, named ``temp_<n>`` as Reducto names them) holding its value, written in
``edit_options.color`` (red by default, as Reducto does), so the output stays fillable. Fields are
created for unfilled boxes too. ``edit_options.flatten`` bakes the values into the page and removes
the fields. ``edit_options.flat_fields = false`` (ours, not in Reducto's API) draws the text on the
page instead, as before. Without a ``form_schema`` the fields are detected first: one model call per
page on the page image, boxes refined on the pixels (``tasks/detect.py``); the detected schema is
filled and returned.

The returned ``form_schema`` carries, like Reducto's, the ``value`` written to each field and
``filled`` (true / false).

DOCX: content controls (text, checkbox, dropdown) and empty table cells next to a label; inserted
text is written in ``edit_options.color`` (as in Reducto's real output; ``docx_color_mode: highlight``
shades it instead).

Known limits (same spirit as Reducto's): no signature/image/barcode fields; a dropdown or radio value
that is not one of the options is skipped silently; ``enable_overflow_pages`` is accepted and ignored;
text drawn by us is limited to the Windows-1252 character set; rotated pages are not handled.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .common import TaskError, sum_usage
from .llm import LLM, LLMError, LLMResult, ask_json

# docx_color_mode: "text" colours the inserted text (what Reducto's real output does), "highlight" shades it
DEFAULTS: dict[str, Any] = {"max_tokens": 8000, "docx_color_mode": "text"}
WIDGET_TYPES = ("text", "checkbox", "radio", "dropdown", "barcode")

SYSTEM = """You fill in a form. You are given the instructions from the user and the list of the form's fields (id, type, description, options, current value).
Decide which fields the instructions give a value for, and that value.
- text: the string to write, formatted the way the field's description asks for when it says so.
- checkbox: true to tick, false to untick.
- radio and dropdown: exactly one of the listed options.
- Leave out every field the instructions say nothing about. Never invent a value, and never write placeholders such as "None", "N/A" or "-" for a field the instructions give no value for.
- Copy values as the instructions give them. Keep the separators of dates, phone numbers and identifiers (write 03/14/1990, not 03141990) unless the field itself is split into separate boxes or the description gives a maximum length that the value would exceed. A hint such as "mmddyyyy" in a field name describes the order of the parts, not the punctuation.
- When the same field occurs several times (for example several copies of one form on a page), fill every occurrence.
Reply with JSON only, in this shape: {"fields": [{"id": 0, "value": "..."}]}"""


# ---------------------------------------------------------------- shared

@dataclass
class Target:
    """One thing the model (or a preset value) can fill."""

    id: int
    type: str                       # text | checkbox | radio | dropdown
    description: str
    options: list[str] = field(default_factory=list)
    current: Any = None
    fill: bool = True
    value: Any = None               # preset value (form_schema.value); skips the model
    font_size: Optional[float] = None
    ref: Any = None                 # backend handle
    max_len: Optional[int] = None   # /MaxLen of a PDF text field


def _is_true(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return isinstance(v, str) and v.strip().lower() in ("true", "yes", "y", "on", "x", "1", "checked", "selected")


def _is_false(v: Any) -> bool:
    if isinstance(v, bool):
        return not v
    return isinstance(v, str) and v.strip().lower() in ("false", "no", "n", "off", "0", "unchecked", "")


def _norm(s: Any) -> str:
    return " ".join(str(s).strip().lstrip("/").lower().split())


def _pick_option(value: Any, options: list[str]) -> Optional[int]:
    want = _norm(value)
    for i, o in enumerate(options):
        if _norm(o) == want:
            return i
    return None


def _ask_values(targets: list[Target], instructions: str, llm: LLM, opts: dict,
                results: list[LLMResult]) -> dict[int, Any]:
    """Values per target id: presets first, the model for the rest."""
    values: dict[int, Any] = {t.id: t.value for t in targets if t.fill and t.value is not None}
    open_targets = [t for t in targets if t.fill and t.value is None]
    if not open_targets or not instructions.strip():
        return values
    lines = []
    for t in open_targets:
        d: dict[str, Any] = {"id": t.id, "type": t.type, "description": t.description}
        if t.options:
            d["options"] = t.options
        if t.current not in (None, "", False):
            d["current"] = t.current
        if getattr(t, "max_len", None):
            d["max_length"] = t.max_len
        lines.append(json.dumps(d, ensure_ascii=False))
    prompt = "Instructions:\n" + instructions.strip() + "\n\nFields:\n" + "\n".join(lines) + "\n\nReturn the JSON now."
    try:
        data, res = ask_json(llm, SYSTEM, prompt, max_tokens=opts["max_tokens"])
    except LLMError as e:
        raise TaskError(502, f"model call failed: {e}") from e
    results.extend(res)
    items = data.get("fields") if isinstance(data, dict) else data if isinstance(data, list) else None
    ids = {t.id for t in open_targets}
    for it in items or []:
        if isinstance(it, dict) and isinstance(it.get("id"), int) and not isinstance(it.get("id"), bool) \
                and it["id"] in ids and it.get("value") is not None:
            if isinstance(it["value"], str) and it["value"].strip().lower() in ("", "none", "null"):
                continue  # a placeholder, not a value
            values[it["id"]] = it["value"]
    return values


def _hex(color: Any) -> str:
    c = str(color or "#FF0000")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", c):
        raise TaskError(400, "edit_options.color must be a hex colour like #FF0000")
    return c[1:].upper()


def _font_size(v: Any, where: str) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (1 <= v <= 72):
        raise TaskError(400, f"{where} must be a number between 1 and 72")
    return float(v)


# ---------------------------------------------------------------- PDF

@dataclass
class Widget:
    index: int
    page: int                # 0-based
    annot: Any
    fld: Any                 # terminal field dictionary (annot itself or its parent)
    type: str
    name: str
    rect: tuple[float, float, float, float]
    bbox: dict[str, Any]     # normalised, top-left origin, page 1-based
    on_state: Optional[str] = None      # checkbox / radio
    options: list[str] = field(default_factory=list)   # dropdown display values
    exports: list[str] = field(default_factory=list)   # dropdown export values
    label: str = ""
    tooltip: str = ""
    multiline: bool = False
    fld_key: Any = None      # identity of the terminal field (object id/generation)
    created: bool = False    # a field we added to a flat page (not one of the file's own)
    color: Optional[str] = None

    @property
    def description(self) -> str:
        base = self.tooltip or self.label
        if self.type == "radio" and self.on_state:
            base = f"{base}: {self.on_state}" if base and _norm(base) != _norm(self.on_state) else (base or self.on_state)
        if not base:
            return self.name or f"{self.type} field"
        return f"{base} (field: {self.name})" if self.name and _norm(self.name) != _norm(base) else base


def _inherited(obj: Any, key: str) -> Any:
    seen = 0
    while obj is not None and seen < 20:
        if key in obj:
            return obj[key]
        obj = obj.get("/Parent")
        seen += 1
    return None


def _full_name(obj: Any) -> str:
    parts, seen = [], 0
    while obj is not None and seen < 20:
        if "/T" in obj:
            parts.append(str(obj["/T"]))
        obj = obj.get("/Parent")
        seen += 1
    return ".".join(reversed(parts))


def _states(annot: Any) -> list[str]:
    ap = annot.get("/AP")
    n = ap.get("/N") if ap is not None else None
    try:
        return [str(k).lstrip("/") for k in n.keys() if str(k) != "/Off"] if n is not None and not _is_stream(n) else []
    except Exception:
        return []


def _is_stream(obj: Any) -> bool:
    import pikepdf
    return isinstance(obj, pikepdf.Stream)


def _page_box(page: Any) -> tuple[float, float, float, float]:
    box = page.obj.get("/CropBox") or page.mediabox
    x0, y0, x1, y1 = (float(v) for v in box)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _enumerate_widgets(pdf: Any) -> list[Widget]:
    import pikepdf
    widgets: list[Widget] = []
    for pi, page in enumerate(pdf.pages):
        bx0, by0, bx1, by1 = _page_box(page)
        pw, ph = (bx1 - bx0) or 1.0, (by1 - by0) or 1.0
        for annot in page.obj.get("/Annots") or []:
            if annot.get("/Subtype") != pikepdf.Name.Widget or "/Rect" not in annot:
                continue
            ft = _inherited(annot, "/FT")
            if ft is None:
                continue
            ff = int(_inherited(annot, "/Ff") or 0)
            fld = annot if "/T" in annot or "/Parent" not in annot else annot["/Parent"]
            ft = str(ft)
            if ft == "/Tx":
                wtype = "text"
            elif ft == "/Btn":
                if ff & (1 << 16):
                    continue  # push button
                wtype = "radio" if ff & (1 << 15) else "checkbox"
            elif ft == "/Ch":
                wtype = "dropdown"
            else:
                continue  # signatures and anything else: not fillable here
            x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
            x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
            bbox = {"left": round(min(max((x0 - bx0) / pw, 0.0), 1.0), 4),
                    "top": round(min(max((by1 - y1) / ph, 0.0), 1.0), 4),
                    "width": round(min((x1 - x0) / pw, 1.0), 4), "height": round(min((y1 - y0) / ph, 1.0), 4),
                    "page": pi + 1, "original_page": pi + 1}
            w = Widget(index=len(widgets), page=pi, annot=annot, fld=fld, type=wtype, name=_full_name(annot),
                       rect=(x0, y0, x1, y1), bbox=bbox, multiline=bool(ff & (1 << 12)),
                       tooltip=str(_inherited(annot, "/TU") or "").strip())
            w.fld_key = fld.objgen if fld.is_indirect else ("direct", w.index)
            if wtype in ("checkbox", "radio"):
                st = _states(annot)
                w.on_state = st[0] if st else ("Yes" if wtype == "checkbox" else None)
            if wtype == "dropdown":
                for o in _inherited(annot, "/Opt") or []:
                    if isinstance(o, pikepdf.Array) and len(o) >= 2:
                        w.exports.append(str(o[0]))
                        w.options.append(str(o[1]))
                    else:
                        w.exports.append(str(o))
                        w.options.append(str(o))
            widgets.append(w)
    return widgets


def _label_widgets(path: Path, widgets: list[Widget]) -> None:
    """Nearby text from the PDF text layer: same line to the left (to the right for check/radio), else above."""
    if not widgets:
        return
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as doc:
            for pi, page in enumerate(doc.pages):
                mine = [w for w in widgets if w.page == pi]
                if not mine:
                    continue
                pw, ph = float(page.width) or 1.0, float(page.height) or 1.0
                words = [{"text": x["text"], "l": x["x0"] / pw, "r": x["x1"] / pw, "t": x["top"] / ph,
                          "b": x["bottom"] / ph} for x in page.extract_words()]
                boxes = [(w.bbox["left"], w.bbox["top"], w.bbox["left"] + w.bbox["width"],
                          w.bbox["top"] + w.bbox["height"]) for w in mine]
                # words that sit inside a widget are values, not labels
                words = [x for x in words if not any(l <= (x["l"] + x["r"]) / 2 <= r and t <= (x["t"] + x["b"]) / 2 <= b
                                                     for l, t, r, b in boxes)]
                for w in mine:
                    w.label = _nearby_label(w, words, boxes)
    except Exception:
        return  # no usable text layer: descriptions fall back to field names


def _nearby_label(w: Widget, words: list[dict], boxes: list[tuple]) -> str:
    l, t = w.bbox["left"], w.bbox["top"]
    r, b = l + w.bbox["width"], t + w.bbox["height"]

    def same_line(x: dict) -> bool:
        ov = min(b, x["b"]) - max(t, x["t"])
        return ov > 0.5 * min(b - t, x["b"] - x["t"])

    def blocked(x0: float, x1: float) -> bool:  # another widget between the word and this widget
        return any(bl >= x0 - 1e-6 and br <= x1 + 1e-6 and min(b, bb) - max(t, bt) > 0 and (bl, bt, br, bb) != (l, t, r, b)
                   for bl, bt, br, bb in boxes)

    line = sorted((x for x in words if same_line(x)), key=lambda x: x["l"])
    left = [x for x in line if x["r"] <= l + 0.005 and not blocked(x["r"], l)]
    right = [x for x in line if x["l"] >= r - 0.005 and not blocked(r, x["l"])]

    def run(seq: list[dict], from_end: bool) -> str:
        if not seq:
            return ""
        seq = seq[::-1] if from_end else seq
        edge = l if from_end else r
        first = seq[0]
        if (edge - first["r"] if from_end else first["l"] - edge) > 0.25:
            return ""
        out = [first]
        for x in seq[1:]:
            gap = (out[-1]["l"] - x["r"]) if from_end else (x["l"] - out[-1]["r"])
            if gap > 0.03 or len(out) >= 10:
                break
            out.append(x)
        out = out[::-1] if from_end else out
        return " ".join(x["text"] for x in out).strip(" :_.")

    if w.type in ("checkbox", "radio"):
        label = run(right, False) or run(left, True)
    else:
        label = run(left, True)
    if label:
        return label
    h = max(b - t, 0.01)
    above = [x for x in words if x["b"] <= t + 0.003 and t - x["b"] < 2.0 * h and x["r"] > l and x["l"] < r]
    if above:
        base = max(x["b"] for x in above)
        row = sorted((x for x in above if base - x["b"] < 0.5 * h), key=lambda x: x["l"])
        return " ".join(x["text"] for x in row[:10]).strip(" :_.")
    return ""


def _max_len(w: Any) -> Optional[int]:
    try:
        v = _inherited(w.annot, "/MaxLen")
        return int(v) if v is not None and int(v) > 0 else None
    except (TypeError, ValueError):
        return None


def _radio_value(v: Any) -> Optional[str]:
    name = str(v or "").lstrip("/")
    return None if name in ("", "Off") else name


def _widget_schema(w: Widget) -> dict[str, Any]:
    return {"bbox": dict(w.bbox), "description": w.description, "type": w.type, "fill": True, "value": None,
            "font_size": None}


def _overlap(a: dict, b: dict) -> float:
    """Intersection over the smaller box (0 when on different pages)."""
    if a.get("page") != b.get("page"):
        return 0.0
    w = min(a["left"] + a["width"], b["left"] + b["width"]) - max(a["left"], b["left"])
    h = min(a["top"] + a["height"], b["top"] + b["height"]) - max(a["top"], b["top"])
    if w <= 0 or h <= 0:
        return 0.0
    small = min(a["width"] * a["height"], b["width"] * b["height"])
    return (w * h) / small if small > 0 else 0.0


def _check_schema_entry(e: Any, n_pages: int) -> dict[str, Any]:
    if not isinstance(e, dict) or not isinstance(e.get("bbox"), dict) or e.get("type") not in WIDGET_TYPES \
            or not isinstance(e.get("description"), str):
        raise TaskError(400, "form_schema entries need bbox, description and a type of "
                             "text|checkbox|radio|dropdown|barcode")
    box = e["bbox"]
    try:
        bb = {k: float(box[k]) for k in ("left", "top", "width", "height")}
        bb["page"] = int(box.get("page", 1))
    except (KeyError, TypeError, ValueError):
        raise TaskError(400, "form_schema bbox needs left, top, width, height and page") from None
    if not (1 <= bb["page"] <= n_pages):
        raise TaskError(400, f"form_schema bbox page {bb['page']} is outside the document (1-{n_pages})")
    bb["original_page"] = int(box.get("original_page", bb["page"]))
    return {"bbox": bb, "description": e["description"], "type": e["type"], "fill": e.get("fill", True) is not False,
            "value": None if e.get("value") is None else str(e["value"]),
            "font_size": _font_size(e.get("font_size"), "form_schema font_size")}


def _pdf_str(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _wrap(text: str, size: float, width: float) -> list[str]:
    per_line = max(int(width / (0.5 * size)), 1)
    lines: list[str] = []
    for para in text.splitlines() or [""]:
        cur = ""
        for word in para.split():
            if cur and len(cur) + 1 + len(word) > per_line:
                lines.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        lines.append(cur)
    return lines


def _auto_size(height: float, multiline: bool = False) -> float:
    return 10.0 if multiline else round(min(max(height * 0.65, 6.0), 10.0), 1)


def _rgb(hex6: Optional[str]) -> str:
    """PDF fill-colour operator for a 6-digit hex colour (black when None)."""
    if not hex6:
        return "0 g"
    r, g, b = (int(hex6[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return f"{r:.3f} {g:.3f} {b:.3f} rg"


def _text_width(text: str, size: float) -> float:
    """Rough Helvetica width: narrow glyphs count less, capitals and digits more."""
    w = 0.0
    for ch in text:
        w += 0.28 if ch in " .,:;'|!ilIjt()-/" else 0.67 if ch.isupper() or ch in "mwMW@%" else 0.56
    return w * size


def _text_ops(text: str, size: float, w: float, h: float, multiline: bool, font: str = "/Helv",
              color: Optional[str] = None) -> bytes:
    fill = _rgb(color)
    if multiline:
        lines = _wrap(text, size, w - 4)
        y = h - size - 2
        ops = [b"BT", f"{font} {size:g} Tf {fill}".encode(), f"2 {y:.2f} Td {size * 1.15:.2f} TL".encode()]
        for i, ln in enumerate(lines):
            ops.append((b"T* " if i else b"") + b"(" + _pdf_str(ln) + b") Tj")
        ops.append(b"ET")
        return b"\n".join(ops)
    y = max((h - size) / 2 + size * 0.22, 1.0)
    return b"BT\n" + f"{font} {size:g} Tf {fill}\n2 {y:.2f} Td\n".encode() + b"(" + _pdf_str(text) + b") Tj\nET"


def _fit(text: str, width: float, height: float, size: Optional[float]) -> tuple[float, bool, bool]:
    """Font size and line mode for ``text`` in a box: (size, multiline, overflows). A given size is kept;
    otherwise a long single line shrinks, then wraps if the box is tall enough."""
    multiline = height > 30
    eff = size or _auto_size(height, multiline)
    overflow = False
    if size is None:
        if not multiline and _text_width(text, eff) > width - 4:
            fit = eff * (width - 4) / max(_text_width(text, eff), 1e-6)
            if fit >= 5.5:
                eff = round(fit, 1)
            elif height >= 16:
                multiline, eff = True, max(min((height - 4) / 2.4, 9.0), 5.5)
            else:
                eff, overflow = 5.5, True
        if multiline:
            while eff > 5.5 and len(_wrap(text, eff, width - 4)) * eff * 1.15 > height - 2:
                eff = round(eff - 0.5, 1)
    return eff, multiline, overflow


class _PdfForm:
    def __init__(self, src: Path):
        import pikepdf
        self.pk = pikepdf
        self.src = src
        try:
            self.pdf = pikepdf.open(str(src))
        except pikepdf.PasswordError as e:
            raise TaskError(442, "the PDF is password protected") from e
        except Exception as e:
            raise TaskError(400, f"could not open the PDF: {e}") from e
        self.widgets = _enumerate_widgets(self.pdf)
        _label_widgets(src, self.widgets)
        self._helv = None
        self.notes: list[str] = []

    # -- resources
    def helv(self) -> Any:
        if self._helv is None:
            pk = self.pk
            self._helv = self.pdf.make_indirect(pk.Dictionary(
                Type=pk.Name.Font, Subtype=pk.Name.Type1, BaseFont=pk.Name.Helvetica, Encoding=pk.Name.WinAnsiEncoding))
        return self._helv

    def _acroform(self) -> Any:
        return self.pdf.Root.get("/AcroForm")

    def _ensure_dr(self) -> None:
        pk = self.pk
        af = self._acroform()
        if af is None:
            return
        if "/DR" not in af:
            af["/DR"] = pk.Dictionary()
        if "/Font" not in af["/DR"]:
            af["/DR"]["/Font"] = pk.Dictionary()
        if "/Helv" not in af["/DR"]["/Font"]:
            af["/DR"]["/Font"]["/Helv"] = self.helv()

    def _appearance(self, w: Widget, text: str, size: float, color: Optional[str] = None,
                    multiline: Optional[bool] = None) -> None:
        pk = self.pk
        x0, y0, x1, y1 = w.rect
        width, height = x1 - x0, y1 - y0
        multiline = w.multiline if multiline is None else multiline
        body = b"/Tx BMC\nq\n" + _text_ops(text, size, width, height, multiline, color=color) + b"\nQ\nEMC" \
            if text else b"/Tx BMC\nEMC"
        xobj = self.pdf.make_stream(body)
        xobj["/Type"] = pk.Name.XObject
        xobj["/Subtype"] = pk.Name.Form
        xobj["/BBox"] = pk.Array([0, 0, width, height])
        xobj["/Resources"] = pk.Dictionary(Font=pk.Dictionary(Helv=self.helv()))
        w.annot["/AP"] = pk.Dictionary(N=xobj)

    # -- real fields on a flat page
    def _new_acroform(self) -> Any:
        pk = self.pk
        af = self._acroform()
        if af is None:
            af = self.pdf.Root["/AcroForm"] = self.pdf.make_indirect(pk.Dictionary(Fields=pk.Array()))
        if "/Fields" not in af:
            af["/Fields"] = pk.Array()
        if "/DA" not in af:
            af["/DA"] = pk.String("/Helv 0 Tf 0 g")
        self._ensure_dr()
        return af

    def add_field(self, entry: dict, color: Optional[str] = None) -> Optional[Widget]:
        """A real text or checkbox field at a schema box of a flat page. Returns its Widget (None for a
        barcode box or a box with no area)."""
        pk = self.pk
        if entry["type"] not in ("text", "checkbox", "radio", "dropdown"):
            return None
        box = entry["bbox"]
        pi = box["page"] - 1
        page = self.pdf.pages[pi]
        bx0, by0, bx1, by1 = _page_box(page)
        pw, ph = bx1 - bx0, by1 - by0
        x0, y1 = bx0 + box["left"] * pw, by1 - box["top"] * ph
        x1, y0 = x0 + box["width"] * pw, y1 - box["height"] * ph
        if x1 - x0 < 1 or y1 - y0 < 1:
            return None
        af = self._new_acroform()
        taken = {w.name for w in self.widgets}
        n = getattr(self, "_temp_n", 0)
        while f"temp_{n}" in taken:
            n += 1
        self._temp_n = n + 1
        name = f"temp_{n}"
        button = entry["type"] in ("checkbox", "radio")  # a lone radio box behaves as a checkbox
        multiline = not button and (y1 - y0) > 30
        annot = pk.Dictionary(Type=pk.Name.Annot, Subtype=pk.Name.Widget, FT=pk.Name.Btn if button else pk.Name.Tx,
                              T=pk.String(name), Rect=pk.Array([round(v, 3) for v in (x0, y0, x1, y1)]), F=4,
                              Ff=4096 if multiline else 0, P=page.obj,
                              DA=pk.String(f"{_rgb(color)} /Helv 0 Tf"))
        desc = str(entry.get("description") or "").strip()
        if desc:
            annot["/TU"] = pk.String(desc[:500])  # shown as the tooltip and read by screen readers
        annot = self.pdf.make_indirect(annot)
        if "/Annots" not in page.obj:
            page.obj["/Annots"] = pk.Array()
        page.obj["/Annots"].append(annot)
        af["/Fields"].append(annot)
        w = Widget(index=len(self.widgets), page=pi, annot=annot, fld=annot, type="checkbox" if button else "text",
                   name=name, rect=(x0, y0, x1, y1),
                   bbox={**{k: box[k] for k in ("left", "top", "width", "height")}, "page": pi + 1,
                         "original_page": pi + 1},
                   multiline=multiline, tooltip=desc, fld_key=annot.objgen, created=True, color=color)
        if button:
            width, height = x1 - x0, y1 - y0
            side = min(width, height)
            cx, cy, a = width / 2, height / 2, side / 2 - side * 0.15
            stroke = _rgb(color).replace("rg", "RG").replace(" g", " G")
            on = self.pdf.make_stream((f"q {max(side * 0.11, 0.8):.2f} w {stroke} {cx - a:.2f} {cy - a:.2f} m "
                                       f"{cx + a:.2f} {cy + a:.2f} l {cx - a:.2f} {cy + a:.2f} m {cx + a:.2f} "
                                       f"{cy - a:.2f} l S Q").encode())
            off = self.pdf.make_stream(b"")
            for st in (on, off):
                st["/Type"], st["/Subtype"] = pk.Name.XObject, pk.Name.Form
                st["/BBox"] = pk.Array([0, 0, round(width, 3), round(height, 3)])
            w.on_state = "Yes"
            annot["/AP"] = pk.Dictionary(N=pk.Dictionary(Yes=on, Off=off))
            annot["/AS"] = annot["/V"] = pk.Name.Off
            annot["/MK"] = pk.Dictionary(CA=pk.String("8"))  # viewers that redraw it use a cross
        else:
            self._appearance(w, "", 10.0)
        self.widgets.append(w)
        return w

    # -- setters
    def set_text(self, w: Widget, value: Any, size: Optional[float]) -> bool:
        if getattr(w, "created", False):  # our own field on a flat page: sized to its box, in the edit colour
            pk = self.pk
            text = "" if value is None else str(value)
            if text.encode("cp1252", errors="replace") != text.encode("cp1252", errors="ignore"):
                self.notes.append("some characters are outside Windows-1252 and are shown as '?' in the page "
                                  "appearance (the field value keeps them)")
            width, height = w.rect[2] - w.rect[0], w.rect[3] - w.rect[1]
            eff, multiline, overflow = _fit(text, width, height, size)
            if overflow:
                self.notes.append(f"text does not fit its box and may overflow: {text[:40]!r}")
            if multiline and not w.multiline:
                w.multiline = True
                w.annot["/Ff"] = 4096
            w.annot["/V"] = pk.String(text)
            w.annot["/DA"] = pk.String(f"{_rgb(w.color)} /Helv {eff:g} Tf")
            self._appearance(w, text, eff, color=w.color, multiline=multiline)
            return bool(text)
        pk = self.pk
        text = "" if value is None else str(value)
        da = str(_inherited(w.annot, "/DA") or "")
        m = re.search(r"([\d.]+)\s+Tf", da)
        da_size = float(m.group(1)) if m else 0.0
        eff = size or (da_size if da_size > 0 else _auto_size(w.rect[3] - w.rect[1], w.multiline))
        self._ensure_dr()
        w.fld["/V"] = pk.String(text)
        siblings = [x for x in self.widgets if x.fld_key == w.fld_key]
        for x in siblings or [w]:
            x.annot["/DA"] = pk.String(f"/Helv {eff:g} Tf 0 g")
            self._appearance(x, text, eff)
        return True

    def _btn_appearance(self, w: Widget) -> None:
        """Give a check/radio widget on and off appearances when the file has none (needed to flatten)."""
        pk = self.pk
        if _states(w.annot):
            return
        x0, y0, x1, y1 = w.rect
        width, height = x1 - x0, y1 - y0
        on = self.pdf.make_stream(f"q 1 w 0 G 2 2 m {width - 2:.2f} {height - 2:.2f} l 2 {height - 2:.2f} m "
                                  f"{width - 2:.2f} 2 l S Q".encode())
        off = self.pdf.make_stream(b"")
        for s in (on, off):
            s["/Type"], s["/Subtype"], s["/BBox"] = pk.Name.XObject, pk.Name.Form, pk.Array([0, 0, width, height])
        w.on_state = w.on_state or "Yes"
        w.annot["/AP"] = pk.Dictionary(N=pk.Dictionary({"/" + w.on_state: on, "/Off": off}))

    def set_checkbox(self, w: Widget, value: Any) -> bool:
        pk = self.pk
        if not (_is_true(value) or _is_false(value)):
            if w.on_state and _norm(value) == _norm(w.on_state):
                value = True
            else:
                return False
        on = _is_true(value)
        self._btn_appearance(w)
        state = pk.Name("/" + (w.on_state or "Yes")) if on else pk.Name.Off
        w.fld["/V"] = state
        w.annot["/AS"] = state
        return True

    def radio_group(self, w: Widget) -> list[Widget]:
        return [x for x in self.widgets if x.type == "radio" and (x.fld_key == w.fld_key or
                                                                  (x.name and x.name == w.name))]

    def set_radio(self, group: list[Widget], value: Any, labels: Optional[list[str]] = None) -> bool:
        pk = self.pk
        names = [x.on_state or "" for x in group]
        i = _pick_option(value, names)
        if i is None and labels:
            i = _pick_option(value, labels)
        if i is None:
            return False  # not one of the options: skip silently
        for x in group:
            self._btn_appearance(x)
        chosen = group[i]
        chosen.fld["/V"] = pk.Name("/" + (chosen.on_state or "Yes"))
        for x in group:
            x.annot["/AS"] = pk.Name("/" + (x.on_state or "Yes")) if x is chosen else pk.Name.Off
        return True

    def set_dropdown(self, w: Widget, value: Any, size: Optional[float]) -> bool:
        pk = self.pk
        i = _pick_option(value, w.options)
        if i is None:
            i = _pick_option(value, w.exports)
        if i is None:
            return False  # option does not exist: skip silently, as Reducto does
        w.fld["/V"] = pk.String(w.exports[i])
        eff = size or _auto_size(w.rect[3] - w.rect[1])
        self._ensure_dr()
        w.annot["/DA"] = pk.String(f"/Helv {eff:g} Tf 0 g")
        self._appearance(w, w.options[i], eff)
        return True

    # -- drawing on a flat page
    def draw(self, entry: dict, value: Any, size: Optional[float], color: Optional[str] = None) -> bool:
        pk = self.pk
        box = entry["bbox"]
        page = self.pdf.pages[box["page"] - 1]
        bx0, by0, bx1, by1 = _page_box(page)
        pw, ph = bx1 - bx0, by1 - by0
        x0 = bx0 + box["left"] * pw
        y1 = by1 - box["top"] * ph
        width, height = box["width"] * pw, box["height"] * ph
        y0 = y1 - height
        if entry["type"] in ("checkbox", "radio"):
            if not _is_true(value):
                return _is_false(value)
            side = min(width, height)  # a cross in the centred square of the box
            cx, cy, pad = x0 + width / 2, y0 + height / 2, side * 0.15
            a = side / 2 - pad
            stroke = _rgb(color).replace("rg", "RG").replace(" g", " G")
            ops = (f"q {max(side * 0.11, 0.8):.2f} w {stroke} {cx - a:.2f} {cy - a:.2f} m {cx + a:.2f} {cy + a:.2f} l "
                   f"{cx - a:.2f} {cy + a:.2f} m {cx + a:.2f} {cy - a:.2f} l S Q").encode()
        else:
            text = str(value)
            if not text:
                return False
            if text.encode("cp1252", errors="replace") != text.encode("cp1252", errors="ignore"):
                self.notes.append("some characters are outside Windows-1252 and were drawn as '?'")
            eff, multiline, overflow = _fit(text, width, height, size)
            if overflow:
                self.notes.append(f"text does not fit its box and may overflow: {text[:40]!r}")
            name = page.add_resource(self.helv(), pk.Name.Font, prefix="DFHelv")
            ops = (f"q 1 0 0 1 {x0:.2f} {y0:.2f} cm\n".encode()
                   + _text_ops(text, eff, width, height, multiline, font=str(name), color=color) + b"\nQ")
        page.contents_add(self.pdf.make_stream(b"q"), prepend=True)
        page.contents_add(self.pdf.make_stream(b"Q\n" + ops))
        return True

    def finish(self, out: Path, flatten: bool) -> None:
        pk = self.pk
        af = self._acroform()
        if af is not None:
            if flatten:
                if "/NeedAppearances" in af:
                    del af["/NeedAppearances"]
                self.pdf.flatten_annotations("all")
                for page in self.pdf.pages:
                    annots = page.obj.get("/Annots")
                    if annots is not None:
                        keep = [a for a in annots if a.get("/Subtype") != pk.Name.Widget]
                        if keep:
                            page.obj["/Annots"] = pk.Array(keep)
                        else:
                            del page.obj["/Annots"]
                if "/AcroForm" in self.pdf.Root:
                    del self.pdf.Root["/AcroForm"]
            elif any(not getattr(w, "created", False) for w in self.widgets):
                af["/NeedAppearances"] = True  # the file's own fields: let the viewer redraw them
        self.pdf.save(str(out))
        self.pdf.close()


def _edit_pdf(src: Path, out: Path, config: dict, llm: LLM, opts: dict, results: list[LLMResult]) -> tuple[list, int]:
    eo = config.get("edit_options") or {}
    color = _hex(eo.get("color"))
    global_size = _font_size(eo.get("font_size"), "edit_options.font_size")
    form = _PdfForm(src)
    n_pages = len(form.pdf.pages)
    incoming = config.get("form_schema")
    instructions = str(config.get("edit_instructions") or "")
    targets: list[Target] = []
    written: dict[int, Any] = {}   # schema index / widget index -> value actually written

    def shown(v: Any) -> Optional[str]:
        return None if v is None else ("True" if v is True else "False" if v is False else str(v))

    if incoming is None and not form.widgets:
        # flat or scanned form: find the fields on the page images, then fill them like a given schema
        from .detect import detect_fields
        incoming = detect_fields(src, n_pages, llm, results, max_tokens=opts.get("detect_max_tokens", 16000),
                                 render=opts.get("render"))
        if not incoming:
            form.pdf.close()
            raise TaskError(422, "no fillable fields were found on this PDF; pass a form_schema with the boxes to fill")

    if incoming is not None:
        if not isinstance(incoming, list):
            raise TaskError(400, "form_schema must be a list")
        schema = [_check_schema_entry(e, n_pages) for e in incoming]
        as_fields = eo.get("flat_fields", opts.get("flat_fields", True)) is not False
        existing = list(form.widgets)  # overlap is tested against the file's own widgets only
        for i, e in enumerate(schema):
            best, best_ov = None, 0.0
            for w in existing:
                ov = _overlap(e["bbox"], w.bbox)
                if ov > best_ov:
                    best, best_ov = w, ov
            w = best if best is not None and best_ov >= 0.5 else None  # the >= 50% overlap rule
            if w is None and as_fields:
                w = form.add_field(e, color)  # flat page: a real field, also for boxes that stay empty
            kind = w.type if w is not None else ("text" if e["type"] == "barcode" else e["type"])
            targets.append(Target(id=i, type=kind, description=e["description"],
                                  options=list(w.options) if w is not None and w.type == "dropdown" else [],
                                  fill=e["fill"] and e["type"] != "barcode", value=e["value"],
                                  font_size=e["font_size"], ref=(w, e)))
        values = _ask_values(targets, instructions, llm, opts, results)
        for t in targets:
            if t.id not in values:
                continue
            w, e = t.ref
            v, size = values[t.id], t.font_size or global_size
            ok = False
            if w is None:
                if e["type"] in ("checkbox", "radio") and not (_is_true(v) or _is_false(v)):
                    v = _norm(v) not in ("", "no", "off", "unchecked", "none")
                ok = form.draw(e, v, size, color)
                ok = ok and not (e["type"] in ("checkbox", "radio") and not _is_true(v))
                v = True if ok and e["type"] in ("checkbox", "radio") else v
            elif w.type == "text":
                ok = form.set_text(w, v, size)
            elif w.type == "checkbox":
                if getattr(w, "created", False) and not (_is_true(v) or _is_false(v)):
                    v = _norm(v) not in ("", "no", "off", "unchecked", "none")
                ok = form.set_checkbox(w, v) and _is_true(v)
                v = True if ok and getattr(w, "created", False) else v
            elif w.type == "radio":
                if _is_true(v) or (w.on_state and _norm(v) == _norm(w.on_state)):
                    ok = form.set_radio(form.radio_group(w), w.on_state)
                    v = True
            elif w.type == "dropdown":
                ok = form.set_dropdown(w, v, size)
            if ok:
                written[t.id] = v
        form_schema = [{**{k: e[k] for k in ("bbox", "description", "type", "fill", "font_size")},
                        "value": shown(written[i]) if i in written else e["value"], "filled": i in written}
                       for i, e in enumerate(schema)]
    else:
        seen: set[int] = set()
        for w in form.widgets:
            if w.index in seen:
                continue
            if w.type == "radio":
                group = form.radio_group(w)
                seen.update(x.index for x in group)
                labels = [x.label or x.on_state or "" for x in group]
                states = [x.on_state or "" for x in group]
                opts_shown = labels if len(set(map(_norm, labels))) == len(labels) and all(labels) else states
                desc = w.tooltip or w.name or "radio group"
                targets.append(Target(id=len(targets), type="radio", description=desc, options=opts_shown,
                                      current=_radio_value(w.fld.get("/V")), ref=(group, labels)))
            else:
                seen.add(w.index)
                cur = w.fld.get("/V")
                cur = (str(cur).lstrip("/") not in ("", "Off")) if w.type == "checkbox" and cur is not None else \
                    (str(cur) if cur is not None else None)
                targets.append(Target(id=len(targets), type=w.type, description=w.description,
                                      options=list(w.options), current=cur, ref=w,
                                      max_len=_max_len(w) if w.type == "text" else None))
        values = _ask_values(targets, instructions, llm, opts, results)
        for t in targets:
            if t.id not in values:
                continue
            v = values[t.id]
            if t.type == "radio":
                group, labels = t.ref
                if form.set_radio(group, v, labels):
                    for x in group:
                        if str(x.annot.get("/AS") or "").lstrip("/") not in ("", "Off"):
                            written[x.index] = True
            elif t.type == "text":
                if form.set_text(t.ref, v, global_size) and str(v) != "":
                    for x in form.widgets:
                        if x.fld_key == t.ref.fld_key:
                            written[x.index] = v
            elif t.type == "checkbox":
                if form.set_checkbox(t.ref, v) and _is_true(v):
                    written[t.ref.index] = True
            elif t.type == "dropdown":
                if form.set_dropdown(t.ref, v, global_size):
                    written[t.ref.index] = str(t.ref.fld.get("/V"))
        form_schema = [{**_widget_schema(w), "value": shown(written.get(w.index)), "filled": w.index in written}
                       for w in form.widgets]
    form.finish(out, bool(eo.get("flatten")))
    return form_schema, n_pages


# ---------------------------------------------------------------- DOCX

_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"


def _edit_docx(src: Path, out: Path, config: dict, llm: LLM, opts: dict, results: list[LLMResult]) -> None:
    try:
        import docx
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
    except ImportError as e:  # pragma: no cover
        raise TaskError(501, "DOCX editing needs python-docx") from e
    eo = config.get("edit_options") or {}
    color = _hex(eo.get("color"))
    size = _font_size(eo.get("font_size"), "edit_options.font_size")
    try:
        d = docx.Document(str(src))
    except Exception as e:
        raise TaskError(400, f"could not open the DOCX: {e}") from e
    w14 = lambda tag: f"{{{_W14}}}{tag}"  # noqa: E731

    def style_run(r: Any) -> None:
        rpr = r.find(qn("w:rPr"))
        if rpr is None:
            rpr = OxmlElement("w:rPr")
            r.insert(0, rpr)
        for el in rpr.findall(qn("w:rStyle")):
            if "placeholder" in (el.get(qn("w:val")) or "").lower():
                rpr.remove(el)
        if opts.get("docx_color_mode") == "text":
            el = rpr.find(qn("w:color"))
            if el is None:
                el = OxmlElement("w:color")
                rpr.append(el)
            el.set(qn("w:val"), color)
        else:
            for old in rpr.findall(qn("w:shd")):
                rpr.remove(old)
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), color)
            rpr.append(shd)
        if size:
            for tag in ("w:sz", "w:szCs"):
                for old in rpr.findall(qn(tag)):
                    rpr.remove(old)
                el = OxmlElement(tag)
                el.set(qn("w:val"), str(int(round(size * 2))))
                rpr.append(el)

    def set_sdt_text(sdt: Any, text: str) -> None:
        content = sdt.find(qn("w:sdtContent"))
        if content is None:
            content = OxmlElement("w:sdtContent")
            sdt.append(content)
        runs = list(content.iter(qn("w:r")))
        if runs:
            first = runs[0]
            for r in runs[1:]:
                r.getparent().remove(r)
        else:
            first = OxmlElement("w:r")
            paras = list(content.iter(qn("w:p")))
            (paras[0] if paras else content).append(first)
        for t in list(first):
            if t.tag != qn("w:rPr"):
                first.remove(t)
        t = OxmlElement("w:t")
        t.text = text
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        first.append(t)
        style_run(first)
        pr = sdt.find(qn("w:sdtPr"))
        if pr is not None:
            for el in pr.findall(qn("w:showingPlcHdr")):
                pr.remove(el)

    targets: list[Target] = []
    for sdt in d.element.body.iter(qn("w:sdt")):
        pr = sdt.find(qn("w:sdtPr"))
        alias = tag = ""
        kind, options, values_ = "text", [], []
        if pr is not None:
            a, tg = pr.find(qn("w:alias")), pr.find(qn("w:tag"))
            alias = (a.get(qn("w:val")) if a is not None else "") or ""
            tag = (tg.get(qn("w:val")) if tg is not None else "") or ""
            lst = pr.find(qn("w:dropDownList"))
            lst = lst if lst is not None else pr.find(qn("w:comboBox"))
            if pr.find(w14("checkbox")) is not None:
                kind = "checkbox"
            elif lst is not None:
                kind = "dropdown"
                for li in lst.findall(qn("w:listItem")):
                    options.append(li.get(qn("w:displayText")) or li.get(qn("w:value")) or "")
                    values_.append(li.get(qn("w:value")) or "")
        content = sdt.find(qn("w:sdtContent"))
        current = "".join(t.text or "" for t in content.iter(qn("w:t"))) if content is not None else ""
        showing_placeholder = pr is not None and pr.find(qn("w:showingPlcHdr")) is not None
        desc = alias or tag or (current if showing_placeholder else "") or "content control"
        if alias and tag and _norm(alias) != _norm(tag):
            desc = f"{alias} (tag: {tag})"
        targets.append(Target(id=len(targets), type=kind, description=desc, options=options,
                              current=None if showing_placeholder else current, ref=("sdt", sdt, values_)))

    seen_cells: set[int] = set()
    for ti, table in enumerate(d.tables):
        rows = table.rows
        for ri, row in enumerate(rows):
            cells = row.cells
            for ci, cell in enumerate(cells):
                key = id(cell._tc)
                if key in seen_cells:
                    continue
                seen_cells.add(key)
                if cell.text.strip() or cell._tc.find(".//" + qn("w:sdt")) is not None:
                    continue
                left = next((cells[k].text.strip() for k in range(ci - 1, -1, -1)
                             if cells[k]._tc is not cell._tc and cells[k].text.strip()), "")
                head = rows[0].cells[ci].text.strip() if ri > 0 and ci < len(rows[0].cells) else ""
                if not left and not head:
                    continue
                desc = f"table {ti + 1}, row {ri + 1}: " + " / ".join(x for x in (left, head) if x)
                targets.append(Target(id=len(targets), type="text", description=desc, ref=("cell", cell, None)))

    values = _ask_values(targets, str(config.get("edit_instructions") or ""), llm, opts, results)
    for t in targets:
        if t.id not in values:
            continue
        v = values[t.id]
        kind, obj, extra = t.ref
        if kind == "cell":
            para = obj.paragraphs[0] if obj.paragraphs else obj.add_paragraph()
            style_run(para.add_run(str(v))._r)
        elif t.type == "checkbox":
            if not (_is_true(v) or _is_false(v)):
                continue
            box = obj.find(qn("w:sdtPr")).find(w14("checkbox"))
            checked = box.find(w14("checked"))
            if checked is None:
                checked = box.makeelement(w14("checked"), {})
                box.insert(0, checked)
            checked.set(w14("val"), "1" if _is_true(v) else "0")
            set_sdt_text(obj, "☒" if _is_true(v) else "☐")
        elif t.type == "dropdown":
            i = _pick_option(v, t.options)
            if i is None:
                i = _pick_option(v, extra)
            if i is None:
                continue  # not one of the options: skip silently
            set_sdt_text(obj, t.options[i])
        else:
            set_sdt_text(obj, str(v))
    d.save(str(out))


# ---------------------------------------------------------------- entry point

def _sniff(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(1024)
    if b"%PDF-" in head:
        return "pdf"
    if head[:2] == b"PK" and path.suffix.lower() in (".docx", ".docm", ""):
        return "docx"
    if head[:2] == b"PK":
        return "docx" if path.suffix.lower() not in (".xlsx", ".pptx") else path.suffix.lower()[1:]
    return path.suffix.lower().lstrip(".") or "unknown"


def run(document: Any, config: dict, *, llm: LLM, source_path: Any, output_path: Any = None,
        options: Optional[dict] = None) -> dict:
    opts = {**DEFAULTS, **(options or {})}
    config = config or {}
    if not isinstance(config.get("edit_instructions"), str):
        raise TaskError(400, "edit_instructions must be a string")
    src = Path(source_path)
    if not src.is_file():
        raise TaskError(400, f"document not found: {src}")
    kind = _sniff(src)
    if kind not in ("pdf", "docx"):
        raise TaskError(415, f"edit supports PDF and DOCX files, not {kind!r}")
    if output_path is None:
        out_dir = Path(tempfile.mkdtemp(prefix="readanydoc_edit_"))
        out = out_dir / f"{src.stem}.edited.{kind}"
    else:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
    results: list[LLMResult] = []
    try:
        if kind == "pdf":
            form_schema, n_pages = _edit_pdf(src, out, config, llm, opts, results)
        else:
            if config.get("form_schema"):
                raise TaskError(400, "form_schema only works for PDFs")
            _edit_docx(src, out, config, llm, opts, results)
            form_schema = None
            n_pages = len(document.pages) if getattr(document, "pages", None) else 1
    except TaskError:
        if output_path is None:
            shutil.rmtree(out.parent, ignore_errors=True)
        raise
    spent = sum_usage(results)
    if getattr(document, "usage", None) is not None:
        document.usage.append({"task": "edit", **spent})
    usage: dict[str, Any] = {"num_pages": n_pages, "credits": None}
    if spent["calls"]:  # Edit has no parsed Document to carry its calls, so it reports them itself
        m = {"calls": spent["calls"], "input_tokens": spent["input_tokens"], "output_tokens": spent["output_tokens"],
             "cached_calls": 0}
        usage["model_usage"] = {**m, "models": {spent.get("model") or "task:edit": dict(m)}}
    return {"response_type": "edit", "document_url": str(out), "form_schema": form_schema, "usage": usage}
