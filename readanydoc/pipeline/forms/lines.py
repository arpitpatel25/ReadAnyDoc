"""``kv_lines``: label lines of a flat form that the page read left as plain text. No model call, no
character changes: only the block type, and where a block ends.

1. A Key Value block that ends in a label with nothing after its colon takes the next block as its value
   when that block is one short line of plain text ("INDEX BINDER RE-LABELED BY:" / "Doris Mallory").
2. On a page that already shows it is a form (two or more Key Value blocks, checkboxes or blanks), a short
   text block whose every line is a label in capitals followed by a value that is not ("STUDY NUMBER
   I-7016.401", "DATE 2/15/90") is typed Key Value.
"""

from __future__ import annotations

import re

from ...model import Page, Region, RegionType
from ...textnorm import plain_text
from .grid import BLANK, CHECKBOX

_NOT_LABEL = re.compile(r"^(?:TABLE|FIGURE|FIG|SECTION|PART|CHAPTER|APPENDIX|EXHIBIT|NOTE|NOTES|SCHEDULE|ARTICLE|"
                        r"ITEM|PAGE|SOURCE|RE|TITLE|VOLUME|VOL|NO|US|USA|UK|EU|UN|I|A)\b\.?$")
_SENTENCE = re.compile(r"[.!?][\"”’)]?\s+[A-Z]")


def _caps(tok: str) -> bool:
    letters = [c for c in tok if c.isalpha()]
    return len(letters) >= 2 and all(c.isupper() for c in letters)


def caps_label_line(line: str) -> bool:
    """A label in capitals followed by a value that is not in capitals, 12 words at most."""
    p = plain_text(line).strip()
    toks = p.split()
    if not 2 <= len(toks) <= 12 or p.startswith(("#", "-", "*", "(", "[")):
        return False
    k = 0
    while k < len(toks) and (_caps(toks[k]) or (k > 0 and toks[k] in ("&", "/", "-", "#"))):
        k += 1
    if k == 0 or k == len(toks) or k > 5:
        return False  # no label, no value, or a phrase in capitals rather than a label
    label, value = toks[:k], toks[k:]
    if any(t.endswith((".", ",", ";")) and t not in ("NO.",) for t in label) or value[0].startswith("("):
        return False  # "PRINT NAME AND ADDRESS PLAINLY. (See Instruction C)" is an instruction
    if len(label) == 1 and _NOT_LABEL.match(label[0]):
        return False
    if _NOT_LABEL.match(label[0]) and value[0][:1].isdigit():
        return False  # "TABLE 3 ...", "SECTION 2 ..."
    if any(_caps(t) for t in value[:1]):
        return False
    if len(value) > 6 and (_SENTENCE.search(" ".join(value)) or p.endswith(".")):
        return False  # a heading in capitals run into a sentence
    return True


def form_context(page: Page) -> bool:
    n = 0
    for r in page.regions:
        if r.type == RegionType.KEY_VALUE or (r.type != RegionType.TABLE and (CHECKBOX.search(r.content) or
                                                                             BLANK.search(r.content))):
            n += 1
    return n >= 2


def _open_label(r: Region) -> bool:
    lines = [l for l in plain_text(r.content).split("\n") if l.strip()]
    return bool(lines) and lines[-1].rstrip().endswith(":") and len(lines[-1].split()) <= 14


def _short_value(r: Region) -> bool:
    if r.type != RegionType.TEXT:
        return False
    p = plain_text(r.content).strip()
    return bool(p) and "\n" not in p and len(p.split()) <= 8 and not p.endswith(":") and not _SENTENCE.search(p) \
        and not (len(p.split()) > 4 and p.endswith("."))


def apply(page: Page, join: bool = True, caps: bool = True) -> dict:
    merged = typed = 0
    out: list[Region] = []
    for r in page.regions:
        if join and out and out[-1].type == RegionType.KEY_VALUE and _open_label(out[-1]) and _short_value(r):
            prev = out[-1]
            prev.content = prev.content + "\n" + r.content
            prev.text = plain_text(prev.content)
            prev.candidates.extend(c.model_copy() for c in r.candidates)
            prev.meta.setdefault("forms", {})["value_blocks_joined"] = prev.meta.get("forms", {}).get(
                "value_blocks_joined", 0) + 1
            merged += 1
            continue
        out.append(r)
    page.regions = out
    if caps and form_context(page):
        for r in page.regions:
            if r.type != RegionType.TEXT:
                continue
            lines = [l for l in r.content.split("\n") if l.strip()]
            if 1 <= len(lines) <= 4 and all(caps_label_line(l) for l in lines):
                r.type = RegionType.KEY_VALUE
                r.meta.setdefault("forms", {})["rule"] = "caps_label_lines"
                typed += 1
    return {"value_blocks_joined": merged, "typed_key_value": typed} if merged or typed else {}
