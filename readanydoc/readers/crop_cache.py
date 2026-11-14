"""Region-crop reads with a disk cache.

``crop_cache:<model>`` wraps a live reader (``claude_cli:<model>``). A crop read is keyed by the
page stem and the normalised crop box and stored under ``spike/out/<model>_crops/``; replays cost
no tokens. A missing crop is read live only when ``READANYDOC_LIVE=1`` is set, otherwise the reader
raises and the caller keeps the fast-tier output (never worse, never an accidental model call).
"""

from __future__ import annotations

import json
import os
from typing import Optional

from .. import paths
from ..model import RegionType
from .base import ImageInput, ReadContext, Reader, ReaderError, ReadResult, Usage


def crop_key(stem: str, bbox) -> str:
    q = lambda v: f"{int(round(max(min(v, 1.0), 0.0) * 1000)):04d}"
    return f"{stem}__{q(bbox.left)}_{q(bbox.top)}_{q(bbox.right)}_{q(bbox.bottom)}"


class CropCacheReader(Reader):
    granularity = "region"

    def __init__(self, model: str = "haiku", live: Optional[bool] = None):
        self.model = model
        self.name = f"crop_cache:{model}"
        self.dir = paths.spike_dir() / "out" / f"{model}_crops"
        self._live = live
        self._inner: Optional[Reader] = None

    @property
    def live(self) -> bool:
        return self._live if self._live is not None else os.environ.get("READANYDOC_LIVE") == "1"

    @property
    def needs_image(self) -> bool:  # type: ignore[override]
        return self.live

    def path(self, context: ReadContext):
        return self.dir / f"{crop_key(context.stem or 'nostem', context.bbox)}.md"

    def has(self, context: ReadContext) -> bool:
        return context.bbox is not None and bool(context.stem) and self.path(context).exists()

    def read(self, image: ImageInput = None, hint: Optional[RegionType] = None,
             context: Optional[ReadContext] = None) -> ReadResult:
        if context is None or context.bbox is None:
            raise ReaderError("crop_cache reads region crops only (context.bbox is required)")
        f = self.path(context)
        if context.stem and f.exists():
            meta = {}
            if f.with_suffix(".md.meta.json").exists():
                meta = json.loads(f.with_suffix(".md.meta.json").read_text())
            return ReadResult(text=f.read_text(), stop_reason="end_turn",
                              usage=Usage(model=self.model, cached=True, cost_usd=meta.get("cost"),
                                          seconds=meta.get("secs")))
        if not self.live:
            raise ReaderError(f"no cached crop read {f.name} (set READANYDOC_LIVE=1 to read it live)")
        if image is None:
            raise ReaderError("live crop read needs the crop image")
        if self._inner is None:
            from . import make_reader
            self._inner = make_reader(f"claude_cli:{self.model}")
        res = self._inner.read(image, hint, context)
        if context.stem and res.text.strip() and res.stop_reason != "error":
            self.dir.mkdir(parents=True, exist_ok=True)
            f.write_text(res.text)
            f.with_suffix(".md.meta.json").write_text(json.dumps(
                {"cost": res.usage.cost_usd, "secs": res.usage.seconds, "hint": hint.value if hint else None,
                 "bbox": context.bbox.model_dump()}))
        return res
