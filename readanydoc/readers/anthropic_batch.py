"""Page reads through Anthropic's Message Batches API (half price, asynchronous).

    POST /v1/messages/batches            {"requests": [{"custom_id": ..., "params": <Messages request>}]}
    GET  /v1/messages/batches/{id}       -> processing_status "in_progress" | "canceling" | "ended", request_counts, results_url
    GET  <results_url>                   -> JSON lines {"custom_id", "result": {"type": "succeeded" | "errored" | "canceled" | "expired", ...}}

Limits (Anthropic): 100,000 requests or 256 MB per batch; most batches end within an hour, at most 24 h;
results in any order (matched by ``custom_id``). Raw HTTP like ``anthropic_api.py`` (see docs/decisions.md).

``AnthropicBatchReader.read`` looks like any reader call but blocks until the batch that carries the
request has ended. Requests made within ``window`` seconds of each other by any thread go into one
batch, so a document whose pages are read by parallel workers becomes one batch per round of reads
(page reads; then follow-up reads such as zoom strips). Meant for async jobs only.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import Future
from typing import Optional

import httpx

from ..model import RegionType
from . import prompts
from .anthropic_api import AnthropicAPIReader
from .base import ImageInput, ReadContext, ReaderError, ReadResult, image_bytes

MAX_REQUESTS = 10_000        # far below the API limit of 100,000: keeps one batch under the 256 MB limit for page images
MAX_BYTES = 200 * 1024 * 1024


class BatchCollector:
    """Gathers requests from any thread and sends them as batches. One per (base_url, key env)."""

    def __init__(self, base_url: str, api_version: str, api_key_env: str, client: Optional[httpx.Client] = None,
                 window: float = 2.0, poll: float = 5.0, deadline: float = 24 * 3600.0):
        self.base_url, self.api_version, self.api_key_env = base_url, api_version, api_key_env
        self._client = client or httpx.Client(timeout=300.0)
        self.window, self.poll, self.deadline = window, poll, deadline
        self._lock = threading.Condition()
        self._pending: list[tuple[str, dict, Future, int]] = []
        self._last_add = 0.0
        self._thread: Optional[threading.Thread] = None
        self.batches: list[dict] = []  # one record per finished batch: id, requests, seconds, counts

    def submit(self, params: dict) -> Future:
        fut: Future = Future()
        cid = "r" + uuid.uuid4().hex[:24]
        size = len(json.dumps(params))
        with self._lock:
            self._pending.append((cid, params, fut, size))
            self._last_add = time.time()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="readanydoc-batch", daemon=True)
                self._thread.start()
            self._lock.notify_all()
        return fut

    def _headers(self) -> dict:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ReaderError(f"{self.api_key_env} is not set")
        return {"x-api-key": key, "anthropic-version": self.api_version, "content-type": "application/json"}

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._lock.wait(timeout=30.0)
                    if not self._pending:
                        return  # idle: the next submit starts a new thread
                # wait until no request has arrived for `window` seconds (parallel page workers are still queueing)
                while time.time() - self._last_add < self.window and len(self._pending) < MAX_REQUESTS:
                    self._lock.wait(timeout=max(self.window - (time.time() - self._last_add), 0.05))
                take, size = [], 0
                while self._pending and len(take) < MAX_REQUESTS and size + self._pending[0][3] <= MAX_BYTES:
                    item = self._pending.pop(0)
                    take.append(item)
                    size += item[3]
                if not take and self._pending:  # a single request over the size limit
                    item = self._pending.pop(0)
                    item[2].set_exception(ReaderError("request too large for a batch"))
                    continue
            threading.Thread(target=self._run_batch, args=(take,), name="readanydoc-batch-run", daemon=True).start()

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        last: Optional[Exception] = None
        for attempt in range(6):
            try:
                r = self._client.request(method, url, headers=self._headers(), **kw)
            except httpx.HTTPError as e:
                last = e
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code in (408, 409, 429, 500, 502, 503, 504, 529) and attempt < 5:
                time.sleep(min(float(r.headers.get("retry-after", 2 ** attempt)), 60))
                continue
            return r
        raise ReaderError(f"Anthropic batch API connection error: {last}")

    def _run_batch(self, items: list[tuple[str, dict, Future, int]]) -> None:
        t0 = time.time()
        futures = {cid: fut for cid, _, fut, _ in items}
        record = {"requests": len(items), "started": t0}
        try:
            r = self._request("POST", f"{self.base_url}/v1/messages/batches",
                              json={"requests": [{"custom_id": cid, "params": p} for cid, p, _, _ in items]})
            if r.status_code != 200:
                raise ReaderError(f"Anthropic batch API error {r.status_code}: {r.text[:300]}")
            batch = r.json()
            record["id"] = batch.get("id")
            wait = min(self.poll, 2.0)
            while batch.get("processing_status") != "ended":
                if time.time() - t0 > self.deadline:
                    raise ReaderError(f"batch {batch.get('id')} did not end within {self.deadline:.0f} s")
                time.sleep(wait)
                wait = min(wait * 1.5, max(self.poll, 2.0))
                r = self._request("GET", f"{self.base_url}/v1/messages/batches/{batch['id']}")
                if r.status_code != 200:
                    raise ReaderError(f"Anthropic batch API error {r.status_code}: {r.text[:300]}")
                batch = r.json()
            record["counts"] = batch.get("request_counts")
            url = batch.get("results_url") or f"{self.base_url}/v1/messages/batches/{batch['id']}/results"
            r = self._request("GET", url)
            if r.status_code != 200:
                raise ReaderError(f"Anthropic batch results error {r.status_code}: {r.text[:300]}")
            record["seconds"] = round(time.time() - t0, 2)
            for line in r.text.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                fut = futures.pop(row.get("custom_id"), None)
                if fut is None:
                    continue
                res = row.get("result") or {}
                if res.get("type") == "succeeded":
                    fut.set_result((res.get("message") or {}, record))
                else:
                    detail = json.dumps(res.get("error") or {})[:300]
                    fut.set_exception(ReaderError(f"batch request {res.get('type')}: {detail}"))
            for fut in futures.values():
                fut.set_exception(ReaderError("batch ended without a result for this request"))
        except Exception as e:
            record["error"] = str(e)[:300]
            for fut in futures.values():
                if not fut.done():
                    fut.set_exception(e if isinstance(e, ReaderError) else ReaderError(str(e)))
        finally:
            record.setdefault("seconds", round(time.time() - t0, 2))
            self.batches.append(record)


_COLLECTORS: dict[tuple, BatchCollector] = {}
_COLLECTORS_LOCK = threading.Lock()


def collector(base_url: str, api_version: str, api_key_env: str) -> BatchCollector:
    key = (base_url, api_version, api_key_env)
    with _COLLECTORS_LOCK:
        if key not in _COLLECTORS:
            _COLLECTORS[key] = BatchCollector(base_url, api_version, api_key_env,
                                              window=float(os.environ.get("READANYDOC_BATCH_WINDOW", "2.0")),
                                              poll=float(os.environ.get("READANYDOC_BATCH_POLL", "5.0")))
        return _COLLECTORS[key]


class AnthropicBatchReader(AnthropicAPIReader):
    """Same requests and same parsing as ``AnthropicAPIReader``; delivery through a batch."""

    def __init__(self, *args, collector_: Optional[BatchCollector] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._collector = collector_

    def read(self, image: ImageInput, hint: Optional[RegionType] = None,
             context: Optional[ReadContext] = None) -> ReadResult:
        context = context or ReadContext()
        if not os.environ.get(self.api_key_env):
            raise ReaderError(f"{self.api_key_env} is not set; use the claude_cli or cached reader instead")
        body = self.build_request(image_bytes(image), prompts.inline(context.prompt or prompts.PAGE_PROMPT_INLINE),
                                  context.max_tokens or self.max_tokens)
        col = self._collector or collector(self.base_url, self.api_version, self.api_key_env)
        t0 = time.time()
        message, record = col.submit(body).result()
        res = self.parse_message(message, t0)
        res.usage.batch = True
        return res
