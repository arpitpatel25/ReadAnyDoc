"""Variant b, tables from crops: every detected table is read again from its own full-resolution crop and
replaces the page read's table only under an explicit acceptance rule.

Rules (``mode``):
``grid``     the crop's grid is consistent (every row spans the same number of columns)
``numbers``  ``grid`` and the crop's numbers agree better with the independent words in the table box
             (exact PDF text layer, else high-confidence OCR words) than the page read's numbers do
``broken``   crops are read only where the page read's table has an inconsistent grid; accepted when the
             crop's grid is consistent
``broken_numbers``  ``broken`` and the crop's numbers agree with the word evidence at least as well
In every mode the crop must hold one table of comparable size (0.7x to 1.6x the cells) and must not replace
read cells by [illegible].
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

from ...model import Candidate, Document, Page, Region
from ...textnorm import plain_text
from ..common import words_in
from ..reading.tables import _CELL, _ROW, _SPAN, find_tables, has_table, number_tokens, parse_grid
from . import crops
from .match import TABLE_LABELS, box_of, detections, inside, iou, to_bbox


def grid_consistent(table_html: str) -> Optional[bool]:
    """Same rule as the development set's ``grid_ok``: None under 2 rows, else True when all rows have one width
    (colspan counted, cells carried down by rowspan counted)."""
    carry: list[int] = []
    widths: list[int] = []
    for rm in _ROW.finditer(table_html):
        w = sum(1 for k in carry if k > 0)
        carry = [k - 1 for k in carry if k > 1]
        for cm in _CELL.finditer(rm.group(1)):
            attrs = {k.lower(): int(v) for k, v in _SPAN.findall(cm.group(2))}
            cs, rs = max(attrs.get("col", 1), 1), max(attrs.get("row", 1), 1)
            w += cs
            if rs > 1:
                carry += [rs - 1] * cs
        widths.append(w)
    if len(widths) < 2:
        return None
    return len(set(widths)) == 1


def evidence_numbers(page: Page, box) -> tuple[list[str], str]:
    bb = to_bbox(tuple(box), page.index)
    if page.properties.get("text_layer_exact") and page.words.get("pdf"):
        ws = page.words["pdf"]
        return [n for i in words_in(ws, bb) for n in number_tokens(ws[i].text)], "pdf"
    for src in ("ppocr", "rapidocr", "tesseract"):
        ws = page.words.get(src) or []
        if ws:
            return [n for i in words_in(ws, bb) if (ws[i].confidence.score or 0) >= 0.9
                    for n in number_tokens(ws[i].text)], src
    return [], "none"


def _numbers(table_html: str) -> Counter:
    return Counter(n for c in parse_grid(table_html).cells for n in number_tokens(c))


def number_f1(table_html: str, evidence: list[str]) -> float:
    mine, ev = _numbers(table_html), Counter(evidence)
    n, m = sum(mine.values()), sum(ev.values())
    hit = sum((mine & ev).values())
    if not n or not m or not hit:
        return 0.0
    p, r = hit / n, hit / m
    return 2 * p * r / (p + r)


def decide(mode: str, mine: str, crop: str, evidence: list[str], margin: float = 0.02,
           min_numbers: int = 8) -> tuple[bool, str, dict]:
    """(replace, reason, detail) for one page-read table and one crop-read table."""
    gm, gc = parse_grid(mine), parse_grid(crop)
    ok_m, ok_c = grid_consistent(mine), grid_consistent(crop)
    fm, fc = number_f1(mine, evidence), number_f1(crop, evidence)
    detail = {"grid_ours": ok_m, "grid_crop": ok_c, "cells_ours": len(gm.cells), "cells_crop": len(gc.cells),
              "f1_ours": round(fm, 3), "f1_crop": round(fc, 3), "evidence_numbers": len(evidence)}
    if not gc.cells:
        return False, "crop read has no table", detail
    if plain_text(mine).split() == plain_text(crop).split() and ok_m == ok_c:
        return False, "same table", detail
    if len(gc.cells) < 0.7 * len(gm.cells) or len(gc.cells) > 1.6 * max(len(gm.cells), 1):
        return False, "crop table has a different size", detail
    if crop.count("[illegible") > mine.count("[illegible"):
        return False, "crop read left cells unread", detail
    if ok_c is not True:
        return False, "crop grid is not consistent", detail
    if mode in ("broken", "broken_numbers") and ok_m is not False:
        return False, "page-read grid is consistent", detail
    if mode in ("numbers", "broken_numbers"):
        if len(evidence) < min_numbers:
            return False, "too few independent numbers to compare", detail
        if mode == "numbers" and fc < fm + margin:
            return False, "crop numbers do not agree better with the words", detail
        if mode == "broken_numbers" and fc < fm - 1e-9:
            return False, "crop numbers agree worse with the words", detail
    return True, {"grid": "crop grid consistent", "numbers": "crop grid consistent and numbers agree better",
                  "broken": "page-read grid broken, crop grid consistent",
                  "broken_numbers": "page-read grid broken, crop consistent, numbers not worse"}[mode], detail


def pairs(page: Page, min_score: float = 0.5) -> list[tuple[dict, Region]]:
    """(table detection, the one page block that holds that table). Ambiguous cases are left out."""
    dets = detections(page, TABLE_LABELS, min_score)
    blocks = [r for r in page.regions if has_table(r.content) and len(find_tables(r.content)) == 1]
    n_table_blocks = sum(1 for r in page.regions if has_table(r.content))
    out = []
    taken: set[str] = set()
    for d in dets:
        db = tuple(d["box"])
        hits = [r for r in blocks if r.bbox is not None
                and (inside(box_of(r.bbox), db) >= 0.5 or iou(box_of(r.bbox), db) >= 0.3)]
        if len(hits) == 1 and hits[0].id not in taken:
            # the block must not also belong to another table detection
            others = [x for x in dets if x is not d and (inside(box_of(hits[0].bbox), tuple(x["box"])) >= 0.5)]
            if not others:
                out.append((d, hits[0]))
                taken.add(hits[0].id)
    if not out and len(dets) == 1 and len(blocks) == 1 and n_table_blocks == 1:
        out.append((dets[0], blocks[0]))  # one table on the page and one in the read: same table
    return out


def run(doc: Document, page: Page, mode: str, reader_spec: str, max_crops: int = 6) -> bool:
    todo = []
    for d, r in pairs(page):
        mine = find_tables(r.content)[0]
        if mode in ("broken", "broken_numbers") and grid_consistent(mine) is not False:
            continue
        todo.append((d, r, mine))
    todo = todo[:max_crops]
    if not todo:
        return False
    results = crops.read_many(doc, page, [(tuple(d["box"]), crops.TABLE_PROMPT, f"table:{d['id']}") for d, _, _ in todo],
                              reader_spec)
    changed = False
    report = []
    for (d, r, mine), res in zip(todo, results):
        entry = {"detection": d["id"], "region": r.id}
        report.append(entry)
        if res is None:
            entry["skipped"] = "crop read unavailable"
            continue
        found = find_tables(res.text)
        crop = max(found, key=len) if found else ""
        ev, src = evidence_numbers(page, d["box"])
        ok, why, detail = decide(mode, mine, crop, ev)
        entry.update({"replaced": ok, "reason": why, "evidence": src, **detail})
        if crop:
            r.candidates.append(Candidate(source="regions:table_crop", content=crop, meta={"selected": ok}))
        if ok:
            r.candidates.append(Candidate(source=r.source or "reader", content=r.content, meta={"replaced": True}))
            r.content = r.content.replace(mine, crop)
            r.text = plain_text(r.content)
            r.meta["table_from_crop"] = why
            changed = True
            doc.note("regions", f"page {page.index}: table replaced by its crop read ({why})")
    page.properties["region_tables"] = report
    return changed
