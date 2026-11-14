"""Displayed formulas. Switches, off by default (tables spec key ``formulas``):

``tag``     an equation number the read wrote as ``\\quad (1)`` at the end of a displayed formula becomes
            ``\\tag{1}`` (deterministic; what the number is: a label, not part of the formula);
``matrix``  the page read wrote a matrix as an HTML table: where the layout model shows a formula and no table,
            the region is read again from a crop with a formula prompt;
``lines``   a displayed formula that is printed over several lines (a tall formula region) is read again from
            its crop so that the printed lines are kept.
A crop read replaces the page read only when it holds the same numbers and the same letters (LaTeX commands and
layout aside): the characters of the formula are not changed on trust, only its layout.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ...model import Candidate, Document, Page, Region, RegionType
from ...readers import prompts
from ...textnorm import plain_text
from ..regions import crops
from ..regions.match import TABLE_LABELS, blocks_in, box_of, detections, inside, to_bbox

_TAG_END = re.compile(r"(?:\\qquad|\\quad|\;|\\,|\s)+\(\s*(\d{1,3}[a-z]?(?:\.\d{1,3})?)\s*\)\s*(\$\$|\\\])\s*$")
_DISPLAY = re.compile(r"^\s*(\$\$|\\\[).*(\$\$|\\\])\s*$", re.S)
_CMD = re.compile(r"\\[a-zA-Z]+")
_HTML = re.compile(r"<[^>]+>")


def tag_numbers(content: str) -> str:
    """``$$ a = b \\quad (1)$$`` -> ``$$ a = b \\tag{1}$$``. Only a displayed formula that is the whole block."""
    if not _DISPLAY.match(content or "") or "\\tag" in content:
        return content
    return _TAG_END.sub(lambda m: f" \\tag{{{m.group(1)}}}{m.group(2)}", content)


def symbols(content: str) -> Counter:
    """Digits and letters of a formula, LaTeX commands, markup and tags left out (a multiset)."""
    t = _HTML.sub(" ", content or "")
    t = re.sub(r"\\tag\{[^}]*\}", " ", t)
    t = re.sub(r"\\(?:begin|end)\{[a-zA-Z*]+\}(?:\{[lcr|]*\})?", " ", t)
    t = re.sub(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\b", " ", t)
    t = _CMD.sub(" ", t).replace("−", "-")
    return Counter(ch.lower() for ch in t if ch.isalnum())


def same_symbols(old: str, new: str, at: float = 1.0) -> tuple[bool, float, float]:
    a, b = symbols(old), symbols(new)
    na, nb = sum(a.values()), sum(b.values())
    hit = sum((a & b).values())
    rec, prec = (hit / na if na else 0.0), (hit / nb if nb else 0.0)
    da = Counter({k: v for k, v in a.items() if k.isdigit()})
    db = Counter({k: v for k, v in b.items() if k.isdigit()})
    return (da == db and rec >= at and prec >= at), round(rec, 3), round(prec, 3)


def _matrix_like(r: Region) -> bool:
    if r.type != RegionType.TABLE or "<th" in r.content.lower():
        return False
    cells = [plain_text(c).strip() for c in re.findall(r"<td\b[^>]*>(.*?)</td\s*>", r.content, flags=re.I | re.S)]
    return 4 <= len(cells) <= 100 and all(len(c) <= 24 for c in cells)


def run(doc: Document, page: Page, modes: set[str], reader_spec: str, spec: dict[str, Any]) -> bool:
    changed = False
    if "tag" in modes:
        for r in page.regions:
            if r.type == RegionType.TEXT and ("$$" in r.content or "\\[" in r.content):
                new = tag_numbers(r.content)
                if new != r.content:
                    r.content, r.text = new, plain_text(new)
                    r.meta["formula_tag"] = True
                    changed = True
    if not modes & {"matrix", "lines"}:
        return changed
    tables = detections(page, TABLE_LABELS, 0.5)
    todo = []
    for d in detections(page, {"formula"}, float(spec.get("formula_score", 0.6))):
        box = tuple(d["box"])
        if any(inside(box, tuple(t["box"])) >= 0.5 for t in tables):
            continue
        blocks = blocks_in(page, box, 0.5)
        idx = [i for i, r in enumerate(page.regions) if any(r is b for b in blocks)]
        if not idx:
            continue
        # a connector between two matrices ("~", "=") is too short for Ground to place: it belongs to the run
        gaps = [i for i in range(idx[0], idx[-1] + 1) if i not in idx]
        if any(page.regions[i].bbox is not None or len(page.regions[i].content.strip()) > 4 for i in gaps):
            continue
        idx = list(range(idx[0], idx[-1] + 1))
        regs = [page.regions[i] for i in idx]
        has_matrix = any(_matrix_like(r) for r in regs)
        if any(r.type == RegionType.TABLE and not _matrix_like(r) for r in regs):
            continue
        if any(r.type in (RegionType.FIGURE, RegionType.KEY_VALUE, RegionType.SIGNATURE, RegionType.COMMENT) for r in regs):
            continue  # (position rules may have typed a formula line as List Item or furniture: those are fine)
        tall = (box[3] - box[1]) >= float(spec.get("formula_tall", 0.045))
        single = len(regs) == 1 and _DISPLAY.match(regs[0].content or "") and "\\\\" not in regs[0].content
        if ("matrix" in modes and has_matrix) or ("lines" in modes and tall and single and not has_matrix):
            todo.append((d, idx, "matrix" if has_matrix else "lines"))
    todo = todo[:int(spec.get("max_formulas", 8))]
    if not todo:
        return changed
    results = crops.read_many(doc, page, [(tuple(d["box"]), prompts.FORMULA_CROP_FILE, f"formula:{d['id']}") for d, _, _ in todo],
                              reader_spec)
    report, plan = [], []
    for (d, idx, kind), res in zip(todo, results):
        entry = {"detection": d["id"], "kind": kind, "blocks": len(idx)}
        report.append(entry)
        if res is None:
            entry["skipped"] = "crop read unavailable"
            continue
        text = res.text.strip()
        old = "\n\n".join(page.regions[i].content for i in idx)
        if "NO_FORMULA" in text or "<table" in text.lower() or not _DISPLAY.match(text):
            entry.update({"replaced": False, "reason": "the crop read is not one displayed formula"})
            continue
        ok, rec, prec = same_symbols(old, text)
        entry.update({"replaced": ok, "symbols_kept": rec, "symbols_precise": prec,
                      "reason": "same digits and letters as the page read" if ok else "digits or letters differ from the page read"})
        if spec.get("keep"):
            entry["candidate"], entry["mine"] = text, old
        if ok:
            plan.append((d, idx, text, old, kind))
    for d, idx, text, old, kind in sorted(plan, key=lambda p: -p[1][0]):
        new = Region(type=RegionType.TEXT, content=text, text=plain_text(text), bbox=to_bbox(tuple(d["box"]), page.index),
                     source="formulas:crop", candidates=[Candidate(source="formulas:crop", content=text, meta={"selected": True}),
                                                         Candidate(source="reader", content=old, meta={"replaced": True})],
                     meta={"bbox_source": "layout", "layout_type": "Text", "formula_reread": kind, "tier": "strong"})
        page.regions[idx[0]:idx[-1] + 1] = [new]
        changed = True
        doc.note("tables", f"page {page.index}: formula region {d['id']} replaced by its crop read ({kind})")
    for k, r in enumerate(page.regions):
        r.order = k
    page.properties["formula_reads"] = report
    return changed
