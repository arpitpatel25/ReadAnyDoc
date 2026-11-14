"""readanydoc command line.

    readanydoc parse invoice.pdf [--mode engine|model-only] [--model claude-haiku-5-5] [--markdown]
    readanydoc extract invoice.pdf --schema schema.json [--instructions "..."]
    readanydoc serve [--port 8000]
    readanydoc cleanup
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


def _extract(args: argparse.Namespace) -> int:
    from .library import extract
    with open(args.schema) as f:
        schema = json.load(f)
    result = extract(args.input, schema, instructions=args.instructions, citations=not args.no_citations,
                     extract_model=args.extract_model, **_common(args))
    _warn(result.warnings)
    out = {"data": result.data, "citations": {k: [c.model_dump() for c in v] for k, v in result.citations.items()},
           "usage": result.usage.model_dump()}
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="readanydoc", description="Parse and extract documents with vision models.")
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
    x.add_argument("--instructions", help="system prompt for the extraction model")
    x.add_argument("--extract-model", help="model that fills the schema (default: --model)")
    x.add_argument("--no-citations", action="store_true")
    _model_args(x)
    x.set_defaults(fn=_extract)

    s = sub.add_parser("serve", help="run the HTTP API (Reducto-compatible)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=lambda a: __import__("readanydoc.api.__main__", fromlist=["serve"]).serve(a.host, a.port))

    c = sub.add_parser("cleanup", help="one clean-up pass over the server's data folder and working caches")
    c.add_argument("--json", action="store_true", help="print the report as JSON")
    c.set_defaults(fn=_cleanup)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
