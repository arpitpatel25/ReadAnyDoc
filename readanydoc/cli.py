"""readanydoc command line.

    readanydoc parse invoice.pdf [--mode engine|model-only] [--model claude-haiku-5-5] [--markdown]
    readanydoc extract invoice.pdf --schema schema.json [--instructions "..."]
    readanydoc serve [--port 8000]
    readanydoc cleanup
    readanydoc bench list | run <set> [--systems a,b] [--limit N] | mine <folder> --schema s.json
"""

from __future__ import annotations

import argparse
import json
import sys


def _model_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mode", default="engine", choices=["engine", "model-only"])
    p.add_argument("--model", default="claude-haiku-5-5", help="page-reading model (default: %(default)s)")
    p.add_argument("--effort", default="high", help="the model's effort level (default: %(default)s)")
    p.add_argument("--model-options", type=json.loads, metavar="JSON", help="further request fields for the model")
    p.add_argument("--handwriting-model", default="claude-sonnet-5-5",
                   help="reader for handwritten pages in engine mode; 'none' reads them like other pages")
    p.add_argument("--pages", help="comma-separated 1-based page numbers")


def _common(args: argparse.Namespace) -> dict:
    return {"mode": args.mode, "model": args.model, "effort": args.effort, "model_options": args.model_options,
            "handwriting_model": None if args.handwriting_model.lower() == "none" else args.handwriting_model,
            "pages": [int(p) for p in args.pages.split(",")] if args.pages else None}


def _warn(warnings: list[str]) -> None:
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)


def _parse(args: argparse.Namespace) -> int:
    if args.profile:  # a registered profile by name (development configurations, see research/)
        return _parse_profile(args)
    from .library import parse
    result = parse(args.input, citations=args.citations, **_common(args))
    _warn(result.warnings)
    if args.markdown:
        sys.stdout.write(result.markdown + "\n")
    else:
        json.dump(result.raw, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    print(f"{result.pages} pages, {result.usage.calls} model calls, "
          f"${result.usage.cost_usd if result.usage.cost_usd is not None else '?'}, {result.usage.seconds} s",
          file=sys.stderr)
    from .star import maybe_ask
    maybe_ask()
    return 0


def _parse_profile(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline
    from .profiles import get_profile
    from .serialize import to_parse_response
    cfg = get_profile(args.profile)
    cfg.parse.return_citations = args.citations
    if args.pages:
        cfg.parse.page_range = [int(p) for p in args.pages.split(",")]
    doc = Pipeline(cfg).run(args.input)
    _warn([f"{n.stage}: {n.message}" for n in doc.notes if n.level in ("warning", "error")])
    json.dump(to_parse_response(doc), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def _fill(v):
    if v is None:
        return "choices"
    if v == []:
        return True
    if v == ["none"]:
        return False
    return v


def _extract(args: argparse.Namespace) -> int:
    from .library import extract
    with open(args.schema) as f:
        schema = json.load(f)
    instructions = args.instructions
    if instructions and instructions.startswith("@"):  # long rubrics live in a file: --instructions @rules.md
        with open(instructions[1:], encoding="utf-8") as f:
            instructions = f.read()
    alts = args.alternatives
    alts = True if alts == [] else (alts or False)
    result = extract(args.input, schema, instructions=instructions, citations=not args.no_citations,
                     extract_model=args.extract_model, review=args.review, alternatives=alts,
                     derived_values=not args.printed_only, fill_required=_fill(args.fill_required),
                     left_out_warnings=not args.no_left_out_warnings, **_common(args))
    _warn(result.warnings)
    out = {"data": result.data, "citations": {k: [c.model_dump() for c in v] for k, v in result.citations.items()},
           **({"review": result.review} if args.review else {}),
           **({"alternatives": result.alternatives} if alts else {}), "usage": result.usage.model_dump()}
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    from .star import maybe_ask
    maybe_ask()
    return 0


def _cleanup(args: argparse.Namespace) -> int:
    """Same pass the server's janitor runs every READANYDOC_JANITOR_SECONDS; safe while a server is running."""
    from .api.config import ServerSettings
    from .store import Database, FileStore, JobStore, cleanup
    s = ServerSettings()
    db = Database(s.data_dir / "readanydoc.sqlite3")
    rep = cleanup.clean(FileStore(db, s.data_dir / "files"), JobStore(db, s.data_dir / "jobs"), data_dir=s.data_dir,
                        cache_root=s.cache_dir, scratch_ttl=s.cache_ttl, reused_ttl=s.reused_cache_ttl,
                        max_data_bytes=s.max_data_bytes, max_cache_bytes=s.max_cache_bytes, min_age=s.clean_min_age)
    db.close()
    if args.json:
        print(json.dumps(rep.as_dict()))
    else:
        print(f"freed {rep.bytes_freed / 1e6:.1f} MB: {rep.jobs_expired} expired jobs, {rep.files_expired} expired "
              f"files, {rep.orphans_removed} orphan folders, {rep.jobs_evicted} jobs and {rep.files_evicted} files "
              f"over the storage limit, {rep.cache_files_removed} cache entries; data folder "
              f"{rep.data_bytes / 1e6:.1f} MB, caches {rep.cache_bytes / 1e6:.1f} MB")
        for n in rep.notes:
            print("note:", n)
    return 0


def _mcp(args) -> int:
    from .mcp_server import main as run
    run()
    return 0


def _bench(args: argparse.Namespace) -> int:
    from . import bench
    if args.bench_cmd == "list":
        rows = bench.sets()
        if args.json:
            print(json.dumps(rows, indent=1))
            return 0
        for r in rows:
            print(f"{r['id']:20} {r['documents']:4} docs  {r['document_type']:28} {r['licence']}")
            print(f"{'':20} {r['what_it_tests']}")
        print("\nSystems: " + ", ".join(bench.systems.NAMES) + "   (your own keys: ANTHROPIC_API_KEY, REDUCTO_API_KEY)")
        return 0
    if args.bench_cmd == "rescore":
        summary = bench.rescore(args.out)
        for name, r in summary["systems"].items():
            print(f"{name:28} values found {100 * (r.get('recall') or 0):.1f}%  right {100 * (r.get('precision') or 0):.1f}%")
        print(f"Report: {summary['report']}")
        return 0
    specs = [s.strip() for s in args.systems.split(",") if s.strip()] if args.systems else None
    if args.bench_cmd == "run":
        plan = bench.prepare(args.set, specs, args.limit, args.out)
    else:
        instr = args.instructions
        if instr and instr.startswith("@"):
            instr = open(instr[1:]).read()
        plan = bench.prepare(None, specs, args.limit, args.out, folder=args.folder, schema=args.schema,
                             answers=args.answers, instructions=instr)
    est = plan["estimate"]
    print(f"{plan['title']}: {est['documents']} documents, {est['pages']} pages")
    for name, usd in est["estimated_usd"].items():
        why = plan["not_ready"].get(name)
        print(f"  {name:28} about ${usd:.2f}" + (f"   SKIPPED: {why}" if why else ""))
    print(f"  ({est['note']})")
    if plan["info"]:
        print(f"Licence: {plan['info']['licence']}")
    if not args.yes:
        if not sys.stdin.isatty():
            print("Not run: add --yes to confirm the cost when there is no terminal to ask.")
            return 2
        if input("Run it? [y/N] ").strip().lower() not in ("y", "yes"):
            return 1

    def done(system: str, case: str, rec: dict) -> None:
        sc = rec.get("score")
        print(f"  {system:28} {case[:40]:40} " + (f"{100 * sc['recall']:5.1f}%" if sc else "     ") +
              f"  ${rec.get('cost_usd') or 0:.4f}  {rec.get('seconds')} s" + (f"  ERROR {rec['error'][:80]}" if rec.get("error") else ""),
              flush=True)
    summary = bench.run_prepared(plan, budget_usd=args.budget, workers=args.workers, fresh=args.fresh, on_done=done)
    print()
    for name, r in summary["systems"].items():
        rec = "" if r.get("recall") is None else f"values found {100 * r['recall']:.1f}%  right {100 * (r.get('precision') or 0):.1f}%  "
        if r.get("schema_valid") is not None:
            rec += f"schema valid {100 * r['schema_valid']:.0f}%  "
        print(f"{name:28} {rec}cost/doc ${r.get('cost_per_document_usd') or 0:.4f}  median {r.get('median_seconds')} s  "
              f"failed {r.get('failed', 0)}")
    if summary.get("stopped_by_budget"):
        print("Stopped early: the budget was reached.")
    print(f"\nReport: {summary['report']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="readanydoc", description="Parse and extract documents with vision models.",
                                 epilog="Docs: https://arpitpatel25.github.io/ReadAnyDoc/docs/  Questions: "
                                        "https://github.com/arpitpatel25/ReadAnyDoc/discussions")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("parse", help="parse a document; prints Reducto-shaped JSON (or Markdown)")
    p.add_argument("input", help="a PDF, image, Office or text file")
    _model_args(p)
    p.add_argument("--citations", action="store_true", help="word and cell positions in the result")
    p.add_argument("--markdown", action="store_true", help="print the document as Markdown instead of JSON")
    p.add_argument("--profile", help=argparse.SUPPRESS)
    p.set_defaults(fn=_parse)

    x = sub.add_parser("extract", help="extract fields described by a JSON Schema; prints values and citations")
    x.add_argument("input")
    x.add_argument("--schema", required=True, help="path to a JSON Schema file")
    x.add_argument("--instructions", help="system prompt for the extraction model, or @file to read it from a file")
    x.add_argument("--extract-model", help="model that fills the schema (default: --model)")
    x.add_argument("--no-citations", action="store_true")
    x.add_argument("--review", action="store_true",
                   help="also list values to check (not printed as returned, ambiguous dates, crossed out, lists "
                        "shorter than their table); found by code, no extra model call")
    x.add_argument("--no-left-out-warnings", action="store_true",
                   help="do not warn about printed values the output left out (on by default; code only)")
    x.add_argument("--alternatives", nargs="*", metavar="FIELD",
                   help="also return other values the document gives for a field (a special term, an amendment); "
                        "for every field, or only the fields named")
    x.add_argument("--fill-required", nargs="*", metavar="FIELD",
                   help="always give a value to required fields: alone, every required field (numbers and text "
                        "too: the model may guess, and each guess is flagged in --review); with FIELDs, only those; "
                        "'none' for no field. Default: required yes/no and enum fields only")
    x.add_argument("--printed-only", action="store_true",
                   help="only values printed in the document: never one worked out or assumed")
    _model_args(x)
    x.set_defaults(fn=_extract)

    s = sub.add_parser("serve", help="run the HTTP API (Reducto-compatible)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=lambda a: __import__("readanydoc.api.__main__", fromlist=["serve"]).serve(a.host, a.port))

    c = sub.add_parser("cleanup", help="one clean-up pass over the server's data folder and working caches")
    c.add_argument("--json", action="store_true", help="print the report as JSON")
    c.set_defaults(fn=_cleanup)

    m = sub.add_parser("mcp", help="run as an MCP server (stdio) for Claude, Cursor and other agents")
    m.set_defaults(fn=_mcp)

    b = sub.add_parser("bench", help="test ReadAnyDoc against other systems on public test sets or your own documents")
    bs = b.add_subparsers(dest="bench_cmd", required=True)
    bl = bs.add_parser("list", help="the test sets and systems")
    bl.add_argument("--json", action="store_true")
    for name, hlp in (("run", "run a public test set"), ("mine", "run your own documents")):
        q = bs.add_parser(name, help=hlp)
        if name == "run":
            q.add_argument("set", help="a test set id (readanydoc bench list)")
        else:
            q.add_argument("folder", help="a folder of documents")
            q.add_argument("--schema", required=True, help="a JSON Schema file for every document")
            q.add_argument("--answers", help="a folder of answer keys, <file stem>.json (optional)")
            q.add_argument("--instructions", help="instructions for every system, or @file")
        q.add_argument("--systems", help="comma-separated: readanydoc, readanydoc-one-stage, readanydoc-model-only, "
                                         "plain:<model>, reducto (default: readanydoc,plain:claude-haiku-5-5)")
        q.add_argument("--limit", type=int, help="number of documents (default: the set's sample size)")
        q.add_argument("--budget", type=float, help="stop starting new documents once this many dollars are spent")
        q.add_argument("--workers", type=int, default=4)
        q.add_argument("--out", help="results folder (default: ./readanydoc-bench/<set>-<time>)")
        q.add_argument("--fresh", action="store_true", help="run documents again even if results exist")
        q.add_argument("--yes", action="store_true", help="do not ask before spending")
    rs = bs.add_parser("rescore", help="score a finished run again and rewrite its report (no model call)")
    rs.add_argument("out", help="the results folder of a run")
    b.set_defaults(fn=_bench)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
