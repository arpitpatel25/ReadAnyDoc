"""Record and replay model API calls, for testing code changes without paying for the same call twice.

Set ``READANYDOC_REPLAY_DIR`` to a directory. Every POST that the Anthropic and OpenAI-compatible adapters send is
keyed by its URL and exact body (the API key is a header, so it is not part of the key). A key seen before is answered
from disk; a new key goes to the API and a successful answer is stored. So a benchmark run once with recording on can
be re-run after a code change, and only the calls whose request changed cost anything.

``READANYDOC_REPLAY_ONLY=1`` refuses calls that are not on disk (a guaranteed-free run). Each call that did go to the
API is appended to ``<dir>/live.jsonl`` with its token usage, so the real spend of a run can be added up.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

_LOCK = threading.Lock()
STATS = {"hits": 0, "live": 0}


class ReplayMiss(httpx.TransportError):
    pass


class ReplayTransport(httpx.BaseTransport):
    def __init__(self, directory: str, inner: Optional[httpx.BaseTransport] = None, only: bool = False):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.inner = inner or httpx.HTTPTransport()
        self.only = only

    def _key(self, request: httpx.Request) -> str:
        h = hashlib.sha256()
        h.update(request.method.encode() + b" " + request.url.path.encode() + b"\n")
        h.update(request.content)
        return h.hexdigest()[:40]

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return self.inner.handle_request(request)
        request.read()
        key = self._key(request)
        f = self.dir / key[:2] / f"{key}.json"
        if f.exists():
            try:
                d = json.loads(f.read_text())
                with _LOCK:
                    STATS["hits"] += 1
                return httpx.Response(d["status"], headers={"content-type": "application/json"},
                                      content=d["body"].encode(), request=request)
            except (OSError, ValueError, KeyError):
                pass
        if self.only:
            raise ReplayMiss(f"replay-only: no stored answer for {request.url.path} ({key})", request=request)
        resp = self.inner.handle_request(request)
        resp.read()
        with _LOCK:
            STATS["live"] += 1
        if resp.status_code == 200:
            body = resp.content.decode("utf-8", "replace")
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"status": 200, "body": body}))
            tmp.replace(f)
            usage, model = {}, None
            try:
                j = json.loads(body)
                usage, model = j.get("usage") or {}, j.get("model")
            except ValueError:
                pass
            with _LOCK, open(self.dir / "live.jsonl", "a") as log:
                log.write(json.dumps({"t": time.time(), "key": key, "path": request.url.path, "model": model,
                                      "usage": usage}) + "\n")
        headers = {k: v for k, v in resp.headers.items()
                   if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")}
        return httpx.Response(resp.status_code, headers=headers, content=resp.content, request=request)


def client(timeout: float) -> httpx.Client:
    """An httpx client that records and replays when READANYDOC_REPLAY_DIR is set, else a plain one."""
    d = os.environ.get("READANYDOC_REPLAY_DIR")
    if not d:
        return httpx.Client(timeout=timeout)
    only = os.environ.get("READANYDOC_REPLAY_ONLY") == "1"
    return httpx.Client(timeout=timeout, transport=ReplayTransport(d, only=only))
