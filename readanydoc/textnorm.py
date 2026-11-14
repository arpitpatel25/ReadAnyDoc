"""Shared text normalisation / tokenisation used by triage, read, verify and ground."""

from __future__ import annotations

import re
from collections import Counter
from typing import NamedTuple

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_TAG = re.compile(r"<[^>]+>")


class Token(NamedTuple):
    norm: str
    start: int
    end: int


def tokenize(text: str, skip_tags: bool = True) -> list[Token]:
    """Alphanumeric runs, lower-cased, with char offsets into ``text``. HTML tags are skipped."""
    if not text:
        return []
    masked = text
    if skip_tags and "<" in text:
        masked = _TAG.sub(lambda m: " " * len(m.group(0)), text)
    return [Token(m.group(0).lower(), m.start(), m.end()) for m in _TOKEN.finditer(masked)]


def norm_tokens(text: str) -> list[str]:
    return [t.norm for t in tokenize(text)]


def bag_overlap(a: list[str], b: list[str]) -> int:
    return sum((Counter(a) & Counter(b)).values())


def containment(a: list[str], b: list[str]) -> float:
    """Fraction of a's tokens also present in b (multiset)."""
    return bag_overlap(a, b) / len(a) if a else 0.0


def agreement(a: list[str], b: list[str]) -> float:
    """Symmetric bag agreement (same measure as spike/router_sim.py ``agree``)."""
    if not a or not b:
        return 0.0
    bag = bag_overlap(a, b)
    return min(bag / len(a), bag / len(b))


def plain_text(content: str) -> str:
    """Markdown/HTML content -> rough plain text."""
    t = _TAG.sub(" ", content or "")
    t = re.sub(r"^\s{0,3}#{1,6}\s+", "", t, flags=re.M)
    t = re.sub(r"[*_`]{1,3}", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()


_SCRIPTS = (
    ("Arabic", 0x0600, 0x06FF), ("Cyrillic", 0x0400, 0x04FF), ("Greek", 0x0370, 0x03FF),
    ("Hebrew", 0x0590, 0x05FF), ("Devanagari", 0x0900, 0x097F), ("Han", 0x4E00, 0x9FFF),
    ("Hiragana", 0x3040, 0x309F), ("Katakana", 0x30A0, 0x30FF), ("Hangul", 0xAC00, 0xD7AF),
    ("Thai", 0x0E00, 0x0E7F),
)


def guess_script(text: str) -> str | None:
    counts: Counter[str] = Counter()
    for ch in text:
        if not ch.isalpha():
            continue
        o = ord(ch)
        for name, lo, hi in _SCRIPTS:
            if lo <= o <= hi:
                counts[name] += 1
                break
        else:
            counts["Latin" if o < 0x0250 else "Other"] += 1
    return counts.most_common(1)[0][0] if counts else None
