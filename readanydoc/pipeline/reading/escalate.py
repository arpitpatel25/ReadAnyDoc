"""Escalation policy for the cheap profile: fast tier by default, strong tier only where the
evidence disagrees, plus the cost model used to report $/page.

Page level: the fast read is broken (empty, loop, truncated), or two fast reads dispute enough
words (``disputed tokens = tokens * (1 - agreement)``: the cost of a page re-read is fixed, its
value grows with the amount of text in doubt). Region level: a table that fails ``check_table``
is re-read as a crop, which costs in proportion to its area.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ...model import Document
from ...textnorm import agreement, norm_tokens
from .robust import Problem
from .tables import TableCheck


@dataclass
class CostModel:
    """Estimated $ per call (brief: fast tier $0.0002/page; Haiku ~$0.008/page, crops by area)."""

    fast_page: float = 0.0002
    strong_page: float = 0.008
    min_crop_fraction: float = 0.02  # a crop never costs less than this share of a page

    def crop(self, area_fraction: float) -> float:
        return self.strong_page * max(min(area_fraction, 1.0), self.min_crop_fraction)


@dataclass
class EscalationPolicy:
    disputed_tokens: int = 150  # page: fast reads disagree on at least this many tokens
    max_agreement: float = 0.9  # ... and their agreement is below this
    tables: bool = True  # region: re-read tables that fail verification
    pages: bool = True

    def page_reason(self, fast_text: str, second_text: Optional[str],
                    problems: Optional[list[Problem]] = None) -> Optional[str]:
        if not self.pages:
            return None
        fatal = [p for p in (problems or []) if p.fatal]
        if fatal:
            return "fast read is broken: " + ", ".join(p.kind for p in fatal)
        if second_text is None:
            return None
        a, b = norm_tokens(fast_text), norm_tokens(second_text)
        if not a or not b:
            return None
        agree = agreement(a, b)
        disputed = (1.0 - agree) * max(len(a), len(b))
        if agree < self.max_agreement and disputed >= self.disputed_tokens:
            return f"fast reads dispute about {int(disputed)} tokens (agreement {agree:.2f})"
        return None

    def table_reason(self, check: TableCheck) -> Optional[str]:
        if not self.tables or check.ok:
            return None
        return "; ".join(check.reasons)


def cost_summary(doc: Document) -> dict:
    """Totals of the ``est_cost_usd`` entries Read wrote into ``Document.usage``."""
    total = sum(u.get("est_cost_usd", 0.0) for u in doc.usage)
    strong = [u for u in doc.usage if u.get("escalation")]
    return {"est_cost_usd": round(total, 6),
            "page_escalations": sum(1 for u in strong if u["escalation"] == "page"),
            "region_escalations": sum(1 for u in strong if u["escalation"] == "region"),
            "escalated_area": round(sum(u.get("area", 0.0) for u in strong), 4)}
