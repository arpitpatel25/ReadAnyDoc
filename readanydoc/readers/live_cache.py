"""Live reads with a disk cache.

``live_cache:<model>#<sample>`` wraps ``claude_cli:<model>``. A call is keyed by the image bytes,
the prompt, the model and the sample number, so (1) a rerun costs nothing and (2) two readers with
different sample numbers give two independent reads of the same page. Cache directory:
``READANYDOC_CALL_CACHE`` (default ``.cache/calls``). Failed or empty calls are not stored.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Optional

from .. import paths
from ..model import RegionType
from . import prompts
from .base import ImageInput, ReadContext, Reader, ReaderError, ReadResult, Usage, image_bytes


def call_cache_dir() -> Path:
    p = Path(os.environ.get("READANYDOC_CALL_CACHE") or (paths.cache_dir() / "calls"))
    p.mkdir(parents=True, exist_ok=True)
    return p


class LiveCacheReader(Reader):
    granularity = "page"
    needs_image = True

    def __init__(self, model: str = "haiku", sample: str = "1", inner: Optional[Reader] = None,
                 page_prompt: Optional[str] = None):
        self.model, self.sample = model, sample
        self.name = f"live_cache:{model}#{sample}"
        self._inner = inner
        self.page_prompt = page_prompt or prompts.PAGE_PROMPT_FULL_FILE

    def key(self, image: ImageInput, prompt: str) -> str:
        h = hashlib.sha1()
        h.update(image_bytes(image))
        h.update(f"|{prompt}|{self.model}|{self.sample}".encode())
        return h.hexdigest()[:24]

    def read(self, image: ImageInput, hint: Optional[RegionType] = None,
             context: Optional[ReadContext] = None) -> ReadResult:
        context = context or ReadContext()
        prompt = context.prompt or self.page_prompt
        f = call_cache_dir() / f"{self.key(image, prompt)}.json"
        if f.exists():
            d = json.loads(f.read_text())
            return ReadResult(text=d["text"], stop_reason=d.get("stop_reason", "end_turn"),
                              usage=Usage(**{**d.get("usage", {}), "cached": True}))
        if os.environ.get("READANYDOC_NO_LIVE") == "1":
            raise ReaderError("call not cached and READANYDOC_NO_LIVE=1")
        if self._inner is None:
            from . import make_reader
            self._inner = make_reader(f"claude_cli:{self.model}")
        res = self._inner.read(image, hint, context.model_copy(update={"prompt": prompt}))
        log = os.environ.get("READANYDOC_CALL_LOG")  # one line per real model call: spend of one server, from tokens
        if log:
            with open(log, "a") as fh:
                fh.write(json.dumps({"reader": self.name, "sample": self.sample, "stem": context.stem,
                                     **res.usage.model_dump(exclude_none=True)}) + "\n")
        if res.text.strip() and res.stop_reason != "error":
            tmp = f.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")  # another request may read f now
            tmp.write_text(json.dumps({"text": res.text, "stop_reason": res.stop_reason, "reader": self.name,
                                       "stem": context.stem, "usage": res.usage.model_dump(exclude_none=True)}))
            tmp.replace(f)
        return res


class TesseractWords:
    """Word boxes from the local ``tesseract`` binary . Needs a page image."""

    name = "tesseract"
    needs_image = True

    def __init__(self, lang: str = "eng"):
        self.lang = lang or "eng"

    def words(self, image, context: Optional[ReadContext] = None):
        from ..pipeline.grounding.sources import tesseract_words
        if image is None:
            return []
        return tesseract_words(image, context.page_index if context else 0, self.lang)
