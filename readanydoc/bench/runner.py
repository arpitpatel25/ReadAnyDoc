"""Run test cases through systems, score them, and write the results.

Results go to ``<out>/<system>/<case id>.json`` as each document finishes, so a stopped run resumes where it left
off (``fresh=True`` runs everything again). A run stops starting new documents once its measured spend reaches
``budget_usd``.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from . import grade
from .systems import System, pages_of


def estimate(cases: list[dict], systems: list[System]) -> dict:
    pages = sum(pages_of(c["file"]) for c in cases)
    per = {s.name: round(pages * s.est_per_page, 2) for s in systems}
    return {"documents": len(cases), "pages": pages, "estimated_usd": per, "total_usd": round(sum(per.values()), 2),
            "note": "rough, from the cost per page measured in docs/research; the run reports what it really spent"}


def citation_check(case: dict, result: dict) -> Optional[dict]:
    """For a PDF: how many values have a box, and how many boxes show the value (words read from the PDF itself)."""
    if not case["file"].lower().endswith(".pdf") or not result.get("data"):
        return None
    try:
        import pdfplumber
    except ImportError:
        return None
    vals = {p: v for p, v in grade.leaves(result["data"]) if not grade._blank(v) and not isinstance(v, bool)}
    if not vals:
        return None
    cites = result.get("citations") or {}
    boxed = shows = 0
    words: dict[int, list] = {}
    with pdfplumber.open(case["file"]) as pdf:
        for path, value in vals.items():
            boxes = [c["bbox"] for c in cites.get(path) or [] if c.get("bbox") and c["bbox"].get("page")]
            if not boxes:
                continue
            boxed += 1
            for b in boxes:
                n = int(b["page"]) - 1
                if not 0 <= n < len(pdf.pages):
                    continue
                if n not in words:
                    pg = pdf.pages[n]
                    words[n] = [((w["x0"] + w["x1"]) / 2 / pg.width, (w["top"] + w["bottom"]) / 2 / pg.height,
                                 w["text"]) for w in pg.extract_words()]
                pad = 0.004
                inside = " ".join(t for x, y, t in words[n] if b["left"] - pad <= x <= b["left"] + b["width"] + pad
                                  and b["top"] - pad <= y <= b["top"] + b["height"] + pad)
                if inside and (grade.same(value, inside) or _contains(value, inside)):
                    shows += 1
                    break
    return {"values": len(vals), "with_box": boxed, "box_shows_value": shows}


def _contains(value, text: str) -> bool:
    vn = grade.number(value) if grade._numeric(value) else None
    if vn is not None:
        return any(abs(vn - (grade.number(t) or 1e18)) <= max(0.01, 0.005 * abs(vn)) or
                   abs(abs(vn) - abs(grade.number(t) or 1e18)) <= 0.01 for t in text.split())
    v, t = grade._text(value), grade._text(text)
    return bool(v) and (v in t or v.replace(" ", "") in t.replace(" ", ""))


def run(cases: list[dict], systems: list[System], out: str, budget_usd: Optional[float] = None,
        workers: int = 4, fresh: bool = False, on_done: Optional[Callable[[str, str, dict], None]] = None) -> dict:
    out_dir = Path(out)
    spent = {"usd": 0.0}
    lock = threading.Lock()
    for s in systems:
        (out_dir / s.name.replace(":", "_")).mkdir(parents=True, exist_ok=True)

    def one(job: tuple[System, dict]) -> None:
        s, c = job
        dest = out_dir / s.name.replace(":", "_") / f"{c['id']}.json"
        if dest.exists() and not fresh:
            return
        with lock:
            if budget_usd is not None and spent["usd"] >= budget_usd:
                return
        r = s.run(c)
        with lock:
            spent["usd"] += r.get("cost_usd") or 0.0
        rec = {"case": c["id"], "system": s.name, "file": c["file"], "pages": pages_of(c["file"]),
               "meta": c.get("meta") or {}, **r}
        if c.get("gold") is not None:
            rec["score"] = grade.score_case(c, r.get("data"))
        rec["citation_check"] = citation_check(c, r)
        dest.write_text(json.dumps(rec, default=str))
        if on_done:
            on_done(s.name, c["id"], rec)

    jobs = [(s, c) for c in cases for s in systems]
    t = time.time()
    with ThreadPoolExecutor(max(1, workers)) as pool:
        list(pool.map(one, jobs))
    summary = summarize(out, cases, systems)
    summary["wall_seconds"] = round(time.time() - t, 1)
    summary["stopped_by_budget"] = budget_usd is not None and spent["usd"] >= budget_usd
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary


def load(out: str, system: str, case_id: str) -> Optional[dict]:
    p = Path(out) / system.replace(":", "_") / f"{case_id}.json"
    return json.loads(p.read_text()) if p.exists() else None


def summarize(out: str, cases: list[dict], systems: list[System]) -> dict:
    res: dict = {"systems": {}, "documents": len(cases)}
    for s in systems:
        recs = [r for c in cases if (r := load(out, s.name, c["id"]))]
        scored = [r for r in recs if r.get("score")]
        cc = [r["citation_check"] for r in recs if r.get("citation_check")]
        costs = [r["cost_usd"] for r in recs if r.get("cost_usd") is not None]
        secs = [r["seconds"] for r in recs if r.get("seconds")]
        row = {"ran": len(recs), "failed": sum(1 for r in recs if r.get("error")),
               **({k: v for k, v in grade.summarize(scored).items() if k != "documents"} if scored else {}),
               "cost_usd": round(sum(costs), 4) if costs else None,
               "cost_per_document_usd": round(sum(costs) / len(costs), 4) if costs else None,
               "cost_note": next((r.get("cost_note") for r in recs if r.get("cost_note")), None),
               "median_seconds": round(statistics.median(secs), 1) if secs else None}
        by_id = {c["id"]: c for c in cases}
        with_schema = [r for r in recs if r.get("case") in by_id and by_id[r["case"]].get("schema")]
        if with_schema:
            for key, ok in (("schema_valid", True), ("schema_valid_strict", False)):
                checked = [(r, grade.schema_errors(None if r.get("error") else r.get("data"),
                                                   by_id[r["case"]]["schema"], nulls_ok=ok)) for r in with_schema]
                row[key] = sum(1 for _, e in checked if not e) / len(checked)
                if ok:
                    row["schema_errors"] = {r["case"]: e[:5] for r, e in checked if e}
        if cc:
            v = sum(x["values"] for x in cc)
            row["values_with_citation"] = sum(x["with_box"] for x in cc) / v if v else None
            wb = sum(x["with_box"] for x in cc)
            row["citations_showing_value"] = sum(x["box_shows_value"] for x in cc) / wb if wb else None
        conf = confidence_check(scored)
        if conf:
            row["confidence_check"] = conf
        res["systems"][s.name] = row
    return res


def confidence_check(recs: list[dict]) -> Optional[dict]:
    """How often values labelled high and low were right (answer-key values with a citation label). A useful
    label has "high" right more often than "low"; a code change that breaks that shows up here."""
    st = {"high": [0, 0], "low": [0, 0]}
    for r in recs:
        cites = r.get("citations") or {}
        for f in (r.get("score") or {}).get("fields") or []:
            cs = cites.get(f.get("path")) or []
            lab = cs[0].get("confidence") if cs and isinstance(cs[0], dict) else None
            if lab in st and f.get("got") is not None:
                st[lab][0] += 1
                st[lab][1] += bool(f.get("ok"))
    if not any(n for n, _ in st.values()):
        return None
    return {f"{k}_values": n for k, (n, _) in st.items()} | {f"{k}_right": (ok / n if n else None) for k, (n, ok) in st.items()}
