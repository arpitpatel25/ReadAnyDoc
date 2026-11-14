"""Layout regions after grounding. Every idea is a switch, off by default.

The Layout stage (option ``detect_only``) stores the detector's regions in
``page.properties["layout_detections"]``. ``GroundRegions`` (stage implementation ``ground_regions``) grounds
the page exactly as ``Ground`` does and then applies the switches that are on:

    types=layout     block types from the matched layout region            (structure.py)
    boxes            block box from the matched layout region              (structure.py)
    split            text blocks cut at line breaks between layout regions (structure.py)
    tables=<rule>    detected tables re-read from a crop, accepted by rule (tables.py: grid | numbers | broken | broken_numbers)
    forms[=notables] form-like regions re-read with a form prompt, guarded (forms.py); ``notables``: not where
                     the page read has a table
    assemble         the page built only from parallel region reads        (assemble.py)
    complete         text regions missing from the output re-read          (complete.py)
    route=tables|tables+forms|full   only table (and form) regions are read from crops and the page read
                     stays for the rest; ``full``: the whole page from region reads (= assemble) but only on pages
                     that pass ``route_min``. ``route_min=0.3``: only where those regions cover that share of
                     the page; ``route_guard``: a crop read must hold the page read's words
    deny=A>B|C>D     type changes that ``types`` must not make ("*" = any type on one side)
    allow=A>B|C>D    the only type changes that ``types`` may make
    hfsplit, kvsplit, kv=form|page, formread, figures, boxfill   form fields and block structure
                     (fields.py, formevidence.py; all off by default)

Switches come from the ground stage option ``regions`` (same grammar, or a dict) and from the environment
variable ``READANYDOC_REGIONS`` (which wins). A page whose content changed is grounded again.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

from ...model import Document
from ..base import PipelineContext, register_stage
from ..ground import Ground
from . import assemble as _assemble
from . import complete as _complete
from . import crops as _crops
from . import fields as _fields
from . import forms as _forms
from . import structure as _structure
from . import tables as _tables
from .crops import crop_png, read_crop
from .match import blocks_in, checkbox_marks, detect_pages, detections, form_regions

__all__ = ["Switches", "switches", "switches_on", "detect_pages", "detections", "form_regions", "checkbox_marks",
           "blocks_in", "crop_png", "read_crop", "GroundRegions"]

TABLE_MODES = ("grid", "numbers", "broken", "broken_numbers")


@dataclass
class Switches:
    types: Optional[str] = None
    boxes: bool = False
    split: bool = False
    tables: Optional[str] = None
    forms: Optional[str] = None
    assemble: bool = False
    complete: bool = False
    deny: frozenset = field(default_factory=frozenset)
    allow: Optional[frozenset] = None
    more: dict = field(default_factory=dict)   # switch name -> value
    # tables | tables+forms | full; see the module docstring
    route: Optional[str] = None
    route_min: float = 0.0  # only on pages where the routed regions cover this share of the page
    route_guard: bool = False

    def any(self) -> bool:
        return bool(self.types or self.boxes or self.split or self.tables or self.forms or self.assemble or self.complete
                    or self.more or self.route)

    @classmethod
    def parse(cls, value: Any) -> "Switches":
        s = cls()
        if not value:
            return s
        if isinstance(value, dict):
            items = [(k, v) for k, v in value.items()]
        else:
            items = []
            for part in str(value).split(","):
                part = part.strip()
                if part:
                    k, eq, v = part.partition("=")
                    items.append((k.strip(), v.strip() if eq else True))
        for k, v in items:
            if v in (False, None, "", "off", "0"):
                continue
            if k == "types":
                s.types = "layout" if v is True else str(v)
            elif k == "tables":
                s.tables = "grid" if v is True else str(v)
                if s.tables not in TABLE_MODES:
                    raise ValueError(f"unknown tables rule {s.tables!r}; known: {TABLE_MODES}")
            elif k == "allow":
                s.allow = frozenset(x.strip() for x in (v if isinstance(v, (list, tuple, set, frozenset)) else str(v).split("|")) if str(x).strip())
            elif k == "deny":
                s.deny = frozenset(x.strip() for x in (v if isinstance(v, (list, tuple, set, frozenset)) else str(v).split("|")) if str(x).strip())
            elif k == "forms":
                s.forms = "on" if v is True else str(v)
                if s.forms not in ("on", "notables"):
                    raise ValueError(f"unknown forms rule {s.forms!r}; known: on, notables")
            elif k == "route":
                s.route = "tables" if v is True else str(v)
                if s.route not in ("tables", "tables+forms", "full"):
                    raise ValueError(f"unknown route {s.route!r}; known: tables, tables+forms, full")
            elif k == "route_min":
                s.route_min = float(v)
            elif k == "route_guard":
                s.route_guard = True
            elif k in ("boxes", "split", "assemble", "complete"):
                setattr(s, k, True)
            elif k in _fields.SWITCHES:
                s.more[k] = "on" if v is True else str(v)
            else:
                raise ValueError(f"unknown regions switch {k!r}")
        return s


def switches(ctx: PipelineContext) -> Switches:
    env = os.environ.get("READANYDOC_REGIONS")
    if env is not None:
        return Switches.parse(env)
    return Switches.parse(ctx.config.stage("ground").options.get("regions"))


def switches_on(ctx: PipelineContext) -> bool:
    return switches(ctx).any() or bool(GroundRegions._tables_spec(ctx))  # the table switches need detections too


@register_stage("ground_regions")
class GroundRegions(Ground):
    """``Ground`` plus the region switches. With no switch on it is ``Ground``."""

    def run(self, doc: Document, ctx: PipelineContext) -> Document:
        doc = super().run(doc, ctx)
        sw = switches(ctx)
        doc = self.apply_regions(doc, ctx, sw) if sw.any() or self._tables_spec(ctx) else doc
        from ..reading import budget as _budget  # per-page spending cap, final figures (no cap: nothing)
        _budget.close(doc)
        return doc

    @staticmethod
    def _tables_spec(ctx: PipelineContext) -> dict:
        """The table switches (ground option ``tables`` / READANYDOC_TABLES), off by default."""
        from .. import tables as _tables2
        return _tables2.spec_from(ctx.config.stage("ground").options.get("tables"))

    def apply_regions(self, doc: Document, ctx: PipelineContext, sw: Switches) -> Document:
        """The region steps on a grounded document."""
        import time
        spec = ctx.config.reader
        tspec = self._tables_spec(ctx)
        for page in doc.pages:
            dets = page.properties.get("layout_detections")
            if dets is None or not page.regions and not sw.assemble:
                continue
            changed = False
            t0 = time.time()
            shared = _crops.shared_reads(page)  # one form crop read serves every step of the page
            shared.__enter__()
            try:
                if tspec and not sw.assemble:  # table re-reads, joins and cell hygiene come first
                    from .. import tables as _tables2
                    if sw.more:  # combine step: blocks that kv=page will make Text are text for the table step
                        _fields.mark_text_like(page, dets, sw.more)
                    if _tables2.run_page(self, doc, page, tspec, spec):
                        self._page(doc, page)
                        _assemble.restore_types(page)
                full_route = False
                if sw.route == "full":  # assemble only on table / form dominated pages
                    from .match import FORM_LABELS, TABLE_LABELS
                    share = _assemble.routed_share(page, set(TABLE_LABELS) | set(FORM_LABELS))
                    full_route = share >= sw.route_min
                    page.properties["region_route"] = {"what": "full", "share": round(share, 3), "used": full_route}
                if sw.assemble or full_route:
                    changed = _assemble.run(doc, page, spec)
                else:
                    if sw.route and sw.route != "full":
                        changed = _assemble.run_routed(doc, page, spec, sw.route, sw.route_min, sw.route_guard) or changed
                    if sw.tables:
                        changed = _tables.run(doc, page, sw.tables, spec) or changed
                    if sw.forms:
                        changed = _forms.run(doc, page, spec, keep_tables=sw.forms == "notables") or changed
                    if sw.complete:
                        changed = _complete.run(doc, page, spec) or changed
                if changed:
                    self._page(doc, page)  # new content: word citations, boxes and position rules again
                    _assemble.restore_types(page)
                if sw.split and not (sw.assemble or full_route) and _structure.split_blocks(page, dets):
                    self._page(doc, page)
                    _assemble.restore_types(page)
                if sw.more and not sw.assemble:   # form fields and block structure (fields.py)
                    _fields.before_types(self, doc, page, dets, sw.more, spec)
                if (sw.types or sw.boxes) and not (sw.assemble or full_route):
                    page.properties["region_structure"] = _structure.apply(page, dets, sw.types, sw.boxes, sw.deny, allow=sw.allow)
                if sw.more and not sw.assemble:
                    _fields.after_types(doc, page, dets, sw.more)
            except Exception as e:  # a region step must never lose the page
                doc.note("regions", f"page {page.index}: region step failed ({type(e).__name__}: {str(e)[:160]})", "warning")
            finally:
                shared.__exit__(None, None, None)
            page.properties["region_seconds"] = round(time.time() - t0, 3)
        _crops.settle_prefetched(doc)  # form reads started with the page read and not asked for
        return doc
