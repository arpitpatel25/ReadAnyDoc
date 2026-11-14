"""Effort by page difficulty.

Haiku 5.5 at medium effort (the API default) costs about a third less than at high effort and is as
good on running text, but measurably worse on tables with many numbers. ``Read`` option
``effort_route`` sends the pages that look easy *before they are read* to the cheaper effort:

    {"read": {"effort_route": "medium"}}                     easy pages -> medium, the rest unchanged
    {"read": {"effort_route": "medium", "effort_route_numbers": 40, "effort_route_share": 0.12}}

A page is "easy" when the words triage already has for it (the PDF text layer when usable, else
local OCR) hold few numbers: fewer than ``effort_route_numbers`` tokens with two or more digits and
a share of such tokens below ``effort_route_share``. A page without any words (no text layer, OCR
found nothing or is not installed) is never easy. The decision is recorded in
``Page.properties["effort_route"]``.
"""

from __future__ import annotations

import re

from ...model import Page

_NUM = re.compile(r"\d[\d,.]*\d")
OCR_NAMES = ("ppocr", "rapidocr", "tesseract")


def signals(page: Page) -> dict:
    """Counts from the words known before reading."""
    src = "pdf" if page.text_layer_usable and page.words.get("pdf") else next((s for s in OCR_NAMES if page.words.get(s)), "")
    words = page.words.get(src) or []
    numbers = sum(1 for w in words if _NUM.search(w.text or ""))
    return {"kind": page.kind, "source": src, "words": len(words), "numbers": numbers,
            "number_share": round(numbers / len(words), 4) if words else None}


def decide(page: Page, max_numbers: int = 40, max_share: float = 0.12, min_words: int = 20) -> tuple[bool, dict]:
    """(easy?, record)."""
    s = signals(page)
    easy = bool(s["source"]) and s["words"] >= min_words and s["numbers"] < max_numbers and (s["number_share"] or 0) < max_share
    return easy, {**s, "easy": easy}


def spec_with_effort(spec: str, effort: str) -> str:
    """``anthropic_api:haiku#high@dev1`` -> ``anthropic_api:haiku#medium@dev1`` (other readers unchanged)."""
    if not spec or "#" not in spec:
        return spec
    head, _, rest = spec.partition("#")
    _, at, sample = rest.partition("@")
    return f"{head}#{effort}{at}{sample}"
