"""State shared by the checks of one page, and the record every check leaves on a block."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ...model import Candidate, Document, Page, Region
from ...textnorm import plain_text
from . import reread


@dataclass
class PageChecks:
    doc: Document
    page: Page
    reader_spec: str
    ground_options: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)
    calls: int = 0

    def opt(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def located(self) -> Optional[Page]:
        return reread.locate(self.doc, self.page, self.ground_options, self.cache)

    def box_of(self, region: Region) -> Optional[reread.Box]:
        """The block's box found by word alignment, or None when it was not located on the page."""
        g = self.located()
        if g is None:
            return None
        k = next((i for i, r in enumerate(self.page.regions) if r is region), None)
        if k is None or k >= len(g.regions) or g.regions[k].bbox is None:
            return None
        if g.regions[k].meta.get("bbox_source") not in (None, "ground", "layout"):
            return None
        return reread.xyxy(g.regions[k].bbox)

    def ask(self, box: reread.Box, prompt: str, tag: str, zoom: Optional[float] = None) -> Optional[str]:
        if self.calls >= int(self.opt("max_calls_per_page", 6)):
            return None
        from ..reading import budget  # per-page spending cap (off unless a cap is set)
        if not budget.allow(self.doc, self.page, "verify", tag=tag):
            return None
        png = reread.crop_png(self.doc, self.page, box, zoom=zoom)
        if png is None:
            return None
        self.calls += 1
        return reread.ask(self.doc, self.page, self.reader_spec, png, prompt, f"{tag}{self.calls}")

    def record(self, region: Region, check: str, status: str, detail: str = "", **more: Any) -> None:
        """status: "repaired" (content changed on evidence), "unresolved" (fault found, not fixed),
        "restored" (missing text put back)."""
        entry = {"check": check, "status": status, "detail": detail, **more}
        region.meta.setdefault("checks", []).append(entry)
        self.page.properties.setdefault("checks", []).append({"block": region.id, **entry})

    def replace(self, region: Region, new_content: str, why: str) -> None:
        if not any(c.meta.get("before_checks") for c in region.candidates):
            region.candidates.append(Candidate(source=region.source or "reader", content=region.content,
                                               meta={"before_checks": True}))
        region.content = new_content
        region.text = plain_text(new_content)
        self.doc.note("verify", f"page {self.page.index}: {why}")
