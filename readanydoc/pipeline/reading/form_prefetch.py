"""Form region reads started together with the page read.

The form crop read (``regions/forms.py``, switch ``forms``) and the form evidence read (``regions/formevidence.py``,
switch ``formread``) need the page read only to decide what to do with the answer: the crop and the prompt depend
on the layout alone, and the layout model runs before the page read. With the Read option ``form_prefetch`` the
read of every form region those steps would ask for is started before the page read; ``crops.read_many`` then
waits for it instead of making it. Same crop, same prompt, same reader: the response does not change, and a form
page waits max(page read, form read) instead of page read + grounding + form read.

A region is started when the steps would almost surely read it whatever the page read says: an outermost
``form`` / ``key_value_region`` detection (among the first four, as the steps take them) that holds no filled form
field of the file and covers at least 5% of the page (Read option ``form_prefetch_min_area``). A read that is then not asked for (no block of the page read was located in the region) is still paid;
``crops.settle_prefetched`` bills it as ``form_prefetch_unused``.
"""

from __future__ import annotations

from typing import Any, Optional

from ...model import Document, Page


def wanted(ground_options: Optional[dict]) -> bool:
    """Is a step that reads form regions switched on? (ground option ``regions`` / READANYDOC_REGIONS)"""
    import os
    from ..regions import Switches
    env = os.environ.get("READANYDOC_REGIONS")
    try:
        sw = Switches.parse(env if env is not None else (ground_options or {}).get("regions"))
    except ValueError:
        return False
    return bool(sw.forms or "formread" in sw.more) and not sw.assemble


def filled_fields(doc: Document, page: Page) -> list[dict]:
    """Filled form fields of the file on this page (what ``forms/fillable.py`` stores after the read)."""
    if doc.file_type != "pdf" or not doc.meta.get("path"):
        return []
    try:
        from ..forms import acroform
        fields = acroform.page_fields(doc.meta["path"], page.original_page, doc.meta.get("password")) or []
    except Exception:
        return []
    return [f for f in fields if f.get("bbox") and (str(f.get("value") or "").strip() or f.get("checked"))]


MIN_AREA = 0.05


def regions_to_read(doc: Document, page: Page, max_crops: int = 4, min_area: float = MIN_AREA) -> list[dict]:
    """``min_area``: a small form region rarely holds a whole block of the page read, and then the steps do not ask
    for its read (development set, 239 form regions: 46 of the 97 regions under 5% of the page were never read, 6 of the
    142 larger ones). Small regions are therefore not started early; a step that wants one reads it as before."""
    from ..regions.match import form_regions
    filled = filled_fields(doc, page)
    out = []
    for d in form_regions(page)[:max_crops]:
        x0, y0, x1, y1 = d["box"]
        if (x1 - x0) * (y1 - y0) < min_area:
            continue
        if any(x0 <= (f["bbox"][0] + f["bbox"][2]) / 2 <= x1 and y0 <= (f["bbox"][1] + f["bbox"][3]) / 2 <= y1
               for f in filled):
            continue
        out.append(d)
    return out


def start(doc: Document, page: Page, ground_options: Optional[dict], reader_spec: str,
          min_area: float = MIN_AREA) -> int:
    """Called by Read before the page read. Returns the number of reads started."""
    if not page.properties.get("layout_detections") or not wanted(ground_options):
        return 0
    if page.properties.get("rotation_applied") or page.properties.get("skew_applied"):
        # a page that was turned or straightened is put back into its original frame at the end of grounding, so the
        # crop the steps make then is not the crop that can be made now (seen on tax_irs_f6744_p044: another image,
        # another read). Such a page keeps the late read.
        return 0
    from ..regions import crops
    jobs: list[tuple[Any, str, str]] = [(tuple(d["box"]), crops.FORM_PROMPT, f"form:{d['id']}")
                                        for d in regions_to_read(doc, page, min_area=min_area)]
    n = crops.prefetch(doc, page, jobs, reader_spec)
    if n:
        page.properties["form_prefetch"] = n
    return n
