"""Review checks on an extraction, all in code (no model call, no extra model output). Opt-in: settings.review.

Each check names a field and a reason a reader should look at it:

- ``not_found_on_page``: the value is not printed in the document as returned (worked out, converted, assumed);
- ``ambiguous_date``, ``parentheses``, ``scale``, ``percent_scaled``: the printed form needs a second look (day
  and month order, a negative in parentheses, "amounts in thousands", a percentage returned as a fraction);
- ``struck_through``: every place the value is printed is crossed out; ``void_page``: its page is stamped VOID;
- ``segments_disagree``: two parts of a long document gave different values;
- ``list_may_be_incomplete``: a list has fewer items than the rows with an amount in the table it was read from;
- ``unreadable``: the model's reply for the field could not be read.

Nothing here changes a value.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from . import schema as S
from .common import PageRef


# ---------------------------------------------------------------- crossed-out words (from the page image)


def _dark_rows(img: Any) -> Any:
    import numpy as np
    a = np.asarray(img.convert("L"), dtype=np.uint8)
    if a.size == 0:
        return None
    thr = min(160, int(a.mean() * 0.75))  # dark ink against the paper around it
    return a < thr


def is_struck(mask: Any) -> bool:
    """A crop of one word (True = ink): a near-solid horizontal line through the middle of the letters.

    Letter strokes never cover the whole width (there are gaps between letters); an underline sits at the
    bottom, not in the middle band.
    """
    import numpy as np
    if mask is None or mask.shape[0] < 6 or mask.shape[1] < 8:
        return False
    h = mask.shape[0]
    band = mask[int(h * 0.3):max(int(h * 0.75), int(h * 0.3) + 1)]
    if band.size == 0:
        return False
    cover = band.mean(axis=1)
    # the line must be thin: rows at the very top and bottom of the word are not as covered
    edge = max(mask[: max(h // 6, 1)].mean(axis=1).max(), mask[h - max(h // 6, 1):].mean(axis=1).max())
    return bool(cover.max() >= 0.9 and edge < 0.8 and np.count_nonzero(cover >= 0.9) <= max(h * 0.25, 2))


def struck_spans(pages: list[PageRef]) -> dict[str, list[tuple[int, int]]]:
    """{region label: [(char_start, char_end), ...]} of words with a line through them.

    Needs a rendered page image and word boxes; pages without them are skipped (nothing is reported for them).
    """
    out: dict[str, list[tuple[int, int]]] = {}
    for p in pages:
        if not p.page.renders:
            continue
        key = max(p.page.renders, key=lambda k: float(k) if str(k).replace(".", "", 1).isdigit() else 0.0)
        path = p.page.renders[key]
        if not os.path.isfile(path):
            continue
        try:
            from PIL import Image
            img = Image.open(path)
            img.load()
        except Exception:
            continue
        W, H = img.size
        for ref in p.regions:
            words = [w for w in ref.region.iter_words()
                     if w.bbox is not None and w.char_start is not None and w.char_end is not None
                     and any(ch.isalnum() for ch in w.text)]  # a dash looks like a line
            hit = []
            for w in words:
                box = (int(w.bbox.left * W), int(w.bbox.top * H), int(w.bbox.right * W),
                       int((w.bbox.top + w.bbox.height) * H))
                hit.append(box[2] - box[0] >= 8 and box[3] - box[1] >= 6 and is_struck(_dark_rows(img.crop(box))))
            spans = []
            for i, w in enumerate(words):
                if not hit[i]:
                    continue
                if sum(ch.isalnum() for ch in w.text) < 2:
                    # one character ("4" of "4,500", or an "e" whose bar looks like a strike): only next to a
                    # struck neighbour of the same piece of text
                    near = [j for j in (i - 1, i + 1) if 0 <= j < len(words) and hit[j]
                            and len(words[j].text) > 1 and _adjacent(ref.region.content, words[min(i, j)], words[max(i, j)])]
                    if not near:
                        continue
                spans.append((w.char_start, w.char_end))
            if spans:
                out[ref.label] = _merge(ref.region.content, spans)
    return out


def _adjacent(content: str, a: Any, b: Any) -> bool:
    gap = content[a.char_end:b.char_start]
    return len(gap) <= 2 and not gap.strip(".,:/-'")


def _merge(content: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Struck words separated only by a comma, dot or similar become one span ("4" "," "500" -> "4,500")."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and len(content[out[-1][1]:a]) <= 2 and not content[out[-1][1]:a].strip(".,:/-'"):
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


_VOID = re.compile(r"^\W*(void|voided|cancell?ed|superseded|not valid|draft|copy - not original)\W*$", re.I)


def void_pages(pages: list[PageRef]) -> dict[int, str]:
    """{page number: stamp} for pages with a block that is only a stamp such as VOID or CANCELLED."""
    out: dict[int, str] = {}
    for p in pages:
        for ref in p.regions:
            txt = " ".join(re.sub(r"<[^>]+>|[#*_`]", " ", ref.region.content).split())
            if txt and len(txt) <= 30 and _VOID.match(txt):
                out[p.number] = txt.strip(" .!-").upper()
    return out


# ---------------------------------------------------------------- conversions worth a second look

_DMY = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\s*$")
_PAREN = re.compile(r"^\s*\(\s*[^\d()]{0,4}[\d.,\s]+\)\s*$")
_SCALE = re.compile(r"\b(?:in|amounts? in|figures in|\(in)\s+(thousands|millions|billions|lakhs|crores)\b|"
                    r"\(\s*(?:\$|€|£|usd|eur|gbp)?\s*(000s?|'000|mn|m|k)\s*\)|\b(?:usd|eur|gbp|inr)\s*(000s?|'000)\b",
                    re.I)
_CURRENCY = re.compile(r"(?:[$€£¥₹]|\b(?:USD|EUR|GBP|INR|JPY|CAD|AUD|CHF|CNY|SGD)\b)")


def format_flags(value: Any, printed: Optional[str], context: str) -> list[tuple[str, Optional[str]]]:
    """[(flag, note)] for a value whose printed form needs a second look. ``context`` is the text of the block
    and of its page, where a line such as "amounts in thousands" would be."""
    out: list[tuple[str, Optional[str]]] = []
    p = (printed or "").strip()
    m = _DMY.match(p)
    if m and int(m.group(1)) <= 12 and int(m.group(2)) <= 12 and m.group(1).lstrip("0") != m.group(2).lstrip("0"):
        out.append(("ambiguous_date", f"printed as {p}: day and month could be either way round"))
    number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if number and _PAREN.match(p) and value > 0:
        out.append(("parentheses", f"printed as {p}: in accounts, parentheses usually mean a negative amount"))
    if number:
        sc = _SCALE.search(context or "")
        if sc:
            out.append(("scale", f'the page says "{sc.group(0).strip()}": the printed number may need scaling'))
        if "%" in p:
            num = re.search(r"-?\d+(?:[.,]\d+)?", p)
            printed_num = float(num.group(0).replace(",", ".")) if num else None
            if printed_num is not None and abs(printed_num) > 1 and abs(float(value)) <= 1 \
                    and abs(float(value) * 100 - printed_num) < 1e-6:
                out.append(("percent_scaled", f"printed as {p}; returned as a fraction ({value})"))
            else:
                out.append(("percent", None))
        cur = _CURRENCY.search(p)
        if cur:
            out.append(("currency", f"printed with the currency {cur.group(0)}"))
    return out


WARN_FLAGS = {"ambiguous_date", "parentheses", "scale", "percent_scaled"}


# ---------------------------------------------------------------- building the report

def pattern(path: tuple) -> tuple:
    """A path with list indexes replaced by "*": the key under which per-value notes from the model are kept
    (indexes move when segments are merged and rows de-duplicated)."""
    return tuple("*" if isinstance(p, int) else p for p in path)


def value_key(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).lower().split()) if value is not None else ""


def _overlaps(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(a < end and b > start for a, b in spans)


_SUMMARY_ROW = re.compile(r"\b(sub-?\s?total|total|grand total|tax|vat|gst|balance|amount due|sum|"
                          r"carried forward|brought forward|discount)\b", re.I)


_AMOUNT = re.compile(r"[$€£¥₹]\s?\d|\b\d{1,3}(?:[,.' ]\d{3})+(?:[.,]\d{2})?\b|\b\d+[.,]\d{2}\b")


def amount_rows(content: str) -> int:
    """Rows of a parsed table that look like items: not header rows, not total / tax / balance rows, and with an
    amount in a cell (so wrapped description lines and label rows do not count)."""
    c = content or ""
    if re.search(r"<tr\b", c, re.I):
        n = 0
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", c, re.I | re.S):
            if re.search(r"<th\b", row, re.I):
                continue
            cells = [" ".join(re.sub(r"<[^>]+>", " ", x).split()) for x in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.I | re.S)]
            if any(_SUMMARY_ROW.search(x) for x in cells if x) or not any(_AMOUNT.search(x) for x in cells if x):
                continue
            n += 1
        return n
    lines = [ln for ln in c.splitlines() if ln.strip().startswith("|")]
    body = [ln for ln in lines if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", ln)]
    body = body[1:] if len(body) < len(lines) else body
    return sum(1 for ln in body if not _SUMMARY_ROW.search(ln) and _AMOUNT.search(ln))


def review(value: Any, schema: Any, cites: dict, ex: Any, *, conflicts: list, unreadable: set,
           pages: list[PageRef]) -> list[dict]:
    """[{"field", "flag", "detail"}] for the values a reader should check (see the module docstring)."""
    out: list[dict] = []
    void = void_pages(pages)

    def add(field: str, flag: str, detail: Optional[str]) -> None:
        item = {"field": field, "flag": flag, "detail": detail}
        if item not in out:
            out.append(item)

    for path, v, _ in S.iter_leaves(value, schema):
        if not path or v is None or isinstance(v, bool):
            continue
        key = S.path_str(path)
        good = [c for c in cites.get(path) or [] if c.get("confidence") == "high"]
        if not good:
            add(key, "not_found_on_page", "not printed in the document as returned: worked out, converted or assumed")
        for c in good[:1]:
            for flag, note in format_flags(v, c.get("content"), (c.get("parentBlock") or {}).get("content", "")):
                if flag in WARN_FLAGS:
                    add(key, flag, note)
            pg = (c.get("bbox") or {}).get("page")
            if pg in void:
                add(key, "void_page", f"page {pg} is stamped {void[pg]}")
        if ex.struck and any(ex.struck.values()):
            hits = ex.found_anywhere(v)
            if hits and all(_overlaps(ex.struck.get(r.label, []), m.start, m.end) for r, m in hits):
                add(key, "struck_through", "every place this value is printed is crossed out")
    for p, a, b in conflicts:
        add(S.path_str(p), "segments_disagree", f"parts of the document give {a!r} and {b!r}")
    for f in sorted(unreadable):
        add(f, "unreadable", "the model's reply for this field could not be read; it may be in the document")

    def lists(v: Any, s: Any, path: tuple) -> None:
        s = S.branch_for(s, v) if isinstance(s, dict) else s
        if isinstance(v, list) and S.is_array(s):
            if v and all(isinstance(it, dict) for it in v):
                tables = {}
                for p_, cs in cites.items():
                    if len(p_) > len(path) and p_[:len(path)] == path:
                        for c in cs:
                            blk = c.get("parentBlock") or {}
                            if blk.get("type") == "Table" and blk.get("content"):
                                tables[blk["content"]] = True
                rows = sum(amount_rows(t) for t in tables)
                if rows >= 2 and len(v) < rows:
                    add(S.path_str(path), "list_may_be_incomplete",
                        f"{len(v)} items, but the table they come from has {rows} rows with an amount")
            return  # lists inside list items cite the same table: not checked again
        if isinstance(v, dict) and S.is_object(s):
            for k, x in v.items():
                lists(x, s["properties"].get(k, {}), path + (k,))

    lists(value, schema, ())
    return out
