"""When are the zoomed strip reads of a scanned page worth their cost?

The zoom vote reads a scan again as 3 enlarged strips, and as 4 more when anything differs. Its
measured benefit is corrected digits in scanned tables. Gates decided after the page read:

``table_numbers``   strips only when the page read contains a table that holds numbers
``ocr_disagree``    strips only when local OCR words do not confirm the numbers of the page read
``table_and_disagree`` / ``table_or_disagree``   both / either

``table_legible``   ``table_numbers`` and no ``[illegible]`` mark in the page read (where the reader cannot read
                    the writing, two zoomed reads agree by chance; such pages belong to the handwriting route)

A gate that is decided after the read cannot save strips that were started with the page read
(``zoom_prefetch``). ``pre_decide`` is the gate before the read (``Read`` option ``zoom_pre_gate`` = the
minimum count): strips only when local OCR already sees that many numbers on the page. On the 147 business
pages 20 keeps every digit correction the always-on vote made on printed scans.

Every decision is recorded in ``Page.properties["zoom_gate"]`` / ``["zoom_pre_gate"]`` with its counts.
"""

from __future__ import annotations

import re
from collections import Counter

from ...model import Page
from .tables import find_tables, number_tokens

OCR_NAMES = ("rapidocr", "ppocr", "tesseract")
_TAGS = re.compile(r"<[^>]+>")
_MD_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)


def _digits(tok: str) -> str:
    return re.sub(r"\D", "", tok)


def table_number_count(text: str) -> int:
    """Numbers with two or more digits inside the tables (HTML or pipe tables) of a page read."""
    parts = find_tables(text)
    parts += _MD_ROW.findall(_TAGS.sub(" ", text or "")) if not parts else []
    n = 0
    for t in parts:
        n += sum(1 for x in number_tokens(_TAGS.sub(" ", t)) if len(_digits(x)) >= 2)
    return n


def unsupported_numbers(page: Page, text: str) -> tuple[int, int, str]:
    """(numbers of the read that no OCR word confirms, numbers of the read, OCR source used).
    Numbers are compared as digit strings (separators ignored), with multiplicity; only numbers with
    two or more digits count."""
    src = next((s for s in OCR_NAMES if page.words.get(s)), "")
    mine = Counter(d for d in (_digits(x) for x in number_tokens(_TAGS.sub(" ", text or ""))) if len(d) >= 2)
    if not src:
        return sum(mine.values()), sum(mine.values()), ""
    theirs: Counter = Counter()
    for w in page.words[src]:
        for x in number_tokens(w.text):
            d = _digits(x)
            if len(d) >= 2:
                theirs[d] += 1
    whole = Counter(_digits(w.text) for w in page.words[src] if len(_digits(w.text)) >= 2)
    theirs |= whole  # "1,234.50" split differently by the two tokenisers still counts
    missing = sum((mine - theirs).values())
    return missing, sum(mine.values()), src


def decide(mode: str, page: Page, text: str, min_table_numbers: int = 4, min_unsupported: int = 1) -> tuple[bool, dict]:
    """(run the strips?, record)."""
    tn = table_number_count(text)
    miss, total, src = unsupported_numbers(page, text)
    table = tn >= min_table_numbers
    disagree = miss >= min_unsupported
    legible = "[illegible" not in (text or "")
    run = {"table_numbers": table, "ocr_disagree": disagree, "table_and_disagree": table and disagree,
           "table_or_disagree": table or disagree, "table_legible": table and legible}.get(mode, True)
    return run, {"mode": mode, "run": run, "table_numbers": tn, "numbers": total, "numbers_unsupported": miss,
                 "ocr_source": src}


_NUM_WORD = re.compile(r"\d[\d,.]*\d")


def pre_decide(page: Page, min_numbers: int = 20) -> tuple[bool, dict]:
    """Before the read: (may this page get strips?, record). Counts OCR words that hold a number with two
    or more digits. A page without any OCR words passes (nothing is known about it)."""
    src = next((s for s in OCR_NAMES if page.words.get(s)), "")
    if not src:
        return True, {"run": True, "ocr_source": "", "numbers": None, "min": min_numbers}
    n = sum(1 for w in page.words[src] if _NUM_WORD.search(w.text or ""))
    return n >= min_numbers, {"run": n >= min_numbers, "ocr_source": src, "numbers": n, "min": min_numbers}
