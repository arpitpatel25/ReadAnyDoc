"""Table-only reads of table regions.

The page prompt asks for everything on the page; a prompt that asks only for the table reads the same table with
a better grid (RD-TableBench: 91.4 against 87.8 for the page read). But a second read also swaps errors, so a
table-only read replaces the page read only under a rule that rests on evidence (see ``decide_*``).

Kinds of candidates:
``missed``  the layout model shows a table (or an index: rows of entries with a value column) where the page read
            wrote running text;
``dropped`` the layout model shows a table where the page read has nothing at all (dense pages: the read sometimes
            writes "[listing in tabular columns]" instead of the listing), and local OCR sees words there that the
            page read does not hold;
``broken``  the page read's table has rows of different widths;
``table``   every other table of the page read (mode ``all``).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from ...model import Candidate, Document, Page, Region, RegionType
from ...readers import prompts
from ...textnorm import norm_tokens, plain_text
from ..reading.tables import find_tables, has_table
from ..regions import crops
from ..regions.match import area, blocks_in, box_of, detections, inside, to_bbox
from ..regions.tables import pairs
from . import evidence as ev
from .hygiene import clean_table

TABLE_LIKE = {"table": 0.5, "document_index": 0.6}
# Block types that may be running text of a missed table. Header / Footer are in: Ground's position rules type a
# short line near the page edge as furniture before the layout types are applied (seen on an olmOCR page: the last
# row of a table was a "Footer"), and the acceptance rule still requires the table to hold every word.
TEXT_TYPES = {RegionType.TEXT, RegionType.LIST_ITEM, RegionType.SECTION_HEADER, RegionType.TITLE, RegionType.HEADER,
              RegionType.FOOTER, RegionType.PAGE_NUMBER}
PROMPTS = {"only": prompts.TABLE_ONLY_FILE, "clean": prompts.TABLE_ONLY_CLEAN_FILE,
           "strict": prompts.TABLE_CROP_STRICT_FILE}
WHOLE: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)


@dataclass
class Cand:
    kind: str  # missed | broken | table
    box: tuple[float, float, float, float]
    tag: str
    region: Optional[Region] = None  # the page-read table block (broken, table)
    mine: str = ""  # its table HTML
    idx: list[int] = field(default_factory=list)  # the page-read blocks the region holds (missed)
    blocks: list = field(default_factory=list)  # the same blocks as objects (indices move when tables are added)
    label: str = "table"
    score: float = 1.0
    tag_why: Optional[list] = None  # the ``all_if`` signs that made this table a candidate


def find(page: Page, modes: set[str], opts: Optional[dict] = None) -> list[Cand]:
    out: list[Cand] = []
    opts = opts or {}
    like = {"table": float(opts.get("missed_table_score", TABLE_LIKE["table"])),
            "document_index": float(opts.get("missed_index_score", TABLE_LIKE["document_index"]))}
    table_blocks = [r for r in page.regions if has_table(r.content)]
    if "missed" in modes and all(r.bbox is not None for r in table_blocks):
        dets = [d for d in detections(page, like, 0.0) if d["score"] >= like[d["label"]]]
        for d in dets:
            box = tuple(d["box"])
            if any(x is not d and inside(box, tuple(x["box"])) >= 0.8 and area(tuple(x["box"])) > area(box) for x in dets):
                continue  # a table inside a larger table region: the outer one is the candidate
            if any(inside(box_of(r.bbox), box) >= 0.3 or inside(box, box_of(r.bbox)) >= 0.3 for r in table_blocks):
                continue  # the page read has a table there
            blocks = blocks_in(page, box)
            idx = [i for i, r in enumerate(page.regions) if any(r is b for b in blocks)]
            if not idx or idx != list(range(idx[0], idx[-1] + 1)):
                continue  # nothing located there, or not one run of the reading order
            if any(page.regions[i].type not in TEXT_TYPES and not page.regions[i].meta.get("kv_is_text") for i in idx):
                continue  # figures, form fields: not text that could be a table (kv_is_text: a Key Value block
                #           on a page without any form region, which the region switch kv=page makes Text)
            if sum(len(norm_tokens(page.regions[i].content)) for i in idx) < 8:
                continue
            out.append(Cand("missed", box, f"missed:{d['id']}", idx=idx, blocks=[page.regions[i] for i in idx],
                            label=d["label"], score=d["score"]))
    if "dropped" in modes:
        page_tokens = Counter(t for r in page.regions for t in norm_tokens(r.content))
        for d in detections(page, {"table"}, 0.5):
            box = tuple(d["box"])
            if any(r.bbox is not None and has_table(r.content) and
                   (inside(box_of(r.bbox), box) >= 0.3 or inside(box, box_of(r.bbox)) >= 0.3) for r in page.regions):
                continue  # the page read has a table there
            # block boxes are not trusted here (on a dense page a text block that Ground placed badly can cover
            # the region): what counts is whether the words OCR sees in the region are in the page read at all
            words, _ = ev.box_evidence(page, box)
            if len(words) < 6:
                continue
            have = sum((Counter(words) & page_tokens).values()) / len(words)
            if have >= float(0.5):
                continue  # the words are in the page read: not dropped
            out.append(Cand("dropped", box, f"dropped:{d['id']}", label=d["label"], score=d["score"]))
    if modes & {"broken", "all"}:
        paired = pairs(page)
        for d, r in paired:
            mine = find_tables(r.content)[0]
            broken = ev.shape(mine).consistent is False
            if broken and "broken" in modes or "all" in modes:
                out.append(Cand("broken" if broken else "table", tuple(d["box"]), f"table:{d['id']}", region=r, mine=mine,
                                score=d["score"]))
        if not paired and len(table_blocks) == 1 and len(find_tables(table_blocks[0].content)) == 1:
            # no usable table detection, but the page is one table: the whole page image is the crop
            r = table_blocks[0]
            mine = find_tables(r.content)[0]
            total = sum(len(norm_tokens(x.content)) for x in page.regions)
            if total and len(norm_tokens(mine)) >= 0.7 * total:
                broken = ev.shape(mine).consistent is False
                if broken and "broken" in modes or "all" in modes:
                    out.append(Cand("broken" if broken else "table", WHOLE, "table:page", region=r, mine=mine))
    return out


ALL_IF = ("spans", "header2", "empty", "fit")
_SPAN_ATTR = re.compile(r"(?:row|col)span\s*=\s*[\"']?\s*([0-9]+)", re.I)
_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.I | re.S)
_CELL_RE = re.compile(r"<(t[hd])\b[^>]*>(.*?)</t[hd]\s*>", re.I | re.S)


def all_trigger(rules: list[str], mine: str, region: Optional[Region], opts: Optional[dict] = None) -> list[str]:
    """``all_if``: which of the cheap signs that the page read's grid may be wrong this table shows
    (none = the table is not re-read). No model call; everything is read off the page read's own table.

    ``spans``    a cell with rowspan / colspan above 1 (merged cells are where two reads of a table differ);
    ``header2``  a header of more than one row (two or more leading rows of <th> cells);
    ``empty``    many empty cells (``all_empty_at``, default 0.15 of the cells): values may sit in the wrong column;
    ``fit``      the cells Ground located on the page do not stand in their columns and rows (score under
                 ``all_fit_at``, default 0.9), or under 70% of them were located."""
    opts = opts or {}
    hit = []
    if "spans" in rules and any(int(v) > 1 for v in _SPAN_ATTR.findall(mine)):
        hit.append("spans")
    rows = [[(t.lower(), c) for t, c in _CELL_RE.findall(r)] for r in _ROW_RE.findall(mine)]
    if "header2" in rules:
        lead = 0
        for r in rows:
            if r and all(t == "th" for t, _ in r):
                lead += 1
            else:
                break
        if lead >= 2:
            hit.append("header2")
    if "empty" in rules:
        cells = [c for r in rows for _, c in r]
        if cells and sum(1 for c in cells if not plain_text(c).strip()) >= float(opts.get("all_empty_at", 0.15)) * len(cells):
            hit.append("empty")
    if "fit" in rules and region is not None:
        f = ev.fit(list(region.cells))
        if f.cells and (f.located < 0.7 or (f.checked >= 4 and min(f.cols, f.rows) < float(opts.get("all_fit_at", 0.9)))):
            hit.append("fit")
    return hit


def all_if_rules(spec: Optional[dict]) -> list[str]:
    v = (spec or {}).get("all_if")
    if not v or v in (True, 1, "1"):
        return []
    rules = v if isinstance(v, list) else [x for x in str(v).split("+") if x]
    bad = [r for r in rules if r not in ALL_IF]
    if bad:
        raise ValueError(f"unknown all_if rule(s) {bad}; known: {ALL_IF}")
    return rules


def locate_cells(stage, doc: Document, page: Page, table_html: str, box) -> list:
    """Cells of ``table_html`` located on the page by Ground (word alignment inside ``box``; no model call)."""
    reg = Region(type=RegionType.TABLE, content=table_html, text=plain_text(table_html),
                 bbox=to_bbox(tuple(box), page.index))
    copy = page.model_copy(update={"regions": [reg], "words": dict(page.words), "properties": dict(page.properties)})
    try:
        stage._page(doc, copy)
    except Exception:
        return []
    return list(copy.regions[0].cells) if copy.regions else []


def _insert_at(page: Page, box) -> int:
    """Where a table the page read lacks goes: after the last block that lies above it in the same column."""
    at = None
    for k, r in enumerate(page.regions):
        if r.bbox is None:
            continue
        b = box_of(r.bbox)
        if b[3] <= box[1] + 0.01 and min(b[2], box[2]) - max(b[0], box[0]) > 0:
            at = k + 1
    return len(page.regions) if at is None else at


def table_of(text: Optional[str]) -> str:
    found = find_tables(text or "")
    return max(found, key=len) if found else ""


# ---------------------------------------------------------------------------------------------- rules
def decide_missed(old_text: str, cand: str, fit: ev.Fit, opts: dict) -> tuple[bool, str, dict]:
    """Running text of the page read -> table. All of: the model says it is a table; a real grid (3+ rows, 2+
    columns that are filled in most rows, consistent); short cells (a paragraph is not a cell); the table says
    what the page read says (nothing added, nothing lost); and the cells Ground located on the page stand in
    columns (each cell in the band of its own column) and rows."""
    if not cand:
        return False, "the re-read says it is not a table", {}
    sh = ev.shape(cand)
    ot, nt = norm_tokens(old_text), ev.tokens(cand)
    from collections import Counter
    hit = sum((Counter(ot) & Counter(nt)).values())
    keep, prec = hit / max(len(ot), 1), hit / max(len(nt), 1)
    detail = {"rows": sh.rows, "cols": sh.cols, "filled_cols": sh.filled_cols, "consistent": sh.consistent,
              "median_cell_words": sh.median_cell_words, "longest_cell_words": sh.longest_cell_words,
              "words_kept": round(keep, 3), "words_precise": round(prec, 3), "fit": fit.as_dict()}
    if sh.consistent is not True or sh.rows < 3 or sh.cols < 2 or sh.filled_cols < 2:
        return False, "the re-read is not a grid of 3+ rows and 2+ filled columns", detail
    if sh.median_cell_words > float(opts.get("missed_median_words", 12)) or \
            sh.longest_cell_words > int(opts.get("missed_longest_words", 60)):
        return False, "cells as long as paragraphs: running text, not a table", detail
    if keep < float(opts.get("missed_keep", 0.95)) or prec < float(opts.get("missed_precision", 0.9)):
        return False, "the re-read does not hold the text of the page read", detail
    if fit.located < float(opts.get("missed_located", 0.7)) or fit.checked < 4:
        return False, "too few cells located on the page to check the columns", detail
    if fit.cols < float(opts.get("missed_fit", 0.9)) or fit.rows < float(opts.get("missed_fit_rows", 0.5)):
        return False, "the located cells do not stand in columns and rows", detail
    return True, "layout shows a table; the re-read is a grid with the page read's text, cells stand in columns", detail


def decide_dropped(cand: str, evidence: list[str], page_tokens: Counter, fit: ev.Fit, opts: dict) -> tuple[bool, str, dict]:
    """Nothing in the page read -> table. The crop read must be a grid, its words must be the words local OCR sees in
    the box (not invented), must not already stand in the page read (no duplicate), and its cells must be found
    on the page."""
    if not cand:
        return False, "the re-read says it is not a table", {}
    sh = ev.shape(cand)
    nt = ev.tokens(cand)
    hit_ev = sum((Counter(nt) & Counter(evidence)).values())
    prec, rec = hit_ev / max(len(nt), 1), hit_ev / max(len(evidence), 1)
    dup = sum((Counter(nt) & page_tokens).values()) / max(len(nt), 1)
    detail = {"rows": sh.rows, "cols": sh.cols, "consistent": sh.consistent, "ocr_precision": round(prec, 3),
              "ocr_recall": round(rec, 3), "in_page_read": round(dup, 3), "fit": fit.as_dict()}
    if sh.consistent is False or sh.rows < 2 or sh.cols < 2:
        return False, "the re-read is not a grid", detail
    if prec < float(opts.get("dropped_precision", 0.6)) or rec < float(opts.get("dropped_recall", 0.5)):
        return False, "the re-read does not hold the words local OCR sees there", detail
    if fit.located < float(opts.get("dropped_located", 0.5)):
        return False, "too few cells of the re-read are found on the page", detail
    return True, "layout shows a table the page read lacks; the crop read holds the words OCR sees there", detail


def _f1(mine: Counter, layer: Counter) -> float:
    hit = sum((mine & layer).values())
    n, m = sum(mine.values()), sum(layer.values())
    if not hit or not n or not m:
        return 0.0
    p, r = hit / n, hit / m
    return 2 * p * r / (p + r)


def layer_numbers(page: Page, box) -> Optional[Counter]:
    """Numbers of the exact PDF text layer inside ``box``; None when the page has no exact text layer."""
    from ..common import words_in
    from ..reading.tables import number_tokens
    if not (page.properties.get("text_layer_exact") and page.words.get("pdf")):
        return None
    ws = page.words["pdf"]
    return Counter(n for i in words_in(ws, to_bbox(tuple(box), page.index)) for n in number_tokens(ws[i].text))


def decide_table(kind: str, mine: str, cand: str, fit_mine: ev.Fit, fit_cand: ev.Fit, accept: str, opts: dict
                 ) -> tuple[bool, str, dict]:
    """Page-read table -> table-only read.

    ``content``  the two reads hold the same numbers and (nearly) the same words, and the re-read's grid is
                 consistent: no character changes on trust, only the grid;
    ``fit``      ``content`` and the re-read's cells are found on the page and stand in their columns and rows at
                 least as well as the page read's;
    ``strict``   ``content`` and the re-read's cells fit strictly better;
    ``evidence`` ``content``, and where numbers differ the re-read is taken only on a born-digital page whose exact
                 text layer agrees better with the re-read's numbers;
    ``blind``    a consistent grid of comparable size is enough (reference only: this is the swap that lost).
    A broken page-read table (``kind`` broken) needs ``content`` only: its own grid is the evidence against it."""
    if not cand:
        return False, "no table in the re-read", {}
    a, b = clean_table(mine), clean_table(cand)
    sm, sc = ev.shape(a), ev.shape(b)
    c = ev.content(a, b)
    detail = {"grid_ours": sm.consistent, "grid_new": sc.consistent, "rows": [sm.rows, sc.rows], "cols": [sm.cols, sc.cols],
              "numbers": [round(c.number_recall, 3), round(c.number_precision, 3)],
              "words": [round(c.word_recall, 3), round(c.word_precision, 3)],
              "fit_ours": fit_mine.as_dict(), "fit_new": fit_cand.as_dict()}
    if plain_text(a).split() == plain_text(b).split() and sm.consistent == sc.consistent and \
            (sm.rows, sm.cols) == (sc.rows, sc.cols) and a.count("span") == b.count("span"):
        return False, "same table", detail
    if sc.consistent is not True:
        return False, "re-read grid is not consistent", detail
    if b.count("[illegible") > a.count("[illegible"):
        return False, "re-read left cells unread", detail
    if sc.rows * 2 < sm.rows or sc.rows > 2 * max(sm.rows, 1) + 2:
        return False, "re-read has a very different number of rows", detail
    if accept == "blind":
        return True, "consistent grid (accepted without evidence)", detail
    if c.number_recall < float(opts.get("numbers_at", 1.0)) or c.number_precision < float(opts.get("numbers_at", 1.0)):
        # ``evidence``: where the two reads differ in a number, the exact text layer of a born-digital page decides.
        # (Local OCR of a scan is not exact enough to decide a digit: scans keep the page read's numbers.)
        layer = opts.get("_layer")
        if accept == "evidence" and layer is not None and len(layer) >= int(opts.get("layer_min", 8)):
            fm, fc = _f1(ev.numbers(a), layer), _f1(ev.numbers(b), layer)
            detail["layer_f1"] = [round(fm, 3), round(fc, 3)]
            if fc > fm and min(c.word_recall, c.word_precision) >= float(opts.get("layer_words_at", 0.9)):
                return True, "numbers differ; the re-read agrees better with the exact text layer", detail
        return False, "numbers differ from the page read", detail
    if c.word_recall < float(opts.get("words_at", 0.95)) or c.word_precision < float(opts.get("words_at", 0.95)):
        return False, "words differ from the page read", detail
    if kind == "broken":
        return True, "page-read grid broken; re-read consistent with the same cell text", detail
    if accept in ("content", "evidence"):
        return True, "same cell text, consistent grid", detail
    if fit_cand.located + 0.02 < fit_mine.located or fit_cand.located < float(opts.get("located", 0.6)):
        return False, "fewer cells of the re-read are found on the page", detail
    eps = float(opts.get("fit_eps", 0.02))
    if accept == "strict":
        if fit_cand.score <= fit_mine.score + eps:
            return False, "re-read cells do not fit the page better", detail
        return True, "same cell text; cells fit the columns and rows of the page better", detail
    if fit_cand.score + eps < fit_mine.score:
        return False, "re-read cells fit the page worse", detail
    return True, "same cell text; cells fit the columns and rows of the page at least as well", detail


def _read_jobs(doc: Document, page: Page, jobs: list, reader_spec: str, pad: float) -> list:
    """``crops.read_many``, except that a read the Read stage already started (prefetch.py) is waited for instead of
    made again. The result is checked and billed exactly as ``crops.read_crop`` does."""
    from ..reading.robust import detect_problems
    from . import prefetch
    out: list = [None] * len(jobs)
    rest = []
    for k, (box, prompt, tag) in enumerate(jobs):
        fut = prefetch.take(doc, page, box, prompt)
        if fut is None:
            rest.append(k)
            continue
        try:
            res = fut.result()
        except Exception as e:
            doc.note("tables", f"page {page.index}: prefetched table read ({tag}) unavailable ({str(e)[:120]})", "warning")
            continue
        doc.usage.append({"page": page.index, "reader": crops.reader_for(reader_spec).name, "escalation": "region",
                          "reason": tag, "prefetched": True, **res.usage.model_dump(exclude_none=True)})
        if res.refused or res.truncated or any(p.fatal for p in detect_problems(res.text, res.stop_reason, res.refused,
                                                                                res.truncated) if p.kind != "empty"):
            doc.note("tables", f"page {page.index}: prefetched table read ({tag}) is broken; ignored", "warning")
            continue
        out[k] = res
    if rest:
        for k, res in zip(rest, crops.read_many(doc, page, [jobs[k] for k in rest], reader_spec, pad=pad)):
            out[k] = res
    return out


# ---------------------------------------------------------------------------------------------- page driver
def run(stage, doc: Document, page: Page, spec: dict[str, Any], reader_spec: str) -> bool:
    modes = set(spec.get("reread") or [])
    found = find(page, modes, spec)
    min_area = float(spec.get("min_area", 0.0))  # ``all`` only for tables that take up this share of the page
    if min_area > 0:
        found = [c for c in found if c.kind != "table" or area(c.box) >= min_area]
    rules = all_if_rules(spec)  # ``all`` only for tables that show a sign of a wrong grid
    if rules:
        kept, skipped = [], 0
        for c in found:
            if c.kind == "table":
                why = all_trigger(rules, c.mine, c.region, spec)
                if not why:
                    skipped += 1
                    continue
                c.tag_why = why
            kept.append(c)
        found = kept
        page.properties["table_all_if"] = {"rules": rules, "skipped": skipped,
                                           "read": sum(1 for c in found if c.kind == "table")}
    cands = [c for c in found if c.kind != "dropped"][:int(spec.get("max_tables", 6))] + \
            [c for c in found if c.kind == "dropped"][:int(spec.get("max_dropped", 30))]
    if not cands:
        return False
    accept = str(spec.get("accept", "content"))
    prompt = PROMPTS[str(spec.get("prompt", "clean"))]
    # a region that takes up most of the page is read from the whole page image: a detection box that is a little
    # too small would cut rows or columns off the crop (seen on RD-TableBench: 4 of 100 crops lost rows)
    whole_at = float(spec.get("whole_at", 0.5))
    # ``missed_prompt=force``: the table prompt without the way out (NOT_A_TABLE); the rules alone then decide
    maybe = prompt if spec.get("missed_prompt") == "force" else prompts.TABLE_MAYBE_FILE
    jobs = [(WHOLE if area(c.box) >= whole_at else c.box, maybe if c.kind in ("missed", "dropped") else prompt,
             f"tread:{c.tag}") for c in cands]
    results = _read_jobs(doc, page, jobs, reader_spec, float(spec.get("pad", 0.012)))
    report, plan = [], []
    for c, res in zip(cands, results):
        entry: dict[str, Any] = {"kind": c.kind, "tag": c.tag, "label": c.label, "score": round(c.score, 3),
                                 "box": [round(v, 4) for v in c.box]}
        if c.tag_why:
            entry["all_if"] = c.tag_why
        report.append(entry)
        if res is None:
            entry["skipped"] = "re-read unavailable"
            continue
        cand = table_of(res.text)
        if spec.get("keep"):
            entry["candidate"] = cand or res.text[:200]
            entry["mine"] = c.mine or "\n\n".join(page.regions[i].content for i in c.idx)
        fit_c = ev.fit(locate_cells(stage, doc, page, cand, c.box)) if cand else ev.Fit()
        if c.kind == "dropped":
            words, src = ev.box_evidence(page, c.box)
            ok, why, detail = decide_dropped(cand, words, Counter(t for r in page.regions for t in norm_tokens(r.content)),
                                             fit_c, spec)
            detail["evidence"] = src
        elif c.kind == "missed":
            old = "\n\n".join(page.regions[i].content for i in c.idx)
            ok, why, detail = decide_missed(old, cand, fit_c, spec)
        else:
            fit_m = ev.fit(list(c.region.cells))
            ok, why, detail = decide_table(c.kind, c.mine, cand, fit_m, fit_c, accept,
                                           {**spec, "_layer": layer_numbers(page, c.box) if accept == "evidence" else None})
        entry.update({"replaced": ok, "reason": why, **detail})
        if ok:
            plan.append((c, cand, why))
    changed = False
    for c, cand, why in plan:
        if c.kind != "dropped":
            continue
        new = Region(type=RegionType.TABLE, content=cand, text=plain_text(cand), bbox=to_bbox(c.box, page.index),
                     source="tables:dropped", candidates=[Candidate(source="tables:table_only", content=cand, meta={"selected": True})],
                     meta={"bbox_source": "layout", "layout_type": "Table", "table_reread": why, "tier": "strong"})
        page.regions.insert(_insert_at(page, c.box), new)
        changed = True
        doc.note("tables", f"page {page.index}: table the page read lacks added from its crop read")
    idx_of = {id(r): k for k, r in enumerate(page.regions)}
    missed = [(c, cand, why, [idx_of[id(r)] for r in c.blocks]) for c, cand, why in plan if c.kind == "missed"]
    for c, cand, why, idx in sorted(missed, key=lambda p: -p[3][0]):  # from the end, so indices stay valid
        old = "\n\n".join(page.regions[i].content for i in idx)
        new = Region(type=RegionType.TABLE, content=cand, text=plain_text(cand), bbox=to_bbox(c.box, page.index),
                     source="tables:missed", candidates=[Candidate(source="tables:table_only", content=cand, meta={"selected": True}),
                                                         Candidate(source="reader", content=old, meta={"replaced": True})],
                     meta={"bbox_source": "layout", "layout_type": "Table", "table_reread": why, "tier": "strong"})
        page.regions[idx[0]:idx[-1] + 1] = [new]
        changed = True
        doc.note("tables", f"page {page.index}: text replaced by the table-only read of its region ({why})")
    for c, cand, why in plan:
        if c.kind in ("missed", "dropped"):
            continue
        r = c.region
        r.candidates.append(Candidate(source="tables:table_only", content=cand, meta={"selected": True}))
        r.candidates.append(Candidate(source=r.source or "reader", content=r.content, meta={"replaced": True}))
        r.content = r.content.replace(c.mine, cand)
        r.text = plain_text(r.content)
        r.meta["table_reread"] = why
        changed = True
        doc.note("tables", f"page {page.index}: {c.kind} table replaced by its table-only read ({why})")
    for k, r in enumerate(page.regions):
        r.order = k
    page.properties["table_reads"] = report
    return changed
