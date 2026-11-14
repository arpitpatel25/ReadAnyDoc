"""Variant c, forms from crops: a detected form-like region (``form`` / ``key_value_region``) is read again
from its own crop with a form prompt (fields as ``label: value`` lines). The crop read replaces the page
read's blocks of that region only when it keeps their words, does not bloat, agrees with the independent
words at least as well, and has more label/value lines than before."""

from __future__ import annotations

import re
from collections import Counter

from ...model import Candidate, Document, Page, Region
from ...textnorm import containment, norm_tokens, plain_text
from ..common import words_in
from ..grounding.sources import source_priority
from ..mdblocks import split_markdown
from . import crops
from .match import blocks_in, form_regions, to_bbox

_KV_LINE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?[^:\n|<>]{1,80}:(?:\*\*)?[ \t]*\S", re.M)
_TABLE = re.compile(r"<table\b.*?</table\s*>", re.I | re.S)


def kv_lines(text: str) -> int:
    return len(_KV_LINE.findall(_TABLE.sub("\n", text or "")))


def box_words(page: Page, box) -> tuple[list[str], str]:
    """Tokens of the best independent word source inside ``box``."""
    for name, _trusted in source_priority(page):
        ws = page.words.get(name) or []
        if ws:
            return norm_tokens(" ".join(ws[i].text for i in words_in(ws, to_bbox(tuple(box), page.index)))), name
    return [], "none"


def decide(old: str, new: str, evidence: list[str]) -> tuple[bool, str, dict]:
    ot, nt = norm_tokens(old), norm_tokens(new)
    keep = containment(ot, nt)
    detail = {"tokens_old": len(ot), "tokens_new": len(nt), "old_in_new": round(keep, 3),
              "kv_old": kv_lines(old), "kv_new": kv_lines(new)}
    if not nt:
        return False, "crop read is empty", detail
    if keep < 0.9:
        return False, "crop read lost words of the page read", detail
    if len(nt) > 1.3 * len(ot) + 10:
        return False, "crop read has much more text than the page read", detail
    if len(evidence) >= 20:
        bag = Counter(evidence)
        po = sum((Counter(ot) & bag).values()) / max(len(ot), 1)
        pn = sum((Counter(nt) & bag).values()) / max(len(nt), 1)
        detail.update({"precision_old": round(po, 3), "precision_new": round(pn, 3)})
        if pn < po - 0.02:
            return False, "crop read agrees worse with the independent words", detail
    if detail["kv_new"] <= detail["kv_old"]:
        return False, "crop read has no more label/value lines", detail
    return True, "form crop keeps the words and has more label/value lines", detail


def run(doc: Document, page: Page, reader_spec: str, max_crops: int = 4, keep_tables: bool = False) -> bool:
    """``keep_tables``: a form region whose page-read blocks contain a table is left alone."""
    todo = []
    # combine phase: values and checkbox states that the forms step took from the PDF's form fields are exact and
    # are not visible in the page render, so a region that holds a filled field is never replaced by a crop read
    filled = [f for f in page.properties.get("form_fields") or []
              if f.get("bbox") and (str(f.get("value") or "").strip() or f.get("checked"))]
    for d in form_regions(page)[:max_crops]:
        x0, y0, x1, y1 = d["box"]
        if any(x0 <= (f["bbox"][0] + f["bbox"][2]) / 2 <= x1 and y0 <= (f["bbox"][1] + f["bbox"][3]) / 2 <= y1 for f in filled):
            page.properties.setdefault("region_forms_skipped", []).append(
                {"detection": d["id"], "reason": "holds filled form fields of the file"})
            continue
        blocks = blocks_in(page, tuple(d["box"]))
        idx = [i for i, r in enumerate(page.regions) if any(r is b for b in blocks)]
        if not idx or idx != list(range(idx[0], idx[-1] + 1)):
            continue  # nothing located there, or the region's blocks are not one run of the reading order
        if keep_tables and any("<table" in page.regions[i].content.lower() for i in idx):
            continue
        todo.append((d, idx))
    if not todo:
        return False
    results = crops.read_many(doc, page, [(tuple(d["box"]), crops.FORM_PROMPT, f"form:{d['id']}") for d, _ in todo],
                              reader_spec)
    report, changed = [], False
    plan = []
    for (d, idx), res in zip(todo, results):
        entry = {"detection": d["id"], "label": d["label"], "blocks": len(idx)}
        report.append(entry)
        if res is None:
            entry["skipped"] = "crop read unavailable"
            continue
        old = "\n\n".join(page.regions[i].content for i in idx)
        ev, src = box_words(page, d["box"])
        ok, why, detail = decide(old, res.text, ev)
        entry.update({"replaced": ok, "reason": why, "evidence": src, **detail})
        if ok:
            plan.append((d, idx, res.text.strip(), old))
    taken: set[int] = set()
    for d, idx, text, old in sorted(plan, key=lambda p: -p[1][0]):  # from the end, so indices stay valid
        if taken & set(idx):
            continue
        taken |= set(idx)
        new = [Region(type=b.type, heading_level=b.heading_level, content=b.text, text=plain_text(b.text),
                      source="regions:form_crop", candidates=[Candidate(source="regions:form_crop", content=b.text)],
                      meta={"search_bbox": to_bbox(tuple(d["box"]), page.index).model_dump(), "form_from_crop": d["id"],
                            "tier": "strong"})
               for b in split_markdown(text)]
        if not new:
            continue
        new[0].candidates.append(Candidate(source="reader", content=old, meta={"replaced": True}))
        page.regions[idx[0]:idx[-1] + 1] = new
        changed = True
        doc.note("regions", f"page {page.index}: form region {d['id']} replaced by its crop read")
    page.properties["region_forms"] = report
    return changed
