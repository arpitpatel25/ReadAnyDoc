"""Detection of broken reads: empty output, refusals, truncation, repetition loops, dropped text.

Pure functions over text and ``ReadResult``; the retry policy that acts on them is in
``pipeline/verify.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

ProblemKind = Literal["empty", "refusal", "error", "truncated", "repetition", "incomplete"]

_OPEN_TABLE = re.compile(r"<table\b", re.I)
_CLOSE_TABLE = re.compile(r"</table\s*>", re.I)
_ENTITY = re.compile(r"&[a-zA-Z#0-9]+;")


def _has_word(unit: str) -> bool:
    """A loop unit must carry text: rules, dot leaders and runs of &nbsp; are layout, not loops."""
    return bool(re.search(r"\w", re.sub(r"</?[a-zA-Z][^<>]*>?|^[^<>]*>", " ", _ENTITY.sub(" ", unit))))


_REFUSAL = re.compile(r"^\s*(i['’]?m sorry|i am sorry|i can(?:'|’)?t|i cannot|i'm unable|i am unable|"
                      r"unable to (?:read|transcribe|view|access))", re.I)


@dataclass
class Problem:
    kind: ProblemKind
    detail: str = ""
    fatal: bool = True  # fatal = the read must not be used as it is
    position: Optional[int] = None  # char offset where the damage starts (repetition)


def find_repetition(text: str, min_repeats: int = 8, min_chars: int = 120,
                    max_unit: int = 200) -> Optional[tuple[int, str, int]]:
    """A unit repeated back-to-back at least ``min_repeats`` times covering ``min_chars`` or more.
    Returns (start offset of the 2nd copy, unit, repeat count). Table rules and dot leaders are
    not loops: units without a letter or digit are ignored."""
    if len(text) < min_chars:
        return None
    best: Optional[tuple[int, str, int]] = None
    # line-level loops (a model repeating the same line or row)
    lines = text.split("\n")
    offs, pos = [], 0
    for l in lines:
        offs.append(pos)
        pos += len(l) + 1
    i = 0
    while i < len(lines):
        unit = lines[i].strip()
        j = i + 1
        while j < len(lines) and lines[j].strip() == unit:
            j += 1
        n = j - i
        if unit and _has_word(unit) and n >= min_repeats and n * len(unit) >= min_chars:
            if best is None or n * len(unit) > best[2] * len(best[1]):
                best = (offs[i + 1], unit, n)
        i = j
    if best:
        return best
    # in-line loops ("the the the ...", "1.1.1.1."): a short unit repeated to the end of a line
    m = re.search(r"(?s)(.{2,%d}?)\1{%d,}" % (max_unit, min_repeats - 1), text[-20000:])
    if m:
        unit = m.group(1)
        if _has_word(unit) and len(m.group(0)) >= min_chars and len(set(unit.strip())) > 1:
            base = max(len(text) - 20000, 0)
            return base + m.start() + len(unit), unit, len(m.group(0)) // len(unit)
    return None


def trim_repetition(text: str) -> str:
    """Cut a repetition loop after its first copy (what is left is real output; the tail is not)."""
    rep = find_repetition(text)
    if not rep:
        return text
    start, unit, count = rep
    end = start
    step_line = unit + "\n"
    if text[start:].lstrip("\n").startswith(unit) and "\n" in text[start:start + len(unit) + 2]:
        while text.startswith(step_line, end) or text[end:].strip() == unit:
            nxt = end + len(step_line)
            if nxt <= end or end >= len(text):
                break
            end = nxt
    else:
        while text.startswith(unit, end):
            end += len(unit)
    return (text[:start].rstrip() + "\n" + text[end:].lstrip("\n")).strip("\n") if end > start else text


def detect_problems(text: str, stop_reason: Optional[str] = None, refused: bool = False,
                    truncated: bool = False, evidence_recall: Optional[float] = None,
                    n_evidence: int = 0, min_recall: float = 0.5, min_evidence: int = 80) -> list[Problem]:
    """Everything wrong with one read. ``evidence_recall`` = share of the page's independent words
    that the read contains (``PageEvidence.recall``), when evidence exists."""
    out: list[Problem] = []
    body = (text or "").strip()
    if refused or stop_reason == "refusal":
        out.append(Problem("refusal", "model refused"))
    if stop_reason == "error":
        out.append(Problem("error", "provider reported an error"))
    if not body:
        out.append(Problem("empty", "no text"))
        return out
    if not refused and len(body) < 400 and _REFUSAL.match(body):
        out.append(Problem("refusal", "output reads as a refusal"))
    if truncated or stop_reason == "max_tokens":
        out.append(Problem("truncated", "stopped at max_tokens"))
    elif len(_OPEN_TABLE.findall(body)) > len(_CLOSE_TABLE.findall(body)):
        out.append(Problem("truncated", "unclosed <table>: output stops inside a table", fatal=False))
    rep = find_repetition(body)
    if rep:
        # Repeated table cells or rows are usually real (blank form rows, "[illegible]", ditto marks).
        # Inside a table a repeat is fatal, and may be cut, only when the output is also truncated.
        in_table = bool(re.search(r"</?t[dhr]\b|\|", rep[1]))
        cut_short = any(p.kind == "truncated" for p in out)
        out.append(Problem("repetition", f"{rep[1][:40]!r} x{rep[2]}", position=rep[0],
                           fatal=not in_table or cut_short))
    if evidence_recall is not None and n_evidence >= min_evidence and evidence_recall < min_recall:
        out.append(Problem("incomplete", f"only {evidence_recall:.0%} of the page's words are in the read",
                           fatal=False))
    return out


_RESTART = re.compile(r"(?im)^\s*(?:wait,[^\n]{0,120}(?:transcri|present|start over|redo|let me)[^\n]{0,120}|(?:let me|i(?:'ll| will)) (?:start over|redo|restart|try again)[^\n]{0,200}|"
                      r"[^\n]{0,80}here(?: is|'s) the (?:complete|full|corrected|final|revised) transcription[^\n]{0,40}:)\s*$")


def strip_restart(text: str) -> str:
    """A model sometimes abandons a first attempt in the middle of its answer ("Wait, that is not how
    I want to present this. Here is the complete transcription:") and starts again. Keep only what
    follows the last such line when a substantial answer follows it; otherwise leave the text alone."""
    last = None
    for m in _RESTART.finditer(text or ""):
        last = m
    if last is None:
        return text
    rest = text[last.end():].strip()
    return rest if len(rest) >= 0.3 * len(text.strip()) else text
