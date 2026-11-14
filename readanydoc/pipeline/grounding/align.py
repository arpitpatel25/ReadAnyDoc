"""Token alignment between a block's text and candidate source words.

``align`` returns, for region tokens, the candidate tokens they correspond to and how:
- ``exact``    identical normalised tokens, found as common runs (difflib anchors);
- ``fuzzy``    near-identical tokens between anchors (OCR / model character slips);
- ``merge``    one token on one side equals two or three on the other (line-end hyphenation,
               OCR splitting or gluing words);
- ``slot``     a 1:1 or 2:2 hole between two matched neighbours on the same source line, same
               character class and similar length: the position is fixed by its neighbours;
- ``residual`` runs found in a second, order-free pass over what is still unmatched (table cells
               that wrap, footnotes, text the reader moved).
Nothing here looks at geometry; the caller filters matches by position.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from rapidfuzz.distance import Indel

MAX_DP_CELLS = 90_000


@dataclass
class Match:
    cand: tuple[int, ...]  # indices into the candidate token list
    kind: str
    score: float  # 1.0 exact; similarity otherwise


def similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return Indel.normalized_similarity(a, b)


def _threshold(a: str, b: str) -> float:
    n = min(len(a), len(b))
    if n <= 2:
        return 1.01  # short tokens must be identical
    if n <= 4:
        return 0.66
    return 0.6


def _pair(a: str, b: str) -> float:
    """Score of pairing two single tokens in the gap DP; <= 0 means not allowed."""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if la <= 2 or lb <= 2 or min(la, lb) * 2 < max(la, lb):
        return 0.0
    if a.isdigit() and b.isdigit():
        return 0.0  # two different numbers are different numbers, not a misread
    s = Indel.normalized_similarity(a, b)
    return s if s >= _threshold(a, b) else 0.0


def _join(tokens: list[str], i: int, k: int) -> str:
    return "".join(tokens[i:i + k])


def _gap_dp(rt: list[str], ct: list[str], i0: int, i1: int, j0: int, j1: int,
            out: dict[int, Match]) -> None:
    """Best monotone pairing inside one gap. Maximises summed (score - 0.35) so that a weak fuzzy
    pair never displaces an exact one and unmatched tokens cost nothing."""
    nr, nc = i1 - i0, j1 - j0
    if nr <= 0 or nc <= 0 or nr * nc > MAX_DP_CELLS:
        return
    NEG = 0.35
    rl = [len(t) for t in rt[i0:i1]]
    cl = [len(t) for t in ct[j0:j1]]
    best = [[0.0] * (nc + 1) for _ in range(nr + 1)]
    back: list[list[Optional[tuple[int, int, str, float]]]] = [[None] * (nc + 1) for _ in range(nr + 1)]
    for a in range(1, nr + 1):
        ra, la = rt[i0 + a - 1], rl[a - 1]
        row, prow, brow = best[a], best[a - 1], back[a]
        for b in range(1, nc + 1):
            v, bk = prow[b], None
            if row[b - 1] > v:
                v, bk = row[b - 1], (0, 1, "", 0.0)
            cb, lb = ct[j0 + b - 1], cl[b - 1]
            if ra == cb:
                cand = prow[b - 1] + 1.0 - NEG
                if cand > v:
                    v, bk = cand, (1, 1, "exact", 1.0)
            elif la > 2 and lb > 2:
                s = _pair(ra, cb)
                if s > 0:
                    cand = prow[b - 1] + s - NEG
                    if cand > v:
                        v, bk = cand, (1, 1, "fuzzy", s)
            # one region token = 2..3 candidate tokens (hyphenation, OCR split)
            if la >= 4 and b >= 2 and lb < la:
                tot = lb
                for k in (2, 3):
                    if b < k:
                        break
                    tot += cl[b - k]
                    if tot > la + 2:
                        break
                    if tot >= la - 2:
                        s2 = similarity(ra, _join(ct, j0 + b - k, k))
                        if s2 >= (0.84 if k == 2 else 0.88):
                            cand = best[a - 1][b - k] + k * (s2 - NEG)
                            if cand > v:
                                v, bk = cand, (1, k, "merge", s2)
            # 2..3 region tokens = one candidate token (OCR glued words, reader split a word)
            if lb >= 4 and a >= 2 and la < lb:
                tot = la
                for k in (2, 3):
                    if a < k:
                        break
                    tot += rl[a - k]
                    if tot > lb + 2:
                        break
                    if tot >= lb - 2:
                        s2 = similarity(_join(rt, i0 + a - k, k), cb)
                        if s2 >= (0.84 if k == 2 else 0.88):
                            cand = best[a - k][b - 1] + k * (s2 - NEG)
                            if cand > v:
                                v, bk = cand, (k, 1, "merge", s2)
            row[b] = v
            brow[b] = bk
    a, b = nr, nc
    while a > 0 and b > 0:
        bk = back[a][b]
        if bk is None:
            a -= 1
            continue
        da, db, kind, s = bk
        if da == 0:
            b -= 1
            continue
        cand = tuple(range(j0 + b - db, j0 + b))
        for x in range(a - da, a):
            out[i0 + x] = Match(cand, kind, s)
        a -= da
        b -= db


def _monotone(rt: list[str], ct: list[str]) -> dict[int, Match]:
    out: dict[int, Match] = {}
    if not rt or not ct:
        return out
    sm = SequenceMatcher(None, rt, ct, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    # A lone short token between two long gaps is as likely a coincidence as a match: let the gap
    # DP decide with its neighbours instead of pinning it as an anchor.
    anchors = [b for b in blocks if b.size >= 2 or len(rt[b.a]) >= 4]
    pi = pj = 0
    for blk in anchors + [None]:
        i1, j1 = (blk.a, blk.b) if blk is not None else (len(rt), len(ct))
        _gap_dp(rt, ct, pi, i1, pj, j1, out)
        if blk is not None:
            for k in range(blk.size):
                out[blk.a + k] = Match((blk.b + k,), "exact", 1.0)
            pi, pj = blk.a + blk.size, blk.b + blk.size
    return out


def _char_class(t: str) -> str:
    return "d" if t.isdigit() else ("a" if t.isalpha() else "m")


def _fill_slots(rt: list[str], ct: list[str], out: dict[int, Match], same_line) -> None:
    """Pair what sits in a k:k hole (k <= 2) between two matched neighbours on one source line."""
    used = {c for m in out.values() for c in m.cand}
    i, n = 0, len(rt)
    while i < n:
        if i in out:
            i += 1
            continue
        j = i
        while j < n and j not in out:
            j += 1
        k = j - i
        if 0 < i and j < n and k <= 2:
            left, right = out[i - 1].cand[-1], out[j].cand[0]
            hole = list(range(left + 1, right))
            if len(hole) == k and not any(c in used for c in hole) and same_line(left, right):
                ok = all(_char_class(rt[i + x]) == _char_class(ct[hole[x]])
                         and min(len(rt[i + x]), len(ct[hole[x]])) * 2 >= max(len(rt[i + x]), len(ct[hole[x]]))
                         for x in range(k))
                if ok:
                    for x in range(k):
                        s = similarity(rt[i + x], ct[hole[x]])
                        out[i + x] = Match((hole[x],), "slot", max(s, 0.5))
                        used.add(hole[x])
        i = j


def _residual(rt: list[str], ct: list[str], out: dict[int, Match], strict: bool, rounds: int = 3) -> None:
    """Order-free pass: align the still-unmatched region runs against the still-unused candidates.
    Accepts exact runs of >= 2 tokens (>= 3 when ``strict``), fuzzy/merge pairs attached to such a
    run, and single tokens of >= 4 characters that are unique on both sides."""
    min_run = 3 if strict else 2
    for _ in range(rounds):
        used = {c for m in out.values() for c in m.cand}
        r_idx = [i for i in range(len(rt)) if i not in out]
        c_idx = [j for j in range(len(ct)) if j not in used]
        if not r_idx or not c_idx:
            return
        # sentinels keep runs from spanning a discontinuity on the region side
        rs: list[str] = []
        r_map: list[int] = []
        for n, i in enumerate(r_idx):
            if n and r_idx[n - 1] != i - 1:
                rs.append(f"\x00{n}")
                r_map.append(-1)
            rs.append(rt[i])
            r_map.append(i)
        cs = [ct[j] for j in c_idx]
        sub = _monotone(rs, cs)
        r_count, c_count = Counter(rs), Counter(cs)
        added = 0
        # group consecutive matched residual tokens into runs
        keys = sorted(k for k in sub if r_map[k] >= 0)
        run: list[int] = []
        runs: list[list[int]] = []
        for k in keys:
            if run and k == run[-1] + 1 and sub[k].cand[0] <= sub[run[-1]].cand[-1] + 2:
                run.append(k)
            else:
                if run:
                    runs.append(run)
                run = [k]
        if run:
            runs.append(run)
        for run in runs:
            exact = [k for k in run if sub[k].kind == "exact"]
            ok = len(exact) >= min_run or (len(run) >= min_run and len(exact) >= 1
                                           and sum(len(rs[k]) for k in exact) >= 4 and not strict)
            if not ok and len(run) == 1 and not strict:
                k = run[0]
                t = rs[k]
                ok = (sub[k].kind == "exact" and len(t) >= 4 and r_count[t] == 1 and c_count[t] == 1)
            if not ok:
                continue
            for k in run:
                m = sub[k]
                out[r_map[k]] = Match(tuple(c_idx[c] for c in m.cand), "residual" if m.kind == "exact" else m.kind,
                                      m.score)
                added += 1
        if not added:
            return


def align(rt: list[str], ct: list[str], same_line=None, residual: bool = True,
          strict: bool = False) -> dict[int, Match]:
    """Region tokens -> candidate tokens. ``same_line(j1, j2)`` says whether two candidate tokens
    sit on one source line (enables slot filling). ``strict`` is for untrusted sources: exact runs
    of three or more tokens only."""
    if not rt or not ct:
        return {}
    if strict:
        out: dict[int, Match] = {}
        _residual(rt, ct, out, strict=True, rounds=1)
        return out
    out = _monotone(rt, ct)
    if same_line is not None:
        _fill_slots(rt, ct, out, same_line)
    if residual:
        _residual(rt, ct, out, strict=False)
        if same_line is not None:
            _fill_slots(rt, ct, out, same_line)
    return out
