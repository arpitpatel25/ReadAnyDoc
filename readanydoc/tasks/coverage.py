"""Coverage: printed values the output did not take, found by code (no model call).

Two rules, built only from what the parser gives every document (form fields, table rows) and the output itself:

- ``printed_but_empty``: a field came back empty (null or []) while the document prints a label and value that no
  output value used, and the label shares a word with that field's name, title or description;
- ``row_not_extracted``: a list took some rows of a table, but another item row of the same table was used by no
  output value.

"Used" is decided per value, not per block: a printed value counts as used only if an output value matches it (as
text, number or date). Two fields printed in one box, or one value printed twice, are therefore told apart.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Optional

from ..textnorm import tokenize
from . import schema as S

_TAG = re.compile(r"<[^>]+>")
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|#3[49]|#160);", re.I)
_SKIP_TYPES = {"Header", "Footer", "Page Number", "Figure", "Signature", "Comment"}
_BASIC = {"the", "a", "an", "of", "to", "and", "or", "for", "in", "on", "at", "by", "is", "it", "its", "as",
          "be", "this", "that", "with", "from", "e", "g", "eg", "if", "when", "any", "each", "all", "per", "not"}
_STOP = {"the", "a", "an", "of", "to", "and", "or", "for", "in", "on", "at", "by", "is", "it", "its", "as", "be",
         "this", "that", "with", "from", "no", "number", "date", "value", "type", "name", "id", "code", "info",
         "details", "detail", "total", "amount", "item", "items", "list", "field", "e", "g", "eg", "if", "when",
         "any", "each", "all", "per", "not", "one", "who", "which", "what", "their", "they", "them", "has", "have"}
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                           "nov", "dec"])}
_SUMMARY = re.compile(r"\b(sub-?\s?total|total|grand total|tax|vat|gst|balance|amount due|sum|carried forward|"
                      r"brought forward)\b", re.I)


def _plain(s: str) -> str:
    return " ".join(_ENTITY.sub(" ", _TAG.sub(" ", s or "")).replace("**", " ").split())


def _stem(w: str) -> str:
    """Singular of a plural word: parties -> party, taxes -> tax, lines -> line, contacts -> contact."""
    w = w.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and re.search(r"(s|x|z|ch|sh)es$", w):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _words(s: str, stop: set = _STOP) -> set[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s or "").replace("_", " ")
    return {_stem(t.norm) for t in tokenize(s) if len(t.norm) > 1 and not t.norm.isdigit()} - stop


# ------------------------------------------------------------------------------------------- printed pieces

class Piece:
    """A printed label and value (a form field, a pair in a two-column table) or a table item row."""

    def __init__(self, label: str, value: str, page: int, block: str, kind: str, table: Optional[int] = None):
        self.label, self.value, self.page, self.block, self.kind, self.table = label, value, page, block, kind, table

    def __repr__(self) -> str:
        return f"Piece({self.kind}, {self.label!r}: {self.value[:50]!r}, p{self.page})"


def _rows(html: str) -> list[list[tuple[str, bool]]]:
    out = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, re.I | re.S):
        out.append([(_plain(c), tag.lower() == "th")
                    for tag, c in re.findall(r"<(t[dh])\b[^>]*>(.*?)</t[dh]>", row, re.I | re.S)])
    return out


def pieces(pages: Iterable[dict]) -> list[Piece]:
    """Every printed label/value and table item row of the parsed pages (headers, footers, figures skipped)."""
    out: list[Piece] = []
    tid = 0
    for p in pages:
        for r in p["regions"]:
            t, c = r["type"], r["content"] or ""
            if t in _SKIP_TYPES:
                continue
            if t == "Table" and "<tr" in c.lower():
                tid += 1
                rows = [x for x in _rows(c) if any(v for v, _ in x)]
                if not rows:
                    continue
                width = max(len(x) for x in rows)
                head = [v for v, th in rows[0]] if all(th for _, th in rows[0]) or _header_like(rows) else None
                body = rows[1:] if head else rows
                if _pair_table(rows, width):  # label | value (| label | value): a form drawn as a table
                    for x in rows:
                        for i in range(0, len(x) - 1, 2):
                            lab, val = x[i][0].rstrip(":").strip(), x[i + 1][0]
                            if lab and val and not x[i + 1][1] and not _HEADER_WORDS.issuperset(_words(lab, _BASIC)):
                                out.append(Piece(lab, val, p["number"], r["label"], "pair"))
                    continue
                for x in body:
                    cells = [v for v, _ in x]
                    if len(cells) == 2 and cells[0] and cells[1] and not re.search(r"\d", cells[0]) \
                            and len(cells[0].split()) <= 4:
                        out.append(Piece(cells[0].rstrip(":"), cells[1], p["number"], r["label"], "pair"))
                        # "TOTAL | 46,000": a label and its value; it may also be an item ("Y.B.BAT | 46,000")
                    if len(x) < width or not cells[0] or any(_SUMMARY.search(v) for v in cells if v):
                        continue  # a total under the items, a spanned label
                    if any(v.endswith(":") for v in cells) or not any(re.search(r"\d", v) for v in cells):
                        continue  # labels with their values ("Terms: | 60 days"), or a header row: not an item
                    label = " ".join(head) if head else ""
                    out.append(Piece(label, " | ".join(cells), p["number"], r["label"], "row", tid))
                continue
            if t == "Key Value" or t == "Text":
                for line in re.split(r"\n| / |<br\s*/?>", c):
                    for seg in re.split(r"\s{3,}|\t", _TAG.sub(" ", line)):  # side-by-side columns of a form
                        seg = _plain(seg)
                        m = re.match(r"^([A-Za-z][^:]{0,40}):\s+(.+)$", seg)
                        if m and (t == "Key Value" or len(m.group(1).split()) <= 4):
                            out.append(Piece(m.group(1).strip(), m.group(2).strip(), p["number"], r["label"], "pair"))
    return out


_HEADER_WORDS = {"label", "value", "field", "key", "item", "description", "detail", "name"}


def _pair_table(rows: list, width: int) -> bool:
    """A table of labels and their values: 2 or 4 columns, the label cells short text (or ending in ":")."""
    if width not in (2, 4):
        return False
    body = [x for x in rows if not all(th for _, th in x) and len(x) == width]  # a narrower row is a heading
    if len(body) < 2:
        return False
    labels = [x[i][0] for x in body for i in range(0, len(x) - 1, 2) if x[i][0] or x[i + 1][0]]
    if not labels:
        return False
    short = [v for v in labels if v and len(v.split()) <= 5 and not re.search(r"\d", v)]
    colon = [v for v in labels if v.endswith(":")]
    return len(short) >= 0.8 * len(labels) and (width == 2 or len(colon) >= 0.5 * len(labels) or
                                                 len(short) == len(labels))


def _header_like(rows: list) -> bool:
    """A first row of labels over rows of values (no amount or digit in it, more digits below)."""
    if len(rows) < 2:
        return False
    first = [v for v, _ in rows[0]]
    return not any(re.search(r"\d", v) for v in first) and any(re.search(r"\d", v) for x in rows[1:] for v, _ in x)


# ------------------------------------------------------------------------------------------------ matching

_NUM = re.compile(r"\d{1,3}(?:[ ,.'\u00a0\u202f]\d{3})+(?:[.,]\d{1,2})?(?!\d)|\d+(?:[.,]\d+)?")


def _numbers(s: str) -> set[float]:
    """Numbers as printed in any common form: 1,370.53, 1 370.53, 1.370,53, 1'370.53, 370.53."""
    out = set()
    for m in _NUM.finditer(s or ""):
        t = m.group(0)
        seps = re.findall(r"[ ,.'\u00a0\u202f]", t)
        dec = ""
        if seps and re.search(r"[.,]\d{1,2}$", t) and (len(seps) > 1 or len(t.split(seps[-1])[-1]) != 3):
            dec = t[-3:] if t[-3] in ".," else t[-2:]
            t = t[: -len(dec)]
            dec = "." + dec[1:]
        t = re.sub(r"[ ,.'\u00a0\u202f]", "", t) if len(seps) > (1 if dec else 0) or (seps and not dec and
                                                                               len(t.split(seps[0])[-1]) == 3) else t.replace(",", ".")
        try:
            out.add(round(float(t + dec), 4))
        except ValueError:
            pass
    return out


def _dates(s: str) -> set[date]:
    out = set()
    for y, mo, d in re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s):
        try:
            out.add(date(int(y), int(mo), int(d)))
        except ValueError:
            pass
    for a, b, c in re.findall(r"\b(\d{1,4})[/.](\d{1,2})[/.](\d{2,4})\b", s):
        a, b, c = int(a), int(b), int(c)
        for y, mo, d in ((a, b, c), (c, b, a), (c, a, b)) if a > 31 else ((c, b, a), (c, a, b)):
            y = y + 2000 if y < 100 else y
            try:
                out.add(date(y, mo, d))
            except ValueError:
                pass
    for mname, d, y in re.findall(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b", s):
        mo = _MONTHS.get(mname[:3].lower())
        if mo:
            try:
                out.add(date(int(y), mo, int(d)))
            except ValueError:
                pass
    for d, mname, y in re.findall(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", s):
        mo = _MONTHS.get(mname[:3].lower())
        if mo:
            try:
                out.add(date(int(y), mo, int(d)))
            except ValueError:
                pass
    return out


class _Used:
    """The output's values, ready to test whether a printed value was taken."""

    def __init__(self, value: Any, schema: Any):
        self.tokens: list[list[str]] = []
        self.nums: set[float] = set()
        self.dates: set[date] = set()
        for _, v, _s in S.iter_leaves(value, schema):
            if v is None or isinstance(v, bool) or v == "":
                continue
            if isinstance(v, (int, float)):
                self.nums.add(round(float(v), 4))
                continue
            s = str(v)
            self.dates |= _dates(s)
            if re.fullmatch(r"[^\w]*[A-Za-z.]{0,4}\s?[-(]?[\d ,.'\u00a0\u202f]+\)?[^\w]*", s.strip()):
                self.nums |= {n for n in _numbers(s)}  # a number written as text: "10,091", "Rp 46.000"
            toks = [t.norm for t in tokenize(s)]
            if toks:
                self.tokens.append(toks)

    def took(self, printed: str) -> bool:
        """An output value took this printed text: the same date, a distinctive number (10 or more, or with
        decimals), or output words covering at least 40% of its words. A short common value ("USD", "1") alone
        does not take a longer text."""
        toks = [t.norm for t in tokenize(printed)]
        if not toks:
            return True  # nothing to take
        if _dates(printed) & self.dates:
            return True
        nums = {n for n in _numbers(printed) if abs(n) >= 10 or n != int(n)}
        if nums & self.nums:
            return True
        covered = [False] * len(toks)
        for t in self.tokens:
            if len(t) == 1 and (t[0].isdigit() or len(t[0]) < 3):
                continue
            n = len(t)
            for i in range(len(toks) - n + 1):
                if toks[i:i + n] == t:
                    for j in range(i, i + n):
                        covered[j] = True
        words = [i for i, w in enumerate(toks) if not w.isdigit()] or list(range(len(toks)))
        return sum(covered[i] for i in words) >= max(1.0, 0.4 * len(words))

    def took_row(self, cells: list[str]) -> bool:
        """A table row is taken when what identifies it was: its first cell, a text cell of two words or more,
        a distinctive number (10 or more, or with decimals), or any two cells."""
        cells = [c for c in cells if c.strip()]
        if not cells:
            return True
        hits = 0
        for i, c in enumerate(cells):
            if not self.took(c):
                continue
            hits += 1
            words = [w for w in tokenize(c) if not w.norm.isdigit()]
            if i == 0 or len(words) >= 2 or {n for n in _numbers(c) if abs(n) >= 10 or n != int(n)}:
                return True
        return hits >= 2


# ---------------------------------------------------------------------------------------------- the checks

def _field_words(schema: Any) -> dict[str, set[str]]:
    """Normalised field path ("customer.contacts") -> words of its name, title and description, for every field
    and list in the schema."""
    out: dict[str, set[str]] = {}

    def go(node: Any, path: tuple) -> None:
        if not isinstance(node, dict):
            return
        n = S.branch_for(node, {}) if (node.get("anyOf") or node.get("oneOf")) else node
        if not isinstance(n, dict):
            return
        if path:
            words = _words(path[-1], _BASIC) | _words(str(n.get("title") or node.get("title") or ""), _BASIC)
            words |= _words(str(n.get("description") or node.get("description") or ""))
            out[".".join(path)] = out.get(".".join(path), set()) | words
        if S.is_object(n):
            for k, v in (n.get("properties") or {}).items():
                go(v, path + (k,))
        elif S.is_array(n):
            items = S.branch_for(n.get("items") or {}, {})
            if path and isinstance(items, dict) and S.is_object(items):  # a list is also named by its items' fields
                for k, v in (items.get("properties") or {}).items():
                    out[".".join(path)] |= _words(k, _BASIC) | _words(str((v or {}).get("title") or ""), _BASIC)
            go(n.get("items") or {}, path)

    go(schema, ())
    return out


def _list_paths(schema: Any) -> set[str]:
    out: set = set()

    def go(node: Any, path: tuple) -> None:
        n = S.branch_for(node, {}) if isinstance(node, dict) and (node.get("anyOf") or node.get("oneOf")) else node
        if not isinstance(n, dict):
            return
        if S.is_object(n):
            for k, v in (n.get("properties") or {}).items():
                go(v, path + (k,))
        elif S.is_array(n):
            out.add(".".join(path))
            go(n.get("items") or {}, path)

    go(schema, ())
    return out


def _empty_fields(value: Any, schema: Any) -> list[str]:
    """Normalised paths of fields that came back null or [] (inside a list item: per item, path without index)."""
    out: list[str] = []

    def go(v: Any, s: Any, path: tuple) -> None:
        s = S.branch_for(s, v) if isinstance(s, dict) else s
        if not isinstance(s, dict):
            return
        if S.is_object(s):
            if not isinstance(v, dict):
                if v is None and path:
                    out.append(".".join(path))
                return
            for k, sub in (s.get("properties") or {}).items():
                go(v.get(k), sub, path + (k,))
        elif S.is_array(s):
            if not v:
                out.append(".".join(path))
                return
            for item in v:
                go(item, s.get("items") or {}, path)
        elif v is None or v == "":
            out.append(".".join(path))

    go(value, schema, ())
    return list(dict.fromkeys(out))


def _shape(v: str) -> Optional[str]:
    """The kind of value a printed text is, when its form says so: "email", "phone", "url"."""
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", v):
        return "email"
    if re.search(r"https?://|www\.", v):
        return "url"
    if _dates(v):
        return "date"
    if re.fullmatch(r"[+()\d\s.-]{7,}", v.strip()) and len(re.sub(r"\D", "", v)) >= 7:
        return "phone"
    return None


def check(value: Any, schema: Any, pages: list[dict]) -> list[dict]:
    """[{"field", "flag", "detail"}]: printed values the output did not take (see the module docstring)."""
    found = pieces(pages)
    used = _Used(value, schema)
    taken = {id(p): (used.took_row(p.value.split(" | ")) if p.kind == "row" else used.took(p.value)) for p in found}
    free = [p for p in found if not taken[id(p)]]
    words = _field_words(schema)
    out: list[dict] = []

    # A. printed_but_empty: the most specific empty field whose words the free label shares
    empty = _empty_fields(value, schema)
    lists_ = _list_paths(schema)
    flagged_tables: set = set()
    for p in free:
        if p.kind == "row" and (p.table in flagged_tables or not p.label):
            continue
        lw = _words(p.label, _BASIC if p.kind == "row" else _STOP)
        shape = _shape(p.value)
        best, score = None, 0
        for f in empty:
            if p.kind == "row" and f not in lists_:
                continue  # a table's rows are the items of a list, never one single value
            fw = words.get(f, set())
            own = _words(f.rsplit(".", 1)[-1], _BASIC)
            hit = len(lw & fw) + len(_words(p.label, _BASIC) & own)  # the field's own name counts twice, and
            # there a common word ("Total", "Date") counts: the field is empty and is named exactly that
            if lw and not lw & (fw | own) and not (shape in ("email", "url", "phone") and shape in own):
                continue  # "Credit Date" is not the due date: the label's own words name something else
            if p.kind == "pair" and shape and shape in own:
                hit += 2  # an email printed, an email field empty: matched by the value's shape
            if f in lists_ and p.kind == "pair" and hit < 2:
                continue  # a list is named by all its items' fields: one shared word is not enough
            if hit > score:
                best, score = f, hit
        if best and p.kind == "row":
            need = 2  # a table header shares at least two words with the list, or it is not that list's table
            if score >= need:
                flagged_tables.add(p.table)
                out.append({"field": best, "flag": "printed_but_empty",
                            "detail": f'a table with "{p.label[:60]}" (page {p.page}) has rows such as '
                                      f'"{p.value[:60]}", and {best} is empty'})
        elif best:
            out.append({"field": best, "flag": "printed_but_empty",
                        "detail": f'"{p.label}: {p.value[:80]}" is printed on page {p.page}, and {best} is empty'})

    # B. row_not_extracted: a table some of whose item rows were taken, and another row not
    tables: dict[int, list[Piece]] = {}
    for p in found:
        if p.kind == "row" and p.table is not None:
            tables.setdefault(p.table, []).append(p)
    for rows in tables.values():
        took = [r for r in rows if taken[id(r)]]
        if not took or len(took) == len(rows) or (len(took) < 2 and len(rows) > 3):
            continue  # one row matching by chance in a long table says nothing about that table
        for r in rows:
            if r not in took:
                out.append({"field": None, "flag": "row_not_extracted",
                            "detail": f'table row "{r.value[:80]}" (page {r.page}) is not in the output, though '
                                      f"{len(took)} other rows of its table are"})
    return out
