"""Cross-page paragraph continuation: a sentence cut by a page break.

Evidence, all needed for an accepted (high-confidence) join:
- the last body block of page N and the first body block of page N+1 are both Text
  (headers, footers, page numbers and footnote-like blocks are skipped);
- the first ends without sentence-final punctuation;
- the second starts with a lower-case letter, or the first ends in a hyphen and the second starts
  with a letter;
- when boxes exist, the first block reaches the lower part of the page.
Weaker cases (function word or comma at the end, capitalised start) are recorded as low-confidence
links and never joined. Blocks are not modified; Shape joins the text when asked to.
"""

from __future__ import annotations

import re
from typing import Optional

from ...model import Confidence, CrossPageLink, Document, Page, Region, RegionType
from ...textnorm import plain_text
from .furniture import FURNITURE

_TERMINAL = re.compile(r"[.!?:;。！？…][\"'”’»)\]]*(?:\[\d+\]|\d{1,2})?$")
_LIST_START = re.compile(r"^\s*(?:[-*+•▪◦]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)])\s+")
_FUNCTION_WORDS = {"a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "but", "with", "by", "from",
                   "that", "which", "as", "is", "are", "was", "were", "be", "been", "this", "these", "those", "its",
                   "their", "his", "her", "our", "your", "into", "than", "not", "also", "such", "between", "per"}
_FOOTNOTE = re.compile(r"^\s*(?:\[\d{1,3}\]|\d{1,3}[.)]?\s|[*†‡§]\s?)")
_SKIP = set(FURNITURE) | {RegionType.COMMENT, RegionType.SIGNATURE}


def _body(page: Page) -> list[Region]:
    return [r for r in page.regions if r.type not in _SKIP and r.content.strip()]


def last_body(page: Page) -> Optional[Region]:
    """Last body block of a page, stepping over trailing footnotes."""
    body = _body(page)
    while len(body) > 1 and body[-1].type == RegionType.TEXT and _FOOTNOTE.match(plain_text(body[-1].content)) \
            and body[-1].bbox is not None and body[-1].bbox.top > 0.75:
        body.pop()
    return body[-1] if body else None


def first_body(page: Page) -> Optional[Region]:
    body = _body(page)
    return body[0] if body else None


def judge(end: str, start: str, last: Optional[Region] = None) -> tuple[float, str]:
    """(score 0-1, reason) for 'start continues end'. >= 0.85 is an accepted join."""
    end, start = end.rstrip(), start.lstrip()
    if not end or not start:
        return 0.0, "empty"
    if _TERMINAL.search(end):
        return 0.0, "sentence ends"
    if _LIST_START.match(start):
        return 0.0, "list item starts"
    if len(end) < 60 and "\n" not in end and len(end.split()) < 9:
        return 0.0, "short line (heading-like)"
    low_box = last is None or last.bbox is None or last.bbox.bottom >= 0.6
    first = start[0]
    if end.endswith(("-", "‐", "­")) and first.isalpha():
        return (0.95, "hyphenated word") if low_box else (0.6, "hyphenated word, block ends mid-page")
    if first.islower():
        return (0.9, "unfinished sentence, lower-case start") if low_box \
            else (0.6, "lower-case start, block ends mid-page")
    words = re.findall(r"[^\W\d_]+", end.lower())
    if end.endswith(",") or (words and words[-1] in _FUNCTION_WORDS):
        return 0.6, "unfinished sentence, capitalised start"
    return 0.3, "no end punctuation only"


def find_paragraph_continuations(doc: Document) -> list[CrossPageLink]:
    links = []
    for a, b in zip(doc.pages, doc.pages[1:]):
        if b.original_page != a.original_page + 1:
            continue  # page_range skipped pages in between
        last, first = last_body(a), first_body(b)
        if last is None or first is None or last.type != RegionType.TEXT or first.type != RegionType.TEXT:
            continue
        score, why = judge(plain_text(last.content), plain_text(first.content), last)
        if score >= 0.5:
            link = CrossPageLink(kind="paragraph_continuation", from_region=last.id, to_region=first.id,
                                 confidence=Confidence.of(score))
            links.append(link)
            first.meta["continues"] = {"region": last.id, "score": score, "reason": why}
    return links
