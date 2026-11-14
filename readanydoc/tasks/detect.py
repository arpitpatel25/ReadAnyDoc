"""Form-field detection on flat and scanned PDF pages (no AcroForm widgets).

One model call per page on the page image returns the fields a person would fill in (label, type,
approximate box). The box is then refined on the pixels, never trusted as given: inside the model's
box the largest rectangle that is free of ink is the place to write, so the printed label and the
ruling lines are not written over. The result is a ``form_schema`` (Reducto ``EditWidget`` entries)
that Edit fills through its normal ``form_schema`` path and returns to the caller, who can pass it
back to skip detection.

Limits: a field the model does not report is not found; boxes on low-contrast scans fall back to the
model's box (inset); rotated pages are not handled.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Optional

from .common import TaskError
from .llm import LLM, LLMError, LLMResult, ask_json

SYSTEM = """You find the fillable fields of a form on a page image. A field is a place where a person filling in the form writes or ticks: an empty cell or box with a printed label, a blank line after a label, a checkbox.
For every field return:
- "description": the printed label with its item number if it has one (for example "12 Wages, tips, other compensation"). When the same form is printed several times on the page, add which copy it is (for example "(copy 2 of 3)"). For a checkbox, say what ticking it means (for example "3. SEX: F").
- "type": "text" or "checkbox".
- "bbox": [left, top, right, bottom] in thousandths of the page width and height (0-1000). For a text field, the whole cell or blank area that belongs to the field; for a checkbox, the small square itself.
Rules:
- Report every field, also the ones that stay empty. Do not report areas marked for official use or "make no entry".
- Do not report a field twice. Do not report printed text, headings or instructions as fields.
- Where a field is split into sub-fields by tick marks or small labels (MM / DD / YY, area code and number), report it as one field unless the parts are separate boxes.
Reply with JSON only, in this shape:
{"fields": [{"description": "...", "type": "text", "bbox": [0, 0, 0, 0]}]}"""

INK = 150          # grey level below which a pixel is ink
CELL = 3           # px per grid cell for the empty-rectangle search
MAX_FIELDS = 400


def _largest_empty_rect(grid: list[list[bool]]) -> Optional[tuple[int, int, int, int]]:
    """Largest all-False rectangle of a boolean grid as (col0, row0, col1, row1), end-exclusive."""
    if not grid or not grid[0]:
        return None
    cols = len(grid[0])
    heights = [0] * cols
    best, best_area = None, 0
    for r, row in enumerate(grid):
        for c in range(cols):
            heights[c] = 0 if row[c] else heights[c] + 1
        stack: list[int] = []
        for c in range(cols + 1):
            h = heights[c] if c < cols else 0
            while stack and heights[stack[-1]] >= h:
                top = stack.pop()
                left = stack[-1] + 1 if stack else 0
                area = heights[top] * (c - left)
                if area > best_area:
                    best_area = area
                    best = (left, r - heights[top] + 1, c, r + 1)
            stack.append(c)
    return best


def _open_band(grid: list[list[bool]], min_share: float = 0.8) -> Optional[tuple[int, int, int, int]]:
    """Tallest band of rows that are free of ink across at least ``min_share`` of the width (the blank
    line under or above a label), as (col0, row0, col1, row1); None when no row is that open."""
    cols = len(grid[0])
    runs: list[Optional[tuple[int, int]]] = []
    for row in grid:
        best, start = (0, 0), None
        for c in range(cols + 1):
            free = c < cols and not row[c]
            if free and start is None:
                start = c
            elif not free and start is not None:
                if c - start > best[1] - best[0]:
                    best = (start, c)
                start = None
        runs.append(best if best[1] - best[0] >= min_share * cols else None)
    out, r = None, 0
    while r < len(runs):
        if runs[r] is None:
            r += 1
            continue
        r0, c0, c1 = r, runs[r][0], runs[r][1]
        while r < len(runs) and runs[r] is not None and min(c1, runs[r][1]) - max(c0, runs[r][0]) >= min_share * cols:
            c0, c1 = max(c0, runs[r][0]), min(c1, runs[r][1])
            r += 1
        if out is None or r - r0 > out[3] - out[1]:
            out = (c0, r0, c1, r)
    return out


def snap_checkbox(gray: Any, box: tuple[float, float, float, float]) -> Optional[tuple[float, float, float, float]]:
    """The inside of the printed square nearest to the model's checkbox box: an enclosed, roughly square
    blank area about the size of the box. None when there is no such square."""
    W, H = gray.size
    x0, y0, x1, y1 = box
    bw, bh = (x1 - x0) * W, (y1 - y0) * H
    side = max(bw, bh)
    if side < 4 or side > 0.08 * max(W, H):
        return None
    m = side
    px0, py0 = max(int(x0 * W - m), 0), max(int(y0 * H - m), 0)
    px1, py1 = min(int(x1 * W + m) + 1, W), min(int(y1 * H + m) + 1, H)
    crop = gray.crop((px0, py0, px1, py1))
    w, h = crop.size
    data = crop.tobytes()
    seen = bytearray(1 if v < INK else 0 for v in data)   # ink counts as visited
    cx, cy = (x0 + x1) / 2 * W - px0, (y0 + y1) / 2 * H - py0
    best, best_d = None, None
    for start in range(w * h):
        if seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        n, minx, maxx, miny, maxy, border = 0, w, 0, h, 0, False
        while stack:
            i = stack.pop()
            y, x = divmod(i, w)
            n += 1
            minx, maxx, miny, maxy = min(minx, x), max(maxx, x), min(miny, y), max(maxy, y)
            if x == 0 or y == 0 or x == w - 1 or y == h - 1:
                border = True
            for j in ((i - 1) if x else -1, (i + 1) if x < w - 1 else -1, i - w, i + w):
                if 0 <= j < w * h and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        cw, ch = maxx - minx + 1, maxy - miny + 1
        if border or min(cw, ch) < max(0.45 * min(bw, bh), 4) or max(cw, ch) > 1.8 * side:
            continue
        if not (0.6 <= cw / ch <= 1.7) or n < 0.7 * cw * ch:
            continue
        d = ((minx + maxx) / 2 - cx) ** 2 + ((miny + maxy) / 2 - cy) ** 2
        if best_d is None or d < best_d:
            best, best_d = (minx, miny, maxx + 1, maxy + 1), d
    if best is None:
        return None
    return (px0 + best[0]) / W, (py0 + best[1]) / H, (px0 + best[2]) / W, (py0 + best[3]) / H


def refine_box(gray: Any, box: tuple[float, float, float, float], kind: str,
               _depth: int = 0) -> tuple[float, float, float, float]:
    """Shrink a normalised (left, top, right, bottom) box to its largest ink-free rectangle.

    ``gray`` is a PIL "L" image of the page. Returns the box unchanged (slightly inset) when nothing
    useful is found, so a refinement can only move the text off printed matter, never lose the field.
    A text field prefers the blank band that spans the cell (under or above the label) over a larger
    blank area beside the label; a checkbox snaps to the printed square nearest to the model's box."""
    W, H = gray.size
    x0, y0, x1, y1 = (max(0.0, min(1.0, v)) for v in box)
    if kind == "checkbox":
        snapped = snap_checkbox(gray, (x0, y0, x1, y1))
        if snapped is not None:
            return snapped
    px0, py0, px1, py1 = int(x0 * W), int(y0 * H), int(round(x1 * W)), int(round(y1 * H))
    if px1 - px0 < 2 * CELL or py1 - py0 < 2 * CELL:
        return x0, y0, x1, y1
    crop = gray.crop((px0, py0, px1, py1))
    w, h = crop.size
    data = crop.tobytes()
    cols, rows = w // CELL, h // CELL
    grid = [[False] * cols for _ in range(rows)]
    for r in range(rows):
        base = r * CELL
        for dy in range(CELL):
            line = data[(base + dy) * w:(base + dy) * w + cols * CELL]
            if min(line) >= INK:
                continue
            grow = grid[r]
            for c in range(cols):
                if not grow[c] and min(line[c * CELL:(c + 1) * CELL]) < INK:
                    grow[c] = True
    rect = _largest_empty_rect(grid)
    if rect is None:
        return x0, y0, x1, y1
    if kind == "text":
        band = _open_band(grid)
        if band is not None and (band[3] - band[1]) * CELL >= max(14, 0.5 * (rect[3] - rect[1]) * CELL):
            rect = band   # a line of text fits in the band
    c0, r0, c1, r1 = rect
    rw, rh = (c1 - c0) * CELL, (r1 - r0) * CELL
    # too small to write in (dense print, or the model's box sits on text): keep the model's box
    min_h = 6 if kind == "text" else 5
    if rh < min_h or rw < (12 if kind == "text" else 5) or rw * rh < 0.12 * w * h:
        if kind == "text" and _depth == 0:
            # the model's box sits on print (often on the label): look just below and above it
            bh = y1 - y0
            wider = refine_box(gray, (x0, max(y0 - bh, 0.0), x1, min(y1 + bh, 1.0)), kind, _depth=1)
            if wider != (x0, max(y0 - bh, 0.0), x1, min(y1 + bh, 1.0)) and (wider[3] - wider[1]) * H >= 8 \
                    and (wider[2] - wider[0]) >= 0.4 * (x1 - x0):
                return wider
        return x0, y0, x1, y1
    pad = 1
    nx0, ny0 = px0 + c0 * CELL + pad, py0 + r0 * CELL + pad
    nx1, ny1 = px0 + c1 * CELL - pad, py0 + r1 * CELL - pad
    return nx0 / W, ny0 / H, nx1 / W, ny1 / H


def _parse_fields(data: Any) -> list[dict]:
    items = data.get("fields") if isinstance(data, dict) else data if isinstance(data, list) else None
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        bb = it.get("bbox")
        if isinstance(bb, dict):
            bb = [bb.get(k) for k in ("left", "top", "right", "bottom")]
        if not (isinstance(bb, list) and len(bb) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bb)):
            continue
        scale = 1000.0 if max(bb) > 1.5 else 1.0
        l, t, r, b = (float(v) / scale for v in bb)
        if r <= l or b <= t or l < -0.01 or t < -0.01 or r > 1.01 or b > 1.01:
            continue
        kind = str(it.get("type") or "text").strip().lower()
        kind = "checkbox" if kind in ("checkbox", "radio", "check") else "text"
        desc = str(it.get("description") or "").strip()
        if not desc:
            continue
        out.append({"description": desc, "type": kind, "box": (l, t, r, b)})
    return out[:MAX_FIELDS]


def _dedupe(fields: list[dict]) -> list[dict]:
    """Drop a field whose box is almost the same as an earlier one (the model listing a field twice)."""
    kept: list[dict] = []
    for f in fields:
        l, t, r, b = f["box"]
        dup = False
        for k in kept:
            kl, kt, kr, kb = k["box"]
            iw, ih = min(r, kr) - max(l, kl), min(b, kb) - max(t, kt)
            if iw > 0 and ih > 0 and iw * ih >= 0.8 * min((r - l) * (b - t), (kr - kl) * (kb - kt)) \
                    and f["type"] == k["type"]:
                dup = True
                break
        if not dup:
            kept.append(f)
    return kept


def detect_fields(src: Path, n_pages: int, llm: LLM, results: list[LLMResult], *, max_tokens: int = 16000,
                  render: Any = None) -> list[dict[str, Any]]:
    """``form_schema`` entries for every page of a flat PDF. ``render(path, page_number) -> PNG bytes``
    can be injected for tests; the default renders with pdfium at the model's native image size."""
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover
        raise TaskError(501, "field detection needs Pillow") from e
    if not getattr(llm, "supports_images", False):
        raise TaskError(501, "field detection on a flat PDF needs a model that reads images")
    if render is None:
        from ..readers.sizing import render_pdf_native
        render = render_pdf_native
    schema: list[dict[str, Any]] = []
    for page in range(1, n_pages + 1):
        try:
            png = render(src, page)
        except Exception as e:
            raise TaskError(400, f"could not render page {page} of the PDF: {e}") from e
        try:
            data, res = ask_json(llm, SYSTEM, "Find every fillable field on this page.", images=[png],
                                 max_tokens=max_tokens)
        except LLMError as e:
            raise TaskError(502, f"model call failed: {e}") from e
        results.extend(res)
        gray = Image.open(io.BytesIO(png)).convert("L")
        for f in _dedupe(_parse_fields(data)):
            l, t, r, b = refine_box(gray, f["box"], f["type"])
            schema.append({"bbox": {"left": round(l, 4), "top": round(t, 4), "width": round(r - l, 4),
                                    "height": round(b - t, 4), "page": page, "original_page": page},
                           "description": f["description"], "type": f["type"], "fill": True, "value": None,
                           "font_size": None})
    return schema
