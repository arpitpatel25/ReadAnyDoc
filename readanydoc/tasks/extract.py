"""Extract: JSON Schema in, JSON out, working from the Document's regions (see tasks/__init__.py).

Flow: validate the schema -> render the selected pages with region labels -> ask the model (per page
segment when the document is long) -> unwrap source labels -> conform -> validate -> bounded repair
re-asks -> merge segments and de-duplicate arrays -> (deep_extract) deterministic checks and re-asks
for failing fields only -> null out whatever still breaks the schema -> build citations.

Nothing here reads page images unless ``settings.include_images`` is set.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from rapidfuzz import fuzz

from ..model import BBox, RegionType
from ..textnorm import norm_tokens, tokenize
from . import schema as S
from .common import (DocView, PageRef, RegionRef, TaskError, bbox_dict, page_images, render_pages,
                     select_pages, sum_usage)
from . import reply_schema as RS
from . import coded as CD
from .. import trace
from .llm import CACHE_MARK, LLM, LLMError, LLMResult, ask_json, repair_brackets

# Largest page part sent to the extraction model, in tokens. Measured on a 43-page 10-Q (ExtractBench, 153 values):
# ~17K-token parts found 130, 30K found 129 (fewer extra copies, cheaper), 45K found 89, one 79K part found 85.
# A model with a smaller window gets its own largest; settings.part_tokens overrides either way.
DEFAULT_PART_TOKENS = 30_000

DEFAULTS: dict[str, Any] = {
    "segment_pages": 8,           # pages per segment for long documents
    "segment_overlap": 1,         # pages shared by neighbouring segments
    "auto_segment_chars": 120_000,  # segment automatically above this much rendered text
    "segment_chars": 40_000,      # text per segment when segmenting (dense tables: fewer pages per call)
    "max_repairs": 2,             # schema-repair re-asks per segment
    "max_deep_iterations": 2,     # deep_extract check/re-ask rounds
    "row_check_min_rows": 10,     # list completeness: only segments whose tables have at least this many rows
    "row_check_ratio": 0.5,       # ... and whose items fall below this share of the expected count
    "max_tokens": 16000,
    "coded_group_leaves": 40,     # fields per call in the coded format (lists kept whole): larger groups were
                                  # measured to skip whole metrics; the document is cached, so groups are cheap
}

RULES = """You extract structured data from a parsed document.
- Use only what the document text says. Never invent values and never use outside knowledge.
- Return one JSON value that follows the JSON Schema exactly: include every declared property, use null when the document does not contain the value (or "" where the schema does not allow null), never add properties that are not declared, and respect enum, pattern, minimum/maximum and array item rules.
- Copy values as written (names, numbers, identifiers, dates) unless the schema or the instructions ask for another format.
- For arrays, include every matching item found in the pages you are given, in document order. Do not stop early and do not summarise.
- Reply with the JSON only: no prose, no code fences."""

TRACK = """Source labels: every block of the document starts with a label such as [r12|Table].
Reply with one JSON object with exactly two keys, in this order:
- "data": the extracted value, following the JSON Schema exactly (plain values, nothing wrapped).
- "sources": a list with one entry for every non-null value in "data": {"path": "<where the value is in data>", "block": "<label>"}. Write the path with dots and [index], for example "buyer.name" or "line_items[0].amount"; "block" is the label of the block the value was read from, for example "r12"."""


def _field_names(schema: Any, seen: Optional[set] = None) -> set[str]:
    """Every property name declared anywhere in a ($ref-free) schema."""
    seen = set() if seen is None else seen
    if isinstance(schema, dict):
        for k, v in schema.items():
            if k == "properties" and isinstance(v, dict):
                seen |= set(v)
                for sub in v.values():
                    _field_names(sub, seen)
            elif isinstance(v, (dict, list)):
                _field_names(v, seen)
    elif isinstance(schema, list):
        for v in schema:
            _field_names(v, seen)
    return seen


def envelope_schema(schema: dict) -> dict:
    """The reply shape asked for when sources are tracked: the user's schema under "data", sources as a flat list.

    Sources used to be asked for by wrapping every value as {"v": value, "r": [labels]}. That put an object
    around every value (nearly three times the brackets) and long runs of closing brackets at the end of each
    nested object; on a 20-type billing schema the model miscounted them once and the reply could not be
    read. Here "data" has only the brackets of the user's own schema and "sources" is one level deep.
    """
    scalar = {"type": ["string", "number", "boolean"]}
    alt = {"type": "object", "properties": {"value": scalar, "block": {"type": "string"}, "note": {"type": "string"}},
           "required": ["value", "block"]}
    item = {"type": "object", "properties": {
        "path": {"type": "string"}, "block": {"type": "string"}, "printed": {"type": "string"},
        "inferred": {"type": "boolean"}, "note": {"type": "string"}, "alternatives": {"type": "array", "items": alt},
        "empty": {"type": "string", "enum": ["blank", "not_applicable"]}}, "required": ["path"]}
    return {"type": "object", "properties": {"data": schema, "sources": {"type": "array", "items": item}},
            "required": ["data", "sources"]}


_PATH_TOKEN = re.compile(r"\[(\d+)\]|([^.\[\]/]+)")
_LABEL = re.compile(r"r\d+")


def parse_path(text: str) -> Optional[tuple]:
    """``"line_items[0].amount"`` (or ``line_items.0.amount``, ``/line_items/0/amount``) -> ("line_items", 0, "amount")."""
    if not isinstance(text, str) or not text.strip():
        return None
    out: list = []
    for idx, key in _PATH_TOKEN.findall(text.strip().removeprefix("data.").removeprefix("$.")):
        if idx:
            out.append(int(idx))
        elif key.isdigit():
            out.append(int(key))
        else:
            out.append(key.strip().strip('"\''))
    return tuple(out) or None


def flat_sources(entries: Any) -> dict:
    """The "sources" list of an enveloped reply -> {path tuple: [labels]}. Malformed entries are skipped."""
    out: dict = {}
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            continue
        path = parse_path(e.get("path"))
        raw = e.get("block", e.get("blocks", e.get("r")))
        labels = _LABEL.findall(" ".join(map(str, raw)) if isinstance(raw, list) else str(raw or ""))
        if path and labels:
            out.setdefault(path, [])
            out[path] += [l for l in labels if l not in out[path]]
    return out


def source_meta(entries: Any) -> dict:
    """The optional notes of an enveloped reply's sources -> {path tuple: {printed, inferred, note, alternatives, empty}}."""
    out: dict = {}
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            continue
        path = parse_path(e.get("path"))
        if not path:
            continue
        m: dict = {}
        if isinstance(e.get("printed"), (str, int, float)) and str(e["printed"]).strip():
            m["printed"] = str(e["printed"]).strip()
        if e.get("inferred") is True:
            m["inferred"] = True
        if isinstance(e.get("note"), str) and e["note"].strip():
            m["note"] = e["note"].strip()
        if e.get("empty") in ("blank", "not_applicable"):
            m["empty"] = e["empty"]
        alts = []
        for a in e.get("alternatives") or [] if isinstance(e.get("alternatives"), list) else []:
            if isinstance(a, dict) and a.get("value") is not None and not isinstance(a.get("value"), (dict, list)):
                lab = _LABEL.findall(str(a.get("block") or ""))
                alts.append({"value": a["value"], "block": lab[0] if lab else None,
                             **({"note": str(a["note"])} if a.get("note") else {})})
        if alts:
            m["alternatives"] = alts
        if m:
            out.setdefault(path, {}).update(m)
    return out


def is_envelope(data: Any, schema: Any) -> bool:
    if not isinstance(data, dict) or "data" not in data or not set(data) <= {"data", "sources"}:
        return False
    declared = set(schema.get("properties", {})) if S.is_object(schema) else set()
    if "data" in declared:  # the user's own schema has a "data" field: only a reply with "sources" is an envelope
        return "sources" in data and "sources" not in declared
    return True


def root_ok(data: Any, schema: Any) -> bool:
    """Whether a reply has the requested shape at the top. A fragment of a broken reply does not.

    For an object schema the reply must be an object that is empty or shares at least one top-level field
    with the schema (also inside an envelope, or as the only item of a list).
    """
    if data is None:
        return False
    if isinstance(data, list) and len(data) == 1 and S.is_object(schema):
        data = data[0]
    if is_envelope(data, schema):
        data = data["data"]
    if not S.is_object(schema):
        return True
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        return False
    return not data or bool(set(data) & set(schema["properties"]))


# ---------------------------------------------------------------- annotated trees (value + source labels)

@dataclass
class Leaf:
    value: Any
    labels: list[str] = field(default_factory=list)


def _annotate(value: Any, sources: dict, schema: Any) -> Any:
    return S.map_leaves(value, schema, lambda path, v, s: Leaf(v, list(sources.get(path, []))))


def _split_tree(tree: Any, path: tuple = (), sources: Optional[dict] = None) -> tuple[Any, dict]:
    sources = {} if sources is None else sources
    if isinstance(tree, Leaf):
        if tree.labels:
            sources[path] = tree.labels
        return tree.value, sources
    if isinstance(tree, dict):
        return {k: _split_tree(v, path + (k,), sources)[0] for k, v in tree.items()}, sources
    if isinstance(tree, list):
        return [_split_tree(v, path + (i,), sources)[0] for i, v in enumerate(tree)], sources
    return tree, sources


def _plain(tree: Any) -> Any:
    return _split_tree(tree)[0]


def _labels(tree: Any) -> set[str]:
    if isinstance(tree, Leaf):
        return set(tree.labels)
    if isinstance(tree, dict):
        return set().union(*[_labels(v) for v in tree.values()]) if tree else set()
    if isinstance(tree, list):
        return set().union(*[_labels(v) for v in tree]) if tree else set()
    return set()


def _norm_key(value: Any) -> str:
    def n(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: n(x) for k, x in sorted(v.items())}
        if isinstance(v, list):
            return [n(x) for x in v]
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, (int, float)):
            return float(v)
        return " ".join(str(v).lower().split())
    return json.dumps(n(value), sort_keys=True, ensure_ascii=False)


def _is_empty(tree: Any) -> bool:
    if isinstance(tree, Leaf):
        return tree.value is None
    if isinstance(tree, dict):
        return all(_is_empty(v) for v in tree.values())
    if isinstance(tree, list):
        return len(tree) == 0
    return tree is None


def merge_trees(a: Any, b: Any, conflicts: list, path: tuple = (), adjacent: bool = True) -> Any:
    """Merge the result of a later segment (b) into the running result (a).

    Scalars: first non-null wins (a differing later value is recorded as a conflict).
    Arrays: concatenated; an item of b is a duplicate when its content equals an item of a and the two
    cite a common block (or, when one of them has no source labels, the segments are neighbours).
    """
    if a is None or (isinstance(a, Leaf) and a.value is None):
        return b if b is not None else a
    if b is None or (isinstance(b, Leaf) and b.value is None):
        return a
    if isinstance(a, Leaf) and isinstance(b, Leaf):
        if _norm_key(a.value) != _norm_key(b.value):
            conflicts.append((path, a.value, b.value))
        elif not a.labels:
            return b
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge_trees(a.get(k), v, conflicts, path + (k,), adjacent) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        out_list = list(a)
        keys = [(_norm_key(_plain(x)), _labels(x)) for x in a]
        used = [False] * len(keys)  # one earlier item absorbs at most one later item (real repeats survive)
        for item in b:
            k, labs = _norm_key(_plain(item)), _labels(item)
            dup = False
            for j, (k0, labs0) in enumerate(keys):
                if used[j] or k != k0:
                    continue
                if (labs and labs0 and labs & labs0) or ((not labs or not labs0) and adjacent):
                    dup = used[j] = True
                    break
            if not dup and not _is_empty(item):
                out_list.append(item)
        return out_list
    return a


# ---------------------------------------------------------------- locating a value in a region

_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_TAG = re.compile(r"<[^>]+>")


def _mask(content: str) -> str:
    return _TAG.sub(lambda m: " " * len(m.group(0)), content) if "<" in content else content


def _find_number(masked: str, number: float) -> list[tuple[int, int]]:
    target = abs(float(number))
    out = []
    for m in _NUMBER.finditer(masked):
        try:
            if abs(float(m.group(0).replace(",", "")) - target) < 1e-9:
                out.append((m.start(), m.end()))
        except ValueError:
            continue
    return out or _find_number_alt(masked, target)


def _find_number_alt(masked: str, target: float) -> list[tuple[int, int]]:
    """Other ways a page prints the same number, tried only when the plain form is not there:
    thousands grouped by spaces or dots with a decimal comma ("19 670", "1.234,56", "23,9"), and
    ledger style with dollars and cents separated by a space ("28 50" for 28.50)."""
    text = f"{target:.4f}".rstrip("0").rstrip(".")
    whole, _, frac = text.partition(".")
    groups = []
    w = whole
    while len(w) > 3:
        groups.insert(0, w[-3:])
        w = w[:-3]
    groups.insert(0, w)
    sep = r"[ \u00a0\u202f.']"
    grouped = (sep + "?").join(groups) if len(groups) > 1 else whole
    patterns = []
    if frac:
        patterns.append(rf"(?<![\w.,]){grouped},{frac}0*(?![\w])")
    elif len(groups) > 1:
        patterns.append(rf"(?<![\w.,]){sep.join(groups)}(?![\w]|[.,]\d)")
    if len(frac) <= 2:
        cents = (frac + "00")[:2]
        patterns.append(rf"(?<![\w.,]){grouped}\s+{cents}(?![\w.,])")
    out: list[tuple[int, int]] = []
    for pat in patterns:
        out = [(m.start(), m.end()) for m in re.finditer(pat, masked)]
        if out:
            break
    return out


_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_ISO_DATE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?\s*$")
_NUM_DATE = re.compile(r"(?<![\w])(\d{1,4})\s?([/.\-])\s?(\d{1,2})\s?\2\s?(\d{2,4})(?![\w])")
_SPACED_DATE = re.compile(r"(?<![\w])(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})(?![\w])")
_NAME_DATE = re.compile(r"(?<![\w])(?:(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+(\d{2,4})"
                        r"|([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{2,4}))(?![\w])", re.I)


def _year(y: str) -> int:
    v = int(y)
    return v + (2000 if v < 50 else 1900) if len(y) <= 2 else v


def _find_date(masked: str, value: str) -> list[tuple[int, int]]:
    """Where an ISO date ("2025-03-31") is printed another way: 03/31/2025, 31.03.25, 3 31 2025, March 31, 2025,
    31 Mar 2025. Day and month order is not known from the text, so either order counts as the same date."""
    m = _ISO_DATE.match(str(value))
    if not m:
        return []
    want = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    out = []
    for mm in _NUM_DATE.finditer(masked):
        a, _, b, c = mm.groups()
        if len(a) == 4:  # 2025/03/31
            cands = [(int(a), int(b), int(c))]
        else:
            y = _year(c)
            cands = [(y, int(a), int(b)), (y, int(b), int(a))]
        if want in cands:
            out.append((mm.start(), mm.end()))
    for mm in _SPACED_DATE.finditer(masked):  # boxes on forms: "03 31 2025"
        a, b, c = mm.groups()
        if want in [(_year(c), int(a), int(b)), (_year(c), int(b), int(a))]:
            out.append((mm.start(), mm.end()))
    for mm in _NAME_DATE.finditer(masked):
        d, mon, y = (mm.group(1), mm.group(2), mm.group(3)) if mm.group(1) else (mm.group(5), mm.group(4), mm.group(6))
        mon = mon.lower()[:3]
        if mon in _MONTHS and (_year(y), _MONTHS.index(mon) + 1, int(d)) == want:
            out.append((mm.start(), mm.end()))
    return sorted(set(out))


_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|#3[49]|#160|#x2[67]|#xa0);", re.I)


def _words(content: str) -> list:
    """The block's words for matching a value: "&amp;" in the parsed HTML is the printed "&", not a word "amp"."""
    if "&" in (content or ""):
        content = _ENTITY.sub(lambda m: "<" + " " * (len(m.group(0)) - 2) + ">", content)
    return tokenize(content)


def _find_tokens(tokens: list, want: list[str]) -> list[tuple[int, int]]:
    n = len(want)
    if not n or n > len(tokens):
        return []
    norms = [t.norm for t in tokens]
    first = want[0]
    return [(tokens[i].start, tokens[i + n - 1].end) for i in range(len(norms) - n + 1)
            if norms[i] == first and norms[i:i + n] == want]


_PLAIN_NUMBER = re.compile(r"[(\-+]?[$€£¥]?\s*[\d.,\s]+%?\)?")  # a number only: matched as a number, never as loose digits


@dataclass
class Match:
    start: int
    end: int
    exact: bool
    bbox: Optional[BBox]
    content: str
    grounded: bool  # True when the box comes from word/cell boxes rather than the whole region
    occurrences: int = 1  # how many times the value occurs in the region


def spans(value: Any, ref: RegionRef) -> tuple[list[tuple[int, int]], bool]:
    """Every place the value's text occurs in the region (normalised compare), and whether exact."""
    if value is None or isinstance(value, (bool, dict, list)):
        return [], True
    content = ref.region.content or ""
    if not content.strip():
        return [], True
    masked = _mask(content)
    as_num = float(value) if isinstance(value, (int, float)) else S._to_number(str(value))
    if as_num is not None:
        found = _find_number(masked, as_num)
        if not found and not isinstance(value, (int, float)) and not _PLAIN_NUMBER.fullmatch(str(value).strip()):
            found = _find_tokens(_words(content), norm_tokens(str(value)))  # "INV-2041": text that holds a number
        return found, True
    want = norm_tokens(str(value))
    if not want:
        return [], True
    found = _find_tokens(_words(content), want)
    if not found and _ISO_DATE.match(str(value)):
        found = _find_date(masked, str(value))
    if not found and len(want) >= 4:
        needle = " ".join(str(value).lower().split())
        al = fuzz.partial_ratio_alignment(needle, masked.lower())
        if al is not None and al.score >= 90 and (al.dest_end - al.dest_start) >= 0.6 * len(needle):
            return [(al.dest_start, al.dest_end)], False
    return found, True


def _row_span(content: str, pos: int) -> tuple[int, int]:
    """The table row (or text line) that contains a char offset."""
    if "<tr" in content:
        lo = content.rfind("<tr", 0, pos + 1)
        hi = content.find("</tr>", pos)
        if lo >= 0 and hi >= 0:
            return lo, hi
    lo = content.rfind("\n", 0, pos) + 1
    hi = content.find("\n", pos)
    return lo, len(content) if hi < 0 else hi


def _settle_boxes(words: list, content: str, region: Any) -> list:
    """One box per printed word of a match.

    A printed word such as ``01/01/2025`` is several text pieces ("01", "01", "2025") that all carry the
    box of the printed word they were matched to. A short repeated piece ("01") can be matched to the same
    piece of a neighbouring word (the next date in the row), which stretches the union over both. Pieces
    with no whitespace between them are one printed word, so when their boxes are whole-word boxes (two
    pieces carry the same box) they must share one: the box of the piece that is rarest in the region wins
    (weight = length / how often that piece occurs). Pieces that each have their own box are left alone."""
    if len(words) < 2:
        return [w.bbox for w in words]
    freq: dict[str, int] = {}
    for w in region.iter_words():
        freq[w.text] = freq.get(w.text, 0) + 1
    words = sorted(words, key=lambda w: w.char_start)
    groups: list[list] = [[words[0]]]
    for prev, w in zip(words, words[1:]):
        gap = content[prev.char_end:w.char_start]
        if prev.char_end <= w.char_start and gap and not any(ch.isspace() or ch in "<>" for ch in gap):
            groups[-1].append(w)
        else:
            groups.append([w])
    out = []
    for g in groups:
        score: dict[tuple, float] = {}
        keep: dict[tuple, Any] = {}
        for w in g:
            k = (round(w.bbox.left, 4), round(w.bbox.top, 4), round(w.bbox.width, 4), round(w.bbox.height, 4))
            score[k] = score.get(k, 0.0) + len(w.text) / max(freq.get(w.text, 1), 1)
            keep.setdefault(k, w.bbox)
        if len(score) < len(g):   # at least two pieces share a box: the boxes are whole printed words, one is right
            out.append(keep[max(score, key=score.get)])
        else:                     # every piece has its own box (boxes per piece): keep them all
            out.extend(w.bbox for w in g)
    return out


_WORD_SOURCES = ("pdf", "native", "ocr", "tesseract", "rapidocr")


def _page_word_box(value: Any, ref: RegionRef) -> Optional[BBox]:
    """The value's box from the page's own word layer (PDF text or OCR words), for a value the region holds
    but whose words have no position (a form label and its amount printed apart). Of several places, the one
    nearest the region is used."""
    page = getattr(ref.page, "page", None)
    sources = getattr(page, "words", None) or {}
    if not isinstance(sources, dict) or ref.region.bbox is None:
        return None
    order = [k for k in _WORD_SOURCES if sources.get(k)] + [k for k in sources if k not in _WORD_SOURCES and sources[k]]
    rb = ref.region.bbox
    for key in order:
        words = [w for w in sources[key] if w.bbox is not None and w.text]
        if not words:
            continue
        parts, offs, pos = [], [], 0
        for w in words:
            offs.append((pos, pos + len(w.text)))
            parts.append(w.text)
            pos += len(w.text) + 1
        text = " ".join(parts)
        as_num = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else S._to_number(str(value))
        if as_num is not None:
            found = _find_number(text, as_num)
        elif _ISO_DATE.match(str(value)):
            found = _find_date(text, str(value))
        else:
            want = norm_tokens(str(value))
            found = _find_tokens(tokenize(text), want) if want else []
        best, best_d = None, None
        for a, b in found:
            hit = [w for w, (x, y) in zip(words, offs) if x < b and y > a]
            if not hit:
                continue
            box = BBox.union([w.bbox for w in hit])
            cx, cy = box.left + box.width / 2, box.top + box.height / 2
            dx = max(rb.left - cx, 0, cx - (rb.left + rb.width))
            dy = max(rb.top - cy, 0, cy - (rb.top + rb.height))
            d = (dx * dx + dy * dy) ** 0.5
            if best_d is None or d < best_d:
                best, best_d = box, d
        if best is not None and best_d is not None and best_d <= 0.15:
            return best
    return None


def locate(value: Any, ref: RegionRef, near: Optional[int] = None) -> Optional[Match]:
    """Find the value's text inside the region; None when it is not there.

    When the text occurs more than once, ``near`` (a char offset, e.g. where a sibling field of the
    same table row was found) picks the closest occurrence; otherwise the first one is used.
    """
    found, exact = spans(value, ref)
    if not found:
        return None
    region = ref.region
    content = region.content
    if near is not None and len(found) > 1:
        lo, hi = _row_span(content, near)
        same_row = [sp for sp in found if lo <= sp[0] < hi]
        start, end = min(same_row or found, key=lambda sp: abs(sp[0] - near))
    else:
        start, end = found[0]
    words = [w for w in region.iter_words()
             if w.bbox is not None and w.char_start is not None and w.char_end is not None
             and w.char_start < end and w.char_end > start]
    cell = next((c for c in region.cells if c.char_start is not None and c.char_end is not None
                 and c.char_start <= start < c.char_end), None)
    boxes = _settle_boxes(words, content, region)
    bbox = BBox.union(boxes) if boxes else (cell.bbox if cell and cell.bbox else None)
    if bbox is None:  # the region has the value but its words were not placed: look on the page itself
        bbox = _page_word_box(value, ref)
    # the cited text is the matched text itself (as in Reducto's citations), not the cell or line around it
    lo, hi = start, end   # a match ends at its last word: take back punctuation the value itself carries ("Inc.")
    v = str(value).strip()
    for k in (3, 2, 1):
        tail = content[hi:hi + k]
        if len(tail) == k and v.endswith(tail) and not any(ch.isalnum() or ch.isspace() or ch in "<>" for ch in tail):
            hi += k
            break
    for k in (3, 2, 1):
        head = content[max(lo - k, 0):lo]
        if len(head) == k and v.startswith(head) and not any(ch.isalnum() or ch.isspace() or ch in "<>" for ch in head):
            lo -= k
            break
    text = " ".join(_TAG.sub(" ", content[lo:hi]).split())
    if not text:
        text = cell.text.strip() if cell is not None and cell.text.strip() else ""
    return Match(start, end, exact, bbox, text, grounded=bbox is not None, occurrences=len(found))


# ---------------------------------------------------------------- arithmetic rules from the system prompt

_REF = r"[`'\"]?([A-Za-z_][\w]*(?:(?:\[\*?\])?\.[A-Za-z_][\w]*)*(?:\[\*?\])?)[`'\"]?"
_RULE_SUM_A = re.compile(_REF + r"\s+(?:must\s+|should\s+|has to\s+|needs to\s+)?(?:equals?|be equal to|=+|is|match(?:es)?)\s+(?:the\s+)?sum of\s+(?:all\s+|the\s+|every\s+|each\s+)*" + _REF, re.I)
_RULE_SUM_B = re.compile(r"sum of\s+(?:all\s+|the\s+|every\s+|each\s+)*" + _REF + r"\s+(?:must\s+|should\s+|has to\s+|needs to\s+)?(?:equals?|be equal to|=+|is|match(?:es)?)\s+(?:the\s+)?" + _REF, re.I)
_RULE_ADD = re.compile(_REF + r"\s*=+\s*(" + _REF + r"(?:\s*[+\-]\s*" + _REF + r")+)", re.I)


@dataclass
class SumRule:
    target: tuple            # path of a scalar
    terms: list[tuple[int, tuple]]  # (sign, path); a path may run through one array ("*" marker)
    text: str


def _resolve_ref(ref: str, schema: Any) -> Optional[tuple]:
    parts = [p for p in re.split(r"\.", ref.replace("[*]", "").replace("[]", "")) if p]
    node = schema
    path: list = []
    for i, part in enumerate(parts):
        node = S.branch_for(node, {}) if not S.is_object(node) else node
        if S.is_array(node):
            node = S.branch_for(node.get("items", {}), {})
            path.append("*")
        if not S.is_object(node) or part not in node["properties"]:
            if i == 0 and len(parts) == 1:
                return _search_ref(part, schema)
            return None
        node = node["properties"][part]
        path.append(part)
    return tuple(path)


def _search_ref(name: str, schema: Any) -> Optional[tuple]:
    """A bare name that is not top-level: accept it when exactly one nested field has that name."""
    found: list[tuple] = []

    def go(node: Any, path: tuple, depth: int) -> None:
        node = S.branch_for(node, {}) if not S.is_object(node) else node
        if S.is_array(node):
            go(node.get("items", {}), path + ("*",), depth)
            return
        if S.is_object(node) and depth < 4:
            for k, sub in node["properties"].items():
                if k == name:
                    found.append(path + (k,))
                go(sub, path + (k,), depth + 1)

    go(schema, (), 0)
    return found[0] if len(found) == 1 else None


def parse_sum_rules(system_prompt: str, schema: Any) -> list[SumRule]:
    """Sum rules stated in the system prompt whose field names all resolve against the schema."""
    rules: list[SumRule] = []
    text = system_prompt or ""
    seen = set()

    def add(target: Optional[tuple], terms: list[tuple[int, Optional[tuple]]], raw: str) -> None:
        if target is None or "*" in target or any(p is None for _, p in terms):
            return
        key = (target, tuple(terms))
        if key not in seen:
            seen.add(key)
            rules.append(SumRule(target, terms, raw.strip()))  # type: ignore[arg-type]

    for m in _RULE_SUM_A.finditer(text):
        add(_resolve_ref(m.group(1), schema), [(1, _resolve_ref(m.group(2), schema))], m.group(0))
    for m in _RULE_SUM_B.finditer(text):
        add(_resolve_ref(m.group(2), schema), [(1, _resolve_ref(m.group(1), schema))], m.group(0))
    for m in _RULE_ADD.finditer(text):
        terms = []
        for tm in re.finditer(r"([+\-]?)\s*" + _REF, m.group(2)):
            terms.append((-1 if tm.group(1) == "-" else 1, _resolve_ref(tm.group(2), schema)))
        add(_resolve_ref(m.group(1), schema), terms, m.group(0))
    return rules


def _values_at(value: Any, path: tuple) -> Optional[list]:
    """All values at a path ("*" fans out over an array). None when the path cannot be followed."""
    cur = [value]
    for p in path:
        nxt = []
        for v in cur:
            if p == "*":
                if not isinstance(v, list):
                    return None
                nxt.extend(v)
            else:
                if not isinstance(v, dict):
                    return None
                nxt.append(v.get(p))
        cur = nxt
    return cur


def check_sum_rules(value: Any, rules: list[SumRule]) -> list[dict]:
    failures = []
    for rule in rules:
        tv = _values_at(value, rule.target)
        if not tv or not isinstance(tv[0], (int, float)) or isinstance(tv[0], bool):
            continue  # nothing to compare against: not a failure, just unverifiable
        total, ok = 0.0, True
        for sign, path in rule.terms:
            vals = _values_at(value, path)
            if vals is not None:
                vals = [v for v in vals if v is not None]  # a row without an amount (a heading line) adds nothing
            if vals is None or any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in vals):
                ok = False
                break
            total += sign * sum(vals)
        if not ok:
            continue
        if abs(total - tv[0]) > max(0.01, 1e-6 * abs(tv[0])):
            fields = {rule.target[0]} | {p[0] for _, p in rule.terms}
            failures.append({"kind": "arithmetic", "fields": sorted(fields),
                             "message": f"rule \"{rule.text}\" does not hold: {S.path_str(rule.target)} is "
                                        f"{tv[0]} but the terms add up to {round(total, 4)}"})
    return failures


# ---------------------------------------------------------------- context carried between segments

def _last_items(value: Any) -> dict[str, dict]:
    """The last object of every top-level array of objects."""
    out = {}
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, list) and v and isinstance(v[-1], dict):
                out[k] = v[-1]
    return out


def needs_carry(prev: Any, cur: Any) -> bool:
    """True when the first row of an array in this segment lacks a value that the last row of the previous
    segment has: typically a group heading (state, account, section) printed once, on an earlier page."""
    last = _last_items(prev)
    if not isinstance(cur, dict):
        return False
    for k, item in last.items():
        rows = cur.get(k)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            if any(rows[0].get(f) is None and v is not None and not isinstance(v, (dict, list)) for f, v in item.items()):
                return True
    return False


def carry_hint(prev: Any) -> str:
    last = _last_items(prev)
    if not last:
        return ""
    return ("Context from the preceding pages (already extracted, do not repeat these items): the last item of "
            f"each list there was {json.dumps(last, ensure_ascii=False)}. A value that the document prints once "
            "for a group of rows (a group heading, section, state, account or category) keeps applying to the "
            "rows on these pages until the pages print a new one; use it for those rows instead of null.")


# ---------------------------------------------------------------- the extractor

class _Extractor:
    fill: Any = "choices"  # the fill_required policy (reply_schema.fill_policy)

    def __init__(self, view: DocView, pages: list[PageRef], user_schema: dict, system_prompt: str,
                 llm: LLM, opts: dict, track: bool, include_images: bool):
        if getattr(llm, "prompt_cache", False) and not opts.get("max_tokens_set"):
            # Anthropic: the reply may use the model's whole output allowance (from its public limits); only the
            # tokens written are billed, and a reply cut off at a lower cap would have to be asked again
            from .. import model_limits as ML
            from .llm import THINKING_HEADROOM
            try:
                out_cap = int(ML.for_llm(llm)[0].get("max_output") or 0)
            except ML.UnknownModel:
                out_cap = 0
            if out_cap > THINKING_HEADROOM:
                opts = {**opts, "max_tokens": out_cap - THINKING_HEADROOM}
        self.view, self.pages, self.llm, self.opts = view, pages, llm, opts
        self.prompt_schema = S.resolve_refs(user_schema)
        self.strict = S.enforced(self.prompt_schema)
        self.system = (system_prompt.strip() + "\n\n" if system_prompt.strip() else "") + RULES
        self.track = track
        self.labels = track  # block labels in the rendered pages (also for "_block" fields when citing)
        self.reply: Optional[dict] = None  # the schema the model answers in (reply_schema), when used
        self.reply_note = ""  # how to answer in the reply schema (reply_schema.NOTE and request options)
        self.failed: list[tuple[int, int, list[str]]] = []  # (first page, last page, fields) whose reply failed
        self.include_images = include_images
        self.results: list[LLMResult] = []
        self.notes: list[str] = []
        self.unreadable: set[str] = set()  # top-level fields whose reply could not be read, even after retries
        self.leaf_meta: dict = {}  # (path pattern, value key) -> the model's notes on that value (field report)
        self.struck: dict = {}  # region label -> crossed-out char spans, shown to the model as ~~text~~
        self._lock = threading.Lock()
        self.label_page = {ref.label: p.number for p in view.pages for ref in p.regions}
        self.track_text = TRACK
        self.extra_text = ""  # example outputs (instructions.examples), shown with every prompt
        self.direct: dict = {}  # one-stage extraction: {"pdf": path} or {"images": {page: file}} the model reads
        self.pdf_path: Optional[str] = None  # set when the model reads the file itself (direct), not parsed text
        self._pdf_marks: dict = {}

    def _pdf_part(self, pages: list[PageRef]) -> str:
        """The placeholder for these pages of the file itself: a PDF of only these pages, or their images."""
        from ..textlayer import pdf_subset
        from .llm import image_mark, pdf_mark
        key = tuple(p.number for p in pages)
        with self._lock:
            if key not in self._pdf_marks:
                if self.direct.get("pdf"):
                    self._pdf_marks[key] = pdf_mark(pdf_subset(self.direct["pdf"], list(key)))
                else:
                    imgs = self.direct["images"]
                    self._pdf_marks[key] = image_mark([Path(imgs[n]).read_bytes() for n in key if n in imgs])
            return self._pdf_marks[key]

    # -- prompts
    def _prompt(self, pages: list[PageRef], schema: dict, part: Optional[tuple[int, int]] = None,
                extra: str = "") -> str:
        # the document comes first and is the same for every group of fields: CACHE_MARK lets the API cache it
        if self.pdf_path:  # one-stage: the model reads the PDF pages themselves (sent as a document block)
            doc_text = self._pdf_part(pages)
        else:
            doc_text = render_pages(pages, labels=self.labels or self.track, marks=self.struck)
        out = ["Document:", doc_text + CACHE_MARK,
               "JSON Schema:", json.dumps(schema, ensure_ascii=False), ""]
        if self.track:
            out += [TRACK, ""]
        if self.reply_note:
            out += [self.reply_note, ""]
        if part and part[1] > 1:
            out += [f"This is part {part[0]} of {part[1]} of a longer document (pages {pages[0].number}-"
                    f"{pages[-1].number}). Extract only what appears in these pages; a field that is not in "
                    "these pages must be null and an array holds only the items found here.", ""]
        if self.extra_text:
            out += [self.extra_text, ""]
        if extra:
            out += [extra, ""]
        out.append("Return the JSON now.")
        return "\n".join(out)

    def _ask(self, pages: list[PageRef], prompt: str, schema: dict) -> Any:
        return self._ask_why(pages, prompt, schema)[0]

    def _ask_why(self, pages: list[PageRef], prompt: str, schema: dict) -> tuple[Any, str]:
        """One answer for ``prompt``: a reply that parses and has the schema's top-level shape, or None.

        A reply that parses but is not in the requested shape (a fragment, another object) is asked for again
        once, saying what was wrong; it is never used as the answer. The second value says why there is no
        answer: "truncated", "broken" (JSON with an error in it) or "no_json".
        """
        images = page_images(pages) if self.include_images and self.llm.supports_images else None
        reply_schema = envelope_schema(schema) if self.track else schema
        fmt_schema = reply_schema
        want = '"data" and "sources"' if self.track else (
            ", ".join(f'"{k}"' for k in schema["properties"]) if S.is_object(schema) else "")
        got: list[LLMResult] = []
        strict = S.enforced(schema)

        def repair(text: str) -> Any:
            # the bracket repair can reshape an answer (a missing opener looks like an extra closer): use it
            # only when the result has exactly the shape of the schema, with nothing moved, dropped or invalid
            fixed = repair_brackets(text)
            if fixed is None or not root_ok(fixed, schema):
                return None
            plain = fixed
            if isinstance(plain, list) and len(plain) == 1 and S.is_object(strict):
                plain = plain[0]
            if is_envelope(plain, strict):
                plain = plain["data"]
            plain, _ = S.unwrap(plain, strict)
            check = strict
            if getattr(self, "reply", None) is not None and isinstance(plain, dict):
                # a reply-schema answer is judged as the user's values (a model without structured outputs may
                # write 4200 for "4200"): converted back, then checked against the user's schema
                plain = copy.deepcopy(plain)
                RS.take_blocks(plain)
                plain.pop(RS.ALT_FIELD, None)
                plain = RS.from_reply(plain, self.prompt_schema, getattr(self, "fill", "choices"))
                check = S.enforced(self.prompt_schema)
            _, moved, stray = S.relocate(plain, check)
            if moved or stray or S.find_errors(S.conform(plain, check), check):
                return None
            return fixed

        try:
            data, results = ask_json(self.llm, self.system, prompt, images=images or None, json_schema=fmt_schema,
                                     max_tokens=self.opts["max_tokens"], retries=1, repair=repair)
            got += results
            if data is not None and not root_ok(data, schema):
                again = (prompt + "\n\nYour previous reply was not one complete JSON value in the requested shape"
                         + (f" (a JSON object with the top-level keys {want})" if want else "")
                         + ". Reply again with the complete JSON only.")
                data, results = ask_json(self.llm, self.system, again, images=images or None,
                                         json_schema=fmt_schema, max_tokens=self.opts["max_tokens"], retries=0,
                                         repair=repair)
                got += results
                if not root_ok(data, schema):
                    data = None
        except LLMError as e:
            raise TaskError(502, f"model call failed: {e}") from e
        finally:
            self.results.extend(got)
        why = ""
        if data is None:
            last = got[-1] if got else None
            text = (last.text if last else "") or ""
            why = ("truncated" if last is not None and last.truncated else
                   "broken" if ("{" in text or "[" in text) and '":' in text else "no_json")
        return data, why

    def _digest(self, data: Any, strict: dict) -> tuple[Any, dict, list]:
        flat: dict = {}
        notes: dict = {}
        if isinstance(data, list) and len(data) == 1 and S.is_object(strict) and is_envelope(data[0], strict):
            data = data[0]
        if is_envelope(data, strict):
            flat = flat_sources(data.get("sources"))
            notes = source_meta(data.get("sources"))
            data = data.get("data")
        if isinstance(data, list) and len(data) == 1 and S.is_object(strict):
            data = data[0]
        value, sources = S.unwrap(data, strict)  # tolerant: also when a reply wraps values as {"v", "r"}
        value, moved, stray = S.relocate(value, strict)
        for src, dst in moved:  # sources follow the values they describe
            flat = {(dst + p[len(src):] if p[:len(src)] == src else p): l for p, l in flat.items()}
            notes = {(dst + p[len(src):] if p[:len(src)] == src else p): m for p, m in notes.items()}
            sources = {(dst + p[len(src):] if p[:len(src)] == src else p): l for p, l in sources.items()}
        lost = [p for p in stray if isinstance(p[-1], str) and p[-1] in _field_names(strict)]
        if lost:  # a declared field written in a place it could not be moved back from: say so, never drop silently
            with self._lock:
                self.notes.append("the model's reply put these fields where the schema does not have them, so "
                                  "they were left out: " + ", ".join(S.path_str(p) for p in lost[:5]))
        value = S.conform(value, strict)
        if value is None and S.is_object(strict):
            value = S.empty_instance(strict)
        sources = {**flat, **sources}
        if flat:  # a source given for an object or a row applies to the values inside it that have none
            for path, v, _ in S.iter_leaves(value, strict):
                if path in sources or v is None:
                    continue
                for k in range(len(path) - 1, 0, -1):
                    if path[:k] in sources:
                        sources[path] = list(sources[path[:k]])
                        break
        if notes:
            from .field_report import pattern, value_key
            with self._lock:
                for path, m in notes.items():
                    key = (pattern(path), value_key(_get(value, path)))
                    prev = self.leaf_meta.setdefault(key, {})
                    alts = prev.get("alternatives", []) + m.get("alternatives", [])
                    prev.update(m)
                    if alts:
                        prev["alternatives"] = alts
        return value, sources, S.find_errors(value, strict)

    # -- one segment (with schema repair re-asks)
    def extract(self, pages: list[PageRef], prompt_schema: Optional[dict] = None, strict: Optional[dict] = None,
                part: Optional[tuple[int, int]] = None, extra: str = "", depth: int = 0,
                field_depth: int = 0, repairs: Optional[int] = None) -> tuple[Any, dict]:
        prompt_schema = prompt_schema or self.prompt_schema
        strict = strict or self.strict
        base = self._prompt(pages, prompt_schema, part, extra)
        data, why = self._ask_why(pages, base, prompt_schema)
        if data is None:
            fields = list(strict["properties"]) if S.is_object(strict) else []
            if len(fields) > 1 and field_depth < 3 and why in ("broken", "truncated"):
                # a reply that keeps breaking or is cut off: ask for part of the fields at a time, the lists apart
                # from the rest first (a list grows with the pages, the other fields do not). The pages stay
                # together, so a value on one page is still read against a special term on another.
                lists = [f for f in fields if S.is_array(S.branch_for(prompt_schema["properties"].get(f, {}), []))]
                if lists and len(lists) < len(fields):
                    split = [[f for f in fields if f not in lists], lists]
                else:
                    half = (len(fields) + 1) // 2
                    split = [fields[:half], fields[half:]]
                out_v: dict = {}
                out_s: dict = {}
                for group in split:
                    v, s_ = self.extract(pages, _sub_schema(prompt_schema, group), _sub_schema(strict, group),
                                         part, extra, depth, field_depth + 1, repairs)
                    out_v.update(v if isinstance(v, dict) else {})
                    out_s.update(s_)
                return S.conform(out_v, strict), out_s
            if len(pages) > 1 and depth < 2:  # one field (a long list) cut off: its pages in two halves
                mid = len(pages) // 2
                a = self.extract(pages[:mid], prompt_schema, strict, part, extra, depth + 1, field_depth, repairs)
                hint = carry_hint(a[0])  # the halves do not overlap: tell the second where the first ended
                b = self.extract(pages[mid:], prompt_schema, strict, part, (extra + "\n\n" + hint).strip(), depth + 1,
                                 field_depth, repairs)
                tree = merge_trees(_annotate(a[0], a[1], strict), _annotate(b[0], b[1], strict), [])
                return _split_tree(tree)
            with self._lock:  # said in a warning only if these fields are still empty at the end
                self.unreadable.update(fields)
                self.failed.append((pages[0].number, pages[-1].number, fields))
            return S.empty_instance(strict), {}
        value, sources, errors = self._digest(data, strict)
        for _ in range(self.opts["max_repairs"] if repairs is None else repairs):
            if not errors:
                break
            problems = "\n".join(f"- {S.path_str(p)}: {msg}" for p, msg in errors[:40])
            fix = (f"Your previous answer:\n{json.dumps(value, ensure_ascii=False)}\n\n"
                   f"It breaks the schema in these places:\n{problems}\n\n"
                   "Return the complete corrected JSON. Fix only these problems; if the document does not "
                   "contain a valid value for a field, use null.")
            data2 = self._ask(pages, self._prompt(pages, prompt_schema, part, fix), prompt_schema)
            if data2 is None:
                continue
            v2, s2, e2 = self._digest(data2, strict)
            if len(e2) < len(errors):
                value, errors = v2, e2
                sources = {**sources, **s2} if self.track else s2
        return value, sources

    # -- the answer through the reply schema (structured outputs), in groups of fields
    def ask(self, pages: list[PageRef], part: Optional[tuple[int, int]] = None, extra: str = "",
            fields: Optional[list[str]] = None) -> tuple[Any, dict, list]:
        """(value in the user's types, {object path: block label}, alternatives) for ``pages``.

        The model answers in ``self.reply`` (reply_schema): every field required, "" for a value the document
        does not have, so the API can enforce the shape. A schema too big for one call is asked in groups of
        fields, decided before the call (reply_schema.plan), in parallel; every group sees the whole pages.
        """
        reply = self.reply
        alt = reply["properties"].get(RS.ALT_FIELD)
        core = {**reply, "properties": {k: v for k, v in reply["properties"].items() if k != RS.ALT_FIELD},
                "required": [r for r in reply.get("required", []) if r != RS.ALT_FIELD]}
        if fields:
            core = RS.project(core, [(f,) for f in fields if f in core["properties"]])
        user = _sub_schema(self.prompt_schema, fields) if fields else self.prompt_schema
        strict = S.enforced(user)

        def group_schema(paths: list[tuple]) -> dict:
            g = RS.project(core, paths)
            if alt is not None:
                g = {**g, "properties": {**g["properties"], RS.ALT_FIELD: alt},
                     "required": list(g.get("required", [])) + [RS.ALT_FIELD]}
            return g

        # One rule (enforcement on in every call where the model supports it):
        #   single: the API compiles the whole reply schema -> one call, the user's schema enforced
        #   coded:  it does not (the grammar is too large) -> the fixed coded format, the same in every call so the
        #           document is cached once (coded.py)
        #   plain:  a model without structured outputs -> groups of fields as before (reply_schema.plan)
        check = getattr(self.llm, "format_ok", None)
        so = check is not None and bool(getattr(self.llm, "structured_outputs", False))
        whole = [(k,) for k in core["properties"]]
        if so:
            ok = check(group_schema(whole))
            trace.event("extract_plan", mode="single" if ok else "coded", fields=RS._leaves(core),
                        top_level=len(whole), pages=len(pages), part=part,
                        reason="whole schema compiles" if ok else "the API refused the whole schema")
            if not ok:
                return self._ask_coded(pages, core, alt, user, strict, part, extra)
            groups = [whole]
        else:
            groups = RS.plan(core)
            trace.event("extract_plan", mode="plain", fields=RS._leaves(core), groups=len(groups), pages=len(pages),
                        part=part, reason="model without structured outputs")

        def one(paths: list[tuple], note: str) -> Any:
            # shape problems are judged after conversion, against the user's schema (below), not here
            g = group_schema(paths)
            if len(groups) > 1:
                # a group alone can be mistaken for its neighbours (the seller filled in with the buyer): it is
                # told which fields of the whole schema are asked separately
                mine = {".".join(p) for p in paths if p and p[-1] not in RS.MARKS}
                others = [".".join(p) for gr in groups for p in gr if p and p[-1] not in RS.MARKS
                          and ".".join(p) not in mine]
                if others:
                    part_note = ("This JSON Schema is one part of a larger one. These other fields are filled "
                                 "separately: " + ", ".join(dict.fromkeys(others)) + ". Fill only the fields "
                                 "here, each with the value that belongs to it and not to one of those fields.")
                    note = f"{note}\n\n{part_note}" if note else part_note
            with trace.tags(why="single" if len(groups) == 1 else f"group {groups.index(paths) + 1}/{len(groups)}"):
                return self.extract(pages, g, S.enforced(g), part, note, repairs=0)[0]

        def answer(note: str, which: list[list[tuple]]) -> tuple[Any, dict, list]:
            if len(which) > 1 and getattr(self.llm, "parallel_safe", False):
                from concurrent.futures import ThreadPoolExecutor
                warm = getattr(self.llm, "warm", None)
                if warm is not None and getattr(self.llm, "prompt_cache", False):
                    # calls sent together cannot read each other's cache: write it once first
                    warm(self.system, self._prompt(pages, group_schema(which[0]), part, note))
                with ThreadPoolExecutor(min(len(which), 8)) as pool:
                    answers = list(pool.map(lambda g: one(g, note), which))
            else:
                answers = [one(g, note) for g in which]
            merged: dict = {}
            alts: list = []
            for v in answers:
                if not isinstance(v, dict):
                    continue
                a = v.pop(RS.ALT_FIELD, None)
                if isinstance(a, list):
                    alts += [x for x in a if isinstance(x, dict) and str(x.get("value", "")).strip()]
                merged = RS.merge(merged, v)
            hints = RS.take_marks(merged)
            value = S.conform(RS.from_reply(merged, user, self.fill), strict)
            if value is None and S.is_object(strict):
                value = S.empty_instance(strict)
            return value, hints, alts

        value, hints, alts = answer(extra, groups)
        # checked against the user's own schema (patterns, ranges, enums): what breaks it is asked for again,
        # only in the groups that hold those fields; a re-ask is kept only when it has fewer problems
        errors = S.find_errors(value, strict)
        for _ in range(self.opts["max_repairs"]):
            if not errors:
                break
            problems = "\n".join(f"- {S.path_str(p)}: {msg}" for p, msg in errors[:40])
            fix = ((extra + "\n\n") if extra else "") + (
                f"Your previous answer:\n{json.dumps(value, ensure_ascii=False, default=str)}\n\nIt breaks the schema "
                f"in these places:\n{problems}\n\nReturn the complete corrected JSON. Fix only these problems; if the "
                'document does not contain a valid value for a field, use "".')
            bad = {p[0] for p, _ in errors if p and isinstance(p[0], str)}
            which = [g for g in groups if any(path[0] in bad for path in g)] or groups
            trace.event("schema_repair", errors=len(errors), fields=sorted(bad)[:20])
            with trace.tags(why="repair: answer broke the user's schema"):
                v2, h2, a2 = answer(fix, which)
            if isinstance(value, dict) and isinstance(v2, dict) and which is not groups:
                v2 = {**value, **{k: v2.get(k) for k in {path[0] for g in which for path in g}}}
                h2 = {**hints, **h2}
            e2 = S.find_errors(v2, strict)
            if len(e2) < len(errors):
                value, hints, errors = v2, h2, e2
                alts = alts + a2
            else:
                break
        return value, hints, alts

    # -- a schema too complex for one structured call: the fixed coded format (coded.py)
    def _ask_coded(self, pages: list[PageRef], core: dict, alt: Optional[dict], user: dict, strict: dict,
                   part: Optional[tuple[int, int]], extra: str) -> tuple[Any, dict, list]:
        leaves = CD.build(core)
        fmt = CD.format_schema(leaves, alt)
        enum_ok = self.llm.format_ok(fmt)
        if not enum_ok:  # the list of codes is too large for the grammar: codes as plain text, checked by code
            fmt = CD.format_schema(leaves, alt, enum_codes=False)
        groups = CD.groups(leaves, int(self.opts.get("coded_group_leaves", 40)))
        trace.event("coded_plan", codes=len(leaves), groups=len(groups), group_sizes=[len(g) for g in groups],
                    code_enum=enum_ok)
        note = self.reply_note.replace(RS.NOTE, CD.NOTE)
        doc = render_pages(pages, labels=self.labels or self.track, marks=self.struck)

        def prompt(gl: list) -> str:
            out = ["Document:", doc + CACHE_MARK, "Fields to fill (code | field | what it takes):", CD.lines(gl), ""]
            if len(groups) > 1:
                out += ["These are some of the fields; the others are filled separately. Fill only these, each "
                        "with the value that belongs to it.", ""]
            out += [note, ""]
            if part and part[1] > 1:
                out += [f"This is part {part[0]} of {part[1]} of a longer document (pages {pages[0].number}-"
                        f"{pages[-1].number}). Extract only what appears in these pages.", ""]
            if self.extra_text:
                out += [self.extra_text, ""]
            if extra:
                out += [extra, ""]
            out.append("Return the JSON now.")
            return "\n".join(out)

        def call(gl: list, why: str, depth: int = 0) -> tuple[dict, list]:
            with trace.tags(why=why, group_fields=len(gl)):
                try:
                    data, results = ask_json(self.llm, self.system, prompt(gl), json_schema=fmt,
                                             max_tokens=self.opts["max_tokens"], retries=0)
                except LLMError as e:
                    raise TaskError(502, f"model call failed: {e}") from e
                self.results.extend(results)
                last = results[-1] if results else None
                if data is None and last is not None and last.truncated and depth < 3:
                    halves = CD.split(gl)
                    trace.event("coded_truncated", fields=len(gl), split=halves is not None)
                    if halves is not None:
                        a = call(halves[0], f"{why} / cut off, first half", depth + 1)
                        b = call(halves[1], f"{why} / cut off, second half", depth + 1)
                        return RS.merge(a[0], b[0]), a[1] + b[1]
                if data is None:
                    trace.event("coded_unreadable", fields=len(gl),
                                stop=last.stop_reason if last is not None else None)
                    with self._lock:
                        names = sorted({l.path[0] for l in gl})
                        self.unreadable.update(names)
                        self.failed.append((pages[0].number, pages[-1].number, names))
                    return {}, []
                value, stats = CD.rebuild(data, {l.code: l for l in gl})
                trace.event("coded_rebuild", fields=len(gl), **stats)
                a = data.get("alternatives") if isinstance(data, dict) else None
                return value, [x for x in a if isinstance(x, dict) and str(x.get("value", "")).strip()] \
                    if isinstance(a, list) else []

        n = len(groups)
        # the first call writes the document to the cache; the rest are sent together after it and read it
        answers = [call(groups[0], f"coded group 1/{n}")]
        if n > 1:
            if getattr(self.llm, "parallel_safe", False):
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(min(n - 1, 8)) as pool:
                    answers += list(pool.map(lambda a: call(a[1], f"coded group {a[0] + 2}/{n}"),
                                             enumerate(groups[1:])))
            else:
                answers += [call(g, f"coded group {i + 2}/{n}") for i, g in enumerate(groups[1:])]
        merged: dict = {}
        alts: list = []
        for v, a in answers:
            merged = RS.merge(merged, v)
            alts += a
        hints = RS.take_marks(merged)
        value = S.conform(RS.from_reply(merged, user, self.fill), strict)
        if value is None and S.is_object(strict):
            value = S.empty_instance(strict)
        errors = S.find_errors(value, strict)
        # no re-ask: what still breaks the user's schema is reported (and emptied later, like any answer)
        trace.event("coded_result", schema_errors=len(errors), examples=[S.path_str(p) for p, _ in errors[:10]])
        if errors:
            with self._lock:
                self.notes.append("values that did not fit the schema: " +
                                  ", ".join(S.path_str(p) for p, _ in errors[:8]))
        return value, hints, alts

    # -- citation support
    def supported(self, value: Any, labels: list[str]) -> Optional[bool]:
        """True/False when the value can be checked against its cited blocks, None when it cannot."""
        if value is None or isinstance(value, bool):
            return None
        refs = [r for r in (self.view.lookup(l) for l in labels) if r is not None]
        if not refs:
            return None
        return any(locate(value, r) is not None for r in refs)

    def found_anywhere(self, value: Any) -> list[tuple[RegionRef, Match]]:
        hits = []
        for p in self.pages:
            for ref in p.regions:
                m = locate(value, ref)
                if m is not None:
                    hits.append((ref, m))
        return hits


def _blank_value(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _partial_copy(a: dict, b: dict) -> bool:
    """``a`` adds nothing to ``b``: every field ``a`` fills, ``b`` fills with the same value, and ``b`` fills more.
    At least two filled fields in common, so two short different items are never merged."""
    filled = [k for k, v in a.items() if k not in RS.MARKS and not _blank_value(v)]
    if len(filled) < 2 or any(b.get(k) != a[k] for k in filled):
        return False
    return any(_blank_value(a.get(k)) and not _blank_value(v) for k, v in b.items() if k not in RS.MARKS)


def drop_partial_copies(value: Any) -> tuple[Any, int]:
    """Every list of objects without the items that are less complete copies of another item; (value, dropped)."""
    dropped = 0

    def go(v: Any) -> Any:
        nonlocal dropped
        if isinstance(v, dict):
            return {k: go(x) for k, x in v.items()}
        if isinstance(v, list):
            items = [go(x) for x in v]
            if all(isinstance(x, dict) for x in items) and len(items) > 1:
                keep = [a for a in items if not any(b is not a and _partial_copy(a, b) for b in items)]
                dropped += len(items) - len(keep)
                return keep
            return items
        return v

    return go(value), dropped


PDF_PAGE_TOKENS = 1600  # a PDF page sent to the model also costs its page image (about 1,600 tokens for a letter page)


def _token_parts(pages: list[PageRef], budget: int, overlap: int = 1, per_page: int = 0) -> list[list[PageRef]]:
    """Pages in parts of at most ``budget`` tokens (estimated from characters, no call), neighbouring parts
    sharing ``overlap`` pages. One part when the whole document fits: every field is then answered from the whole
    document, once."""
    from .. import model_limits as ML
    toks = [ML.estimate_tokens(sum(len(ref.region.content) for ref in p.regions)) + per_page for p in pages]
    if sum(toks) <= budget or len(pages) < 2:
        return [pages]
    parts, i = [], 0
    while i < len(pages):
        j, used = i, 0
        while j < len(pages) and (j == i or used + toks[j] <= budget):
            used += toks[j]
            j += 1
        parts.append(pages[i:j])
        if j >= len(pages):
            break
        i = j - overlap if j - overlap > i else j
    return parts


def part_budget_for(llm: LLM, opts: dict) -> tuple[int, str, dict]:
    """(largest page part in tokens, where the number came from, the model's limits). ``part_tokens`` in the
    options overrides; otherwise the largest part that stays under the model's price jump and context window."""
    from .. import model_limits as ML
    try:
        limits, source = ML.for_llm(llm)
    except ML.UnknownModel as e:
        if opts.get("part_tokens"):
            return int(opts["part_tokens"]), "set in options (part_tokens)", {}
        raise TaskError(400, str(e)) from e
    if opts.get("part_tokens"):
        return int(opts["part_tokens"]), "set in options (part_tokens)", limits
    largest = ML.part_budget(limits)
    if largest <= DEFAULT_PART_TOKENS:
        return largest, f"largest under the model's limits ({source})", limits
    return DEFAULT_PART_TOKENS, f"default (measured best for accuracy; this model allows up to {largest})", limits


def _segments(pages: list[PageRef], opts: dict, force: bool) -> list[list[PageRef]]:
    """Page segments of at most ``segment_pages`` pages and about ``segment_chars`` characters (dense
    table pages produce more output than a model call returns: fewer pages per call then)."""
    size, overlap = max(int(opts["segment_pages"]), 1), max(int(opts["segment_overlap"]), 0)
    overlap = min(overlap, size - 1)
    budget = int(opts.get("segment_chars") or 0)
    chars = [sum(len(ref.region.content) for ref in p.regions) for p in pages]
    if len(pages) <= size and not (force and budget and sum(chars) > budget and len(pages) > 1):
        return [pages]
    if not force and sum(chars) <= opts["auto_segment_chars"]:
        return [pages]
    segs, i = [], 0
    while i < len(pages):
        j, used = i, 0
        while j < len(pages) and j - i < size and (j == i or not budget or used + chars[j] <= budget):
            used += chars[j]
            j += 1
        segs.append(pages[i:j])
        if j >= len(pages):
            break
        i = j - overlap if j - overlap > i else j
    return segs


def _top_fields(paths: Any) -> list[str]:
    out = []
    for p in paths:
        if p and isinstance(p[0], str) and p[0] not in out:
            out.append(p[0])
    return out


def _sub_schema(schema: dict, fields: list[str]) -> dict:
    out = {k: v for k, v in schema.items() if k not in ("properties", "required")}
    out["properties"] = {k: v for k, v in schema["properties"].items() if k in fields}
    if isinstance(schema.get("required"), list):
        out["required"] = [k for k in schema["required"] if k in fields]
    return out


def _array_paths(value: Any, path: tuple = ()) -> list[tuple]:
    """Paths of arrays that are not inside another array."""
    if isinstance(value, list):
        return [path]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _array_paths(v, path + (k,))]
    return []


def _get(value: Any, path: tuple) -> Any:
    for p in path:
        if isinstance(value, dict):
            value = value.get(p)
        elif isinstance(value, list) and isinstance(p, int) and p < len(value):
            value = value[p]
        else:
            return None
    return value


# ---------------------------------------------------------------- list completeness (rows vs parsed tables)

def _rows_in(content: str) -> int:
    """Body rows of one parsed table (HTML or markdown); header rows are not counted."""
    c = content or ""
    if re.search(r"<tr\b", c, re.I):
        rows = len(re.findall(r"<tr\b", c, re.I))
        heads = len(re.findall(r"<tr\b[^>]*>\s*<th\b", c, re.I))
        return max(rows - heads, 0)
    lines = [ln for ln in c.splitlines() if ln.strip().startswith("|")]
    body = [ln for ln in lines if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", ln)]
    return max(len(body) - 1, 0) if len(body) < len(lines) else len(body)


def table_rows(pages: list[PageRef]) -> int:
    return sum(_rows_in(ref.region.content) for p in pages for ref in p.regions
               if ref.region.type == RegionType.TABLE)


def _main_array(parts: list[tuple[Any, dict]]) -> Optional[tuple]:
    """The top-level array with the most items over all segments (the list being extracted)."""
    totals: dict[tuple, int] = {}
    for value, _ in parts:
        for ap in _array_paths(value):
            totals[ap] = totals.get(ap, 0) + len(_get(value, ap) or [])
    return max(totals, key=totals.get) if totals and max(totals.values()) > 0 else None


def short_segments(parts: list[tuple[Any, dict]], rows: list[int], ap: tuple, opts: dict) -> list[int]:
    """Segments whose item count is far below what their tables suggest. With several segments the
    expected share of rows is the median of the others (a schema may want only some rows); alone, all rows."""
    counts = [len(_get(v, ap) or []) for v, _ in parts]
    min_rows, ratio = int(opts["row_check_min_rows"]), float(opts["row_check_ratio"])
    shares = sorted(c / r for c, r in zip(counts, rows) if r >= min_rows)
    if not shares:
        return []
    expected = shares[len(shares) // 2] if len(shares) > 1 else 1.0
    return [i for i, (c, r) in enumerate(zip(counts, rows)) if r >= min_rows and c < ratio * expected * r]


def _complete_rows(parts: list[tuple[Any, dict]], segments: list[list[PageRef]], ex: "_Extractor",
                   opts: dict) -> tuple[list[tuple[Any, dict]], list[str]]:
    """Long lists: a segment that returned far fewer items than its parsed tables have rows is extracted
    again in halves (kept only when it finds more); a gap that remains becomes a warning."""
    ap = _main_array(parts)
    if ap is None:
        return parts, []
    rows = [table_rows(seg) for seg in segments]
    warnings = []
    for i in short_segments(parts, rows, ap, opts):
        seg, before = segments[i], len(_get(parts[i][0], ap) or [])
        part = (i + 1, len(segments))
        note = (f"An earlier answer for these pages listed only {before} items in {S.path_str(ap)}, but the pages' "
                f"tables have about {rows[i]} rows. List every item that belongs there; do not stop early.")
        if len(seg) > 1:
            mid = len(seg) // 2
            a = ex.extract(seg[:mid], part=part, extra=note)
            b = ex.extract(seg[mid:], part=part, extra=(note + "\n\n" + carry_hint(a[0])).strip())
            redo = _split_tree(merge_trees(_annotate(a[0], a[1], ex.strict), _annotate(b[0], b[1], ex.strict), []))
        else:
            redo = ex.extract(seg, part=part, extra=note)
        if len(_get(redo[0], ap) or []) > before:
            parts[i] = redo
        if i in short_segments(parts, rows, ap, opts):
            got = len(_get(parts[i][0], ap) or [])
            warnings.append(f"{S.path_str(ap)}: pages {seg[0].number}-{seg[-1].number} gave {got} items but their "
                            f"tables have about {rows[i]} rows; some items may be missing")
    return parts, warnings


def _overlap_failures(parts: list[tuple[Any, dict]], segments: list[list[PageRef]], ex: _Extractor) -> list[dict]:
    """Neighbouring segments share pages; both must have found the same number of array items there."""
    failures = []
    for k in range(len(parts) - 1):
        shared = sorted({p.number for p in segments[k]} & {p.number for p in segments[k + 1]})
        if not shared:
            continue
        counts = []
        for value, sources in parts[k:k + 2]:
            c: dict[tuple, int] = {}
            for ap in _array_paths(value):
                arr = _get(value, ap) or []
                n = 0
                for i in range(len(arr)):
                    pre = ap + (i,)
                    pages = {ex.label_page.get(l) for sp, labs in sources.items() if sp[:len(pre)] == pre for l in labs}
                    pages.discard(None)
                    if pages and pages <= set(shared):
                        n += 1
                c[ap] = n
            counts.append(c)
        for ap in set(counts[0]) | set(counts[1]):
            a, b = counts[0].get(ap, 0), counts[1].get(ap, 0)
            if a != b:
                failures.append({"kind": "overlap", "fields": [ap[0]], "path": ap, "pages": shared,
                                 "counts": (a, b),
                                 "message": f"{S.path_str(ap)}: page(s) {shared} gave {a} items in one segment "
                                            f"and {b} in the next"})
    return failures


def _support_failures(value: Any, sources: dict, ex: _Extractor) -> list[dict]:
    failures = []
    for path, v, leaf_schema in S.iter_leaves(value, ex.strict):
        if v is None or isinstance(v, bool) or isinstance(leaf_schema, dict) and "enum" in S.branch_for(leaf_schema, v):
            continue
        if ex.supported(v, sources.get(path, [])) is False and not ex.found_anywhere(v):
            failures.append({"kind": "citation", "fields": _top_fields([path]), "path": path,
                             "message": f"{S.path_str(path)}: the value {json.dumps(v, ensure_ascii=False)[:80]} "
                                        "does not appear in the block(s) it cites"})
    return failures


def _checks(value: Any, sources: dict, ex: _Extractor, rules: list[SumRule]) -> list[dict]:
    failures = [{"kind": "schema", "fields": _top_fields([p]), "path": p, "message": f"{S.path_str(p)}: {m}"}
                for p, m in S.find_errors(value, ex.strict)]
    failures += _support_failures(value, sources, ex)
    failures += check_sum_rules(value, rules)
    return failures


def _replace_fields(value: dict, sources: dict, new_value: dict, new_sources: dict, fields: list[str]) -> tuple[dict, dict]:
    out = dict(value)
    for f in fields:
        if f in new_value:
            out[f] = new_value[f]
    src = {p: l for p, l in sources.items() if not (p and p[0] in fields)}
    src.update({p: l for p, l in new_sources.items() if p and p[0] in fields})
    return out, src


def _fix_overlap(value: dict, sources: dict, failure: dict, ex: _Extractor) -> Optional[tuple[dict, dict]]:
    """Re-read one array on the shared pages only and swap in the items found there."""
    ap, shared = failure["path"], set(failure["pages"])
    fld = ap[0]
    pages = [p for p in ex.pages if p.number in shared]
    sub_prompt, sub_strict = _sub_schema(ex.prompt_schema, [fld]), _sub_schema(ex.strict, [fld])
    note = (f"Two earlier passes disagreed on how many items of \"{S.path_str(ap)}\" these pages contain "
            f"({failure['counts'][0]} vs {failure['counts'][1]}). Count carefully and return every item on these pages.")
    nv, ns = ex.extract(pages, sub_prompt, sub_strict, extra=note)
    new_items = _get(nv, ap)
    if not isinstance(new_items, list) or len(new_items) < max(failure["counts"]):
        return None  # never accept a re-read that found less than what we already had
    old = _get(value, ap) or []
    keep_tree, new_tree = [], []
    first_removed = None
    for i, item in enumerate(old):
        pre = ap + (i,)
        item_src = {sp[len(pre):]: l for sp, l in sources.items() if sp[:len(pre)] == pre}
        pgs = {ex.label_page.get(l) for labs in item_src.values() for l in labs}
        pgs.discard(None)
        tree = _annotate_item(item, item_src)
        if pgs and pgs <= shared:
            first_removed = len(keep_tree) if first_removed is None else first_removed
        else:
            keep_tree.append(tree)
    for i, item in enumerate(new_items):
        pre = ap + (i,)
        new_tree.append(_annotate_item(item, {sp[len(pre):]: l for sp, l in ns.items() if sp[:len(pre)] == pre}))
    at = len(keep_tree) if first_removed is None else first_removed
    merged = keep_tree[:at] + new_tree + keep_tree[at:]
    items, item_sources = _split_tree(merged)
    out = json.loads(json.dumps(value))
    parent = _get(out, ap[:-1]) if len(ap) > 1 else out
    parent[ap[-1]] = items
    src = {p: l for p, l in sources.items() if p[:len(ap)] != ap}
    src.update({ap + p: l for p, l in item_sources.items()})
    return out, src


def _annotate_item(item: Any, item_sources: dict, path: tuple = ()) -> Any:
    if isinstance(item, dict):
        return {k: _annotate_item(v, item_sources, path + (k,)) for k, v in item.items()}
    if isinstance(item, list):
        return [_annotate_item(v, item_sources, path + (i,)) for i, v in enumerate(item)]
    return Leaf(item, list(item_sources.get(path, [])))


# ---------------------------------------------------------------- citations

def _citation(ref: RegionRef, match: Optional[Match], extract_conf: float, cit_cfg: dict) -> dict:
    region = ref.region
    numeric = cit_cfg.get("numerical_confidence", True)
    parse_conf = region.confidence.score
    gran = {"extract_confidence": extract_conf if numeric else None, "parse_confidence": parse_conf if numeric else None}
    block_box = bbox_dict(region, ref.page)
    parent = {"type": region.type.value, "bbox": block_box,
              "content": "" if cit_cfg.get("parent_block", "full") == "bbox_only" else region.content,
              "image_url": None, "chart_data": None, "confidence": region.confidence.label,
              "granular_confidence": {"extract_confidence": None, "parse_confidence": parse_conf if numeric else None}}
    if match is not None:
        content = match.content
        box = bbox_dict(region, ref.page, match.bbox) if match.bbox is not None else block_box
        label = "high" if extract_conf >= 0.7 else "low"  # low: one of several places the value is printed
    else:  # the block is real but the value was not found in it: say so
        content = " ".join(_TAG.sub(" ", region.content).split())[:300]
        box, label = block_box, "low"
    # same keys as a citation in Reducto's real responses (image_url / chart_data / extra are null there too)
    return {"type": region.type.value, "bbox": box, "content": content, "image_url": None, "chart_data": None,
            "confidence": label, "granular_confidence": gran, "extra": None, "parentBlock": parent}


def build_citations(value: Any, labels: list[str], ex: _Extractor, cit_cfg: dict,
                    near: Optional[dict[str, int]] = None) -> list[dict]:
    """Citations for one leaf. Never fabricated: a value with no locatable source gets an empty list.

    ``near`` maps a region label to the char offset where a sibling field (same object, e.g. the same
    table row) was found unambiguously; it decides between repeated occurrences of this value.
    """
    if value is None:
        return []
    refs, seen = [], set()
    for l in labels:
        r = ex.view.lookup(l)
        if r is not None and r.label not in seen:
            seen.add(r.label)
            refs.append(r)
    out, missing = [], []
    for r in refs:
        anchor = (near or {}).get(r.label)
        m = locate(value, r, anchor)
        if m is not None:
            # found verbatim: 0.95; fuzzy: 0.85; found several times with nothing to tell them apart: 0.8
            conf = 0.85 if not m.exact else (0.8 if m.occurrences > 1 and anchor is None else 0.95)
            out.append(_citation(r, m, conf, cit_cfg))
        else:
            missing.append(r)
    if out:
        return out
    if missing:  # cited, but the text is not there (reformatted, inferred, or wrong): low confidence
        return [_citation(r, None, 0.3, cit_cfg) for r in missing]
    hits = ex.found_anywhere(value)
    if len(hits) == 1 and hits[0][1].occurrences == 1:  # no usable label, but the value occurs exactly once
        return [_citation(hits[0][0], hits[0][1], 0.7, cit_cfg)]
    return []


def _is_label(schema: Any) -> bool:
    """A field whose value is one of a fixed list (an enum): a category the model chose, not printed text."""
    if not isinstance(schema, dict):
        return False
    if isinstance(schema.get("enum"), list) or "const" in schema:
        return True
    return any(_is_label(b) for b in schema.get("anyOf") or [] if isinstance(b, dict) and b.get("type") != "null")


def _mark_value(x: Any) -> Optional[str]:
    if x is None or isinstance(x, (dict, list)) or (isinstance(x, str) and not x.strip()):
        return None
    if isinstance(x, bool):
        return str(x)
    try:
        return repr(float(x))
    except (TypeError, ValueError):
        return str(x).strip().lower()


def _signature(obj: dict) -> frozenset:
    return frozenset((k, v) for k, x in obj.items() if (v := _mark_value(x)) is not None)


def _objects(value: Any, path: tuple = ()) -> Any:
    if isinstance(value, dict):
        yield path, value
        for k, x in value.items():
            yield from _objects(x, path + (k,))
    elif isinstance(value, list):
        for i, x in enumerate(value):
            yield from _objects(x, path + (i,))


def _shape(path: tuple) -> tuple:
    return tuple("*" if isinstance(p, int) else p for p in path)


def remap_marks(value: Any, parts: list[tuple[Any, dict]]) -> dict[tuple, dict]:
    """The model's marks ("_block": which block an object was read from, "_from": the printed words of a value
    that is not printed as written) moved from each part's answer onto the final value's objects.

    Paths change when parts are merged and copies dropped, so an object is matched by its values: a part's
    object whose filled values are all in the final object (same place in the schema) gives it its marks; of
    several, the one with the most values in common."""
    cands: dict[tuple, list] = {}
    for v, marks in parts:
        for path, mark in (marks or {}).items():
            obj = _get(v, path) if path else v
            if isinstance(obj, dict):
                cands.setdefault(_shape(path), []).append((_signature(obj), mark))
    out: dict[tuple, dict] = {}
    for path, obj in _objects(value):
        options = cands.get(_shape(path))
        if not options:
            continue
        sig = _signature(obj)
        listed = "*" in _shape(path)
        fits = [(len(c), m) for c, m in options if c <= sig and (c or not listed)]
        if not fits:
            continue
        fits.sort(key=lambda t: -t[0])
        best = fits[0][0]
        top = [m for n, m in fits if n == best]
        block = next((m["block"] for m in top if m.get("block")), None)
        frm: dict = {}
        for m in top:
            for k, w in (m.get("from") or {}).items():
                frm.setdefault(k, w)
        out[path] = {"block": block, "from": frm}
    return out


def code_citations(value: Any, ex: _Extractor, cit_cfg: dict, hints: dict) -> dict[tuple, list[dict]]:
    """Citations found by code: every value is looked up in the parsed pages (its exact text, then the other ways
    a number or date is printed). A value printed once is cited there. A value printed in several places is
    cited where the other values of its object (its table row, its address) were found, else in the block the
    model named for that object (``hints``: the "_block" marks), else, for a printed value, at its first place
    (low confidence). A value the model worked out or wrote in another form is cited on the printed words it
    named for it (the "_from" marks: a column heading for a period, "in millions" for a scale). A category the
    model chose from the schema's list (an enum) is cited only where its object's values are, never on the same
    word elsewhere in the document. A value that is not in the document gets no citation, or the named block with
    low confidence."""
    leaves = [(p, v, sch) for p, v, sch in S.iter_leaves(value, ex.strict) if p and v is not None and not isinstance(v, bool)]
    hits = {p: ex.found_anywhere(v) for p, v, _ in leaves}
    label_field = {p: _is_label(sch) for p, _, sch in leaves}
    anchors: dict[tuple, dict[str, int]] = {}
    for p, h in hits.items():  # an object's values (a row, an address) are printed together; the root's are not
        if len(p) > 1 and len(h) == 1 and h[0][1].occurrences == 1 and not label_field[p]:
            anchors.setdefault(p[:-1], {}).setdefault(h[0][0].label, h[0][1].start)

    def mark_of(p: tuple) -> tuple[Optional[str], Optional[str]]:
        """(block label of the nearest object with one, printed words the model named for this value)"""
        block = words = None
        for k in range(len(p) - 1, -1, -1):
            m = hints.get(p[:k])
            if not m:
                continue
            if k == len(p) - 1 and isinstance(p[-1], str):
                words = (m.get("from") or {}).get(p[-1])
            if m.get("block"):
                block = m["block"]
                break
        return block, words

    out: dict[tuple, list[dict]] = {}
    for p, v, _ in leaves:
        hint, words = mark_of(p)
        near = anchors.get(p[:-1], {})
        h = hits[p]
        here = set(near) | ({hint} if hint else set())

        def cite(ref: RegionRef, conf: float, v: Any = v, near: dict = near) -> list[dict]:
            return [_citation(ref, locate(v, ref, near.get(ref.label)), conf, cit_cfg)]

        if words:  # the model named the printed words this value comes from
            wh = ex.found_anywhere(words)
            pick = [x for x in wh if x[0].label in here]
            if pick:
                out[p] = cite(pick[0][0], 0.9, v=words)
                continue
            if len(wh) == 1 and wh[0][1].occurrences == 1:
                out[p] = cite(wh[0][0], 0.85, v=words)
                continue
        if len(h) == 1 and h[0][1].occurrences == 1 and not (label_field[p] and here and h[0][0].label not in here):
            out[p] = cite(h[0][0], 0.95 if h[0][1].exact else 0.85)
            continue
        pick = ([x for x in h if x[0].label in near] or [x for x in h if hint and x[0].label == hint])
        if pick:
            out[p] = cite(pick[0][0], 0.9 if pick[0][1].exact else 0.8)
        elif h and not label_field[p] and sum(ch.isalnum() for ch in str(v)) >= 4:
            out[p] = cite(h[0][0], 0.6)  # a distinctive value printed in several places: its first place, low
        elif h and not label_field[p]:
            out[p] = []  # a short value ("1", "7") printed in several places with nothing to choose by: no guess
        elif label_field[p]:
            out[p] = []  # a chosen category with no printed source near its object: no box beats a wrong one
        else:
            out[p] = build_citations(v, [hint], ex, cit_cfg) if hint else []
    return out


def row_anchors(value: Any, sources: dict, ex: _Extractor) -> dict[tuple, dict[str, int]]:
    """Per parent object: {region label: char offset of a field that occurs exactly once in that region}."""
    anchors: dict[tuple, dict[str, int]] = {}
    for path, v, _ in S.iter_leaves(value, ex.strict):
        if not path or v is None:
            continue
        for l in sources.get(path, []):
            ref = ex.view.lookup(l)
            if ref is None:
                continue
            found, exact = spans(v, ref)
            if len(found) == 1 and exact:
                anchors.setdefault(path[:-1], {}).setdefault(ref.label, found[0][0])
    return anchors


# ---------------------------------------------------------------- entry point

def _schemaless(view: DocView, pages: list[PageRef], system_prompt: str, llm: LLM, opts: dict) -> dict:
    system = (system_prompt.strip() + "\n\n" if system_prompt.strip() else "") + (
        "You extract structured data from a parsed document. No schema was given: choose clear snake_case "
        "field names for what the instructions ask for. Use only what the document says. Reply with one "
        "JSON object only.")
    try:
        data, results = ask_json(llm, system, "Document:\n" + render_pages(pages, labels=False)
                                 + "\n\nReturn the JSON object now.", max_tokens=opts["max_tokens"])
    except LLMError as e:
        raise TaskError(502, f"model call failed: {e}") from e
    view.docs[0].usage.append({"task": "extract", **sum_usage(results)})
    value = data if isinstance(data, dict) else {}

    def count(v: Any) -> int:
        if isinstance(v, dict):
            return sum(count(x) for x in v.values())
        if isinstance(v, list):
            return max((count(x) for x in v), default=0)
        return 1

    return {"response_type": "v3_extract", "result": [value],
            "usage": {"num_pages": len(pages), "num_fields": count(value), "credits": None},
            "studio_link": None, "confidence": None, "confidence_reason": None}


def _count_values(v: Any) -> int:
    if isinstance(v, dict):
        return sum(_count_values(x) for x in v.values())
    if isinstance(v, list):
        return sum(_count_values(x) for x in v)
    return 1


def run(document: Any, config: dict, *, llm: LLM, options: Optional[dict] = None) -> dict:
    opts = {**DEFAULTS, **(options or {})}
    config = config or {}
    instructions = config.get("instructions") or {}
    user_schema = instructions.get("schema")
    if isinstance(user_schema, str) and user_schema.strip():
        try:
            user_schema = json.loads(user_schema)
        except json.JSONDecodeError as e:
            raise TaskError(400, f"Invalid JSON schema: not valid JSON ({e})") from e
    system_prompt = instructions.get("system_prompt")
    system_prompt = "Be precise and thorough." if system_prompt is None else str(system_prompt)
    settings = config.get("settings") or {}
    cit_cfg = settings.get("citations") or {}
    citations_on = bool(cit_cfg.get("enabled"))
    deep = bool(settings.get("deep_extract"))

    view = DocView(document)
    pages = select_pages(view, settings.get("page_range")) or list(view.pages)
    if not user_schema:
        return _schemaless(view, pages, system_prompt, llm, opts)
    S.check_schema(user_schema)

    # ---- optional features (extract_extras.py); every one is off unless the request asks for it
    from . import extract_extras as X
    extras: dict[str, Any] = {}
    warnings: list[str] = []
    all_pages = pages
    pre_results: list[LLMResult] = []
    use_loop = bool(settings.get("request_pages"))
    if settings.get("relevance_filter") or use_loop:
        chosen, info = X.select_pages(pages, S.resolve_refs(user_schema), system_prompt, llm, pre_results, opts)
        extras["relevance"] = info
        if not use_loop:
            pages = chosen
    # checked against the schema as the user wrote it: an example may leave optional fields out
    examples, ex_warnings = X.check_examples(instructions.get("examples"), S.resolve_refs(user_schema))
    warnings += ex_warnings

    if settings.get("part_tokens"):  # largest page part in tokens, from the request's settings
        try:
            opts["part_tokens"] = int(settings["part_tokens"])
        except (TypeError, ValueError) as e:
            raise TaskError(400, f"settings.part_tokens must be a whole number of tokens, not {settings['part_tokens']!r}") from e
        if opts["part_tokens"] < 1000:
            raise TaskError(400, "settings.part_tokens must be at least 1000")
    forced = deep or bool(settings.get("array_extract"))
    budget, budget_source, limits = part_budget_for(llm, opts)
    if forced:  # array_extract / deep_extract ask for smaller parts on purpose (long lists, per-part checks)
        segments = _segments(pages, opts, force=True)
    else:
        segments = _token_parts(pages, budget, int(opts.get("segment_overlap", 1)),
                                PDF_PAGE_TOKENS if (opts.get("direct") or {}) else 0)
    review_on = bool(settings.get("review"))  # code-only checks on the answer, returned under "review"
    # printed values the output left out (code only): listed in "warnings" unless turned off
    left_out_on = settings.get("left_out_warnings", True) is not False
    alt_cfg = settings.get("alternatives")  # other values the document gives for a field (true or a list of paths)
    derived_ok = settings.get("derived_values", True) is not False
    fill = RS.fill_policy(settings.get("fill_required"))  # which required fields must be answered (reply_schema)
    # The model writes only the user's fields. Per-value source labels are asked for only by deep_extract,
    # whose checks need them; citations are found by code (code_citations).
    track = deep
    ex = _Extractor(view, pages, user_schema, system_prompt, llm, opts, track, bool(settings.get("include_images")))
    ex.fill = fill
    ex.direct = dict(opts.get("direct") or {})
    ex.pdf_path = "direct" if (ex.direct.get("pdf") or ex.direct.get("images")) else None
    from . import field_report as FR
    ex.struck = FR.struck_spans(all_pages)  # crossed-out words: shown to the model as ~~text~~ (code, no call)
    ex.results.extend(pre_results)
    ex.extra_text = X.examples_prompt(examples)
    is_obj = S.is_object(ex.strict)
    use_reply = is_obj and not deep and not use_loop
    if use_reply:  # structured outputs for any schema size (reply_schema): the model answers in this shape
        reply = RS.to_reply(ex.prompt_schema, policy=fill)
        if citations_on:
            reply = RS.add_blocks(reply, blocks=not ex.pdf_path)  # the PDF has no block labels to name
            ex.labels = not ex.pdf_path
        if alt_cfg:
            reply = RS.add_alternatives(reply, [str(a) for a in alt_cfg] if isinstance(alt_cfg, list) else None)
        ex.reply = reply
        note = [RS.NOTE]
        if citations_on:
            note.append(("" if ex.pdf_path else
                         'Every block of the document starts with a label such as [r12|Table]; a "_block" field '
                         "takes the label of the block its object's values were read from (for a table row, the "
                         "table's label). ") + "A \"_from\" field names, for each value of its object that is not "
                        "printed word for word (a period read from a column heading, a scale from \"in millions\", "
                        "a code from a name, a date worked out from a term), the printed words it comes from, copied "
                        "exactly, as \"field name: words\" lines (the field's name, not its code); \"\" when every value is printed as written.")
        if any(ex.struck.values()):
            note.append("Text between ~~ and ~~ is crossed out on the page.")
        if not derived_ok:
            note.append('Give only values the document states. Do not work out or assume a value that is not printed '
                        '(an end date from a term, a total by adding, a country from an address): write "" instead. '
                        "Writing a printed value in another format is fine.")
        ex.reply_note = "\n".join(note)
    hints: dict = {}
    seg_marks: dict = {}  # id(part answer) -> (answer, marks)
    alts: list = []
    run_config = {
        "extract_model": getattr(llm, "name", type(llm).__name__), "effort": getattr(llm, "effort", None),
        "structured_outputs": bool(getattr(llm, "structured_outputs", False)),
        "prompt_cache": bool(getattr(llm, "prompt_cache", False)),
        "model_limits": {k: limits.get(k) for k in ("max_input", "max_output", "price_jump") if k in limits},
        "part_tokens": budget, "part_tokens_source": budget_source,
        "parts": len(segments), "pages_per_part": [len(sg) for sg in segments], "forced_parts": forced,
        "fields_per_group": int(opts.get("coded_group_leaves", 40)), "max_output_tokens": ex.opts.get("max_tokens"),
        "citations": citations_on, "deep_extract": deep, "array_extract": bool(settings.get("array_extract")),
        "review": review_on, "left_out_warnings": left_out_on, "alternatives": bool(alt_cfg), "derived_values": derived_ok,
        "fill_required": sorted(fill) if isinstance(fill, set) else fill,
        "include_images": bool(settings.get("include_images")), "page_range": settings.get("page_range"),
        "relevance_filter": bool(settings.get("relevance_filter")), "pages": len(pages),
        "one_stage": bool(ex.pdf_path),  # the model read the PDF itself; no parsing stage
    }
    trace.event("run_config", **run_config)
    import logging
    logging.getLogger("readanydoc").info("extract settings: %s", json.dumps(run_config, default=str))

    def run_segment(i: int, seg: list[PageRef], extra: str = "") -> tuple[Any, dict]:
        if not use_reply:
            return ex.extract(seg, part=(i + 1, len(segments)), extra=extra)
        v, h, a = ex.ask(seg, part=(i + 1, len(segments)), extra=extra)
        with ex._lock:
            # object paths shift when parts are merged and copies dropped: the marks are matched back to the final
            # objects by their values (remap_marks), for every part
            seg_marks[id(v)] = (v, h)
            alts.extend(a)
        return v, {}

    workers = min(int(opts.get("segment_workers", 4)), len(segments))
    if use_loop:  # the model starts from the relevant pages and may ask for more before it answers
        start = [p for p in all_pages if p.number in set(extras["relevance"]["pages_used"])] \
            if extras["relevance"].get("filtered") else all_pages[:max(int(opts.get("loop_start_pages", 3)), 1)]
        lv, ls, linfo = X.loop_extract(ex, all_pages, start, opts)
        extras["page_requests"] = linfo
        segments = [all_pages]
        parts = [(lv, ls)]
    elif workers > 1 and getattr(llm, "parallel_safe", False):  # independent calls: run the segments side by side
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(workers) as pool:
            parts = list(pool.map(lambda a: run_segment(a[0], a[1]), enumerate(segments)))
    else:
        parts = [run_segment(i, seg) for i, seg in enumerate(segments)]
    for i in range(1, len(parts)):
        # a later segment whose first rows miss a value the previous segment's last row has (a heading printed
        # on an earlier page): extract that segment again, told where the previous one ended
        if needs_carry(parts[i - 1][0], parts[i][0]):
            redo = run_segment(i, segments[i], carry_hint(parts[i - 1][0]))
            if not needs_carry(parts[i - 1][0], redo[0]):
                parts[i] = redo
    if (settings.get("array_extract") or len(segments) > 1) and not use_loop and is_obj:
        if use_reply:
            # structured path: no re-ask (it would go without enforcement and replace a whole answer); a list that
            # looks short against its tables is reported
            ap = _main_array(parts)
            rows = [table_rows(seg) for seg in segments]
            for i in short_segments(parts, rows, ap, opts) if ap is not None else []:
                got = len(_get(parts[i][0], ap) or [])
                warnings.append(f"{S.path_str(ap)}: pages {segments[i][0].number}-{segments[i][-1].number} gave "
                                f"{got} items but their tables have about {rows[i]} rows; some items may be missing")
            trace.event("list_check", reported=len(warnings))
        else:
            parts, gaps = _complete_rows(parts, segments, ex, opts)  # long lists: rows checked against the tables
            warnings += gaps
    conflicts: list = []
    tree = None
    for i, (v, s) in enumerate(parts):
        t = _annotate(v, s, ex.strict)
        tree = t if tree is None else merge_trees(tree, t, conflicts)
    if (ex.notes or ex.failed) and _is_empty(tree):
        # every call failed (cut off or unparseable): an all-null answer would look like "nothing in the document"
        raise TaskError(502, "the model returned no usable answer: " + "; ".join(
            ex.notes[:3] or [f"the reply could not be read for pages {a}-{b}" for a, b, _ in ex.failed[:3]]))
    value, sources = _split_tree(tree)
    value = S.conform(value, ex.strict)
    if use_reply:
        # page parts and groups can each write the same list item, one copy with fewer fields filled (a scale or
        # unit given once for a table): the less complete copy is dropped, the fuller one is the evidence
        value, dropped = drop_partial_copies(value)
        if dropped:
            trace.event("partial_copies_dropped", items=dropped)
    if (extras.get("relevance") or {}).get("filtered") and not use_loop and is_obj:
        # the relevance step left pages out: a field that is still empty gets one look at those pages
        lost = X.missing_fields(value, ex.strict)
        rest = [p for p in all_pages if p.number not in {q.number for q in pages}]
        if lost and rest:
            if use_reply:
                nv, _h, _a = ex.ask(rest, fields=lost)
                ns = {}
            else:
                nv, ns = ex.extract(rest, _sub_schema(ex.prompt_schema, lost), _sub_schema(ex.strict, lost))
            found = [f for f in lost if isinstance(nv, dict) and not X._empty(nv.get(f))]
            if found:
                value, sources = _replace_fields(value, sources, nv, ns, found)
                value = S.conform(value, ex.strict)
            extras["relevance"]["second_look"] = {"fields": lost, "found": found, "pages": [p.number for p in rest]}
    if examples:
        value, copied = X.drop_copied_examples(value, ex.strict, examples,
                                               lambda v: any(locate(v, ref) is not None
                                                             for p in all_pages for ref in p.regions))
        warnings += copied

    confidence = reason = None
    if deep:
        rules = parse_sum_rules(system_prompt, ex.strict)
        remaining: list[dict] = []
        if is_obj:
            for f in _overlap_failures(parts, segments, ex):
                fixed = _fix_overlap(value, sources, f, ex)
                if fixed is None:
                    remaining.append(f)
                else:
                    value, sources = fixed
        failures = _checks(value, sources, ex, rules)
        for _ in range(opts["max_deep_iterations"]):
            if not failures or not is_obj:
                break
            fields = sorted({f for fl in failures for f in fl["fields"]})
            if not fields:
                break
            cited_pages = {ex.label_page.get(l) for p, labs in sources.items() if p and p[0] in fields for l in labs}
            cited_pages.discard(None)
            total_chars = sum(len(ref.region.content) for p in pages for ref in p.regions)
            if len(segments) == 1 or total_chars <= 2 * opts["auto_segment_chars"] or not cited_pages:
                ask_pages = pages
            else:
                ask_pages = [p for p in pages if p.number in cited_pages]
            problems = "\n".join(f"- {fl['message']}" for fl in failures[:40])
            prev = {f: value.get(f) for f in fields}
            note = ("A check of your earlier answer found problems. Re-read the document and extract these "
                    f"fields again.\nEarlier answer:\n{json.dumps(prev, ensure_ascii=False)}\nProblems:\n{problems}\n"
                    "If the earlier value is in fact what the document says, repeat it.")
            nv, ns = ex.extract(ask_pages, _sub_schema(ex.prompt_schema, fields), _sub_schema(ex.strict, fields),
                                extra=note)
            cand_v, cand_s = _replace_fields(value, sources, nv if isinstance(nv, dict) else {}, ns, fields)
            cand_v = S.conform(cand_v, ex.strict)
            cand_f = _checks(cand_v, cand_s, ex, rules)
            if len(cand_f) < len(failures):  # never make it worse: keep the old answer otherwise
                value, sources, failures = cand_v, cand_s, cand_f
            else:
                break
        remaining += failures
        remaining += [{"kind": "conflict", "message": f"{S.path_str(p)}: segments disagree ({a!r} vs {b!r})"}
                      for p, a, b in conflicts]
        if ex.notes:
            remaining += [{"kind": "model", "message": n} for n in ex.notes]
        confidence = "low" if remaining else "high"
        reason = ("; ".join(f["message"] for f in remaining[:8]) if remaining
                  else "schema valid; cited values found in their source blocks; cross-segment and arithmetic checks passed")

    shadow = _annotate(value, sources, ex.strict)  # mirrors every removal so source paths stay aligned
    value, _unresolved = S.null_out(value, ex.strict, shadow=shadow)
    # a part whose reply could not be read is reported only when its fields are still empty (a later call, e.g.
    # for the other half of the fields or pages, may have filled them)
    still = sorted(f for f in ex.unreadable if not is_obj or X._empty((value or {}).get(f)))
    if still:
        where = sorted({(a, b) for a, b, fs in ex.failed if set(fs) & set(still)})
        warnings.append("the model's reply could not be read for pages "
                        + ", ".join(f"{a}-{b}" for a, b in where) + f" (fields: {', '.join(still)}); those values are "
                        "empty, but they may be in the document")
    warnings = X.required_warnings(value, S.resolve_refs(user_schema), skip=set(still)) + warnings + list(ex.notes)
    sources = _split_tree(shadow)[1] if isinstance(shadow, (dict, list)) and value is not None else {}
    cites: dict = {}
    if citations_on or review_on:
        if track:  # deep_extract: the model named a block for every value
            anchors = row_anchors(value, sources, ex)
            cites = {p: build_citations(v, sources.get(p, []), ex, cit_cfg, anchors.get(p[:-1]))
                     for p, v, _ in S.iter_leaves(value, ex.strict) if p}
        else:
            hints = remap_marks(value, [seg_marks[id(v)] for v, _ in parts if id(v) in seg_marks])
            trace.event("citation_marks", objects=len(hints), with_block=sum(1 for m in hints.values() if m.get("block")),
                        with_from=sum(1 for m in hints.values() if m.get("from")))
            cites = code_citations(value, ex, cit_cfg, hints)
        from . import confidence as CF
        cites = CF.calibrate(value, cites)
    result: Any = (S.map_leaves(value, ex.strict, lambda path, v, s: {"value": v, "citations": cites.get(path, [])})
                   if citations_on else [value])
    extra_out: dict = {}
    if alt_cfg and use_reply:
        found: dict[str, list] = {}
        for a in alts:
            key = str(a.get("field", "")).strip()
            text = str(a.get("value", "")).strip()
            printed = str(a.get("printed", "")).strip() or text
            leaf = _schema_at(ex.strict, key)
            item = {"value": _as_field(text, leaf), "printed": printed,
                    "note": str(a.get("note", "")).strip() or None}
            if citations_on:
                hit = ex.found_anywhere(printed) or ex.found_anywhere(text)
                item["citations"] = [_citation(hit[0][0], hit[0][1], 0.8, cit_cfg)] if hit else []
            if key and item not in found.setdefault(key, []):
                found[key].append(item)
        extra_out["alternatives"] = found
    if left_out_on and value is not None:
        found = FR.left_out(value, ex.strict, ex)
        trace.event("left_out_check", flags=len(found))
        warnings += FR.left_out_warnings(found)
    if review_on:
        extra_out["review"] = FR.review(value, ex.strict, cites, ex, conflicts=conflicts, unreadable=set(still),
                                        filled=RS.forced_paths(ex.prompt_schema, fill) if use_reply else set(),
                                        pages=all_pages)

    view.docs[0].usage.append({"task": "extract", **sum_usage(ex.results)})
    # num_fields as Reducto reports it: the number of leaf values returned (array items counted), not schema fields
    usage = {"num_pages": len(all_pages), "num_fields": _count_values(value), "credits": None,
             "extract_mode": "super_agent" if deep else "extract"}
    if extras:  # which pages were read in full (relevance step / page requests)
        usage["extract_pages"] = extras
    return {"response_type": "v3_extract", "result": result, "usage": usage,
            "studio_link": None, "confidence": confidence, "confidence_reason": reason,
            "warnings": warnings, **extra_out}


def _schema_at(schema: Any, key: str) -> Any:
    """The user's schema for a field path as the model writes it ("contract.items[0].amount", or with a name in the
    brackets), or None when the path does not lead to a field."""
    node = schema
    for name, bracket in re.findall(r"([^.\[\]]+)|\[([^\]]*)\]", key or ""):
        live = S.branch_for(node, [] if bracket or not name else {}) if isinstance(node, dict) else None
        if not isinstance(live, dict):
            return None
        if name:
            if S.is_array(live) and not S.is_object(live):  # "items.amount" without an index
                live = S.branch_for(live.get("items") or {}, {})
            node = (live.get("properties") or {}).get(name) if S.is_object(live) else None
        else:
            node = live.get("items") if S.is_array(live) else None
        if node is None:
            return None
    return node


def _as_field(text: str, leaf: Any) -> Any:
    """An alternative value in the field's own type ("4500" -> 4500 for a number), like the main value."""
    if not isinstance(leaf, dict) or S.is_object(leaf) or S.is_array(leaf):
        return text
    v = S.conform(RS.from_reply(text, leaf), leaf)
    return text if v is None else v
