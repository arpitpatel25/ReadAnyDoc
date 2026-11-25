"""Downloads for the test sets, cached on disk (``<ReadAnyDoc cache>/bench``). Public sources only, no keys."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx

_lock = threading.Lock()


def root() -> Path:
    from .. import paths
    d = Path(os.environ.get("READANYDOC_BENCH_DATA") or paths.cache_dir() / "bench")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url: str, dest: Path, headers: Optional[dict] = None) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=300, headers=headers) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    tmp.replace(dest)
    return dest


def _json(url: str, dest: Path) -> object:
    return json.loads(_get(url, dest).read_text())


def github_tree(repo: str, rev: str) -> set[str]:
    d = _json(f"https://api.github.com/repos/{repo}/git/trees/{rev}?recursive=1",
              root() / "github" / repo.replace("/", "__") / rev / "_tree.json")
    return {t["path"] for t in d.get("tree", []) if t.get("type") == "blob"}  # type: ignore[union-attr]


def github_file(repo: str, rev: str, path: str) -> Path:
    return _get(f"https://raw.githubusercontent.com/{repo}/{rev}/{quote(path)}",
                root() / "github" / repo.replace("/", "__") / rev / path)


def hf_tree(repo: str, rev: str = "main") -> dict[str, int]:
    d = _json(f"https://huggingface.co/api/datasets/{repo}/tree/{rev}?recursive=1",
              root() / "hf" / repo.replace("/", "__") / rev / "_tree.json")
    return {t["path"]: t.get("size", 0) for t in d if t.get("type") == "file"}  # type: ignore[union-attr]


def hf_file(repo: str, path: str, rev: str = "main") -> Path:
    return _get(f"https://huggingface.co/datasets/{repo}/resolve/{rev}/{quote(path)}",
                root() / "hf" / repo.replace("/", "__") / rev / path)


def hf_rows(repo: str, split: str, n: int, config: str = "default") -> list[tuple[int, dict]]:
    """Rows 0..n-1 of a dataset split through the Hugging Face dataset viewer (no full download)."""
    cache = root() / "hf" / repo.replace("/", "__") / "rows" / f"{config}_{split}_{n}.json"
    if cache.exists():
        return [(r["row_idx"], r["row"]) for r in json.loads(cache.read_text())]
    rows: list = []
    for off in range(0, n, 100):
        url = (f"https://datasets-server.huggingface.co/rows?dataset={quote(repo, safe='')}&config={config}"
               f"&split={split}&offset={off}&length={min(100, n - off)}")
        r = httpx.get(url, timeout=120)
        r.raise_for_status()
        rows += r.json().get("rows", [])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows))
    return [(r["row_idx"], r["row"]) for r in rows]


def hf_row_image(repo: str, split: str, idx: int, image: Optional[dict]) -> Path:
    dest = root() / "hf" / repo.replace("/", "__") / "images" / split / f"{idx:05d}.jpg"
    if dest.exists():
        return dest
    src = (image or {}).get("src")
    if not src:
        raise RuntimeError(f"{repo} {split} row {idx} has no image")
    try:
        return _get(src, dest)
    except httpx.HTTPStatusError:  # a signed viewer link that has expired: ask for the row again
        fresh = hf_rows_one(repo, split, idx)
        return _get(fresh["image"]["src"], dest)


def hf_rows_one(repo: str, split: str, idx: int, config: str = "default") -> dict:
    url = (f"https://datasets-server.huggingface.co/rows?dataset={quote(repo, safe='')}&config={config}"
           f"&split={split}&offset={idx}&length=1")
    r = httpx.get(url, timeout=120)
    r.raise_for_status()
    return r.json()["rows"][0]["row"]


def sec_companyfacts(cik: int) -> dict:
    """A company's XBRL facts from the SEC's data API (public domain). The SEC asks automated clients to name
    themselves in the User-Agent; set READANYDOC_SEC_USER_AGENT to add your contact."""
    ua = os.environ.get("READANYDOC_SEC_USER_AGENT") or "ReadAnyDoc-benchmark/0.1 (open-source research)"
    p = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
             root() / "sec" / f"CIK{cik:010d}.json", headers={"User-Agent": ua})
    return json.loads(p.read_text())
