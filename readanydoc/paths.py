"""Where the cached spike artifacts live. Override with env vars; nothing here is secret."""

from __future__ import annotations

import os
import tarfile
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def spike_dir() -> Path:
    return Path(os.environ.get("READANYDOC_SPIKE_DIR", REPO_ROOT.parent / "spike")).resolve()


def cache_dir() -> Path:
    p = Path(os.environ.get("READANYDOC_CACHE_DIR", REPO_ROOT / ".cache")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def reference_dir() -> Path:
    return Path(os.environ.get("READANYDOC_REFERENCE_DIR", REPO_ROOT.parent / "reference")).resolve()


def pod_boxes_dir() -> Path:
    """Unpacked ``spike/pod_boxes.tgz`` (layout JSON). Extracted on first use."""
    dst = cache_dir() / "pod_boxes"
    if not (dst / "out_pvl" / "json").is_dir():
        tgz = spike_dir() / "pod_boxes.tgz"
        if not tgz.exists():
            raise FileNotFoundError(f"layout cache not found: {tgz}")
        dst.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tgz) as tf:
            tf.extractall(dst, filter="data")
    return dst


@lru_cache(maxsize=1)
def _pdf_index() -> dict[str, Path]:
    """stem -> original bench PDF (spike/olmbench/pdfs/<category>/<stem>.pdf)."""
    root = spike_dir() / "olmbench" / "pdfs"
    return {p.stem: p for p in root.glob("*/*.pdf")} if root.is_dir() else {}


def bench_pdf(stem: str) -> Path | None:
    return _pdf_index().get(stem)


def bench_image(stem: str) -> Path | None:
    p = spike_dir() / "img" / f"{stem}.png"
    return p if p.exists() else None
