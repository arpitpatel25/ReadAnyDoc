"""Which strips the second zoomed pass reads.

The zoom vote reads a scan as 3 enlarged strips and, when that pass disputes any token of the page read, as 4
more strips cut differently; a token changes only when both passes agree against the page read. The second
pass is the dearer one (4 calls) and most of it re-reads parts of the page that the first pass did not dispute,
where no token can change. Read option ``zoom_second`` (off by default = all 4 strips whenever anything is
disputed):

``disputed``         only the strips of the second set that overlap a strip of the first set in which a token was
                     disputed;
``digits``           the second pass runs only when the first pass disputes a token that holds a digit (all strips);
``digits+disputed``  both: only for digit disputes, and only the strips that overlap them.

A dispute is located by the strip of the first pass it came from: the page read is aligned with the three strip
texts joined (exactly as ``zoom.disputes`` does) and each aligned token belongs to one strip.
"""

from __future__ import annotations

import difflib
from typing import Callable, Optional

from . import zoom

Box = tuple[float, float, float, float]
MODES = ("disputed", "digits", "digits+disputed")


def disputes_by_strip(page_text: str, parts: list[str]) -> list[dict]:
    """Per strip of the first pass: {"all": disputed tokens, "digits": those that hold a digit}."""
    pt = zoom.tokens(page_text)
    a = [t[0].lower() for t in pt]
    other, owner = [], []
    for k, part in enumerate(parts):
        toks = [t[0] for t in zoom.tokens(part)]
        other += toks
        owner += [k] * len(toks)
    b = [t.lower() for t in other]
    out = [{"all": 0, "digits": 0} for _ in parts]
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "replace" and i2 - i1 == j2 - j1:
            for k in range(i2 - i1):
                t, x = pt[i1 + k][0], other[j1 + k]
                if x.lower() != t.lower() and zoom.similar(t, x):
                    out[owner[j1 + k]]["all"] += 1
                    if any(c.isdigit() for c in t) or any(c.isdigit() for c in x):
                        out[owner[j1 + k]]["digits"] += 1
    return out


def plan(mode: str, page_text: str, parts_a: list[str], cut_a: list[Box]
         ) -> tuple[bool, Optional[Callable[[Box], bool]], dict]:
    """(run the second pass?, filter for its strips or None for all, record)."""
    if mode not in MODES or len(parts_a) != len(cut_a) or not parts_a:
        return True, None, {"mode": mode, "applied": False}
    per = disputes_by_strip(page_text, parts_a)
    what = "digits" if "digits" in mode else "all"
    hot = [cut_a[k] for k, d in enumerate(per) if d[what] > 0]
    rec = {"mode": mode, "applied": True, "by_strip": per, "disputed_strips": len(hot)}
    if not hot:
        return False, None, rec
    if "disputed" not in mode:
        return True, None, rec

    def keep(box: Box) -> bool:
        return any(min(box[3], h[3]) - max(box[1], h[1]) > 0 and min(box[2], h[2]) - max(box[0], h[0]) > 0 for h in hot)
    return True, keep, rec
