"""Zoom vote (engine v2). A scanned page is read again as horizontal strips enlarged as far as the
model allows (wider than a whole-page read can be), twice with different cuts. The page read keeps the structure; a token of it is replaced only when both strip
reads give the same, similar, different token. Measured 2026-11-12 on seven scanned tables: the
strips fixed 18-19 of 24 digit errors the judge had found in the page read.
"""

from __future__ import annotations

import difflib
import re
from typing import Optional

TOKEN = re.compile(r"\[illegible\]|\d[\d,.]*\d|\d|[^\W\d_]+")
_MARKUP = re.compile(r"<[^>]*>|!\[[^\]]*\]\([^)]*\)")


def tokens(text: str) -> list[tuple[str, int, int]]:
    masked = _MARKUP.sub(lambda m: " " * len(m.group()), text)
    return [(m.group(), m.start(), m.end()) for m in TOKEN.finditer(masked)]


def alternatives(page_tokens: list[tuple[str, int, int]], other_text: str) -> dict[int, str]:
    """page token index -> the token the other read has in the aligned position."""
    a = [t[0].lower() for t in page_tokens]
    other = [t[0] for t in tokens(other_text)]
    b = [t.lower() for t in other]
    out: dict[int, str] = {}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                out[i1 + k] = other[j1 + k]
    return out


def similar(p: str, q: str) -> bool:
    if p == "[illegible]":
        return q != "[illegible]"
    if q == "[illegible]":
        return False
    pd, qd = any(c.isdigit() for c in p), any(c.isdigit() for c in q)
    if pd != qd:
        return False
    if pd:
        return abs(len(p) - len(q)) <= 1 and difflib.SequenceMatcher(None, p, q).ratio() >= 0.5
    return difflib.SequenceMatcher(None, p.lower(), q.lower()).ratio() >= 0.6


def disputes(page_text: str, strip_text: str) -> int:
    pt = tokens(page_text)
    alt = alternatives(pt, strip_text)
    return sum(1 for i, (t, _, _) in enumerate(pt) if i in alt and alt[i].lower() != t.lower() and similar(t, alt[i]))


def vote(page_text: str, a_text: str, b_text: str) -> tuple[str, list[tuple[str, str]]]:
    pt = tokens(page_text)
    a, b = alternatives(pt, a_text), alternatives(pt, b_text)
    out, last, changes = [], 0, []
    for i, (t, s, e) in enumerate(pt):
        x, y = a.get(i), b.get(i)
        if x and y and x == y and x.lower() != t.lower() and similar(t, x):
            out.append(page_text[last:s])
            out.append(x)
            last = e
            changes.append((t, x))
    out.append(page_text[last:])
    return "".join(out), changes


def strip_boxes(n: int, overlap: float = 0.12) -> list[tuple[float, float, float, float]]:
    frac = 1.0 / (n - (n - 1) * overlap)
    return [(0.0, min(i * frac * (1 - overlap), 1 - frac), 1.0, min(i * frac * (1 - overlap) + frac, 1.0))
            for i in range(n)]


def source_zoom(pdf_path: str, page_number: int, password: Optional[str] = None) -> float:
    """How many times more pixels per inch the largest embedded image has than the page read keeps."""
    import pypdfium2 as pdfium

    from ...readers import sizing
    with sizing._PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path), password=password)
        try:
            page = pdf[page_number - 1]
            w, h = page.get_size()
            src = 0.0
            for o in page.get_objects():
                if o.type == 3:  # image object
                    try:
                        src = max(src, o.get_metadata().width / w)
                    except Exception:
                        continue
        finally:
            pdf.close()
    nat = sizing.native_scale(w, h)
    return src / nat if nat else 0.0


_CHECKBOX = re.compile(r"\[ ?[xX\u2713]? ?\]|[\u2610\u2611\u2612\u25a1]")
_TABLE = re.compile(r"<table.*?</table>", re.S | re.I)


def tangled_form(text: str, min_boxes: int = 2) -> bool:
    """A page read that put checkboxes inside tables has forced a fill-in form into a grid. Judged on
    147 business pages, re-reading exactly these pages with the form prompt kept the whole gain of
    using the form prompt everywhere (+38 of +41 score points) without touching real tables."""
    return sum(len(_CHECKBOX.findall(t)) for t in _TABLE.findall(text or "")) >= min_boxes


_EMPTY_CELL = re.compile(r"<td[^>]*>\s*</td>", re.I)


def _insertions(page_tokens, other_text: str) -> dict[int, list[str]]:
    a = [t[0].lower() for t in page_tokens]
    other = [t[0] for t in tokens(other_text)]
    b = [t.lower() for t in other]
    out: dict[int, list[str]] = {}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "insert" and 1 <= j2 - j1 <= 12:
            out[i1] = other[j1:j2]
    return out


def insert_agreed(page_text: str, a_text: str, b_text: str) -> tuple[str, list[tuple[str, str]]]:
    """Text both zoomed reads have at the same place and the page read lacks entirely: fill the one empty
    table cell there, or restore a dropped heading/line between two blocks. Anything else is left alone."""
    pt = tokens(page_text)
    ia, ib = _insertions(pt, a_text), _insertions(pt, b_text)
    low = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", page_text)).lower()
    edits = []
    for i, ta in ia.items():
        tb = ib.get(i)
        if not tb or [x.lower() for x in ta] != [x.lower() for x in tb] or i >= len(pt):
            continue
        if i == 0:  # text both zoomed reads have above everything the page read kept: a dropped running header
            if "[illegible]" not in ta and " ".join(ta).lower() not in low and not all(len(x) < 2 for x in ta):
                edits.append((0, 0, " ".join(ta) + "\n\n", ("line", " ".join(ta))))
            continue
        if "[illegible]" in ta:
            continue
        txt = " ".join(ta)
        if txt.lower() in low or all(len(x) < 2 for x in ta):
            continue
        g0, g1 = pt[i - 1][2], pt[i][1]
        gap = page_text[g0:g1]
        cells = list(_EMPTY_CELL.finditer(gap))
        if len(cells) == 1 and len(ta) <= 2:
            m = cells[0]
            c = m.group()
            k = c.rindex("<")
            edits.append((g0 + m.start(), g0 + m.end(), c[:k] + txt + c[k:], ("cell", txt)))
        elif len(ta) >= 2 and "\n\n" in gap and "<" not in gap and not any(ch.isdigit() for ch in txt):
            k = g0 + gap.index("\n\n")
            edits.append((k, k, "\n\n" + txt, ("line", txt)))
    out, done = page_text, []
    for s, e, rep, info in sorted(edits, reverse=True):
        out = out[:s] + rep + out[e:]
        done.append(info)
    return out, done[::-1]
