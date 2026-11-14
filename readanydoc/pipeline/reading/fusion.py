"""Token-level repair of near-miss characters, by evidence only.

The base read keeps its structure, order, markdown and every token that the evidence does not
contradict. A token is replaced only when all of these hold:

* the base token, with its neighbours, is NOT found in the evidence (neither bigram), and
* a replacement token that is a small edit away IS found in the evidence between the same two
  neighbours, and
* the replacement comes from a source that may supply characters:
  - a trusted PDF text layer (exact characters), or
  - a second model read that the OCR words agree with (two independent reads against one).

OCR alone never overwrites a model token: PP-OCR's own misreads would be injected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rapidfuzz.distance import Levenshtein

from .evidence import PageEvidence, case_tokens


@dataclass
class Edit:
    start: int
    end: int
    old: str
    new: str
    reason: str

    @property
    def exact(self) -> bool:
        """True when the replacement comes from an exact text layer (strong evidence)."""
        return self.reason.endswith("text layer")


def _near(a: str, b: str, max_dist: int, min_ratio: float) -> bool:
    if a == b or not a or not b:
        return False
    if a.isdigit() != b.isdigit() and (a.isdigit() or b.isdigit()):
        return False  # never turn a word into a number or back
    if a.startswith(b) or b.startswith(a) or a.endswith(b) or b.endswith(a):
        return False  # a pure cut or extension is usually line-end hyphenation in the word source
    d = Levenshtein.distance(a, b)
    return d <= max_dist and 1 - d / max(len(a), len(b)) >= min_ratio


def _alt_map(base: list[str], alt: list[str]) -> dict[int, str]:
    """base token index -> the other read's token at the same place (1:1 replacements only)."""
    out: dict[int, str] = {}
    for op in Levenshtein.opcodes(base, alt):
        if op.tag == "replace" and op.src_end - op.src_start == op.dest_end - op.dest_start <= 3:
            for k in range(op.src_end - op.src_start):
                out[op.src_start + k] = alt[op.dest_start + k]
    return out


def propose_edits(text: str, evidences: list[PageEvidence], alt_texts: Optional[list[str]] = None,
                  max_dist: int = 2, min_ratio: float = 0.5, numbers: bool = False,
                  confirm: Optional[list[PageEvidence]] = None) -> list[Edit]:
    """``confirm``: further independent reads (as evidence objects); a second-read edit must be
    found in each of them too. Used when the base is the strongest reader."""
    toks = case_tokens(text)
    words = [t for t, _, _ in toks]
    alts = [_alt_map(words, [t for t, _, _ in case_tokens(a)]) for a in (alt_texts or [])]
    trusted = [e for e in evidences if e.trusted]
    edits: list[Edit] = []
    for i in range(1, len(words) - 1):
        tok, prev, nxt = words[i], words[i - 1], words[i + 1]
        if len(tok) < 2 or (tok.isdigit() and not numbers):
            continue
        if any(e.supports(tok, prev, nxt) for e in evidences):
            continue  # some evidence already agrees with the model: leave it
        new, why = None, ""
        for e in trusted:  # exact characters from a trusted text layer
            mids = [m for m in e.trigram_mid.get((prev, nxt), ()) if _near(tok, m, max_dist, min_ratio)
                    and (tok.isascii() or m.isascii())]  # accent-for-accent swaps are font-encoding noise
            if len(mids) == 1:
                new, why = mids[0], f"{e.source} text layer"
                break
        if new is None:
            for amap in alts:  # a second read that an independent word source confirms in context
                a = amap.get(i)
                if a and _near(tok, a, max_dist, min_ratio) and any(
                        (prev, a) in e.bigrams and (a, nxt) in e.bigrams for e in evidences) and all(
                        c.supports(a, prev, nxt) and not c.supports(tok, prev, nxt) for c in (confirm or [])):
                    new, why = a, "second read + word evidence"
                    break
        if new is not None and (numbers or not any(ch.isdigit() for ch in tok + new)):
            edits.append(Edit(toks[i][1], toks[i][2], tok, new, why))
    return edits


def apply_edits(text: str, edits: list[Edit]) -> str:
    for e in sorted(edits, key=lambda e: -e.start):
        if text[e.start:e.end] == e.old:
            text = text[:e.start] + e.new + text[e.end:]
    return text


def majority_edits(text: str, alt_texts: list[str], min_agree: int = 2) -> list[Edit]:
    """Token-level majority among independent reads of the same page (expert arbiter).

    A token of ``text`` is replaced only when at least ``min_agree`` other reads have the same,
    different token at the aligned position (1:1 aligned replacements only, so structure, order
    and markup never change). One other read alone never changes anything."""
    toks = case_tokens(text)
    words = [t for t, _, _ in toks]
    maps = [_alt_map(words, [t for t, _, _ in case_tokens(a)]) for a in alt_texts]
    edits: list[Edit] = []
    for i, tok in enumerate(words):
        votes: dict[str, int] = {}
        for m in maps:
            a = m.get(i)
            if a and a != tok:
                votes[a] = votes.get(a, 0) + 1
        if not votes:
            continue
        new, n = max(votes.items(), key=lambda kv: kv[1])
        # only a near-miss of the same token (or a number of the same length) is a misreading; an
        # unrelated word at the aligned position means the reads are structured differently there
        same_kind = _near(tok, new, 2, 0.5) or (tok.isdigit() and new.isdigit() and len(tok) == len(new))
        if n >= min_agree and same_kind:
            edits.append(Edit(toks[i][1], toks[i][2], tok, new, f"{n} other reads agree"))
    return edits
