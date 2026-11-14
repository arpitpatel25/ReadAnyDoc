"""Handwriting detection from signals available at run time.

A page is sent to the handwriting reader when it is a scan (no exact text layer) and either
1. the page read itself marked text it could not read (``[illegible]``), or
2. local OCR could not read the page either: its mean word confidence is low AND few of the
   tokens of the page read appear among the OCR words. Print gives OCR confidence above 0.6 and
   agreement above 0.5 even on poor scans; handwriting gives about 0.35 and under 0.3.
Signal 2 is only used when the page read is mostly Latin script, because the local OCR runs with
an English model and fails the same way on printed Japanese, Arabic and so on.
Born-digital pages are never routed. No label of any test set is used.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional

from ...model import Page
from .evidence import page_evidence, read_tokens

OCR_SOURCES = ("ppocr", "tesseract")
# the second local OCR (PP-OCR, multilingual) as a witness for print; the first one found is used
PRINT_SOURCES = ("rapidocr_fill", "rapidocr", "ppocr")
PRINT_MIN_CONFIDENCE = 0.95  # see veto_print; values measured in e2e/rdtb_full/r4/report.md
PRINT_MIN_WORDS = 10


@dataclass
class HandwritingSignal:
    route: bool
    reason: str = ""
    illegible: int = 0
    ocr_confidence: Optional[float] = None
    ocr_agreement: Optional[float] = None  # share of the read's tokens found among the OCR words
    print_confidence: Optional[float] = None  # mean word confidence of the second OCR (PP-OCR)
    print_words: Optional[int] = None

    def as_dict(self) -> dict:
        d = {"route": self.route, "reason": self.reason, "illegible": self.illegible,
             "ocr_confidence": self.ocr_confidence, "ocr_agreement": self.ocr_agreement}
        if self.print_confidence is not None:
            d.update(print_confidence=self.print_confidence, print_words=self.print_words)
        return d


def latin_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 1.0
    return sum(ch < "ɐ" for ch in letters) / len(letters)


def detect(page: Page, read_text: str, is_pdf: bool = True, min_illegible: int = 1,
           max_confidence: float = 0.5, max_agreement: float = 0.5, min_tokens: int = 10,
           min_ocr_words: int = 5, illegible_max_agreement: Optional[float] = None,
           illegible_sure_agreement: Optional[float] = None, illegible_min_density: float = 0.03,
           print_ocr: bool = False, print_min_confidence: float = PRINT_MIN_CONFIDENCE,
           print_min_words: int = PRINT_MIN_WORDS) -> HandwritingSignal:
    sig = _detect(page, read_text, is_pdf, min_illegible, max_confidence, max_agreement, min_tokens, min_ocr_words,
                  illegible_max_agreement, illegible_sure_agreement, illegible_min_density)
    if print_ocr:
        veto_print(page, sig, print_min_confidence, print_min_words)
    return sig


def veto_print(page: Page, sig: HandwritingSignal, min_confidence: float = 0.0, min_words: int = 0) -> None:
    """``handwriting_print_ocr``. Both route signals rest on Tesseract with its English model failing on the
    page; it also fails on printed tables that are small, dense, ruled or not in English (57 of the 1,000
    RD-TableBench tables went to the handwriting reader for no gain). PP-OCR is a second, independent witness that
    was trained on print in many scripts: where it reads the page's words with high confidence the page is print and
    the first read is kept. Handwriting gives it low confidence too. Only its confidence is used, not its agreement
    with the page read: agreement needs comparable tokens, which a CJK page does not give."""
    src = next((s for s in PRINT_SOURCES if page.words.get(s)), None)
    if src is None:
        return
    conf = [w.confidence.score for w in page.words[src] if w.confidence.score is not None]
    if not conf:
        return
    sig.print_confidence, sig.print_words = round(statistics.mean(conf), 3), len(conf)
    if sig.route and len(conf) >= min_words and sig.print_confidence >= min_confidence:
        sig.route = False
        sig.reason = (f"{sig.reason}; but PP-OCR reads the page as print (confidence {sig.print_confidence} over "
                      f"{len(conf)} words): first read kept")


def _detect(page: Page, read_text: str, is_pdf: bool, min_illegible: int, max_confidence: float, max_agreement: float,
            min_tokens: int, min_ocr_words: int, illegible_max_agreement: Optional[float],
            illegible_sure_agreement: Optional[float], illegible_min_density: float) -> HandwritingSignal:
    """``illegible_max_agreement``: an ``[illegible]`` mark routes the
    page only when local OCR does not confirm the page read (agreement below this value, or no OCR evidence).
    A printed form with one unreadable entry has agreement 0.4 to 0.75 and keeps its page read; handwritten
    pages have under 0.3. Without it 13 of the 50 FUNSD forms went to the handwriting reader, whose read keeps
    the words but pairs fewer labels with their values.
    ``illegible_sure_agreement``: between this value and
    ``illegible_max_agreement`` the OCR half-confirms the read (a faint printed form: 0.38 to 0.44 on the FUNSD
    forms that still went to the handwriting reader). Such a page is routed only when the read is full of
    ``[illegible]`` marks (at least ``illegible_min_density`` of its tokens), not for one or two unreadable entries."""
    sig = HandwritingSignal(route=False, illegible=(read_text or "").count("[illegible"))
    if page.properties.get("text_layer_exact") or (is_pdf and page.kind != "scanned"):
        sig.reason = "not a scan"
        return sig
    src = next((s for s in OCR_SOURCES if page.words.get(s)), None)
    toks = read_tokens(read_text)
    if src is not None:
        conf = [w.confidence.score for w in page.words[src] if w.confidence.score is not None]
        sig.ocr_confidence = round(statistics.mean(conf), 3) if conf else None
        ev = page_evidence(page, prefer=src)
        if ev is not None and ev.source == src and toks:
            sig.ocr_agreement = round(ev.precision(toks), 3)
    if sig.illegible >= min_illegible:
        if (illegible_max_agreement is not None and sig.ocr_agreement is not None and len(toks) >= min_tokens
                and sig.ocr_agreement >= illegible_max_agreement):
            sig.reason = (f"page read has {sig.illegible} [illegible] mark(s), but local OCR confirms the read "
                          f"(agreement {sig.ocr_agreement}): mostly print, first read kept")
            return sig
        if (illegible_sure_agreement is not None and sig.ocr_agreement is not None and len(toks) >= min_tokens
                and sig.ocr_agreement >= illegible_sure_agreement and sig.illegible < illegible_min_density * len(toks)):
            sig.reason = (f"page read has {sig.illegible} [illegible] mark(s) in {len(toks)} tokens and local OCR "
                          f"half-confirms the read (agreement {sig.ocr_agreement}): printed page, first read kept")
            return sig
        sig.route, sig.reason = True, f"page read has {sig.illegible} [illegible] mark(s)"
        return sig
    if (src is not None and len(page.words[src]) >= min_ocr_words and len(toks) >= min_tokens
            and sig.ocr_confidence is not None and sig.ocr_confidence < max_confidence
            and sig.ocr_agreement is not None and sig.ocr_agreement < max_agreement
            and latin_share(read_text) >= 0.7):
        sig.route = True
        sig.reason = (f"local OCR cannot read the page (confidence {sig.ocr_confidence}, "
                      f"agreement with the page read {sig.ocr_agreement})")
        return sig
    sig.reason = "no handwriting signal"
    return sig
