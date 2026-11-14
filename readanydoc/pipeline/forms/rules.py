"""``kv_rules``: a table that is a form grid becomes a Key Value block (``grid.classify`` decides)."""

from __future__ import annotations

from ...model import Candidate, Page, Region, RegionType
from ...textnorm import plain_text
from . import grid


def convert_region(r: Region, why: str, retype_only: bool = False, by_row: bool = False) -> list[Region]:
    """The Key Value block(s) for a Table region whose tables are all form grids. Text around the
    tables stays where it was."""
    if retype_only:
        r.meta["forms"] = {"rule": "table_is_form_grid", "why": why, "content": "unchanged"}
        r.type = RegionType.KEY_VALUE
        return [r]
    parts: list[str] = []
    for kind, p in grid.split_content(r.content):
        parts.extend((grid.to_key_value_groups(p) if by_row else [grid.to_key_value(p)]) if kind == "table" else [p])
    parts = [p for p in parts if p.strip()]
    if not parts:
        return [r]
    if by_row and len(parts) > 1:  # ``kv_rules:rows``: one Key Value block per row of the form grid
        keep = {k: v for k, v in r.meta.items() if k in ("tier", "layout_region", "search_bbox")}
        out = [Region(type=RegionType.KEY_VALUE, content=p, text=plain_text(p), source=r.source,
                      native_label=r.native_label, candidates=[Candidate(source=r.source or "reader", content=p)],
                      meta={**keep, "forms": {"rule": "table_to_key_value", "why": why, "piece": k}})
               for k, p in enumerate(parts)]
        out[0].candidates.extend(c.model_copy() for c in r.candidates)
        out[0].candidates.append(Candidate(source=r.source or "reader", content=r.content,
                                           meta={"replaced": True, "by": "forms.kv_rules"}))
        return out
    content = "\n".join(parts)
    new = Region(type=RegionType.KEY_VALUE, content=content, text=plain_text(content), source=r.source,
                 native_label=r.native_label, candidates=[c.model_copy() for c in r.candidates],
                 meta={**{k: v for k, v in r.meta.items() if k in ("tier", "layout_region", "search_bbox")},
                       "forms": {"rule": "table_to_key_value", "why": why}})
    new.candidates.append(Candidate(source=r.source or "reader", content=r.content,
                                    meta={"replaced": True, "by": "forms.kv_rules"}))
    return [new]


GUARD_SCORE = 0.5     # a table detection the layout model is at least this sure of
GUARD_LOCATED = 0.6   # share of the table's words (of those found on the page at all) that lie inside it


def layout_table(page: Page, table_html: str, n_tables: int) -> dict | None:
    """``kv_guard``: independent evidence that this table of the page read is a real table. The layout
    model, which looks at the picture and never saw the read, detects a Table region there with a good score.
    ``grid.classify`` judges the cells' text only, and a data table whose cells start with a count
    ("5 Council Directors") or a check box reads like a grid of form fields.

    The table has no box yet (this runs before grounding), so it is placed by its words: of the table's tokens that
    the page's independent words (text layer or local OCR) contain at all, the share inside the detection. With too
    few such words the detection counts only when the page read has a single table. Returns the detection or None."""
    dets = [d for d in page.properties.get("layout_detections") or []
            if d.get("label") == "table" and d.get("score", 0) >= GUARD_SCORE]
    if not dets:
        return None
    from collections import Counter

    from ..tables import evidence
    mine = Counter(evidence.tokens(table_html))
    everywhere = Counter(evidence.box_evidence(page, (0.0, 0.0, 1.0, 1.0))[0])
    on_page = sum((mine & everywhere).values())
    best = max(dets, key=lambda d: d["score"])
    if on_page < 5:
        return dict(best, located=None) if n_tables == 1 else None
    for d in sorted(dets, key=lambda d: -d["score"]):
        inside = sum((mine & Counter(evidence.box_evidence(page, tuple(d["box"]))[0])).values())
        if inside >= GUARD_LOCATED * on_page:
            return dict(d, located=round(inside / on_page, 3))
    return None


def apply(page: Page, retype_only: bool = False, by_row: bool = False, guard: bool = False) -> dict:
    out: list[Region] = []
    converted, seen, kept = [], 0, []
    n_tables = sum(1 for r in page.regions if r.type == RegionType.TABLE
                   for kind, _ in grid.split_content(r.content) if kind == "table")
    for r in page.regions:
        tables = [p for kind, p in grid.split_content(r.content) if kind == "table"] \
            if r.type == RegionType.TABLE else []
        if not tables:
            out.append(r)
            continue
        seen += len(tables)
        verdicts = [grid.classify(t, amounts=guard) for t in tables]
        if all(v.kind == "form" for v in verdicts):
            det = next((d for d in (layout_table(page, t, n_tables) for t in tables) if d), None) if guard else None
            if det is not None:  # the layout model sees a table here: the read's table stays as it is
                r.meta["forms"] = {"rule": "kv_guard", "why": "layout table", "score": det["score"],
                                   "located": det.get("located"), "verdict": verdicts[0].reason}
                kept.append({"score": det["score"], "located": det.get("located"), "verdict": verdicts[0].reason})
                out.append(r)
                continue
            out.extend(convert_region(r, "; ".join(v.reason for v in verdicts), retype_only, by_row))
            converted.append(verdicts[0].reason)
        else:
            out.append(r)
    page.regions = out
    info = {"tables": seen, "converted": len(converted), "reasons": converted} if converted else {}
    if kept:
        info = {**(info or {"tables": seen, "converted": 0}), "kept_by_layout": kept}
    return info
