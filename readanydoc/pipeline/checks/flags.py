"""Check 4, confidence flags. Every check that found a fault and could not repair it lowers the
confidence of the block and is listed in the response (``extra.checks``). With no repair switched
on, the faults are only detected: inconsistent table grids and printed totals that miss by a
digit-sized amount. Lines that OCR or the text layer show and the read lacks are flagged only with
``flags_completeness=true``: on the development set those flags did not mark worse blocks (flagged
pages had fewer failed olmOCR tests and a lower OmniDocBench edit distance than unflagged ones),
while grid and totals flags did (RD-TableBench 79.8 / 73.5 against 89.2 for unflagged tables).
"""

from __future__ import annotations

from ...model import Confidence, RegionType
from . import complete, htmlgrid, numbers
from .common import PageChecks


def detect(pc: PageChecks, have: set[str]) -> None:
    """Detection for the checks that did not run as repairs (``have`` = names of the repairs that ran)."""
    page = pc.page
    for region in list(page.regions):
        if region.type != RegionType.TABLE:
            continue
        if "grid" not in have:
            for m in htmlgrid.broken_tables(region.content):
                w = htmlgrid.parse(m.group()).widths
                pc.record(region, "table_grid", "unresolved", f"rows span {min(w)}-{max(w)} columns")
    if "numbers" not in have:
        numbers.run(pc, reread_cells=False)
    if "complete" not in have and bool(pc.opt("flags_completeness", False)):
        read, toks = complete._read_state(pc)
        if page.properties.get("text_layer_exact") and page.words.get("pdf"):
            ocr = next((page.words[k] for k in page.words if k != "pdf" and page.words[k]), [])
            left = [s for s in complete.pdf_segments(page) if complete.absent(s, read, toks) and complete.visible(s, ocr)]
            if left and len(left) <= int(pc.opt("complete_max_lines", 12)):
                complete._flag_left(pc, left, "text-layer")
        elif read:
            source = next((k for k in page.words if k != "pdf" and page.words[k]), None)
            if source and "\n".join(r.content for r in page.regions).count("[illegible") < 3:
                left = [s for s in complete.ocr_segments(page, source)
                        if complete.absent(s, read, toks, near_at=80, cover_at=0.5, fuzzy=True)]
                if left and len(left) <= int(pc.opt("complete_max_lines", 12)):
                    complete._flag_left(pc, left, "OCR")


def apply(pc: PageChecks) -> int:
    """Blocks with an unresolved check: confidence low (kept low by Ground through ``meta['flagged']``),
    all their check records listed for the response."""
    n = 0
    for region in pc.page.regions:
        entries = region.meta.get("checks") or []
        if not entries:
            continue
        region.meta["checks_listed"] = entries
        bad = [e for e in entries if e.get("status") == "unresolved"]
        if bad:
            region.meta["flagged"] = [f"{e['check']}: {e.get('detail', '')}" for e in bad]
            score = region.confidence.score
            region.confidence = Confidence(score=min(score, 0.5) if score is not None else 0.5, label="low")
            n += 1
    if n:
        pc.page.properties["flagged_blocks"] = n
    return n
