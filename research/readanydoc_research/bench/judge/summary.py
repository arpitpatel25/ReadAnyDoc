"""Port of Parse Judge's summary.ts: per-system mean/median score, share of pages with a high-severity
error (Wilson 95% interval), error counts by category, head-to-head wins/ties/losses. Added on top:
share of pages with NO high-severity error and win rate against a chosen baseline (the two numbers
Reducto quotes for ParseArena). Cost, latency and the price/accuracy trade-off are left out: this
bench does not call the parsers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .core import Verdict


@dataclass
class PageResult:
    """One (page, system) cell. ``failed`` = the system produced no parse for the page."""
    page_id: str
    system: str
    status: str = "succeeded"        # "succeeded" | "failed"
    verdict: Optional[Verdict] = None


def mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def quantile(values: list[float], q: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def wilson(successes: int, total: int, z: float = 1.96) -> Optional[tuple[float, float]]:
    if total == 0:
        return None
    p = successes / total
    den = 1 + z * z / total
    center = (p + z * z / (2 * total)) / den
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return max(0.0, center - margin), min(1.0, center + margin)


def _page_score(r: Optional[PageResult]) -> Optional[int]:
    """A failed page scores 1, the judge's floor, so failures count against a system head-to-head."""
    if r is None:
        return None
    if r.verdict is not None:
        return r.verdict.score
    return 1 if r.status == "failed" else None


def summarize_system(system: str, results: list[PageResult]) -> dict:
    verdicts = [r.verdict for r in results if r.verdict is not None]
    scores = [v.score for v in verdicts]
    with_high = sum(1 for v in verdicts if v.has_high)
    counts: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for v in verdicts:
        for e in v.errors:
            counts[e["category"]] = counts.get(e["category"], 0) + 1
            by_sev[e["severity"]] = by_sev.get(e["severity"], 0) + 1
    n = len(verdicts)
    return {
        "system": system,
        "pages": len(results),
        "judged_pages": n,
        "failed_pages": sum(1 for r in results if r.status == "failed"),
        "mean_score": mean(scores),
        "median_score": quantile(scores, 0.5),
        "high_severity_rate": with_high / n if n else None,
        "high_severity_interval": wilson(with_high, n),
        "no_high_severity_share": (n - with_high) / n if n else None,
        "no_high_severity_interval": wilson(n - with_high, n),
        "error_counts": counts,
        "errors_by_severity": by_sev,
    }


def head_to_head(systems: list[str], results: list[PageResult]) -> list[dict]:
    cell = {(r.page_id, r.system): r for r in results}
    page_ids = list(dict.fromkeys(r.page_id for r in results))
    out = []
    for a in systems:
        for b in systems:
            if a == b:
                continue
            w = t = l = 0
            for pid in page_ids:
                own, other = _page_score(cell.get((pid, a))), _page_score(cell.get((pid, b)))
                if own is None or other is None:
                    continue
                if own > other:
                    w += 1
                elif own < other:
                    l += 1
                else:
                    t += 1
            out.append({"system": a, "opponent": b, "wins": w, "ties": t, "losses": l})
    return out


def summarize(results: list[PageResult], baseline: Optional[str] = None) -> dict:
    systems = list(dict.fromkeys(r.system for r in results))
    h2h = head_to_head(systems, results)
    out = {"systems": [summarize_system(s, [r for r in results if r.system == s]) for s in systems],
           "head_to_head": h2h, "baseline": baseline, "vs_baseline": []}
    if baseline is not None and baseline in systems:
        for row in h2h:
            if row["opponent"] != baseline:
                continue
            n = row["wins"] + row["ties"] + row["losses"]
            out["vs_baseline"].append({
                **row, "pages": n,
                # strict: a tie is not a win. Reducto has not published how ParseArena counts ties.
                "win_rate": row["wins"] / n if n else None,
                "win_rate_interval": wilson(row["wins"], n),
                "win_rate_ties_half": (row["wins"] + 0.5 * row["ties"]) / n if n else None,
                "win_rate_decided": row["wins"] / (row["wins"] + row["losses"]) if row["wins"] + row["losses"] else None,
            })
    return out


def _pct(x: Optional[float]) -> str:
    return "   n/a" if x is None else f"{100 * x:5.1f}%"


def format_summary(s: dict) -> str:
    lines = [f"{'system':24s} pages  mean  median  no-high-sev (95% CI)        high  low  fig  failed"]
    for r in s["systems"]:
        ci = r["no_high_severity_interval"]
        ci_s = f"[{100 * ci[0]:4.1f}, {100 * ci[1]:5.1f}]" if ci else " " * 13
        ms = "  n/a" if r["mean_score"] is None else f"{r['mean_score']:5.2f}"
        md = "  n/a" if r["median_score"] is None else f"{r['median_score']:5.1f}"
        sev = r["errors_by_severity"]
        lines.append(f"{r['system'][:24]:24s} {r['judged_pages']:5d} {ms}  {md}   {_pct(r['no_high_severity_share'])} "
                     f"{ci_s}   {sev.get('high', 0):5d} {sev.get('low', 0):4d} {sev.get('figure_error', 0):4d} "
                     f"{r['failed_pages']:6d}")
    if s["vs_baseline"]:
        lines.append(f"\nversus baseline {s['baseline']}  (win rate = wins / pages; ties are not wins)")
        lines.append(f"{'system':24s}  W   T   L   win rate  ties=half  decided-only")
        for r in s["vs_baseline"]:
            lines.append(f"{r['system'][:24]:24s} {r['wins']:3d} {r['ties']:3d} {r['losses']:3d}   {_pct(r['win_rate'])}   "
                         f"{_pct(r['win_rate_ties_half'])}   {_pct(r['win_rate_decided'])}")
    lines.append("\nhead-to-head (row beats column: wins-ties-losses)")
    names = [r["system"] for r in s["systems"]]
    cell = {(r["system"], r["opponent"]): r for r in s["head_to_head"]}
    lines.append(" " * 24 + " ".join(f"{n[:11]:>11s}" for n in names))
    for a in names:
        row = []
        for b in names:
            c = cell.get((a, b))
            row.append(f"{'-':>11s}" if c is None else f"{c['wins']}-{c['ties']}-{c['losses']}".rjust(11))
        lines.append(f"{a[:24]:24s}" + " ".join(row))
    cats = sorted({c for r in s["systems"] for c in r["error_counts"]})
    if cats:
        lines.append("\nerrors by category")
        for r in s["systems"]:
            lines.append(f"{r['system'][:24]:24s} " + ", ".join(f"{c}={r['error_counts'][c]}" for c in cats
                                                              if c in r["error_counts"]))
    return "\n".join(lines)
