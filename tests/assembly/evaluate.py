"""Precision / recall of the assembly joins on the synthetic set.

    uv run python -m tests.assembly.evaluate            # prints the table
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from readanydoc.model import Document, RegionType
from readanydoc.pipeline import PipelineConfig, PipelineContext
from readanydoc.pipeline.assemble import Assemble
from readanydoc.textnorm import plain_text

from . import synth


def assemble(doc: Document, **options) -> Document:
    return Assemble(enabled=True, **options).run(doc, PipelineContext(PipelineConfig()))


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 3), "recall": round(r, 3)}


def _edges(levels: list[tuple[str, int]]) -> set:
    out, stack = set(), []
    for i, (title, level) in enumerate(levels):
        while stack and stack[-1][1] >= level:
            stack.pop()
        out.add((stack[-1][0] if stack else None, i))
        stack.append((i, level))
    return out


def evaluate(out_dir: Path | None = None, heading_style: str = "flat", **options) -> dict:
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="readanydoc-synth-"))
    truths = synth.make_set(out_dir)
    counts = {k: [0, 0, 0] for k in ("paragraph", "table", "furniture", "heading_edges")}
    level_ok = level_ok_before = level_n = 0
    misses: list[str] = []
    for t in truths:
        doc = assemble(synth.read_pdf(t.path, heading_style), merge_tables=True, **options)
        page_of = {r.id: p.index for p in doc.pages for r in p.regions}
        for kind, truth in (("paragraph", t.para_joins), ("table", t.table_joins)):
            pred = {page_of[l.from_region] for l in doc.links
                    if l.kind == f"{kind}_continuation" and l.confidence.label == "high"}
            counts[kind][0] += len(pred & truth)
            counts[kind][1] += len(pred - truth)
            counts[kind][2] += len(truth - pred)
            for p in pred - truth:
                misses.append(f"{t.name}: false {kind} join after page {p + 1}")
            for p in truth - pred:
                misses.append(f"{t.name}: missed {kind} join after page {p + 1} ({t.kinds.get(p, '')})")
        for p in doc.pages:
            for r in p.regions:
                text = plain_text(r.content)
                is_furn = text in (t.header, t.footer) or text == f"Page {p.index + 1}"
                want = None
                if is_furn:
                    want = "Header" if text == t.header else "Footer" if text == t.footer else "Page Number"
                got = r.type.value if r.type in (RegionType.HEADER, RegionType.FOOTER, RegionType.PAGE_NUMBER) else None
                if want and got == want:
                    counts["furniture"][0] += 1
                elif got and got != want:
                    counts["furniture"][1] += 1
                    misses.append(f"{t.name}: {text!r} typed {got}, expected {want}")
                if want and got != want:
                    counts["furniture"][2] += 1
                    if not got:
                        misses.append(f"{t.name}: furniture {text!r} on page {p.index + 1} not found")
        heads = [r for p in doc.pages for r in p.regions if r.type in (RegionType.TITLE, RegionType.SECTION_HEADER)]
        got_levels = [(plain_text(r.content), r.heading_level or 0) for r in heads]
        before = [(plain_text(r.content), r.meta.get("heading_level_before", r.heading_level) or 0) for r in heads]
        if [g[0] for g in got_levels] == [h[0] for h in t.headings]:
            for (_, a), (_, b), (_, c) in zip(got_levels, t.headings, before):
                level_n += 1
                level_ok += a == b
                level_ok_before += c == b
            te, pe = _edges(t.headings), _edges(got_levels)
            counts["heading_edges"][0] += len(te & pe)
            counts["heading_edges"][1] += len(pe - te)
            counts["heading_edges"][2] += len(te - pe)
        else:
            misses.append(f"{t.name}: heading list differs from the truth")
    out = {k: _prf(*v) for k, v in counts.items()}
    out["heading_levels"] = {"n": level_n, "correct_after": level_ok, "correct_before": level_ok_before}
    out["documents"] = len(truths)
    out["pages"] = sum(t.pages for t in truths)
    out["misses"] = misses
    return out


def main() -> None:
    for style in ("flat", "per_page"):
        res = evaluate(heading_style=style)
        print(f"\nheading_style={style}: {res['documents']} documents, {res['pages']} pages")
        for k in ("paragraph", "table", "furniture", "heading_edges"):
            print(f"  {k:14s} {res[k]}")
        print(f"  heading_levels {res['heading_levels']}")
        for m in res["misses"]:
            print("   -", m)


if __name__ == "__main__":
    main()
