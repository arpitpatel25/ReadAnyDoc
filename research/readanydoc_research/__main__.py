"""Development commands: ``python -m readanydoc_research bench ...`` (olmOCR-bench sample scores, ablations,
grounding report, and the LLM-judge comparison used early in development)."""

from __future__ import annotations

import argparse
import json
import sys

from . import profiles  # noqa: F401  (registers the development profiles)


def _bench(args: argparse.Namespace) -> int:
    from .bench import olm, run

    stems = None if args.full else olm.dev_subset()
    if args.ablations:
        run.run_ablations(args.profile or ["cached-haiku", "cached-glm", "quality", "cheap"], full=args.full)
        print(f"\nwrote {run.ABLATIONS_FILE}")
        return 0
    if getattr(args, "ground", False):
        from .bench import ground
        for profile in args.profile or ["cached-haiku"]:
            rep = ground.ground_report(profile, stems)
            print(json.dumps(rep, indent=1) if args.json else ground.format_report(rep))
        return 0
    print(f"pages: {'140 (full)' if args.full else '30 (dev subset)'}\n{olm.HEADER}")
    for system in args.systems or []:
        print(olm.score(system, olm.cached_markdown(system), stems).row())
    for profile in args.profile or []:
        res, runner = run.score_pipeline(f"pipeline:{profile}", run.bench_config(profile), stems)
        print(res.row())
        if runner.errors:
            print(f"  pipeline errors on {len(runner.errors)} pages: {list(runner.errors.items())[:3]}")
        if args.metrics:
            print("  " + json.dumps(runner.side_metrics()))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m readanydoc_research")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bench", help="score cached systems / pipeline profiles on olmOCR-bench")
    b.add_argument("--systems", nargs="*", help="cached systems under spike/out (haiku, glm_pdf, ...)")
    b.add_argument("--profile", nargs="*", help="pipeline profiles (cached-haiku, cached-glm)")
    b.add_argument("--full", action="store_true", help="all 140 pages instead of the 30-page dev subset")
    b.add_argument("--ablations", action="store_true", help="stage on/off table -> research/results/ABLATIONS.md")
    b.add_argument("--metrics", action="store_true", help="print grounding / triage side metrics")
    b.add_argument("--ground", action="store_true",
                   help="grounding report: citation coverage, box correctness, confidence vs failed tests")
    b.add_argument("--json", action="store_true", help="with --ground: print the report as JSON")
    from .bench.judge import run as _judge_run
    _judge_run.add_arguments(b)
    b.set_defaults(fn=lambda a: _judge_run.main(a) if a.judge else _bench(a))

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
