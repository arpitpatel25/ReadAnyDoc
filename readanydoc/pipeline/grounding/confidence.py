"""Region confidence from evidence already on the Document. No model call, no test data.

Three independent signals:
- ``ground``: share of the block's tokens found on source words in the right place, each weighted
  by how well it matched (exact 1.0, fuzzy = similarity, positional slot 0.5);
- ``reads``: agreement of the block with other reads of the page (the layout model's own text,
  recorded by Verify, and any non-selected page read kept by Read);
- ``ocr``: mean OCR engine score of the cited words (absent when the PDF text layer was used).
score = (0.6 * ground + 0.4 * reads, or ground alone) * (0.7 + 0.3 * ocr).
A block with no text to check (figure note, comment) gets 0.5; a block on a page with no word
source at all is scored from ``reads`` alone and capped at 0.5: nothing located it on the page.
"""

from __future__ import annotations

from typing import Any, Optional

from ...model import Page, Region
from .tokens import scan


def _other_reads(region: Region, page: Page, cache: dict[str, Any]) -> Optional[float]:
    best: Optional[float] = None
    for e in region.evidence:
        if e.score is not None and e.note == "second model read":
            best = e.score if best is None else max(best, e.score)
    reads = [r for r in page.properties.get("reads", []) if not r.get("selected") and r.get("content")]
    if reads:
        toks = [t.norm for t in scan(region.content)[0]]
        if toks:
            bags = cache.get("bags")
            if bags is None:
                bags = cache["bags"] = []
                for r in reads:
                    bag: dict[str, int] = {}
                    for t in scan(r["content"])[0]:
                        bag[t.norm] = bag.get(t.norm, 0) + 1
                    bags.append(bag)
            want: dict[str, int] = {}
            for t in toks:
                want[t] = want.get(t, 0) + 1
            for bag in bags:
                s = sum(min(n, bag.get(t, 0)) for t, n in want.items()) / len(toks)
                best = s if best is None else max(best, s)
    return best


def score_region(region: Region, page: Page, cache: Optional[dict[str, Any]] = None) -> tuple[float, dict]:
    cache = cache if cache is not None else {}
    n = region.meta.get("ground_tokens") or 0
    if not n:
        return 0.5, {"note": "no text to check"}
    reads = _other_reads(region, page, cache)
    has_words = any(page.words.get(k) for k in page.words)
    ground = region.meta.get("ground_quality") if has_words else None
    ocr = region.meta.get("ground_ocr_score")
    if ground is None:
        score = min(0.5, 0.5 * reads) if reads is not None else 0.3
    elif reads is None:
        score = ground
    else:
        score = 0.6 * ground + 0.4 * reads
    if ground is not None and ocr is not None:
        score *= 0.7 + 0.3 * ocr
    parts = {"ground": ground, "reads": None if reads is None else round(reads, 4), "ocr": ocr}
    return round(min(max(score, 0.0), 1.0), 4), parts
