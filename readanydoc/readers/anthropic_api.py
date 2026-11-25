"""Anthropic Messages API adapter (raw HTTP via httpx; see docs/decisions.md for why not the SDK).

Reads ``ANTHROPIC_API_KEY`` from the environment at call time. Unit-tested with mocked HTTP only.
Current models (Haiku 5.5, Sonnet 5.5) run adaptive thinking by default and reject non-default
sampling parameters, so no ``temperature`` is sent.
"""

from __future__ import annotations

import base64
import os
import random
import threading
import time
from typing import Optional

import httpx

from .. import replay
from ..model import RegionType
from . import prompts
from .base import (ImageInput, ReadContext, Reader, ReaderError, ReadResult, Usage, image_bytes,
                   media_type, strip_code_fence, fit_for_api)

MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
# USD per 1M tokens (input, output); used only for cost estimates in Usage.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
RATE_STATUS = {429, 529}  # rate limit / overloaded: wait as told, and make the other threads wait too

# Process-wide limits. READANYDOC_API_CONCURRENCY: requests in flight at once across all documents (default 32;
# 0 = no limit); READANYDOC_API_RATE_RETRIES: extra attempts after a rate-limit answer (default 6). A rate-limit
# answer pauses every thread, not only the one that received it.
_GATE_LOCK = threading.Lock()
_GATE: dict = {"size": None, "sem": None}
_COOL = {"until": 0.0}
STATS = {"requests": 0, "rate_limited": 0, "waited_seconds": 0.0}


def _gate():
    size = int(os.environ.get("READANYDOC_API_CONCURRENCY", "32") or 0)
    with _GATE_LOCK:
        if _GATE["size"] != size:
            _GATE["size"], _GATE["sem"] = size, (threading.BoundedSemaphore(size) if size > 0 else None)
        return _GATE["sem"]


def _cool_down(seconds: float) -> None:
    with _GATE_LOCK:
        _COOL["until"] = max(_COOL["until"], time.time() + seconds)
        STATS["rate_limited"] += 1


def _wait_cool() -> None:
    while True:
        left = _COOL["until"] - time.time()
        if left <= 0:
            return
        STATS["waited_seconds"] += left
        time.sleep(min(left, 5.0))


class AnthropicAPIReader(Reader):
    granularity = "page"

    def __init__(self, model: str = "haiku", base_url: str = "https://api.anthropic.com",
                 max_tokens: int = 16000, effort: Optional[str] = None, timeout: float = 600.0,
                 max_retries: int = 2, api_version: str = "2023-06-01",
                 client: Optional[httpx.Client] = None, api_key_env: str = "ANTHROPIC_API_KEY",
                 extra: Optional[dict] = None):
        self.model = MODELS.get(model, model)
        extra = dict(extra or {})
        max_tokens = int(extra.pop("max_tokens", max_tokens))
        self.extra = extra  # further request fields, sent as given (readanydoc.modelspec)
        self.name = f"anthropic_api:{self.model}"
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.effort = effort
        self.max_retries = max_retries
        self.api_version = api_version
        self.api_key_env = api_key_env
        self._client = client or replay.client(timeout)

    def build_request(self, data: bytes, prompt: str, max_tokens: int) -> dict:
        data = fit_for_api(data)
        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type(data),
                                             "data": base64.standard_b64encode(data).decode("ascii")}},
                {"type": "text", "text": prompt},
            ]}],
        }
        if self.effort:
            body["output_config"] = {"effort": self.effort}
        for key, value in self.extra.items():
            if key == "output_config" and isinstance(value, dict):
                body["output_config"] = {**body.get("output_config", {}), **value}
            else:
                body[key] = value
        return body

    def read(self, image: ImageInput, hint: Optional[RegionType] = None,
             context: Optional[ReadContext] = None) -> ReadResult:
        context = context or ReadContext()
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ReaderError(f"{self.api_key_env} is not set; use the claude_cli or cached reader instead")
        body = self.build_request(image_bytes(image), prompts.inline(context.prompt or prompts.PAGE_PROMPT_INLINE),
                                  context.max_tokens or self.max_tokens)
        headers = {"x-api-key": key, "anthropic-version": self.api_version, "content-type": "application/json"}
        t0 = time.time()
        resp = None
        rate_retries = int(os.environ.get("READANYDOC_API_RATE_RETRIES", "6") or 0)
        attempt = rate_attempt = 0
        gate = _gate()
        while True:
            _wait_cool()
            try:
                if gate is not None:
                    with gate:
                        resp = self._client.post(f"{self.base_url}/v1/messages", json=body, headers=headers)
                else:
                    resp = self._client.post(f"{self.base_url}/v1/messages", json=body, headers=headers)
                STATS["requests"] += 1
            except httpx.HTTPError as e:
                if attempt >= self.max_retries:
                    raise ReaderError(f"Anthropic API connection error: {e}") from e
                time.sleep(min(2 ** attempt, 8))
                attempt += 1
                continue
            if resp.status_code in RATE_STATUS and rate_attempt < max(rate_retries, self.max_retries):
                # the server's retry-after when given, else exponential; jitter so parallel pages do not return together
                try:
                    told = float(resp.headers.get("retry-after", ""))
                except ValueError:
                    told = None
                delay = min(told if told is not None else 2.0 * (2 ** rate_attempt), 60.0) + random.uniform(0, 1.0)
                _cool_down(delay)
                rate_attempt += 1
                continue
            if resp.status_code in RETRY_STATUS and resp.status_code not in RATE_STATUS and attempt < self.max_retries:
                time.sleep(min(float(resp.headers.get("retry-after", 2 ** attempt)), 30))
                attempt += 1
                continue
            break
        assert resp is not None
        if resp.status_code != 200:
            raise ReaderError(f"Anthropic API error {resp.status_code}: {resp.text[:300]}")
        return self.parse_message(resp.json(), t0)

    def parse_message(self, j: dict, t0: float) -> ReadResult:
        """A Messages API response body -> ReadResult (also used for batch results)."""
        stop = j.get("stop_reason")
        # A refusal carries no usable transcription; never read content as text in that case.
        text = "" if stop == "refusal" else "".join(
            b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        u = j.get("usage") or {}
        cost = None
        if self.model in PRICES and u.get("input_tokens") is not None:
            pi, po = PRICES[self.model]
            cost = round((u.get("input_tokens", 0) * pi + u.get("output_tokens", 0) * po) / 1e6, 6)
        usage = Usage(model=j.get("model", self.model), input_tokens=u.get("input_tokens"),
                      output_tokens=u.get("output_tokens"), cost_usd=cost, seconds=round(time.time() - t0, 2))
        from .. import trace
        trace.record("page_read", self.model, u, usage.seconds, stop=stop)
        return ReadResult(text=strip_code_fence(text), format="markdown", usage=usage, stop_reason=stop,
                          truncated=stop == "max_tokens", refused=stop == "refusal", raw=j)
