"""Split page-level markdown into typed blocks without changing a character inside any block.

Segmentation follows Reducto's granularity where its responses are consistent: every top-level list item
is its own List Item block, and a run of consecutive label/value blocks is one Key Value block (its parts
stay separated by blank lines, so the page text is the same string as before).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..model import RegionType
from .typing.markup import classify_text, split_list_items

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+\S")
_LIST = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+\S")
_TABLE_OPEN = re.compile(r"<table\b", re.I)
_TABLE_CLOSE = re.compile(r"</table\s*>", re.I)
_FIGURE = re.compile(r"^\s*(?:!\[|\[(?:Figure|Image|Photo|Chart|Diagram|Illustration)\b|<img\b)", re.I)


@dataclass
class MdBlock:
    text: str
    type: RegionType
    heading_level: Optional[int] = None


def _classify(text: str) -> tuple[RegionType, Optional[int]]:
    first = text.lstrip("\n").split("\n", 1)[0]
    m = _HEADING.match(first)
    if m and "\n" not in text.strip():
        level = len(m.group(1))
        return (RegionType.TITLE if level == 1 else RegionType.SECTION_HEADER), level
    if _TABLE_OPEN.search(text):
        return RegionType.TABLE, None
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) >= 2 and all(l.lstrip().startswith("|") for l in lines):
        return RegionType.TABLE, None
    if lines and all(_LIST.match(l) or l.startswith((" ", "\t")) for l in lines) and _LIST.match(lines[0]):
        return RegionType.LIST_ITEM, None
    if _FIGURE.match(first):
        return RegionType.FIGURE, None
    t, level, _rule = classify_text(text)
    return t, level


_OPEN_WRAPPER = re.compile(r"^\s*(?:<(?:div|center|section|article|p|span)\b[^<>]*>\s*)+$", re.I)
_CLOSE_WRAPPER = re.compile(r"^\s*(?:(?:</(?:div|center|section|article|p|span)\s*>|<br\s*/?>|<hr\s*/?>)\s*)+$|^\s*(?:[-*_]\s*){3,}$",
                            re.I)


def _attach_wrappers(blocks: list[str]) -> list[str]:
    """A block that is only markup (an opening ``<div>``, a closing ``</div>``, a rule) shows nothing on
    the page. It is attached to the block it wraps, still separated by a blank line, so the page text
    stays the same string and no empty block is returned. Tables are left alone."""
    out: list[str] = []
    pending: list[str] = []
    for b in blocks:
        if _OPEN_WRAPPER.match(b):
            pending.append(b)
            continue
        if _CLOSE_WRAPPER.match(b) and out and not _TABLE_OPEN.search(out[-1]) and not pending \
                and not _HEADING.match(out[-1]):
            out[-1] = out[-1] + "\n\n" + b
            continue
        if pending and not _TABLE_OPEN.search(b) and not _CLOSE_WRAPPER.match(b) and not _HEADING.match(b):
            b = "\n\n".join(pending + [b])
            pending = []
        elif pending:
            out.extend(pending)
            pending = []
        out.append(b)
    out.extend(pending)
    return out


def _strip_wrappers(text: str) -> str:
    """The block without attached wrapper paragraphs, for typing only."""
    parts = [p for p in re.split(r"\n\s*\n", text) if not (_OPEN_WRAPPER.match(p) or _CLOSE_WRAPPER.match(p))]
    return "\n\n".join(parts) if parts and len(parts) != len(re.split(r"\n\s*\n", text)) else text


MAX_KEY_VALUE_CHARS = 1500  # a merged Key Value block stays about the size of a form section


def split_markdown(md: str, split_lists: bool = True, merge_key_values: bool = True) -> list[MdBlock]:
    """Blocks are separated by blank lines; an HTML table or fenced code block is never split."""
    blocks: list[str] = []
    cur: list[str] = []
    depth = 0  # open <table> tags
    fenced = False

    def flush() -> None:
        if cur and any(l.strip() for l in cur):
            blocks.append("\n".join(cur).strip("\n"))
        cur.clear()

    for line in md.replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        opens, closes = len(_TABLE_OPEN.findall(line)), len(_TABLE_CLOSE.findall(line))
        inside = depth > 0 or fenced
        if not line.strip() and not inside:
            flush()
            continue
        if _HEADING.match(line) and not inside:  # a heading is always its own block
            flush()
            cur.append(line)
            flush()
            continue
        cur.append(line)
        depth = max(depth + opens - closes, 0)
    flush()
    blocks = _attach_wrappers(blocks)
    out: list[MdBlock] = []
    for b in blocks:
        t, level = _classify(_strip_wrappers(b))
        if t == RegionType.LIST_ITEM and split_lists:
            out.extend(MdBlock(text=item, type=t) for item in split_list_items(b))
        elif (t == RegionType.KEY_VALUE and merge_key_values and out and out[-1].type == RegionType.KEY_VALUE
              and len(out[-1].text) + len(b) <= MAX_KEY_VALUE_CHARS):
            out[-1] = MdBlock(text=out[-1].text + "\n\n" + b, type=t)
        else:
            out.append(MdBlock(text=b, type=t, heading_level=level))
    return out
