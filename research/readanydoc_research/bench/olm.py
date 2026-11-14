"""olmOCR-bench scorer (port of spike/score_olm.py and the 5-category score of spike/router_sim.py).

The official test classes are loaded from ``spike/src/olmocr`` (Apache-2.0, not vendored). The
scorer imports playwright for math rendering, which none of our 5 categories need, so it is stubbed
exactly as in the spike.
"""

from __future__ import annotations

import json
import os
import random
import sys
import types
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Optional

from readanydoc import paths

CATS = ["headers_footers", "long_tiny_text", "multi_column", "old_scans", "table_tests"]
DEV_SEED = 20261111
DEV_PER_CAT = 6
DEV_FILE = paths.REPO_ROOT / "research" / "results" / "dev30.json"


def _install_stubs() -> None:
    if "olmocr.bench.tests" in sys.modules:
        return
    src = paths.spike_dir() / "src" / "olmocr" / "olmocr"
    if not src.is_dir():
        raise FileNotFoundError(f"olmOCR scorer source not found at {src}")
    pw = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.Error = Exception  # type: ignore[attr-defined]
    sync_api.sync_playwright = None  # type: ignore[attr-defined]
    sys.modules.setdefault("playwright", pw)
    sys.modules.setdefault("playwright.sync_api", sync_api)
    pkg = types.ModuleType("olmocr")
    pkg.__path__ = [str(src)]  # type: ignore[attr-defined]
    sys.modules["olmocr"] = pkg


@lru_cache(maxsize=1)
def tests_by_cat() -> dict[str, list]:
    _install_stubs()
    from olmocr.bench.tests import load_tests  # type: ignore[import-not-found]
    root = paths.spike_dir() / "olmbench" / "sample"
    return {c: load_tests(str(root / f"{c}.jsonl")) for c in CATS}


def stem_of(test) -> str:
    return os.path.basename(test.pdf)[:-4]


@lru_cache(maxsize=1)
def pages_by_cat() -> dict[str, list[str]]:
    return {c: sorted({stem_of(t) for t in ts}) for c, ts in tests_by_cat().items()}


def all_stems() -> list[str]:
    return [s for c in CATS for s in pages_by_cat()[c]]


def dev_subset() -> list[str]:
    """Fixed 30-page dev subset: 6 pages per category, seed 20261111. Saved to research/results/dev30.json."""
    if DEV_FILE.exists():
        return [s for c in CATS for s in json.loads(DEV_FILE.read_text())["pages"][c]]
    rng = random.Random(DEV_SEED)
    pages = {c: sorted(rng.sample(pages_by_cat()[c], DEV_PER_CAT)) for c in CATS}
    DEV_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEV_FILE.write_text(json.dumps({"seed": DEV_SEED, "per_category": DEV_PER_CAT, "pages": pages}, indent=1))
    return [s for c in CATS for s in pages[c]]


@dataclass
class BenchResult:
    name: str
    cats: dict[str, tuple[int, int]] = field(default_factory=dict)
    per_page: dict[str, list[int]] = field(default_factory=dict)
    failures: list[tuple[str, str, str]] = field(default_factory=list)  # (category, test id, reason)
    missing: int = 0

    def rate(self, cat: str) -> float:
        p, n = self.cats[cat]
        return 100.0 * p / n if n else 0.0

    @property
    def mean(self) -> float:
        """Mean of the 5 category pass rates (the score in BUILD_SPEC section 3)."""
        return sum(self.rate(c) for c in CATS) / len(CATS)

    def row(self) -> str:
        return f"{self.name:28s} {self.mean:5.1f} | " + " ".join(f"{self.rate(c):5.1f}" for c in CATS)


HEADER = f"{'config':28s} score | " + " ".join(f"{c[:5]:>5s}" for c in ("headers", "tiny", "multicol", "scans", "tables"))


def score(name: str, get_md: Callable[[str], Optional[str]], stems: Optional[Iterable[str]] = None) -> BenchResult:
    """Run every official test whose page is in ``stems`` against ``get_md(stem)``."""
    keep = set(stems) if stems is not None else None
    res = BenchResult(name=name)
    cache: dict[str, Optional[str]] = {}
    for c in CATS:
        p = n = 0
        for t in tests_by_cat()[c]:
            s = stem_of(t)
            if keep is not None and s not in keep:
                continue
            if s not in cache:
                cache[s] = get_md(s)
            md = cache[s]
            if md is None:
                res.missing += 1
                ok, why = False, "missing output"
            else:
                try:
                    ok, why = t.run(md)
                except Exception as e:  # the scorer treats a crash as a fail
                    ok, why = False, f"scorer error: {e}"
            p += bool(ok)
            n += 1
            pp = res.per_page.setdefault(s, [0, 0])
            pp[0] += bool(ok)
            pp[1] += 1
            if not ok:
                res.failures.append((c, t.id, str(why)[:160]))
        res.cats[c] = (p, n)
    return res


def cached_markdown(system: str) -> Callable[[str], Optional[str]]:
    root = paths.spike_dir() / "out" / system

    def get(stem: str) -> Optional[str]:
        f = root / f"{stem}.md"
        return f.read_text() if f.exists() else None
    return get
