"""ChartParse-Bench (Unsiloed, CC-BY-4.0): 58 synthetic charts with their exact data. Reads each chart with
ReadAnyDoc's chart reader (the same call a parse makes per chart) and scores it by code.

    python research/benchmarks/charts.py run   OUT [--replay DIR] [--replay-only]   # about $0.003 a chart
    python research/benchmarks/charts.py score OUT                                  # no model call

Score per chart, following the dataset card: series found x points covered x accuracy (mark types are left out:
ReadAnyDoc does not return them). Series are paired by shape (smallest RMS error first), the error is relative to
each series' own range, log10 on log axes. Measured 2026-11-25: 0.71 before dense sampling, 0.83 after (0.4.8).
"""
import argparse
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

REPO, REV = "Unsiloed/chart-parse-bench", "ba7316eb4c398963163b8f2d21d97142db1d8ce6"


def data() -> list[dict]:
    from readanydoc.bench import fetch
    meta = [json.loads(l) for l in open(fetch.hf_file(REPO, "metadata.jsonl", REV)) if l.strip()]
    for m in meta:
        m["image_path"] = str(fetch.hf_file(REPO, m["image"], REV))
        m["truth_data"] = json.loads(fetch.hf_file(REPO, m["truth"], REV).read_text())
    return meta


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except Exception:
        return None


def run(out: Path, workers: int = 2) -> None:
    from readanydoc.pipeline.figures import _ask
    from readanydoc.tasks.llm import get_llm
    llm = get_llm("anthropic_api:haiku")
    out.mkdir(parents=True, exist_ok=True)

    def one(m):
        f = out / f"{m['id']}.json"
        if f.exists():
            return
        try:
            ans = _ask(llm, {"chart": True, "prompt": None}, m["image_path"])
        except Exception as e:
            f.write_text(json.dumps({"error": str(e)}))
            return
        series = []
        for s in (ans.get("chart") or {}).get("series") or []:
            pts = [(_num(p.get("x")), _num(p.get("value"))) for p in s.get("data") or [] if isinstance(p, dict)]
            pts = [(x, y) for x, y in pts if x is not None and y is not None]
            series.append({"label": s.get("label"), "x": [p[0] for p in pts], "y": [p[1] for p in pts]})
        f.write_text(json.dumps({"series": series, "raw": ans.get("chart"), "usage": ans.get("usage")}))
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, data()))
def tf(v, log):
    v = np.asarray(v, float)
    return np.log10(np.clip(v, 1e-300, None)) if log else v

def pair_err(t, p, logx, logy):
    tx, ty = tf(t["x"], logx), tf(t["y"], logy)
    px, py = tf(p["x"], logx), tf(p["y"], logy)
    ok = np.isfinite(px) & np.isfinite(py)
    px, py = px[ok], py[ok]
    if len(px) < 2: return 1e9, 0.0
    o = np.argsort(px); px, py = px[o], py[o]
    inside = (tx >= px.min()) & (tx <= px.max())
    if inside.sum() == 0: return 1e9, 0.0
    yi = np.interp(tx[inside], px, py)
    rng = (np.nanmax(ty) - np.nanmin(ty)) or 1.0
    rms = math.sqrt(float(np.nanmean(((yi - ty[inside]) / rng) ** 2)))
    return rms, float(inside.mean())

def score(truth, pred):
    T = [s for s in truth.get("series", []) if s.get("x") is not None and s.get("y") is not None]
    P = [s for s in (pred or {}).get("series", []) if s.get("x") and s.get("y") and len(s["x"]) == len(s["y"])]
    sc = (truth.get("axis_scales") or {}).get("panel0", {})
    logx, logy = sc.get("x") == "log", sc.get("y") == "log"
    if not T or not P: return {"score": 0.0, "recall": 0.0, "coverage": 0.0, "accuracy": 0.0, "rms": None}
    E = np.zeros((len(T), len(P))); C = np.zeros((len(T), len(P)))
    for i, t in enumerate(T):
        for j, p in enumerate(P):
            try: E[i, j], C[i, j] = pair_err(t, p, logx, logy)
            except Exception: E[i, j], C[i, j] = 1e9, 0
    r, c = _pairs(E)
    good = [(i, j) for i, j in zip(r, c) if E[i, j] < 0.5]
    recall = len(good) / len(T)
    if not good: return {"score": 0.0, "recall": 0.0, "coverage": 0.0, "accuracy": 0.0, "rms": None}
    cov = float(np.mean([C[i, j] for i, j in good])); rms = float(np.mean([E[i, j] for i, j in good]))
    acc = max(0.0, 1 - rms)
    return {"score": recall * cov * acc, "recall": recall, "coverage": cov, "accuracy": acc, "rms": rms}


def _pairs(E):  # smallest errors first (charts have at most 12 series)
    out, rows, cols = [], set(), set()
    for _, i, j in sorted((E[i, j], i, j) for i in range(E.shape[0]) for j in range(E.shape[1])):
        if i not in rows and j not in cols:
            out.append((i, j)); rows.add(i); cols.add(j)
    return [p[0] for p in out], [p[1] for p in out]


def score_all(out: Path) -> dict:
    res = {}
    for m in data():
        f = out / f"{m['id']}.json"
        pred = json.loads(f.read_text()) if f.exists() else None
        res[m["id"]] = {**score(m["truth_data"], pred), "family": m["family"]}
    total = {"charts": len(res), "score": float(np.mean([v["score"] for v in res.values()])),
             "series_recall": float(np.mean([v["recall"] for v in res.values()]))}
    (out / "chart_score.json").write_text(json.dumps({"total": total, "charts": res}, indent=1))
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "score"])
    ap.add_argument("out")
    ap.add_argument("--replay", help="record/replay model calls in this folder")
    ap.add_argument("--replay-only", action="store_true")
    a = ap.parse_args()
    if a.replay:
        os.environ["READANYDOC_REPLAY_DIR"] = os.path.abspath(a.replay)
        if a.replay_only:
            os.environ["READANYDOC_REPLAY_ONLY"] = "1"
    if a.cmd == "run":
        run(Path(a.out))
    print(json.dumps(score_all(Path(a.out))))
