"""Verify checks (final experiment round). Each check is a switch that is off by default.

The page read stays. A check changes it only where independent evidence shows a fault, and re-reads
only that part: ``grid`` (table rows of different widths), ``complete`` (text that the PDF text layer
or OCR shows and the read lacks), ``numbers`` (printed totals that do not reconcile), ``flags``
(unresolved checks lower the block's confidence and are listed in ``extra.checks``).

Switching on: Verify stage option ``checks`` or the environment variable ``READANYDOC_VERIFY_CHECKS``,
a spec such as ``grid=norm+draft;complete=text+scan;numbers=1;flags=1`` (see ``parse_spec``). Any other
``key=value`` is passed to the checks as an option (e.g. ``grid_numbers_at=0.97``).
"""

from __future__ import annotations

import os
from typing import Any, Optional, Union

from ...model import Document, Page
from . import complete, flags, gridrepair, numbers
from .common import PageChecks

ENV = "READANYDOC_VERIFY_CHECKS"
SWITCHES = ("grid", "complete", "numbers", "flags")
GRID_MODES = ("norm", "pad", "crop", "draft", "pre")
COMPLETE_MODES = ("text", "crop", "scan")


def _value(v: str) -> Any:
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def parse_spec(spec: Union[str, dict, None]) -> dict[str, Any]:
    """``grid=norm+crop;numbers=1`` -> {"grid": ["norm", "crop"], "numbers": True, ...}. Unknown modes raise."""
    if not spec:
        return {}
    if isinstance(spec, dict):
        items = list(spec.items())
    else:
        items = []
        for part in str(spec).replace(",", ";").split(";"):
            part = part.strip()
            if part:
                k, _, v = part.partition("=")
                items.append((k.strip(), v.strip() or "1"))
    out: dict[str, Any] = {}
    for k, v in items:
        if k in ("grid", "complete"):
            modes = v if isinstance(v, list) else [m for m in str(v).split("+") if m and m not in ("0", "false", "off")]
            allowed = GRID_MODES if k == "grid" else COMPLETE_MODES
            bad = [m for m in modes if m not in allowed]
            if bad:
                raise ValueError(f"unknown {k} mode(s) {bad}; known: {allowed}")
            if modes:
                out[k] = modes
        elif k in ("numbers", "flags"):
            on = v if isinstance(v, bool) else str(v).lower() not in ("0", "false", "off", "")
            if on:
                out[k] = True
        else:
            out[k] = _value(v) if isinstance(v, str) else v
    return out


def spec_from(stage_option: Union[str, dict, None]) -> dict[str, Any]:
    """The stage option wins; else the environment variable; else nothing (all checks off)."""
    return parse_spec(stage_option if stage_option else os.environ.get(ENV))


def run_page(doc: Document, page: Page, spec: dict[str, Any], reader_spec: str,
             ground_options: Optional[dict] = None) -> None:
    if not spec or not page.regions:
        return
    pc = PageChecks(doc=doc, page=page, reader_spec=spec.get("reader") or reader_spec,
                    ground_options=dict(ground_options or {}), options=spec)
    ran: set[str] = set()
    if spec.get("grid"):
        gridrepair.run(pc, spec["grid"])
        ran.add("grid")
    if spec.get("numbers"):
        numbers.run(pc)
        ran.add("numbers")
    if spec.get("complete"):
        complete.run(pc, spec["complete"])
        ran.add("complete")
    if spec.get("flags"):
        flags.detect(pc, ran)
        flags.apply(pc)
    if pc.calls:
        page.properties["check_calls"] = pc.calls
