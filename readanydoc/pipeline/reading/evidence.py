"""Independent evidence for a page: the words of a trusted PDF text layer, or OCR words.

``PageEvidence`` is a token sequence with its bag, bigrams and trigrams. Tokens are alphanumeric
runs with their original case (tests and users are case-sensitive); the bag helpers lower-case.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from ...model import Page
from ...textnorm import tokenize

_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉", "01234567890123456789")
_LATEX = re.compile(r"\$[^$\n]{0,200}\$")


def case_tokens(text: str) -> list[tuple[str, int, int]]:
    """(token with original case, start, end); HTML tags skipped."""
    return [(text[t.start:t.end], t.start, t.end) for t in tokenize(text)]


@dataclass
class PageEvidence:
    source: str  # "pdf" | "ppocr" | ...
    trusted: bool  # exact characters can be taken from it (a PDF layer that passed triage)
    tokens: list[str] = field(default_factory=list)  # original case, reading order of the source
    bag: Counter = field(default_factory=Counter)  # lower-cased
    bigrams: set = field(default_factory=set)  # case-sensitive
    trigram_mid: dict = field(default_factory=dict)  # (prev, next) -> {middle tokens}

    @classmethod
    def from_tokens(cls, source: str, tokens: list[str], trusted: bool = False) -> "PageEvidence":
        ev = cls(source=source, trusted=trusted, tokens=tokens)
        ev.bag = Counter(t.lower() for t in tokens)
        ev.bigrams = set(zip(tokens, tokens[1:]))
        for a, b, c in zip(tokens, tokens[1:], tokens[2:]):
            ev.trigram_mid.setdefault((a, c), set()).add(b)
        return ev

    def __len__(self) -> int:
        return len(self.tokens)

    def recall(self, tokens: list[str]) -> float:
        """Share of the evidence tokens that the read contains (bag): low = the read dropped text."""
        if not self.tokens:
            return 0.0
        return sum((self.bag & Counter(t.lower() for t in tokens)).values()) / len(self.tokens)

    def precision(self, tokens: list[str]) -> float:
        """Share of the read's tokens found in the evidence (bag): low = invented or misread text."""
        if not tokens:
            return 0.0
        return sum((self.bag & Counter(t.lower() for t in tokens)).values()) / len(tokens)

    def numeric_precision(self, tokens: list[str]) -> Optional[float]:
        """Share of the read's number tokens found in the evidence; None when it has under 5.
        A wrong number, date or amount is the costliest error a parser can make."""
        nums = [t for t in tokens if any(ch.isdigit() for ch in t)]
        if len(nums) < 5:
            return None
        return sum((self.bag & Counter(t.lower() for t in nums)).values()) / len(nums)

    def supports(self, tok: str, prev: Optional[str], nxt: Optional[str]) -> bool:
        """True when the evidence has ``tok`` next to the same neighbour on either side."""
        return (prev is not None and (prev, tok) in self.bigrams) or \
               (nxt is not None and (tok, nxt) in self.bigrams)


def read_tokens(text: str) -> list[str]:
    """Tokens of a model read, comparable with evidence tokens (LaTeX spans and tags removed)."""
    return [t for t, _, _ in case_tokens(_LATEX.sub(" ", text or ""))]


def page_evidence(page: Page, prefer: Optional[str] = None) -> Optional[PageEvidence]:
    """The best independent word source of a page: trusted PDF layer first, else OCR words."""
    order = [prefer] if prefer else []
    if page.text_layer_usable:
        order.append("pdf")
    order += ["ppocr", "tesseract"]
    for src in order:
        words = page.words.get(src) or []
        if not words:
            continue
        toks = [t for w in words for t, _, _ in case_tokens(w.text.translate(_SUP))]
        if toks:
            return PageEvidence.from_tokens(src, toks, trusted=(src == "pdf" and bool(page.properties.get("text_layer_exact"))))
    return None


def all_evidence(page: Page) -> list[PageEvidence]:
    """Every usable word source (trusted PDF layer and OCR), strongest first."""
    out = []
    for src in (["pdf"] if page.text_layer_usable else []) + ["ppocr", "tesseract"]:
        ev = page_evidence(page, prefer=src) if page.words.get(src) else None
        if ev is not None and ev.source == src:
            out.append(ev)
    return out
