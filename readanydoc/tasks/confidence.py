"""Calibrated confidence for extracted values: a score that says how often values like this one are right.

A value's citation carries a raw score from how it was located on the page (0.95 found once verbatim, 0.9 found
next to its anchor or where the model said, 0.85 fuzzy, 0.8 or 0.6 printed in several places, 0.3 not found in
the block the model named). On its own that score barely predicts a wrong value (measured: about 95% right either
way on 10-Qs). One free signal does: a value that *conflicts* with another item of the same list for the same thing
(same period, segment and other text fields, a different number) is usually a sub-line filed under a total.

Each (raw score, conflict) group with enough measured values gets the share of such values that were right on the
benchmarks, measured with the code graders (CALIBRATION below; method and numbers: docs/decisions.md). A
conflicting value with any other score gets at most CONFLICT. Labels only ever go down ("low" below HIGH): a "low"
the citation already had says the box may be in the wrong place, which the value graders do not measure.
"""

from __future__ import annotations

from typing import Any, Optional

HIGH = 0.9

# Measured 2026-11-25 on 2,601 values (16 10-Q filings vs their XBRL facts, 100 CUAD contracts vs the lawyers'
# answers). Only groups with 100+ values are listed.
CALIBRATION: dict[tuple[float, bool], float] = {
    (0.9, False): 0.954, (0.9, True): 0.808, (0.95, False): 0.925, (0.95, True): 0.65,
}
CONFLICT = 0.779


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
    bad = conflicts(value)
    for path, cs in cites.items():
        hit = tuple(path) in bad
        for c in cs or []:
            g = c.get("granular_confidence") or {}
            raw = g.get("extract_confidence")
            if not isinstance(raw, (int, float)):
                continue
            score = table.get((round(raw, 2), hit))
            if score is None and hit:
                score = min(raw, CONFLICT)
            if score is None:
                continue
            g["extract_confidence"] = round(score, 3)
            if score < HIGH:
                c["confidence"] = "low"
    return cites
