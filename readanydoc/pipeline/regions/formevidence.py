"""A form region read separately as ADDITIONAL evidence (switch ``formread``, off by default).

A detected form / key-value region is read once from its own crop with the form prompt, which writes every field
as ``label: value``. That read is never put into the output. It is used only to decide structure on the page
read's own blocks inside the region:

* a block whose text is a label followed directly by the value the form read gives that label (or is exactly a
  label of the form read) is typed Key Value;
* a block that ends with a label and the next block that is just that label's value become one Key Value block;
* a block whose every line the form read wrote as plain text (a caption, a note: no label and value) is marked, and
  ``kv=form`` leaves it alone.

Every character of the output is still the page read's. The earlier attempts (``forms``, ``region_reread``)
replaced the blocks by the crop read and broke block boundaries and tables.
"""

from __future__ import annotations

import re

from ...model import Document, Page, Region, RegionType
from ...textnorm import norm_tokens, plain_text
from . import crops
from .match import blocks_in, form_regions

_TABLE = re.compile(r"<table\b.*?</table\s*>", re.I | re.S)
_LINE = re.compile(r"^\s*(?:[-*]\s+)?(?P<label>[^:\n|<>]{1,90}?)\s*:(?!\d)(?P<value>.*)$")
_CHECK = re.compile(r"\[[ xX✓✔]?\]|[☐☑☒□■▢]")
MAX_VALUE_TOKENS = 14
# A region whose own read has fewer ``label: value`` lines is not a form. Dev pages: the form regions the layout model
# finds on OmniDocBench pages (notes, exercises, source lines) give 0 to 3 lines, real forms 4 to 105.
MIN_PAIRS = 4


def pairs(text: str) -> list[tuple[list[str], list[str]]]:
    """(label tokens, value tokens) of every ``label: value`` line of a form read; the value may be empty."""
    out = []
    for line in _TABLE.sub("\n", text or "").split("\n"):
        line = re.sub(r"\*\*|__|</?b>|</?strong>", "", line)
        m = _LINE.match(line)
        if not m or line.lstrip().startswith(("#", "|")):
            continue
        label = norm_tokens(m.group("label"))
        value = norm_tokens(_CHECK.sub(" ", m.group("value")))
        if 1 <= len(label) <= 12 and not (len(label) == 1 and len(label[0]) < 2):
            out.append((label, value))
    return out


def plain_lines(text: str) -> list[list[str]]:
    """Token lists of the lines a form read wrote WITHOUT a label and value: captions, notes, headings."""
    out = []
    for line in _TABLE.sub("\n", text or "").split("\n"):
        line = re.sub(r"\*\*|__|</?b>|</?strong>", "", line).strip()
        if not line or line.startswith("|") or _CHECK.search(line):
            continue
        if _LINE.match(line) and not line.startswith("#"):
            continue
        toks = norm_tokens(line.lstrip("# "))
        if toks:
            out.append(toks)
    return out


def is_plain(content: str, plain: list[list[str]]) -> bool:
    """Every line of the block is a line the form read wrote as plain text (same words)."""
    lines = [norm_tokens(l) for l in plain_text(content).split("\n")]
    lines = [l for l in lines if l]
    return bool(lines) and all(any(l == p for p in plain) for l in lines)


def _find(hay: list[str], needle: list[str], start: int = 0) -> int:
    n = len(needle)
    for i in range(start, len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return i
    return -1


def supports(tokens: list[str], prs: list[tuple[list[str], list[str]]]) -> bool:
    """The block is one or more fields of the form read: a label with its value right after it, a label alone,
    most of a label, or a field number that opens a label."""
    if not tokens or len(tokens) > 60:
        return False
    covered = 0
    for label, value in prs:
        i = _find(tokens, label)
        if i < 0:
            # the block is most of a label (the page read put the field number in a block of its own), or it is
            # that field number: the first one or two short tokens of a label
            if len(tokens) < len(label) and _find(label, tokens) >= 0 and (
                    len(tokens) >= 0.6 * len(label)
                    or len(tokens) <= 2 and label[:len(tokens)] == tokens and all(len(t) <= 4 for t in tokens)
                    and any(ch.isdigit() for ch in tokens[0])):
                return True
            continue
        j = i + len(label)
        if value:
            head = value[:min(len(value), 4)]
            if tokens[j:j + len(head)] == head:
                covered += len(label) + min(len(value), len(tokens) - j)
            elif len(tokens) == len(label):
                covered += len(label)          # the label alone; its value stands in another block
        elif len(tokens) == len(label):
            covered += len(label)
    return covered >= 0.6 * len(tokens)


def value_of(a: list[str], b: list[str], prs: list[tuple[list[str], list[str]]]) -> bool:
    """Block ``a`` ends with a label and block ``b`` is that label's value."""
    if not a or not b or len(b) > MAX_VALUE_TOKENS:
        return False
    for label, value in prs:
        if value and a[-len(label):] == label and len(b) <= len(value) + 1 \
                and value[:len(b)] == b[:len(value)][:len(b)] and b[:min(len(b), len(value))] == value[:min(len(b), len(value))]:
            return True
    return False


def run(doc: Document, page: Page, reader_spec: str, max_crops: int = 4) -> dict:
    """Reads the form regions, marks the supported blocks (``meta["form_evidence"]``) and joins label and value
    blocks. Returns counts; ``joined`` > 0 means the page must be grounded again."""
    regs = form_regions(page)[:max_crops]
    filled = [f for f in page.properties.get("form_fields") or []
              if f.get("bbox") and (str(f.get("value") or "").strip() or f.get("checked"))]
    todo = []
    for d in regs:
        x0, y0, x1, y1 = d["box"]
        if any(x0 <= (f["bbox"][0] + f["bbox"][2]) / 2 <= x1 and y0 <= (f["bbox"][1] + f["bbox"][3]) / 2 <= y1 for f in filled):
            continue        # the file's own field values are exact; a crop of the render cannot show them
        if blocks_in(page, tuple(d["box"])):
            todo.append(d)
    if not todo:
        return {}
    results = crops.read_many(doc, page, [(tuple(d["box"]), crops.FORM_PROMPT, f"form:{d['id']}") for d in todo], reader_spec)
    info = {"regions": len(todo), "read": 0, "pairs": 0, "typed": 0, "joined": 0}
    for d, res in zip(todo, results):
        if res is None or not (res.text or "").strip():
            continue
        prs = pairs(res.text)
        info["read"] += 1
        info["pairs"] += len(prs)
        if len(prs) < MIN_PAIRS:      # the crop read shows no form there: its lines decide nothing
            continue
        inside_ = {id(r) for r in blocks_in(page, tuple(d["box"]))}
        plain_ = plain_lines(res.text)
        out: list[Region] = []
        for r in page.regions:
            plain = id(r) in inside_ and r.type not in (RegionType.TABLE, RegionType.FIGURE, RegionType.SIGNATURE) \
                and "<table" not in r.content.lower() and not r.content.lstrip().startswith("#")
            toks = norm_tokens(plain_text(r.content)) if plain else []
            prev = out[-1] if out else None
            if plain and prev is not None and id(prev) in inside_ and r.type == RegionType.TEXT \
                    and prev.type in (RegionType.TEXT, RegionType.KEY_VALUE) and "<table" not in prev.content.lower() \
                    and value_of(norm_tokens(plain_text(prev.content)), toks, prs):
                prev.content = prev.content + "\n" + r.content
                prev.text = plain_text(prev.content)
                prev.candidates.extend(c.model_copy() for c in r.candidates)
                prev.meta["form_evidence"] = "label and value joined"
                info["joined"] += 1
                continue
            if plain and r.type != RegionType.KEY_VALUE and supports(toks, prs):
                r.meta["form_evidence"] = "fields of the form read"
                info["typed"] += 1
            elif plain and r.type != RegionType.KEY_VALUE and is_plain(r.content, plain_):
                r.meta["form_plain"] = True          # the form read saw this text and did not write it as a field
                info["plain"] = info.get("plain", 0) + 1
            out.append(r)
        if len(out) != len(page.regions):
            page.regions = out
            for i, r in enumerate(page.regions):
                r.order = i
    return info


def retype(page: Page) -> int:
    """After the layout types: the supported blocks are Key Value."""
    n = 0
    for r in page.regions:
        if r.meta.get("form_evidence") and r.type in (RegionType.TEXT, RegionType.HEADER, RegionType.SECTION_HEADER,
                                                      RegionType.LIST_ITEM) and not r.content.lstrip().startswith("#"):
            r.meta.setdefault("type_before_fields", r.type.value)
            r.meta["type_rule"] = "form_read"
            r.type = RegionType.KEY_VALUE
            r.heading_level = None
            n += 1
    return n
