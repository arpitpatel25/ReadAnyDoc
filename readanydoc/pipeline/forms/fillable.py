"""``fillable``: the form fields of a PDF as exact evidence for the page read.

A page render does not show what is typed into form fields (pdfium draws widgets only with a form
environment, which the reader's renderer does not set up), so on a filled fillable PDF the page read
returns the blank form. The file itself stores every value and every checkbox / radio state.

For each field the label printed next to it (PDF text layer) is looked up in the read. Where it is found:
* checkbox / radio: the mark written next to the label is set to the file's state when it differs;
* text / choice field with a value: the value is written after the label unless the read already has it;
* empty text field: ``<empty/>`` is written after the label when nothing follows it (Reducto's convention).
Values and checked boxes whose label cannot be found in the read are returned in one Key Value block at
the end of the page (``label: value`` / ``label: [x]`` lines), so nothing the file stores is lost. A table that holds three or more
located fields and shows no data-table evidence becomes a Key Value block; a text block that holds a
field is typed Key Value. Labels that occur several times are paired in reading order only when the read
has exactly as many occurrences as the page has fields with that label; otherwise they are left alone.
"""

from __future__ import annotations

import html as _html
import re
from typing import Optional

from ...model import Candidate, Document, Page, Region, RegionType
from ...textnorm import plain_text
from . import acroform, grid
from .rules import convert_region

MARK = r"\[[ xX✓✔]?\]|[☐☑☒□■▢]"
_MARK_BEFORE = re.compile(r"(" + MARK + r")(?:\s|<[^>]+>|\*\*|&nbsp;|\(?[0-9a-zA-Z]{1,3}[.)])*$")
_MARK_AFTER = re.compile(r"^(?:[\s:.)]|<[^>]+>|\*\*|&nbsp;|\([^()\n]{0,40}\))*(" + MARK + r")")
_AFTER_LABEL = re.compile(r"^(?:[:.)’'\"”]|\*\*|__|</(?:b|strong|i|em|u)>|\([^()\n<]{0,60}\))*")
_TOKEN = re.compile(r"<[^>]+>|&[a-zA-Z#0-9]+;|[^\W_]+", re.U)
_BLANK_AFTER = re.compile(r"^[ \t]*(_{3,}|\.{6,})")
_REST_OF_LINE = re.compile(r"^(.*?)(?=\n|<br\s*/?>|</t[dh]|<t[dh]\b|</tr|$)", re.I | re.S)


def norm_map(content: str) -> tuple[str, list[tuple[int, int]]]:
    """Lower-case words of ``content`` joined by single spaces (markup and entities skipped), and for
    every word its (start, end) in ``content``."""
    words, spans = [], []
    for m in _TOKEN.finditer(content or ""):
        t = m.group(0)
        if t.startswith("<") or t.startswith("&"):
            continue
        words.append(t.lower())
        spans.append((m.start(), m.end()))
    return " ".join(words), spans


def norm(s: str) -> str:
    return norm_map(_html.unescape(s or ""))[0]


def _occurrences(regions: list[Region], maps: list, key: str) -> list[tuple[int, int, int]]:
    """(region index, start, end in the region's content) of every whole-word occurrence of ``key``."""
    out = []
    n = len(key.split())
    for ri, (text, spans) in enumerate(maps):
        if not text:
            continue
        words = text.split(" ")
        kw = key.split(" ")
        for i in range(len(words) - n + 1):
            if words[i] == kw[0] and words[i:i + n] == kw:
                out.append((ri, spans[i][0], spans[i + n - 1][1]))
    return out


def _reading_order(fields: list[dict]) -> list[dict]:
    return sorted(fields, key=lambda f: (round(f["bbox"][1] / 0.012), f["bbox"][0]))


def adjacent_mark(c: str, s: int, e: int) -> Optional[tuple[int, int, str]]:
    """The mark written right before a label (the usual place: box, then its text) or right after it."""
    mb = _MARK_BEFORE.search(c[max(0, s - 48):s])
    if mb:
        a = max(0, s - 48) + mb.start(1)
        return a, a + len(mb.group(1)), mb.group(1)
    ma = _MARK_AFTER.search(c[e:e + 64])
    if ma:
        return e + ma.start(1), e + ma.end(1), ma.group(1)
    return None


def _keys(f: dict) -> list[str]:
    out = []
    for cand in (f.get("label"), f.get("tooltip")):
        key = norm(cand or "")
        if len(key.replace(" ", "")) >= 3 and len(key.split()) <= 14 and key not in out:
            out.append(key)
    return out


def locate(regions: list[Region], fields: list[dict]) -> dict[int, tuple[int, int, int]]:
    """field position in ``fields`` -> (region index, label start, label end). Only unambiguous matches:
    the read must show a label exactly as often as the page has fields of that kind (boxes to mark, or
    fields to type in) with it; they are then paired in reading order. The printed label next to the
    field is tried first, the field's tooltip second."""
    maps = [norm_map(r.content) for r in regions]
    order = {id(f): k for k, f in enumerate(fields)}
    ordered = _reading_order(fields)
    found: dict[int, tuple[int, int, int]] = {}
    for attempt in (0, 1):
        groups: dict[tuple[str, str], list[int]] = {}
        for f in ordered:
            i = order[id(f)]
            keys = _keys(f)
            if i in found or len(keys) <= attempt:
                continue
            kind = "mark" if f["type"] in ("checkbox", "radio") else "text"
            groups.setdefault((kind, keys[attempt]), []).append(i)
        for (kind, key), idxs in groups.items():
            occ = _occurrences(regions, maps, key)
            if kind == "mark":  # only where the read wrote a box next to these words
                occ = [o for o in occ if adjacent_mark(regions[o[0]].content, o[1], o[2])]
            if occ and len(occ) == len(idxs):
                for i, o in zip(idxs, occ):
                    found[i] = o
    return found


def _label_end(c: str, s: int, e: int) -> int:
    """Where a value goes: after the label, its closing punctuation and markup, and after the bracket the
    label stopped inside ("Place of injury (e.g. 2nd floor, Main Post Office Bldg.)")."""
    line_start = max(c.rfind("\n", 0, s), c.rfind(">", 0, s)) + 1
    if c.count("(", line_start, e) > c.count(")", line_start, e):
        close = c.find(")", e)
        nl = [x for x in (c.find("\n", e), c.find("<", e)) if x >= 0]
        if close >= 0 and (not nl or close < min(nl)) and close - e <= 120:
            e = close + 1
    return e + len(_AFTER_LABEL.match(c[e:]).group(0))


def _mark(checked: bool, like: str) -> str:
    if like.startswith("["):
        return "[x]" if checked else "[ ]"
    return "☑" if checked else "☐"


def _is_checked(mark: str) -> bool:
    return mark in ("☑", "☒", "■") or (mark.startswith("[") and mark[1:-1].strip() != "")


def plan_edits(regions: list[Region], fields: list[dict], found: dict[int, tuple[int, int, int]],
               empty_marks: bool = True) -> tuple[list[tuple[int, int, int, str]], dict, list[dict]]:
    """Edits as (region index, start, end, replacement), statistics, and the fields left over."""
    edits: list[tuple[int, int, int, str]] = []
    stats = {"marks_checked": 0, "marks_corrected": 0, "values_present": 0, "values_inserted": 0,
             "values_unplaced": 0, "marks_unplaced": 0, "empty_marked": 0}
    left: list[dict] = []
    page_text = " " + " ".join(norm_map(r.content)[0] for r in regions) + " "
    taken: dict[tuple[int, int], int] = {}
    mark_edits: dict[tuple[int, int], tuple[int, int, int, str]] = {}
    empties: set[tuple[int, int]] = set()
    for i, f in enumerate(fields):
        loc = found.get(i)
        if f["type"] in ("checkbox", "radio"):
            if f.get("checked") is None:
                continue
            if loc is None:
                if f["checked"]:
                    left.append(f)
                    stats["marks_unplaced"] += 1
                continue
            ri, s, e = loc
            c = regions[ri].content
            span = adjacent_mark(c, s, e)
            if span is None:
                if f["checked"]:
                    left.append(f)
                    stats["marks_unplaced"] += 1
                continue
            k = (ri, span[0])
            taken[k] = taken.get(k, 0) + 1
            stats["marks_checked"] += 1
            if _is_checked(span[2]) != bool(f["checked"]):
                mark_edits[k] = (ri, span[0], span[1], _mark(bool(f["checked"]), span[2]))
            continue
        value = (f.get("value") or "").strip()
        if not value:
            if loc is not None and empty_marks and f["type"] == "text":
                ri, s, e = loc
                c = regions[ri].content
                e = _label_end(c, s, e)
                rest = _REST_OF_LINE.match(c[e:]).group(1)
                if not re.sub(r"<[^>]+>|&nbsp;|[\s$:.]", "", rest) and (ri, e) not in empties:
                    empties.add((ri, e))
                    edits.append((ri, e, e, " <empty/>"))
                    stats["empty_marked"] += 1
            continue
        nv = norm(value)
        if nv and f" {nv} " in page_text:
            stats["values_present"] += 1
            continue
        if loc is None:
            left.append(f)
            stats["values_unplaced"] += 1
            continue
        ri, s, e = loc
        c = regions[ri].content
        e = _label_end(c, s, e)
        shown = value.replace("\n", " ")
        blank = _BLANK_AFTER.match(c[e:])
        if blank:
            edits.append((ri, e + blank.start(1), e + blank.end(1), shown))
        else:
            edits.append((ri, e, e, " " + shown))
        stats["values_inserted"] += 1
    for k, ed in mark_edits.items():
        if taken.get(k, 0) == 1:  # two fields claiming one mark: the pairing is unsure, leave the read
            edits.append(ed)
            stats["marks_corrected"] += 1
    return edits, stats, left


def _apply_edits(regions: list[Region], edits: list[tuple[int, int, int, str]]) -> None:
    by_region: dict[int, list] = {}
    for ri, s, e, rep in edits:
        by_region.setdefault(ri, []).append((s, e, rep))
    for ri, es in by_region.items():
        r = regions[ri]
        c = r.content
        last = len(c) + 1
        for s, e, rep in sorted(es, key=lambda x: (-x[0], -x[1])):
            if e > last:  # overlapping edits: keep the later one in the text
                continue
            c = c[:s] + rep + c[e:]
            last = s
        r.candidates.append(Candidate(source=r.source or "reader", content=r.content,
                                      meta={"replaced": True, "by": "forms.fillable"}))
        r.content, r.text = c, plain_text(c)
        r.meta.setdefault("forms", {})["fields_from_file"] = len(es)


def _field_name(f: dict) -> str:
    return (f.get("label") or f.get("tooltip") or f.get("name") or "field").strip().rstrip(":")


def apply(doc: Document, page: Page) -> dict:
    if doc.file_type != "pdf" or not doc.meta.get("path"):
        return {}
    fields = acroform.page_fields(doc.meta["path"], page.original_page, doc.meta.get("password"))
    if not fields:
        return {}
    page.properties["form_fields"] = fields
    regions = page.regions
    found = locate(regions, fields)
    edits, stats, left = plan_edits(regions, fields, found)
    per_region: dict[int, int] = {}
    for i, (ri, _, _) in found.items():
        per_region[ri] = per_region.get(ri, 0) + 1
    _apply_edits(regions, edits)
    out: list[Region] = []
    tables = typed = 0
    for ri, r in enumerate(regions):
        n = per_region.get(ri, 0)
        if n:
            r.meta["form_fields"] = n
        if r.type == RegionType.TABLE and n >= 3:
            ts = [p for kind, p in grid.split_content(r.content) if kind == "table"]
            if ts and all(grid.classify(t).kind != "data" for t in ts):
                out.extend(convert_region(r, f"{n} form fields of the file are inside"))
                out[-1].meta["form_fields"] = n
                tables += 1
                continue
        elif r.type in (RegionType.TEXT, RegionType.LIST_ITEM) and n:
            r.type = RegionType.KEY_VALUE
            r.meta.setdefault("forms", {})["rule"] = "holds_form_field"
            typed += 1
        out.append(r)
    if left:
        lines = [f"{_field_name(f)}: " + ("[x]" if f["type"] in ("checkbox", "radio") else f["value"].replace("\n", " "))
                 for f in _reading_order(left)]
        content = "\n".join(lines)
        out.append(Region(type=RegionType.KEY_VALUE, content=content, text=plain_text(content), source="pdf_form_fields",
                          candidates=[Candidate(source="pdf_form_fields", content=content)],
                          meta={"forms": {"rule": "field_values_from_file", "fields": len(left)}, "form_fields": len(left)}))
    page.regions = out
    kinds: dict[str, int] = {}
    for f in fields:
        kinds[f["type"]] = kinds.get(f["type"], 0) + 1
    return {"fields": len(fields), "types": kinds, "located": len(found),
            "filled": sum(1 for f in fields if f.get("value") or f.get("checked")),
            **{k: v for k, v in stats.items() if v}, "tables_to_key_value": tables, "typed_key_value": typed}


def retype_located(page: Page, before: Optional[dict] = None) -> None:
    """With boxes: a block that holds a field stays / becomes Key Value (the edge bands would call a
    short block at the top a Header), and a text block with a field inside its box is Key Value."""
    fields = page.properties.get("form_fields") or []
    centres = [((f["bbox"][0] + f["bbox"][2]) / 2, (f["bbox"][1] + f["bbox"][3]) / 2) for f in fields]
    for r in page.regions:
        was = (before or {}).get(r.id, r.type)
        if was not in (RegionType.TEXT, RegionType.KEY_VALUE, RegionType.LIST_ITEM) or r.type in (RegionType.TABLE, RegionType.FIGURE):
            continue
        inside = 0
        if r.bbox is not None and not (r.bbox.width > 0.95 and r.bbox.height > 0.95):
            inside = sum(1 for x, y in centres if r.bbox.contains_point(x, y, 0.003))
        if (r.meta.get("form_fields") or inside) and r.type != RegionType.KEY_VALUE:
            r.meta["type_before_forms"] = r.type.value
            r.type = RegionType.KEY_VALUE
            r.meta.setdefault("forms", {})["rule"] = "holds_form_field"
