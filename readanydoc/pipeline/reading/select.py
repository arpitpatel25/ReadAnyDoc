"""Evidence-based choice between whole candidate reads of one page.

Candidates are never merged here. The first candidate is the default and stays unless another one
agrees with the independent evidence clearly better (``margin``): a read that dropped a column or
stopped early has low recall of the evidence words; a read that invented text has low precision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .evidence import PageEvidence, read_tokens


@dataclass
class CandidateScore:
    source: str
    recall: float
    precision: float
    n_tokens: int
    numeric_precision: Optional[float] = None

    def value(self, precision_weight: float) -> float:
        return self.recall + precision_weight * self.precision


def score_candidate(source: str, text: str, ev: PageEvidence) -> CandidateScore:
    toks = read_tokens(text)
    num = ev.numeric_precision(toks)
    return CandidateScore(source, round(ev.recall(toks), 4), round(ev.precision(toks), 4), len(toks),
                          round(num, 4) if num is not None else None)


def select_page(candidates: list[tuple[str, str]], ev: Optional[PageEvidence], margin: float = 0.02,
                precision_weight: float = 0.5, min_evidence: int = 30,
                numeric_tolerance: float = 0.02) -> tuple[int, list[CandidateScore], str]:
    """Return (index of the chosen candidate, scores, reason). ``candidates`` = [(source, text)]."""
    if len(candidates) <= 1:
        return 0, [], "single candidate"
    if ev is None or len(ev) < min_evidence:
        return 0, [], "not enough independent evidence: default kept"
    scores = [score_candidate(s, t, ev) for s, t in candidates]
    best = 0
    for i in range(1, len(scores)):
        if scores[i].value(precision_weight) > scores[best].value(precision_weight) + margin:
            a, b = scores[i].numeric_precision, scores[0].numeric_precision
            if a is not None and b is not None and a < b - numeric_tolerance:
                continue  # never trade the default for a read whose numbers the evidence backs less
            best = i
    if best == 0:
        return 0, scores, "default kept (no candidate better by the margin)"
    return best, scores, (f"{scores[best].source} agrees better with {ev.source} words "
                          f"(recall {scores[best].recall:.3f} vs {scores[0].recall:.3f}, "
                          f"precision {scores[best].precision:.3f} vs {scores[0].precision:.3f})")
