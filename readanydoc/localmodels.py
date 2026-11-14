"""One worker thread per local model of the process.

The API server parses several documents at once, each on its own request thread. The local models are native code:
PP-OCR detection and recognition (RapidOCR: OpenCV + its ONNX Runtime sessions) and the layout detector (another
ONNX Runtime session). Previously the OCR engine was guarded by a lock around each call and the layout session
was built under a lock but run from whichever request thread asked.

The two segmentation faults of the full RD-TableBench run turned out NOT to be a race: they are an out-of-bounds read
in OpenCV's resize, fixed in ``readanydoc.cvsafe`` (it also kills a process that serves one request at a time). This
module is the second half of the fix, asked for with it: each local model is loaded and run on one dedicated thread.

``ocr.call(fn, ...)`` / ``layout.call(fn, ...)`` run ``fn`` on that model's worker thread and return its result (or
raise its exception) in the calling thread. Everything that loads or runs a local model goes through its worker, so

* a model is loaded once and only one call into it runs at a time, the first use included (16 requests arriving
  together on a fresh server start one load, not a race of loads);
* every call into a model runs on the same thread, which has a large stack (request threads of the server get the
  platform's small default for secondary threads: 512 KB on macOS).

OCR and layout have separate workers: they are separate ONNX Runtime sessions, ran side by side through the whole
benchmark, and one waiting for the other would only add latency. Tesseract is not here: it runs as a separate
process per call and shares nothing with this one. pdfium has its own process-wide lock
(``readers.sizing.pdfium_locked``).
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

STACK_BYTES = 64 * 1024 * 1024
_START = threading.Lock()   # one worker thread is started at a time: threading.stack_size is process-wide


class Worker:
    """A single thread that runs every call given to it, one after the other."""

    def __init__(self, name: str):
        self.name = name
        self._executor: Optional[ThreadPoolExecutor] = None
        self._ident: Optional[int] = None
        self._pid: Optional[int] = None

    def _mark(self) -> None:
        self._ident = threading.get_ident()

    def _get(self) -> ThreadPoolExecutor:
        if self._executor is not None and self._pid == os.getpid():
            return self._executor
        with _START:
            if self._executor is None or self._pid != os.getpid():   # first use, or a forked child (no thread there)
                ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"readanydoc-local-{self.name}")
                old = None
                try:
                    old = threading.stack_size(STACK_BYTES)   # applies to threads started from now on: the worker
                except (ValueError, RuntimeError):
                    old = None
                try:
                    ex.submit(self._mark).result()            # starts the worker thread now, with that stack
                finally:
                    if old is not None:
                        try:
                            threading.stack_size(old)
                        except (ValueError, RuntimeError):
                            pass
                self._executor, self._pid = ex, os.getpid()
        return self._executor

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """``fn(*args, **kwargs)`` on this worker's thread. A call made from that thread runs in place."""
        ex = self._get()
        if threading.get_ident() == self._ident:
            return fn(*args, **kwargs)
        return ex.submit(fn, *args, **kwargs).result()

    def on_worker(self) -> bool:
        return self._executor is not None and self._pid == os.getpid() and threading.get_ident() == self._ident


ocr = Worker("ocr")          # RapidOCR: PP-OCR detection, direction and recognition sessions, OpenCV
layout = Worker("layout")    # the ONNX layout detector
