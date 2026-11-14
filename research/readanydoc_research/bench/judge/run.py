"""`python -m readanydoc_research bench --judge`: resolve pages, page images and parses, judge, summarise."""

from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from readanydoc import paths
from . import prompt as P
from .core import DEFAULT_EFFORT, DEFAULT_JUDGE_MODEL, BudgetExceeded, Judge, JudgeError, PageJudgment, cache_root
from .summary import PageResult, format_summary, summarize

SAMPLE_SEED = 20261111


@dataclass
class PageSet:
    ids: list[str] = field(default_factory=list)
    images: dict[str, Path] = field(default_factory=dict)      # id -> image given with the page set
    category: dict[str, str] = field(default_factory=dict)     # id -> category, when known


def _manifest_categories(folder: Path) -> dict[str, str]:
    for cand in (folder / "manifest.jsonl", folder.parent / "manifest.jsonl"):
        if cand.exists():
            rows = [json.loads(line) for line in cand.read_text(encoding="utf-8").splitlines() if line.strip()]
            return {r["id"]: r.get("category", "") for r in rows if "id" in r}
    return {}


def resolve_pages(specs: list[str], images_dir: Optional[str] = None) -> PageSet:
    """Each spec is a directory of page PNGs (id = file stem), a .txt/.json/.jsonl list of ids,
    ``olm`` (the 140 bench pages), ``olm-dev`` (the 30-page dev subset) or one page id."""
    ps = PageSet()

    def add(pid: str, image: Optional[Path] = None, cat: Optional[str] = None) -> None:
        if pid not in ps.images and pid not in ps.ids:
            ps.ids.append(pid)
        if image is not None:
            ps.images[pid] = image
        if cat:
            ps.category[pid] = cat

    for spec in specs:
        p = Path(spec)
        if spec in ("olm", "olm-dev"):
            from .. import olm
            keep = set(olm.dev_subset()) if spec == "olm-dev" else None
            for cat, stems in olm.pages_by_cat().items():
                for s in stems:
                    if keep is None or s in keep:
                        add(s, cat=cat)
        elif p.is_dir():
            cats = _manifest_categories(p)
            for f in sorted(p.glob("*.png")):
                add(f.stem, f, cats.get(f.stem))
        elif p.is_file():
            text = p.read_text(encoding="utf-8")
            if p.suffix == ".json":
                ids = [x["id"] if isinstance(x, dict) else str(x) for x in json.loads(text)]
            elif p.suffix == ".jsonl":
                ids = [json.loads(line)["id"] for line in text.splitlines() if line.strip()]
            else:
                ids = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
            for i in ids:
                add(i)
        else:
            add(spec)
    if images_dir:
        for pid in ps.ids:
            f = Path(images_dir) / f"{pid}.png"
            if f.exists():
                ps.images[pid] = f
    if any(pid not in ps.category for pid in ps.ids):
        try:
            from .. import olm
            for cat, stems in olm.pages_by_cat().items():
                for s in stems:
                    if s in ps.ids:
                        ps.category.setdefault(s, cat)
        except Exception:
            pass
    return ps


def sample(ps: PageSet, n: int, seed: int = SAMPLE_SEED) -> list[str]:
    """n pages, spread evenly over the known categories (round-robin), fixed seed."""
    rng = random.Random(seed)
    groups: dict[str, list[str]] = {}
    for pid in sorted(ps.ids):
        groups.setdefault(ps.category.get(pid, ""), []).append(pid)
    for g in groups.values():
        rng.shuffle(g)
    out: list[str] = []
    cats = sorted(groups)
    while len(out) < min(n, len(ps.ids)):
        for c in cats:
            if groups[c] and len(out) < n:
                out.append(groups[c].pop())
    return out


def judge_image(pid: str, given: Optional[Path]) -> Optional[Path]:
    """Page image at the judge model's native size (BUILD_SPEC image-sizing rule). Bench pages are
    rendered from the PDF; rasters that are too large are scaled down; nothing is scaled up."""
    from readanydoc.readers import sizing
    from PIL import Image

    out = cache_root() / "img" / f"{pid}.png"
    if given is None:
        pdf = paths.bench_pdf(pid)
        if pdf is not None:
            if not out.exists():
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(sizing.render_pdf_native(pdf, 1))
            return out
        given = paths.bench_image(pid)
        if given is None:
            return None
    with Image.open(given) as im:
        w, h = im.size
    if sizing.fits(w, h):
        return given
    if not out.exists() or out.stat().st_mtime < Path(given).stat().st_mtime:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(sizing.resize_image_native(given, max_scale=1.0))
    return out


def system_dirs(systems: list[str], parses_root: Optional[str]) -> dict[str, Path]:
    """``name`` -> <parses root>/<name>; ``name=/some/dir`` names a directory outright."""
    root = Path(parses_root) if parses_root else paths.spike_dir() / "out"
    out = {}
    for s in systems:
        name, _, d = s.partition("=")
        out[name] = Path(d) if d else root / name
    return out


def run_judge(page_ids: list[str], images: dict[str, Path], sysdirs: dict[str, Path], judge: Judge,
              workers: int = 3, dry_run: bool = False, log=print) -> tuple[list[PageResult], dict]:
    results: list[PageResult] = []
    judgments: dict[str, PageJudgment] = {}
    todo, info = [], {"cached": 0, "judged": 0, "to_judge": 0, "no_image": [], "errors": {}, "not_well_formed": []}
    for pid in page_ids:
        img = judge_image(pid, images.get(pid))
        if img is None:
            info["no_image"].append(pid)
            continue
        md = {}
        for name, d in sysdirs.items():
            f = d / f"{pid}.md"
            if f.exists():
                md[name] = f.read_text(encoding="utf-8", errors="replace")
            else:
                results.append(PageResult(pid, name, "failed"))
        if not md:
            continue
        hit = judge.cached(pid, img, md)
        if hit is not None:
            judgments[pid] = hit
            info["cached"] += 1
        else:
            todo.append((pid, img, md))
    info["to_judge"] = len(todo)
    if not dry_run and todo:
        def one(item):
            pid, img, md = item
            try:
                return pid, judge.judge_page(pid, img, md), None
            except (JudgeError, BudgetExceeded) as e:
                return pid, None, f"{type(e).__name__}: {e}"
        with ThreadPoolExecutor(max(1, min(workers, 4))) as ex:
            for pid, j, err in ex.map(one, todo):
                if j is None:
                    info["errors"][pid] = err
                    log(f"  FAILED {pid[:50]}: {err}")
                else:
                    judgments[pid] = j
                    info["judged"] += 1
                    log(f"  judged {pid[:50]}: " + " ".join(f"{s}={v.score}" for s, v in j.verdicts.items()))
    for pid, j in judgments.items():
        if not j.well_formed:
            info["not_well_formed"].append({"page": pid, "repairs": j.repairs})
        for name, v in j.verdicts.items():
            results.append(PageResult(pid, name, "succeeded", v))
    info["calls"], info["cost_usd"] = judge.calls, round(judge.cost_usd, 4)
    order = {s: i for i, s in enumerate(sysdirs)}
    results.sort(key=lambda r: (page_ids.index(r.page_id), order[r.system]))
    return results, info


def add_arguments(b: argparse.ArgumentParser) -> None:
    b.add_argument("--judge", action="store_true",
                   help="Parse Judge (Reducto's open judge): blind 1-10 scoring of --systems on --pages")
    b.add_argument("--pages", nargs="*", help="directory of page PNGs, list file, 'olm', 'olm-dev' or page ids")
    b.add_argument("--images", help="directory with <id>.png page images (when --pages is a list)")
    b.add_argument("--parses", help="root with <system>/<id>.md (default: spike/out); or give systems as name=dir")
    b.add_argument("--baseline", help="system the win rate is computed against")
    b.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    b.add_argument("--judge-effort", default=DEFAULT_EFFORT)
    b.add_argument("--sample", type=int, help="judge only N pages, spread over categories")
    b.add_argument("--seed", type=int, default=SAMPLE_SEED)
    b.add_argument("--max-calls", type=int, help="stop after this many live judge calls (retries count)")
    b.add_argument("--workers", type=int, default=3)
    b.add_argument("--dry-run", action="store_true", help="report cache hits and pending calls; call nothing")
    b.add_argument("--out", help="write the full report (summary + per-page verdicts) to this JSON file")


def main(args: argparse.Namespace) -> int:
    if not args.systems:
        print("--judge needs --systems", file=sys.stderr)
        return 2
    ps = resolve_pages(args.pages or ["olm-dev"], args.images)
    ids = sample(ps, args.sample, args.seed) if args.sample else ps.ids
    sysdirs = system_dirs(args.systems, args.parses)
    missing = [str(d) for d in sysdirs.values() if not d.is_dir()]
    if missing:
        print(f"no such parse directories: {missing}", file=sys.stderr)
        return 2
    judge = Judge(model=args.judge_model, effort=args.judge_effort, max_calls=args.max_calls)
    print(f"Parse Judge {P.JUDGE_VERSION} (parsejudge@{P.SOURCE_COMMIT[:8]}), judge model {args.judge_model}, "
          f"{len(ids)} pages, systems: {', '.join(sysdirs)}")
    results, info = run_judge(ids, ps.images, sysdirs, judge, args.workers, args.dry_run)
    print(f"cached {info['cached']}, judged now {info['judged']}, pending {info['to_judge'] - info['judged']}, "
          f"live calls {info['calls']}, cost ${info['cost_usd']}, errors {len(info['errors'])}, "
          f"answers needing repair {len(info['not_well_formed'])}, pages without image {len(info['no_image'])}\n")
    s = summarize(results, args.baseline)
    print(format_summary(s))
    cats = sorted(set(ps.category.get(i, "") for i in ids) - {""})
    if len(cats) > 1:
        print("\nmean score by category")
        print(f"{'system':24s} " + " ".join(f"{c[:12]:>12s}" for c in cats))
        for name in sysdirs:
            row = []
            for c in cats:
                sc = [r.verdict.score for r in results if r.system == name and r.verdict and ps.category.get(r.page_id) == c]
                row.append(f"{sum(sc) / len(sc):12.2f}" if sc else f"{'-':>12s}")
            print(f"{name[:24]:24s} " + " ".join(row))
    if args.out:
        rep = {"judge_version": P.JUDGE_VERSION, "source_commit": P.SOURCE_COMMIT, "judge_model": args.judge_model,
               "pages": ids, "categories": {i: ps.category.get(i) for i in ids}, "info": info, "summary": s,
               "results": [{"page_id": r.page_id, "system": r.system, "status": r.status,
                            "score": r.verdict.score if r.verdict else None,
                            "errors": r.verdict.errors if r.verdict else None,
                            "reasoning": r.verdict.reasoning if r.verdict else None} for r in results]}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 1 if info["errors"] else 0
