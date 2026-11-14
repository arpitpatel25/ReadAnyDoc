"""Tables. Every piece is a switch, off by default.

Switching on: ground stage option ``tables`` or the environment variable ``READANYDOC_TABLES`` (which wins), a spec
such as ``reread=missed;clean=1;merge=layout``:

    reread=missed+dropped+broken+all   table-only re-reads of table regions (reread.py); several kinds joined by ``+``
    accept=content|evidence|fit|strict|blind   the rule for replacing a page-read table (``all``); default ``content``
    prompt=clean|only|strict   the table-only prompt (readers/prompts.py); default ``clean``
    min_area=0.5               ``all`` only for tables that cover this share of the page (read from the whole page)
    clean=1                    cell hygiene (hygiene.py): no picture descriptions and no list markup in cells
    merge=layout|cont          a table the read split in two is joined again (hygiene.py)
    formulas=tag+matrix+lines  displayed formulas (reading/formulas.py): equation numbers as \\tag, a matrix the read
                               wrote as a table and formulas printed over several lines read again from the crop
    all_if=spans+header2+empty+fit   ``all`` re-reads only the tables whose page read shows one of these
                               signs (reread.all_trigger); such reads cannot start before the page read
    prefetch=1                 the table-only reads of layout table regions start together with the page read
                               (prefetch.py): same output, a table page waits max(page read, table read)
    keep=1                     keep every candidate table in ``page.properties["table_reads"]`` (analysis)
Any other ``key=value`` is passed to the rules as a threshold (e.g. ``words_at=0.95``). ``missed_table_score`` /
``missed_index_score`` (defaults 0.5 / 0.6) are the layout scores a region needs to be a ``missed`` candidate: with
0.2 the rules accepted two list-like text regions on the development set, so the score is part of the rule.

Runs inside ``GroundRegions`` on the grounded page, before the layout switches (types, boxes, split), so those
see the final content. It needs the layout detections for ``missed`` and ``merge``; ``clean`` needs nothing.
"""

from __future__ import annotations

import os
from typing import Any, Union

from ...model import Document, Page
from . import hygiene, reread

ENV = "READANYDOC_TABLES"
REREAD_MODES = ("missed", "dropped", "broken", "all")
ACCEPT = ("fit", "strict", "content", "evidence", "blind")


def _value(v: str) -> Any:
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return v


def parse_spec(spec: Union[str, dict, None]) -> dict[str, Any]:
    if not spec:
        return {}
    if isinstance(spec, dict):
        items = list(spec.items())
    else:
        items = []
        for part in str(spec).split(";"):
            part = part.strip()
            if part:
                k, _, v = part.partition("=")
                items.append((k.strip(), v.strip() or "1"))
    out: dict[str, Any] = {}
    for k, v in items:
        if k == "reread":
            modes = v if isinstance(v, list) else [m for m in str(v).split("+") if m and m not in ("0", "off", "false")]
            bad = [m for m in modes if m not in REREAD_MODES]
            if bad:
                raise ValueError(f"unknown reread kind(s) {bad}; known: {REREAD_MODES}")
            if modes:
                out[k] = modes
        elif k == "formulas":
            modes = v if isinstance(v, list) else [m for m in str(v).split("+") if m and m not in ("0", "off", "false")]
            bad = [m for m in modes if m not in ("tag", "matrix", "lines")]
            if bad:
                raise ValueError(f"unknown formulas mode(s) {bad}; known: tag, matrix, lines")
            if modes:
                out[k] = modes
        elif k == "accept":
            if v not in ACCEPT:
                raise ValueError(f"unknown accept rule {v!r}; known: {ACCEPT}")
            out[k] = v
        elif k == "prompt":
            if v not in reread.PROMPTS:
                raise ValueError(f"unknown table prompt {v!r}; known: {sorted(reread.PROMPTS)}")
            out[k] = v
        elif k == "merge":
            v = "layout" if v in ("1", "true", True) else v
            if v in ("0", "off", "false", False):
                continue
            if v not in ("layout", "cont"):
                raise ValueError(f"unknown merge rule {v!r}; known: layout, cont")
            out[k] = v
        elif k == "all_if":
            rules = v if isinstance(v, list) else [m for m in str(v).split("+") if m and m not in ("0", "off", "false")]
            bad = [m for m in rules if m not in reread.ALL_IF]
            if bad:
                raise ValueError(f"unknown all_if rule(s) {bad}; known: {reread.ALL_IF}")
            if rules:
                out[k] = rules
        elif k in ("clean", "keep", "prefetch"):
            if v is True or str(v).lower() not in ("0", "false", "off", ""):
                out[k] = True
        else:
            out[k] = _value(v) if isinstance(v, str) else v
    return out


def spec_from(stage_option: Union[str, dict, None]) -> dict[str, Any]:
    env = os.environ.get(ENV)
    return parse_spec(env if env is not None else stage_option)


def run_page(stage, doc: Document, page: Page, spec: dict[str, Any], reader_spec: str) -> bool:
    """Applies the switches that are on to one grounded page. True when content changed (ground again)."""
    try:
        return _run_page(stage, doc, page, spec, reader_spec)
    finally:
        if spec.get("prefetch"):
            from . import prefetch
            prefetch.settle(doc, page)  # started and not used: still billed


def _run_page(stage, doc: Document, page: Page, spec: dict[str, Any], reader_spec: str) -> bool:
    changed = False
    if spec.get("formulas"):
        from ..reading import formulas
        if formulas.run(doc, page, set(spec["formulas"]), reader_spec, spec):
            changed = True
            stage._page(doc, page)
    if spec.get("reread"):
        if reread.run(stage, doc, page, spec, reader_spec):
            changed = True
            stage._page(doc, page)  # boxes of the new tables for the steps below
    if spec.get("merge"):
        joined = hygiene.merge_page(page, spec["merge"])
        if joined:
            page.properties["table_joins"] = joined
            changed = True
    if spec.get("clean"):
        n = hygiene.clean_page(page)
        if n:
            page.properties["table_hygiene"] = {"blocks": n}
            changed = True
    return changed
