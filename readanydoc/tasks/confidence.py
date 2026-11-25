"""Calibrated confidence for extracted values: a score that says how often values like this one are right.

A value's citation already records how it was found on the page (verbatim once, verbatim several times, fuzzily,
not where the model said, nowhere). One more signal is free: a value that *conflicts* with another value of the
same list for the same thing (same period, segment and other text fields, a different number) is often a sub-line
filed under a total. Each (how found, conflict) group gets the share of such values that were right on the
benchmarks, measured once with the code grader (CALIBRATION below; method and numbers: docs/decisions.md).
The score replaces granular_confidence.extract_confidence; the label is "high" at HIGH or above.
"""

from __future__ import annotations

from typing import Any, Optional

HIGH = 0.9

# how the value was found -> the raw score build_citations gives it
KINDS = {0.95: "verbatim", 0.85: "fuzzy", 0.8: "repeated", 0.7: "elsewhere", 0.3: "not_in_cited_block"}

# (kind, conflict) -> share right on the benchmarks. Filled from measurement; missing -> raw score kept.
CALIBRATION: dict[tuple[str, bool], float] = {}


def _scalar(v: Any) -> bool:
    return v is not None and not isinstance(v, (dict, list))


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def conflicts(value: Any, path: tuple = ()) -> set[tuple]:
    """Leaf paths of numbers that disagree with another item of the same list that matches on every text field."""
    out: set[tuple] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            out |= conflicts(v, path + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out |= conflicts(v, path + (i,))
        items = [(i, v) for i, v in enumerate(value) if isinstance(v, dict)]
        if len(items) > 1:
            groups: dict[tuple, list] = {}
            for i, v in items:
                text = tuple(sorted((k, str(x).strip().lower()) for k, x in v.items()
                                    if _scalar(x) and _num(x) is None and not k.startswith("_")))
                groups.setdefault(text, []).append((i, v))
            for members in groups.values():
                if len(members) < 2:
                    continue
                keys = {k for _, v in members for k, x in v.items() if _num(x) is not None}
                for k in keys:
                    vals = {round(_num(v.get(k)), 6) for _, v in members if _num(v.get(k)) is not None}
                    if len(vals) > 1:
                        out |= {path + (i, k) for i, v in members if _num(v.get(k)) is not None}
    return out


def calibrate(value: Any, cites: dict, table: Optional[dict] = None) -> dict:
    """The citations with calibrated scores and labels (in place; returns ``cites``)."""
    table = CALIBRATION if table is None else table
    if not table:
        return cites
    bad = conflicts(value)
    for path, cs in cites.items():
        for c in cs or []:
            g = c.get("granular_confidence") or {}
            raw = g.get("extract_confidence")
            kind = KINDS.get(round(raw, 2)) if isinstance(raw, (int, float)) else None
            score = table.get((kind, tuple(path) in bad)) if kind else None
            if score is None:
                continue
            if g.get("extract_confidence") is not None:
                g["extract_confidence"] = round(score, 3)
            c["confidence"] = "high" if score >= HIGH else "low"
    return cites
