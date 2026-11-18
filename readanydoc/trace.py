"""Per-call trace of model usage, for measuring cost: ``READANYDOC_TRACE=<file.jsonl>`` appends one line per model
call (stage, tokens in and out, prompt-cache reads and writes, cost, seconds). Off unless the variable is set.

The stage is the ReadAnyDoc function that made the call (``pipeline/read.py:read``,
``tasks/extract.py:_ask_why``...), found from the call stack so that no caller has to pass it.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from typing import Any, Optional

ENV = "READANYDOC_TRACE"
_lock = threading.Lock()
_local = threading.local()  # tags of the step being run in this thread (why a call is made, which group)
_SKIP = ("/readanydoc/trace.py", "/readanydoc/tasks/llm.py", "/readanydoc/readers/")


def enabled() -> bool:
    return bool(os.environ.get(ENV))


def stage() -> str:
    for f in reversed(traceback.extract_stack()):
        if "/readanydoc/" in f.filename and not any(s in f.filename for s in _SKIP):
            return f"{f.filename.split('/readanydoc/')[-1]}:{f.name}"
    return "?"


class tags:
    """``with trace.tags(why="coded group 1/3"):`` adds these fields to every record made in this thread."""

    def __init__(self, **fields: Any):
        self.fields = fields

    def __enter__(self) -> "tags":
        self.prev = dict(getattr(_local, "tags", {}))
        _local.tags = {**self.prev, **self.fields}
        return self

    def __exit__(self, *exc: Any) -> None:
        _local.tags = self.prev


def event(kind: str, **fields: Any) -> None:
    """A decision or result that is not a model call (the plan chosen, a reply rebuilt), for the same trace."""
    path = os.environ.get(ENV)
    if not path:
        return
    entry = {"t": round(time.time(), 3), "kind": kind, "stage": stage(), **getattr(_local, "tags", {}), **fields}
    try:
        with _lock, open(path, "a") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def record(kind: str, model: str, usage: Optional[dict], seconds: Optional[float] = None, **extra: Any) -> None:
    """One model call. ``usage`` is the API's usage block."""
    path = os.environ.get(ENV)
    if not path:
        return
    from .tasks.llm import api_cost
    u = usage or {}
    entry = {"t": round(time.time(), 3), "kind": kind, "stage": stage(), "model": model,
             "in": u.get("input_tokens") or 0, "cache_read": u.get("cache_read_input_tokens") or 0,
             "cache_write": u.get("cache_creation_input_tokens") or 0, "out": u.get("output_tokens") or 0,
             "cost_usd": api_cost(model, {**u, "input_tokens": u.get("input_tokens") or 0}) if u else None,
             "seconds": seconds, **getattr(_local, "tags", {}), **extra}
    try:
        with _lock, open(path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # a trace only
