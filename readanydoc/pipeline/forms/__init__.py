"""Forms. Every idea is a switch that is OFF by default.

Switches (stage option ``read.forms`` as a list, or ``READANYDOC_FORMS=a,b`` in the environment):

* ``fillable``    a PDF with form fields: field values and checkbox / radio states are read from the file and
                  used as exact evidence (``fillable.py``): a mark the read got wrong is corrected, a value the
                  read misread is corrected, a boxed section the read wrote as a table becomes a Key Value
                  block, and a located block that holds a field is typed Key Value.
* ``kv_rules``    flat and scanned forms, no model call (``grid.py``): a table whose cells are self-contained
                  fields is written as a Key Value block; data tables are never touched.
                  ``kv_rules:rows`` writes one Key Value block per row of the grid instead of one per table.
* ``kv_guard``    ``kv_rules`` leaves a table alone where the layout model detects a Table region with a
                  good score around the table's words (``rules.layout_table``), or where a column holds amounts
                  with a unit ("0.00 zł") in three or more rows (``grid.is_amount``): a real data table stays a table.
* ``kv_retype``   same decision as ``kv_rules`` but only the block type changes, the HTML table stays.
* ``kv_lines``    label lines the read left as plain text (``lines.py``): a label that ends in a colon takes the
                  short block after it as its value; `CAPS LABEL value` lines on a form page are typed Key Value.
                  ``kv_lines:join`` / ``kv_lines:caps`` run only the first / second half.
* ``region_reread`` the form-like region of a page is read again from a crop with the form prompt and
                  replaces only that region (``region.py``).
* ``checkbox_crops`` every group of checkboxes is read again from an enlarged crop; a mark changes only
                  when two crop reads agree against the page read (``checkbox.py``).

The page read is kept everywhere else. Whatever is replaced stays in ``Region.candidates``.
``READANYDOC_FORMS_LOG=<file>`` appends one JSON line per page with what each switch did.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from ...model import Document, Page

KNOWN = ("fillable", "kv_rules", "kv_retype", "kv_lines", "region_reread", "checkbox_crops", "kv_guard")
_LOG_LOCK = threading.Lock()


def switches(stage: Any = None) -> set[str]:
    opt = stage.opt("forms") if stage is not None else None
    if isinstance(opt, str):
        opt = opt.split(",")
    env = os.environ.get("READANYDOC_FORMS", "")
    names = {s.strip() for s in list(opt or []) + env.split(",") if s and s.strip()}
    return {s for s in names if s.split(":")[0] in KNOWN}


def _log(doc: Document, page: Page, info: dict) -> None:
    path = os.environ.get("READANYDOC_FORMS_LOG")
    if not path or not info:
        return
    row = {"file": os.path.basename(str(doc.meta.get("filename") or doc.meta.get("path") or doc.source)),
           "stem": doc.stem, "page": page.original_page, **info}
    with _LOG_LOCK, open(path, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def apply_page(doc: Document, page: Page, stage: Any) -> None:
    """Called by the Read stage once the page's regions exist (content, no boxes yet)."""
    on = switches(stage)
    if not on or not page.regions:
        return
    info: dict = {}
    page.properties["forms_switches"] = sorted(on)
    try:
        if "fillable" in on:
            from . import fillable
            info["fillable"] = fillable.apply(doc, page)
        if "region_reread" in on:
            from . import region
            info["region_reread"] = region.apply(doc, page, stage)
        if on & {"kv_rules", "kv_rules:rows", "kv_retype"}:
            from . import rules
            info["kv_rules"] = rules.apply(page, retype_only=not on & {"kv_rules", "kv_rules:rows"},
                                           by_row="kv_rules:rows" in on, guard="kv_guard" in on)
        if any(s.split(":")[0] == "kv_lines" for s in on):
            from . import lines
            part = next((s.split(":", 1)[1] for s in on if s.startswith("kv_lines:")), "")  # "join" | "caps" | both
            info["kv_lines"] = lines.apply(page, join=part != "caps", caps=part != "join")
        if "checkbox_crops" in on:
            from . import checkbox
            info["checkbox_crops"] = checkbox.apply(doc, page, stage)
    except Exception as e:  # a forms step must never lose the page read
        doc.note("read", f"page {page.index}: forms step failed ({type(e).__name__}: {str(e)[:160]}); page read kept",
                 "warning")
        info["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    info = {k: v for k, v in info.items() if v}
    if info:
        page.properties["forms"] = info
    _log(doc, page, info)


def retype_located(page: Page, before: dict) -> None:
    """Called at the end of ``typing.layout.retype_page``: typing that needs the blocks' boxes."""
    if "fillable" in (page.properties.get("forms_switches") or []) and page.properties.get("form_fields"):
        from . import fillable
        fillable.retype_located(page, before)
