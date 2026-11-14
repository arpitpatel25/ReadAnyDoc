"""Extract: JSON Schema in, JSON out, working from the Document's regions (see tasks/__init__.py).

Flow: validate the schema -> render the selected pages with region labels -> ask the model (per page
segment when the document is long) -> unwrap source labels -> conform -> validate -> bounded repair
re-asks -> merge segments and de-duplicate arrays -> (deep_extract) deterministic checks and re-asks
for failing fields only -> null out whatever still breaks the schema -> build citations.

Nothing here reads page images unless ``settings.include_images`` is set.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from rapidfuzz import fuzz

from ..model import BBox, RegionType
from ..textnorm import norm_tokens, tokenize
from . import schema as S
from .common import (DocView, PageRef, RegionRef, TaskError, bbox_dict, page_images, render_pages,
                     select_pages, sum_usage)
from .llm import LLM, LLMError, LLMResult, ask_json

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
}

RULES = """You extract structured data from a parsed document.
- Use only what the document text says. Never invent values and never use outside knowledge.
- Return one JSON value that follows the JSON Schema exactly: include every declared property, use null when the document does not contain the value, never add properties that are not declared, and respect enum, pattern, minimum/maximum and array item rules.
- Copy values as written (names, numbers, identifiers, dates) unless the schema or the instructions ask for another format.
- For arrays, include every matching item found in the pages you are given, in document order. Do not stop early and do not summarise.
- Reply with the JSON only: no prose, no code fences."""

TRACK = """Source labels: every block of the document starts with a label such as [r12|Table].
Write every leaf value (string, number, boolean or null, including each item of an array of scalars) as {"v": <value>, "r": ["r12"]}, where "r" lists the labels of the blocks the value was read from (usually exactly one). Use {"v": null, "r": []} when the value is missing. Objects and arrays keep their normal shape; only the leaves are wrapped."""


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


def _find_tokens(tokens: list, want: list[str]) -> list[tuple[int, int]]:
    n = len(want)
    if not n or n > len(tokens):
        return []
    norms = [t.norm for t in tokens]
    first = want[0]
    return [(tokens[i].start, tokens[i + n - 1].end) for i in range(len(norms) - n + 1)
            if norms[i] == first and norms[i:i + n] == want]


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
        if not found and not isinstance(value, (int, float)) and not str(value).strip().isdigit():
            found = _find_tokens(tokenize(content), norm_tokens(str(value)))
        return found, True
    want = norm_tokens(str(value))
    if not want:
        return [], True
    found = _find_tokens(tokenize(content), want)
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
    def __init__(self, view: DocView, pages: list[PageRef], user_schema: dict, system_prompt: str,
                 llm: LLM, opts: dict, track: bool, include_images: bool):
        self.view, self.pages, self.llm, self.opts = view, pages, llm, opts
        self.prompt_schema = S.resolve_refs(user_schema)
        self.strict = S.enforced(self.prompt_schema)
        self.system = (system_prompt.strip() + "\n\n" if system_prompt.strip() else "") + RULES
        self.track = track
        self.include_images = include_images
        self.results: list[LLMResult] = []
        self.notes: list[str] = []
        self.label_page = {ref.label: p.number for p in view.pages for ref in p.regions}
        self.track_text = TRACK
        self.extra_text = ""  # example outputs (instructions.examples), shown with every prompt

    # -- prompts
    def _prompt(self, pages: list[PageRef], schema: dict, part: Optional[tuple[int, int]] = None,
                extra: str = "") -> str:
        out = ["Document:", render_pages(pages, labels=self.track), "",
               "JSON Schema:", json.dumps(schema, ensure_ascii=False), ""]
        if self.track:
            out += [TRACK, ""]
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
        images = page_images(pages) if self.include_images and self.llm.supports_images else None
        try:
            data, results = ask_json(self.llm, self.system, prompt, images=images or None, json_schema=schema,
                                     max_tokens=self.opts["max_tokens"], retries=1)
        except LLMError as e:
            raise TaskError(502, f"model call failed: {e}") from e
        self.results.extend(results)
        return data

    def _digest(self, data: Any, strict: dict) -> tuple[Any, dict, list]:
        if isinstance(data, list) and len(data) == 1 and S.is_object(strict):
            data = data[0]
        value, sources = S.unwrap(data, strict)  # tolerant: also when labels were not asked for
        value = S.conform(value, strict)
        if value is None and S.is_object(strict):
            value = S.empty_instance(strict)
        return value, sources, S.find_errors(value, strict)

    # -- one segment (with schema repair re-asks)
    def extract(self, pages: list[PageRef], prompt_schema: Optional[dict] = None, strict: Optional[dict] = None,
                part: Optional[tuple[int, int]] = None, extra: str = "", depth: int = 0) -> tuple[Any, dict]:
        prompt_schema = prompt_schema or self.prompt_schema
        strict = strict or self.strict
        base = self._prompt(pages, prompt_schema, part, extra)
        data = self._ask(pages, base, prompt_schema)
        if data is None:
            if len(pages) > 1 and depth < 2:  # likely truncated output: halve the segment
                mid = len(pages) // 2
                a = self.extract(pages[:mid], prompt_schema, strict, part, extra, depth + 1)
                hint = carry_hint(a[0])  # the halves do not overlap: tell the second where the first ended
                b = self.extract(pages[mid:], prompt_schema, strict, part, (extra + "\n\n" + hint).strip(), depth + 1)
                tree = merge_trees(_annotate(a[0], a[1], strict), _annotate(b[0], b[1], strict), [])
                return _split_tree(tree)
            self.notes.append(f"no parseable answer for pages {pages[0].number}-{pages[-1].number}")
            return S.empty_instance(strict), {}
        value, sources, errors = self._digest(data, strict)
        for _ in range(self.opts["max_repairs"]):
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
        label = "high"
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

    segments = _segments(pages, opts, force=deep or bool(settings.get("array_extract")))
    track = citations_on or deep or len(segments) > 1
    ex = _Extractor(view, pages, user_schema, system_prompt, llm, opts, track, bool(settings.get("include_images")))
    ex.results.extend(pre_results)
    ex.extra_text = X.examples_prompt(examples)
    is_obj = S.is_object(ex.strict)

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
            parts = list(pool.map(lambda a: ex.extract(a[1], part=(a[0] + 1, len(segments))), enumerate(segments)))
    else:
        parts = [ex.extract(seg, part=(i + 1, len(segments))) for i, seg in enumerate(segments)]
    for i in range(1, len(parts)):
        # a later segment whose first rows miss a value the previous segment's last row has (a heading printed
        # on an earlier page): extract that segment again, told where the previous one ended
        if needs_carry(parts[i - 1][0], parts[i][0]):
            redo = ex.extract(segments[i], part=(i + 1, len(segments)), extra=carry_hint(parts[i - 1][0]))
            if not needs_carry(parts[i - 1][0], redo[0]):
                parts[i] = redo
    if (settings.get("array_extract") or len(segments) > 1) and not use_loop and is_obj:
        parts, gaps = _complete_rows(parts, segments, ex, opts)  # long lists: rows checked against the tables
        warnings += gaps
    conflicts: list = []
    tree = None
    for i, (v, s) in enumerate(parts):
        t = _annotate(v, s, ex.strict)
        tree = t if tree is None else merge_trees(tree, t, conflicts)
    if ex.notes and _is_empty(tree):
        # every call failed (cut off or unparseable): an all-null answer would look like "nothing in the document"
        raise TaskError(502, "the model returned no usable answer: " + "; ".join(ex.notes[:3]))
    value, sources = _split_tree(tree)
    value = S.conform(value, ex.strict)
    if (extras.get("relevance") or {}).get("filtered") and not use_loop and is_obj:
        # the relevance step left pages out: a field that is still empty gets one look at those pages
        lost = X.missing_fields(value, ex.strict)
        rest = [p for p in all_pages if p.number not in {q.number for q in pages}]
        if lost and rest:
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
    warnings = X.required_warnings(value, S.resolve_refs(user_schema)) + warnings + list(ex.notes)
    sources = _split_tree(shadow)[1] if isinstance(shadow, (dict, list)) and value is not None else {}
    if citations_on:
        anchors = row_anchors(value, sources, ex)
        cited = S.map_leaves(value, ex.strict, lambda path, v, s: {
            "value": v, "citations": build_citations(v, sources.get(path, []), ex, cit_cfg, anchors.get(path[:-1]))})
        result: Any = cited
    else:
        result = [value]

    view.docs[0].usage.append({"task": "extract", **sum_usage(ex.results)})
    # num_fields as Reducto reports it: the number of leaf values returned (array items counted), not schema fields
    usage = {"num_pages": len(all_pages), "num_fields": _count_values(value), "credits": None,
             "extract_mode": "super_agent" if deep else "extract"}
    if extras:  # which pages were read in full (relevance step / page requests)
        usage["extract_pages"] = extras
    return {"response_type": "v3_extract", "result": result, "usage": usage,
            "studio_link": None, "confidence": confidence, "confidence_reason": reason,
            "warnings": warnings}
