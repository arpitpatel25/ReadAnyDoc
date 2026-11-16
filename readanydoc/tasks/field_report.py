"""Field report: for every extracted field, how it was found, not just its value.

A bare value hides the cases where a reader should look again. Each field gets a status:

- ``found``: the value is on the page (or, for a yes/no or a choice from a list, the cited block supports it).
- ``conflict``: the document gives two or more different values for the field (a price table and a special
  term, a contract and its amendment, a value that is crossed out). Every candidate is listed with its source.
- ``inferred``: the document does not state the value; the model worked it out or assumed it.
- ``not_in_document``, ``blank`` (the form has the field but it is empty), ``not_applicable``: three kinds
  of empty that used to look the same.
- ``unreadable``: the model's reply for this part could not be read (the value may be in the document).

Plus ``printed_as`` (the text as printed, when a value was converted), ``flags`` for conversions a reader should
check (day/month order, a number in parentheses, amounts stated in thousands, a percentage, a currency), a
``note`` for a condition on the value ("first year only"), crossed-out text and pages stamped VOID, and lists
that have fewer items than the table they come from.

Nothing here changes a value. The candidates come from the model; each one is checked against the page text in
code and dropped when it is not there.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from . import schema as S
from .common import PageRef, RegionRef

STATUSES = ("found", "conflict", "inferred", "not_in_document", "blank", "not_applicable", "unreadable")
EMPTY_KINDS = {"blank", "not_applicable"}

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


def _num(x: Any) -> Optional[float]:
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    t = re.sub(r"[\s$€£¥₹,]|\b(?:USD|EUR|GBP|INR)\b", "", str(x))
    try:
        return float(t)
    except ValueError:
        return None


def _same(a: Any, b: Any) -> bool:
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return abs(na - nb) < 1e-9
    return value_key(a) == value_key(b)


def _brief(ref: RegionRef, text: str) -> dict:
    return {"page": ref.page.number, "block": ref.label, "type": ref.region.type.value, "text": text[:200]}


def _overlaps(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(a < end and b > start for a, b in spans)


def build(value: Any, schema: Any, sources: dict, ex: Any, *, meta: dict, conflicts: list, unreadable: set,
          struck: dict, void: dict, pages: Optional[list[PageRef]] = None) -> tuple[dict, list[str]]:
    """(report by field path, warnings). ``meta`` = {(pattern, value_key): notes from the model}."""
    from .extract import locate  # the same matcher the citations use

    report: dict[str, dict] = {}
    warnings: list[str] = []
    seg_conflicts: dict[tuple, list] = {}
    for p, a, b in conflicts:
        seg_conflicts.setdefault(tuple(p), []).extend([a, b])

    def evidence(v: Any, labels: list[str]) -> tuple[Optional[RegionRef], Any]:
        for l in labels:
            ref = ex.view.lookup(l)
            if ref is not None:
                m = locate(v, ref)
                if m is not None:
                    return ref, m
        for p in pages or ex.pages:  # every page, also those a relevance filter left out
            for ref in p.regions:
                m = locate(v, ref)
                if m is not None:
                    return ref, m
        return None, None

    def printed_ok(printed: str, labels: list[str]) -> Optional[RegionRef]:
        want = " ".join(printed.lower().split())
        refs = [r for r in (ex.view.lookup(l) for l in labels) if r is not None] or \
               [ref for p in (pages or ex.pages) for ref in p.regions]
        for ref in refs:
            if want and want in " ".join(re.sub(r"<[^>]+>", " ", ref.region.content).lower().split()):
                return ref
        return None

    for path, v, leaf_schema in S.iter_leaves(value, schema):
        if not path:
            continue
        key = S.path_str(path)
        m_ = meta.get((pattern(path), value_key(v))) or {}
        labels = sources.get(path, [])
        entry: dict[str, Any] = {"value": v}
        if isinstance(v, str) and not v.strip(" _-.\t\n"):
            # an empty string (or a line of underscores) is a blank on the form, not a value read from it
            entry["status"] = m_.get("empty") if m_.get("empty") in EMPTY_KINDS else "blank"
            report[key] = entry
            continue
        if v is None:
            top = path[0] if isinstance(path[0], str) else None
            if top in unreadable:
                entry["status"] = "unreadable"
            elif m_.get("empty") in EMPTY_KINDS:
                entry["status"] = m_["empty"]
            else:
                entry["status"] = "not_in_document"
            report[key] = entry
            continue

        ref, match = evidence(v, labels)
        printed = m_.get("printed") if isinstance(m_.get("printed"), str) else None
        pref = printed_ok(printed, labels) if printed else None
        if pref is None:
            printed = None
        if printed is None and match is not None and match.content and value_key(match.content) != value_key(v):
            printed = match.content
        if printed:
            entry["printed_as"] = printed
        if isinstance(leaf_schema, dict) and "const" in leaf_schema:
            report[key] = {"value": v, "status": "found"}  # fixed by the schema (a discriminator), not read
            continue
        cited = [r for r in (ex.view.lookup(l) for l in labels) if r is not None]
        flags: list[str] = []
        said_inferred = bool(m_.get("inferred"))
        if match is not None or pref is not None:
            status = "found"  # the text is on the page
        elif isinstance(v, bool):
            # a yes/no is rarely printed as a word: trust a cited block, and the model's own "inferred"
            status = "inferred" if said_inferred else "found"
            if not cited:
                flags.append("not_checked")
        elif isinstance(leaf_schema, dict) and "enum" in leaf_schema and cited and not said_inferred:
            status = "found"  # a choice from the schema's list, read from the cited block
        else:
            status = "inferred"
        notes: list[str] = []
        if m_.get("note"):
            notes.append(str(m_["note"])[:200])
        where = ref or pref or (cited[0] if cited else None)
        context = ""
        if where is not None:
            context = where.region.content + "\n" + "\n".join(r.region.content for r in where.page.regions)
        checks = format_flags(v, printed, context)
        flags += [f for f, _ in checks]
        notes += [n for _, n in checks if n]

        # candidates: other values the model saw for this field, each checked on the page
        cands: list[dict] = []
        if where is not None:
            cands.append({"value": v, "source": _brief(where, match.content if match is not None else printed or "")})
        for alt in m_.get("alternatives") or []:
            av = alt.get("value")
            if av is None or _same(av, v):
                continue
            aref, am = evidence(av, [alt["block"]] if alt.get("block") else [])
            if aref is None:
                continue  # not on the page: never report a candidate the document does not have
            c = {"value": av, "source": _brief(aref, am.content)}
            if alt.get("note"):
                c["note"] = str(alt["note"])[:200]
            if aref.page.number in void:
                c["note"] = (c.get("note", "") + f" (page stamped {void[aref.page.number]})").strip()
            cands.append(c)
        for other in seg_conflicts.get(tuple(path), []):
            if other is not None and not _same(other, v) and not any(_same(c["value"], other) for c in cands):
                aref, am = evidence(other, [])
                if aref is not None:
                    cands.append({"value": other, "source": _brief(aref, am.content),
                                  "note": "found on overlapping pages of a long document"})
        # crossed-out text: the chosen value struck through means the document replaced it
        if ref is not None and match is not None and _overlaps(struck.get(ref.label, []), match.start, match.end):
            flags.append("struck_through")
            notes.append("this value is crossed out on the page; the document may give a replacement")
            status = "conflict"
        if where is not None and where.page.number in void:
            flags.append("void_page")
            notes.append(f"page {where.page.number} is stamped {void[where.page.number]}")
        if len(cands) > 1:
            status = "conflict"
            entry["candidates"] = cands
        entry["status"] = status
        if flags:
            entry["flags"] = sorted(set(flags))
        if notes:
            entry["notes"] = notes
        report[key] = entry
        if status == "conflict":
            vals = ", ".join(f"{c['value']!r} (page {c['source']['page']})" for c in cands) or repr(v)
            warnings.append(f"{key}: the document gives different values: {vals}")
        elif status == "inferred":
            warnings.append(f"{key}: {v!r} is not stated in the document (worked out or assumed)")
        warnings += [f"{key}: {n}" for f, n in checks if f in WARN_FLAGS and n]
    return report, warnings


_SUMMARY_ROW = re.compile(r"\b(sub-?\s?total|total|grand total|tax|vat|gst|balance|amount due|sum|"
                          r"carried forward|brought forward|discount)\b", re.I)


def item_rows(content: str) -> int:
    """Rows of a parsed table that can be items: not header rows, not total / tax / balance rows, and with at
    least two filled cells."""
    c = content or ""
    if re.search(r"<tr\b", c, re.I):
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", c, re.I | re.S)
        n = 0
        for row in rows:
            if re.search(r"<th\b", row, re.I):
                continue
            cells = [" ".join(re.sub(r"<[^>]+>", " ", x).split()) for x in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.I | re.S)]
            if sum(1 for x in cells if x) < 2 or any(_SUMMARY_ROW.search(x) for x in cells if x):
                continue
            n += 1
        return n
    lines = [ln for ln in c.splitlines() if ln.strip().startswith("|")]
    body = [ln for ln in lines if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", ln)]
    body = body[1:] if len(body) < len(lines) else body
    return sum(1 for ln in body if not _SUMMARY_ROW.search(ln)
               and sum(1 for x in ln.strip().strip("|").split("|") if x.strip()) >= 2)


def list_gaps(value: Any, schema: Any, sources: dict, ex: Any) -> list[dict]:
    """Lists with fewer items than the item rows of the table their items were read from.

    One entry per list: {"path", "items", "rows", "block"}. Only lists not nested inside another list (a row's
    own sub-list cites the same table). A schema may want only some rows, so this is a prompt to check, not
    proof of a miss.
    """
    out = []

    def walk(v: Any, s: Any, path: tuple) -> None:
        s = S.branch_for(s, v) if isinstance(s, dict) else s
        if isinstance(v, list) and S.is_array(s):
            if v and all(isinstance(it, dict) for it in v) and not any(isinstance(x, int) for x in path):
                labels: dict[str, int] = {}
                for p, lab in sources.items():
                    if len(p) > len(path) and p[:len(path)] == path:
                        for l in lab:
                            labels[l] = labels.get(l, 0) + 1
                tables = [ex.view.lookup(l) for l in labels]
                tables = [r for r in tables if r is not None and r.region.type.value == "Table"]
                if tables:
                    rows = sum(item_rows(r.region.content) for r in {t.label: t for t in tables}.values())
                    if rows >= 2 and len(v) < rows:
                        out.append({"path": path, "items": len(v), "rows": rows, "block": tables[0].label})
            for i, it in enumerate(v):
                walk(it, s.get("items", {}), path + (i,))
        elif isinstance(v, dict) and S.is_object(s):
            for k, x in v.items():
                walk(x, s["properties"].get(k, {}), path + (k,))

    walk(value, schema, ())
    return out
