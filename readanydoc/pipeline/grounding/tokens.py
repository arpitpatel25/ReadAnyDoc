"""Markup-aware tokenisation of block content, with char offsets into the content string.

Two levels:
- token: the unit of alignment. A run of letters / digits / combining marks; every CJK ideograph or
  kana is its own token (those scripts have no spaces and OCR segments them arbitrarily).
- content word: the unit of a word citation. A whitespace-delimited run of the *visible* text that
  holds at least one token ("O'BRIEN," is one word made of two tokens).

Not content (masked out, never cited, never counted): HTML tags and comments, HTML entities, LaTeX
command names, markdown link/image targets, markdown structural characters, and editorial
placeholders the reader wrote itself (``[illegible]``, ``[Figure: ...]`` descriptions).
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import NamedTuple

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,9}|#[0-9]{1,6}|#x[0-9A-Fa-f]{1,6});")
_LATEX_CMD = re.compile(r"\\[A-Za-z]+")
_LINK_TARGET = re.compile(r"(?<=\])\((?:[^()\s]|\([^()\s]*\))*\)")
# What the reader says about the page instead of transcribing it.
_PLACEHOLDER = re.compile(
    r"\[\s*(?:illegible|unclear|unreadable|signature|handwritten|handwriting|stamp|logo|blank|redacted|sic)"
    r"[^\[\]\n]{0,60}\]", re.I)
_FIGURE_NOTE = re.compile(
    r"[*_]{0,2}\[\s*(?:figure|image|photo|photograph|chart|diagram|illustration|graph|picture|map|logo)\b"
    r"\s*[:\-—][^\[\]]*\][*_]{0,2}", re.I | re.S)
_MD_EDGE = "*_`#|~>"


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0x3040 <= o <= 0x30FF
            or 0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x2FA1F or 0xFF66 <= o <= 0xFF9D)


def _is_token_char(ch: str) -> bool:
    if ch == "_":
        return False
    if ch.isalnum():
        return True
    return unicodedata.category(ch)[0] == "M"


@lru_cache(maxsize=200_000)
def normalize(token: str) -> str:
    """Comparison form: NFKC (ligatures, full-width, presentation forms), case-folded, accents and
    other combining marks removed, any digit mapped to ASCII. Never used for offsets."""
    t = unicodedata.normalize("NFKC", token).casefold()
    out = []
    for ch in unicodedata.normalize("NFD", t):
        cat = unicodedata.category(ch)
        if cat[0] == "M":
            continue
        if cat == "Nd":
            out.append(str(unicodedata.digit(ch)))
        elif ch.isalnum():
            out.append(ch)
    return "".join(out)


class Tok(NamedTuple):
    norm: str
    start: int
    end: int
    word: int  # index into the content-word list


class ContentWord(NamedTuple):
    start: int
    end: int


def mask_markup(content: str, drop_notes: bool = True) -> str:
    """Same length as ``content``; everything that is not visible page text becomes a space."""
    if not content:
        return ""
    blank = lambda m: " " * len(m.group(0))  # noqa: E731
    s = content
    if "<" in s:
        s = _COMMENT.sub(blank, s)
        s = _TAG.sub(blank, s)
    if "&" in s:
        s = _ENTITY.sub(blank, s)
    if "\\" in s:
        s = _LATEX_CMD.sub(blank, s)
    if "](" in s:
        s = _LINK_TARGET.sub(blank, s)
    if drop_notes and "[" in s:
        s = _FIGURE_NOTE.sub(blank, s)
        s = _PLACEHOLDER.sub(blank, s)
    return s


def scan(content: str, drop_notes: bool = True) -> tuple[list[Tok], list[ContentWord]]:
    """Tokens and content words of a block, both with offsets into ``content``."""
    masked = mask_markup(content, drop_notes)
    toks: list[Tok] = []
    words: list[ContentWord] = []
    n = len(masked)
    i = 0
    while i < n:
        if masked[i].isspace():
            i += 1
            continue
        j = i
        while j < n and not masked[j].isspace():
            j += 1
        # one whitespace-delimited run [i, j)
        first = len(toks)
        k = i
        while k < j:
            ch = masked[k]
            if not _is_token_char(ch):
                k += 1
                continue
            if _is_cjk(ch):
                e = k + 1
            else:
                e = k + 1
                while e < j and _is_token_char(masked[e]) and not _is_cjk(masked[e]):
                    e += 1
            norm = normalize(masked[k:e])
            if norm:
                toks.append(Tok(norm, k, e, len(words)))
            k = e
        if len(toks) > first:
            a, b = i, j
            while a < toks[first].start and masked[a] in _MD_EDGE:
                a += 1
            while b > toks[-1].end and masked[b - 1] in _MD_EDGE:
                b -= 1
            words.append(ContentWord(a, b))
        i = j
    return toks, words


def tokens_of(text: str) -> list[str]:
    """Normalised tokens of a source word (PDF / OCR text): no markup handling."""
    out: list[str] = []
    k, n = 0, len(text)
    while k < n:
        ch = text[k]
        if not _is_token_char(ch):
            k += 1
            continue
        e = k + 1
        if not _is_cjk(ch):
            while e < n and _is_token_char(text[e]) and not _is_cjk(text[e]):
                e += 1
        norm = normalize(text[k:e])
        if norm:
            out.append(norm)
        k = e
    return out


def is_figure_note(content: str) -> bool:
    """True when the block is only the reader's description of a picture."""
    return bool(content) and bool(_FIGURE_NOTE.search(content)) and not scan(content)[0]
