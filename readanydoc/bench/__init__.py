"""Test ReadAnyDoc against other systems on public test sets or on your own documents.

    readanydoc bench list
    readanydoc bench run invoices --systems readanydoc,plain:claude-haiku-5-5,reducto --limit 10
    readanydoc bench mine ./my_docs --schema schema.json [--answers ./answer_keys]

Every system uses your own key (``ANTHROPIC_API_KEY``, ``REDUCTO_API_KEY``). A run first shows what it will cost
roughly and asks before spending; ``budget_usd`` stops it from starting more documents once that much is spent.
Scoring is done by code against the answer key, never by a model. The report (``report.html``) has a summary per
system, every document, and a page viewer with each system's citation boxes.

Python:

    from readanydoc import bench
    bench.sets()                                         # the catalogue
    plan = bench.prepare("invoices", ["readanydoc", "reducto"], limit=10)
    plan["estimate"]                                     # documents, pages, rough cost per system
    bench.run_prepared(plan, budget_usd=2)               # -> summary, with plan["out"]/report.html
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from . import catalog, report, runner, systems

DEFAULT_SYSTEMS = ["readanydoc", "plain:claude-haiku-5-5"]


def sets() -> list[dict]:
    return [s.info() for s in catalog.SETS.values()]


def _out(name: str, out: Optional[str]) -> str:
    if out:
        return out
    return str(Path.cwd() / "readanydoc-bench" / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}")


def prepare(set_id: Optional[str] = None, system_specs: Optional[list[str]] = None, limit: Optional[int] = None,
            out: Optional[str] = None, folder: Optional[str] = None, schema: Optional[str] = None,
            answers: Optional[str] = None, instructions: Optional[str] = None) -> dict:
    """Download the documents and work out the plan and its rough cost; nothing is sent to any model."""
    specs = system_specs or DEFAULT_SYSTEMS
    syss = [systems.make(s) for s in specs]
    if folder:
        if not schema:
            raise ValueError("your own documents need a schema (a JSON Schema file)")
        cases = catalog.own_documents(folder, schema, answers, instructions)
        title, info = f"Your documents: {Path(folder).name}", None
    else:
        ts = catalog.get(set_id or "")
        cases = ts.load(limit if limit is not None else ts.default_limit)
        title, info = ts.title, ts.info()
    if limit and folder:
        cases = cases[:limit]
    not_ready = {s.name: why for s in syss if (why := s.ready())}
    return {"title": title, "info": info, "cases": cases, "systems": syss, "out": _out(set_id or "mine", out),
            "estimate": runner.estimate(cases, syss), "not_ready": not_ready,
            "request": {"set_id": set_id, "systems": specs, "limit": limit, "folder": folder, "schema": schema,
                        "answers": answers, "instructions": instructions}}


def run_prepared(plan: dict, budget_usd: Optional[float] = None, workers: int = 4, fresh: bool = False,
                 on_done=None) -> dict:
    syss = [s for s in plan["systems"] if s.name not in plan["not_ready"]]
    Path(plan["out"]).mkdir(parents=True, exist_ok=True)
    (Path(plan["out"]) / "plan.json").write_text(json.dumps(plan["request"], indent=1))
    if not syss:
        raise RuntimeError("no system can run: " + "; ".join(f"{k}: {v}" for k, v in plan["not_ready"].items()))
    summary = runner.run(plan["cases"], syss, plan["out"], budget_usd, workers, fresh, on_done)
    summary["skipped_systems"] = plan["not_ready"]
    summary["report"] = report.write(plan["out"], plan["cases"], [s.name for s in syss], plan["title"], summary,
                                     plan["info"])
    report.write_json(plan["out"], summary)
    return summary


def rescore(out: str) -> dict:
    """Score saved results again (after a grader or answer-key fix) and rewrite the report; no model is called."""
    req = json.loads((Path(out) / "plan.json").read_text())
    plan = prepare(req["set_id"], req["systems"], req["limit"], out, folder=req["folder"], schema=req["schema"],
                   answers=req["answers"], instructions=req["instructions"])
    names = []
    for s in plan["systems"]:
        for c in plan["cases"]:
            p = Path(out) / s.name.replace(":", "_") / f"{c['id']}.json"
            if not p.exists():
                continue
            rec = json.loads(p.read_text())
            if c.get("gold") is not None:
                rec["score"] = runner.grade.score_case(c, rec.get("data"))
            rec["citation_check"] = runner.citation_check(c, rec)
            p.write_text(json.dumps(rec, default=str))
            if s.name not in names:
                names.append(s.name)
    ran = [s for s in plan["systems"] if s.name in names]
    summary = runner.summarize(out, plan["cases"], ran)
    summary["report"] = report.write(out, plan["cases"], names, plan["title"], summary, plan["info"])
    report.write_json(out, summary)
    return summary


__all__ = ["rescore", "sets", "prepare", "run_prepared", "catalog", "systems", "runner", "report", "DEFAULT_SYSTEMS"]
