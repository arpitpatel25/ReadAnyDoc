"""Text LLM interface for the task layer (extract, split, classify, edit, figure summaries).

- ``AnthropicAPILLM``: Messages API over raw HTTP (httpx, see docs/decisions.md). Reads ``ANTHROPIC_API_KEY``
  at call time. Effort defaults to ``high`` (``anthropic_api:claude-haiku-5-5#medium`` or ``READANYDOC_TASK_EFFORT``
  override it). Cost is computed from the token counts the API returns; every call is appended to the
  JSONL file named by ``READANYDOC_TASK_USAGE_LOG`` when that is set.
- ``OpenAILLM``: OpenAI's chat-completions API (``OPENAI_API_KEY``) or any compatible endpoint.
- ``FakeLLM``: deterministic, for tests. Scripted replies or a handler function; records every call.
- ``ClaudeCLILLM`` (development only): Claude through the Claude Code CLI subscription (dev). Same hygiene as
  ``readanydoc/readers/claude_cli.py``: no API key, ``ANTHROPIC_API_KEY`` and ``CLAUDECODE`` stripped,
  runs in the temp dir, only the Read tool (needed for page images). The prompt goes in on stdin so
  long documents do not hit the argv size limit.

Specs: ``anthropic_api:<model>[#effort]``, ``openai:<model>[#effort]``, ``openai_compatible:<model>@<base_url>``,
``fake``, ``claude_cli:<model>``, each optionally followed by ``?<json options>`` (``readanydoc.modelspec``).

``json_schema`` is advisory for the CLI adapter (it is described in the prompt by the caller); the API
adapter can pass it as a structured-output format when ``structured_outputs=True``. Callers always
validate the reply themselves, so no adapter is trusted to enforce the schema.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

import httpx

MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
# USD per 1M tokens (input, output) for prompts up to 100K tokens, and above that.
PRICES = {"claude-haiku-5-5": ((0.10, 0.50), (0.50, 2.50)), "claude-sonnet-5-5": ((2.00, 10.00), (2.00, 10.00))}
USAGE_LOG_ENV = "READANYDOC_TASK_USAGE_LOG"
THINKING_HEADROOM = 16000  # thinking tokens count against max_tokens; keep the caller's budget for the answer
MAX_OUTPUT = 64000


def api_cost(model: str, usage: dict) -> Optional[float]:
    """Cost in USD of one Messages API call from its ``usage`` block (cache reads 0.1x, cache writes 1.25x)."""
    if model not in PRICES or usage.get("input_tokens") is None:
        return None
    read, write = usage.get("cache_read_input_tokens") or 0, usage.get("cache_creation_input_tokens") or 0
    inp, out = usage.get("input_tokens") or 0, usage.get("output_tokens") or 0
    pi, po = PRICES[model][1 if inp + read + write > 100_000 else 0]
    return round((inp * pi + read * pi * 0.1 + write * pi * 1.25 + out * po) / 1e6, 6)


def _log_usage(entry: dict) -> None:
    path = os.environ.get(USAGE_LOG_ENV)
    if not path:
        return
    try:
        with open(path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass

ImageInput = Union[str, Path, bytes]


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str = ""
    data: Any = None  # parsed JSON when the reply contained JSON, else None
    model: Optional[str] = None
    stop_reason: Optional[str] = None
    usage: dict[str, Any] = field(default_factory=dict)  # input_tokens, output_tokens, cost_usd, seconds
    raw: Any = None

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


class LLM(ABC):
    name: str = "llm"
    supports_images: bool = False

    @abstractmethod
    def complete(self, system: str, user: str, *, images: Optional[Sequence[ImageInput]] = None,
                 json_schema: Optional[dict] = None, max_tokens: int = 16000) -> LLMResult: ...


# ---------------------------------------------------------------- JSON parsing

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)```", re.S)


def parse_json(text: str) -> Any:
    """Parse a model reply as JSON. Accepts bare JSON, fenced JSON, or JSON surrounded by prose.

    Raises ``ValueError`` when no JSON value can be found.
    """
    t = (text or "").strip()
    if not t:
        raise ValueError("empty reply")
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for m in _FENCE.finditer(t):
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
    dec = json.JSONDecoder()
    best: Any = None
    best_len = -1
    for m in re.finditer(r"[\[{]", t):
        try:
            val, end = dec.raw_decode(t, m.start())
        except json.JSONDecodeError:
            continue
        if end - m.start() > best_len:
            best, best_len = val, end - m.start()
        if end >= len(t) - 1:
            break
    if best_len < 0:
        raise ValueError("no JSON found in reply")
    return best


def _try_json(text: str) -> Any:
    try:
        return parse_json(text)
    except ValueError:
        return None


def _media_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"


# ---------------------------------------------------------------- Claude Code CLI

class ClaudeCLILLM(LLM):
    supports_images = True

    def __init__(self, model: str = "haiku", timeout: float = 900.0, binary: str = "claude"):
        self.model = MODELS.get(model, model)
        self.name = f"claude_cli:{model}"
        self.timeout = timeout
        self.binary = binary
        self.calls = 0

    def build_command(self, system: str) -> list[str]:
        cmd = [self.binary, "-p", "--model", self.model, "--allowedTools", "Read", "--output-format", "json",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        if system:
            cmd += ["--system-prompt", system]
        return cmd

    def complete(self, system: str, user: str, *, images: Optional[Sequence[ImageInput]] = None,
                 json_schema: Optional[dict] = None, max_tokens: int = 16000) -> LLMResult:
        tmp: list[str] = []
        prompt = user
        try:
            paths = []
            for img in images or []:
                if isinstance(img, bytes):
                    fd, p = tempfile.mkstemp(suffix=".png", prefix="readanydoc_llm_")
                    os.write(fd, img)
                    os.close(fd)
                    tmp.append(p)
                    paths.append(p)
                else:
                    paths.append(str(Path(img).resolve()))
            if paths:
                prompt = ("First read these page images with the Read tool, in order:\n"
                          + "\n".join(paths) + "\n\n" + user)
            env = dict(os.environ)
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("CLAUDECODE", None)
            t0 = time.time()
            self.calls += 1
            try:
                p = subprocess.run(self.build_command(system), input=prompt, capture_output=True, text=True,
                                   timeout=self.timeout, env=env, cwd=tempfile.gettempdir())
            except (OSError, subprocess.TimeoutExpired) as e:
                raise LLMError(f"claude CLI failed: {e}") from e
        finally:
            for p_ in tmp:
                try:
                    os.unlink(p_)
                except OSError:
                    pass
        try:
            j = json.loads(p.stdout)
        except json.JSONDecodeError as e:
            raise LLMError(f"claude CLI returned non-JSON output: {p.stdout[:200]!r} {p.stderr[:200]!r}") from e
        if j.get("is_error"):
            raise LLMError(f"claude CLI error: {str(j.get('result'))[:300]}")
        text = j.get("result") or ""
        u = j.get("usage") or {}
        usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                 "cost_usd": j.get("total_cost_usd"), "seconds": round(time.time() - t0, 1)}
        return LLMResult(text=text, data=_try_json(text), model=self.model, stop_reason="end_turn",
                         usage=usage, raw=j)


# ---------------------------------------------------------------- Anthropic Messages API

class AnthropicAPILLM(LLM):
    supports_images = True
    parallel_safe = True  # stateless HTTP calls: callers may run several at once

    def __init__(self, model: str = "haiku", base_url: str = "https://api.anthropic.com",
                 effort: Optional[str] = None, timeout: float = 600.0, max_retries: int = 2,
                 api_version: str = "2023-06-01", client: Optional[httpx.Client] = None,
                 api_key_env: str = "ANTHROPIC_API_KEY", structured_outputs: bool = False,
                 extra: Optional[dict] = None):
        self.model = MODELS.get(model, model)
        self.extra = dict(extra or {})
        self.name = f"anthropic_api:{self.model}"
        self.base_url = base_url.rstrip("/")
        self.effort = effort if effort is not None else (os.environ.get("READANYDOC_TASK_EFFORT") or "high")
        self.max_retries = max_retries
        self.api_version = api_version
        self.api_key_env = api_key_env
        self.structured_outputs = structured_outputs
        self._client = client or httpx.Client(timeout=timeout)

    def build_request(self, system: str, user: str, images: Optional[Sequence[ImageInput]],
                      json_schema: Optional[dict], max_tokens: int) -> dict:
        content: list[dict] = []
        for img in images or []:
            data = img if isinstance(img, bytes) else Path(img).read_bytes()
            content.append({"type": "image", "source": {"type": "base64", "media_type": _media_type(data),
                                                        "data": base64.standard_b64encode(data).decode("ascii")}})
        content.append({"type": "text", "text": user})
        body: dict = {"model": self.model, "max_tokens": min(max_tokens + THINKING_HEADROOM, MAX_OUTPUT),
                      "messages": [{"role": "user", "content": content}]}
        if system:
            body["system"] = system
        out_cfg: dict = {}
        if self.effort and self.effort != "none":
            out_cfg["effort"] = self.effort
        if json_schema is not None and self.structured_outputs:
            out_cfg["format"] = {"type": "json_schema", "schema": json_schema}
        if out_cfg:
            body["output_config"] = out_cfg
        for key, value in self.extra.items():
            if key == "output_config" and isinstance(value, dict):
                body["output_config"] = {**body.get("output_config", {}), **value}
            elif key == "max_tokens":
                body["max_tokens"] = min(int(value), MAX_OUTPUT)
            else:
                body[key] = value
        return body

    def complete(self, system: str, user: str, *, images: Optional[Sequence[ImageInput]] = None,
                 json_schema: Optional[dict] = None, max_tokens: int = 16000) -> LLMResult:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise LLMError(f"{self.api_key_env} is not set; use the claude_cli adapter instead")
        body = self.build_request(system, user, images, json_schema, max_tokens)
        headers = {"x-api-key": key, "anthropic-version": self.api_version, "content-type": "application/json"}
        t0 = time.time()
        resp = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.post(f"{self.base_url}/v1/messages", json=body, headers=headers)
            except httpx.HTTPError as e:
                if attempt == self.max_retries:
                    raise LLMError(f"Anthropic API connection error: {e}") from e
                time.sleep(min(2 ** attempt, 8))
                continue
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                time.sleep(min(float(resp.headers.get("retry-after", 2 ** attempt)), 30))
                continue
            break
        assert resp is not None
        if resp.status_code != 200:
            raise LLMError(f"Anthropic API error {resp.status_code}: {resp.text[:300]}")
        j = resp.json()
        stop = j.get("stop_reason")
        # A refusal carries no usable answer; never read content as text in that case.
        text = "" if stop == "refusal" else "".join(
            b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        u = j.get("usage") or {}
        usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                 "cost_usd": api_cost(self.model, u), "seconds": round(time.time() - t0, 2)}
        _log_usage({"t": round(time.time(), 2), "model": self.model, "effort": self.effort, "stop": stop,
                    "images": len(images or []), **usage})
        return LLMResult(text=text, data=_try_json(text) if text else None, model=j.get("model", self.model),
                         stop_reason=stop, usage=usage, raw=j)


# ---------------------------------------------------------------- OpenAI chat completions

class OpenAILLM(LLM):
    """OpenAI's API (default; ``OPENAI_BASE_URL`` overrides the address) or any OpenAI-compatible endpoint."""
    supports_images = True
    parallel_safe = True

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1", effort: Optional[str] = None,
                 timeout: float = 600.0, max_retries: int = 2, api_key_env: Optional[str] = "OPENAI_API_KEY",
                 token_field: str = "max_completion_tokens", extra: Optional[dict] = None,
                 client: Optional[httpx.Client] = None):
        self.model = model
        self.name = f"openai:{model}"
        self.base_url = base_url.rstrip("/")
        self.effort = effort
        self.max_retries = max_retries
        self.api_key_env = api_key_env
        self.token_field = token_field
        self.extra = dict(extra or {})
        self._client = client or httpx.Client(timeout=timeout)

    def build_request(self, system: str, user: str, images: Optional[Sequence[ImageInput]],
                      json_schema: Optional[dict], max_tokens: int) -> dict:
        content: list[dict] = []
        for img in images or []:
            data = img if isinstance(img, bytes) else Path(img).read_bytes()
            url = f"data:{_media_type(data)};base64,{base64.standard_b64encode(data).decode('ascii')}"
            content.append({"type": "image_url", "image_url": {"url": url}})
        content.append({"type": "text", "text": user})
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        body: dict = {"model": self.model, "messages": messages, self.token_field: max_tokens}
        if self.effort:
            body["reasoning_effort"] = self.effort
        body.update(self.extra)
        return body

    def complete(self, system: str, user: str, *, images: Optional[Sequence[ImageInput]] = None,
                 json_schema: Optional[dict] = None, max_tokens: int = 16000) -> LLMResult:
        headers = {"content-type": "application/json"}
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise LLMError(f"{self.api_key_env} is not set")
            headers["authorization"] = f"Bearer {key}"
        body = self.build_request(system, user, images, json_schema, max_tokens)
        t0 = time.time()
        resp = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            except httpx.HTTPError as e:
                if attempt == self.max_retries:
                    raise LLMError(f"OpenAI API connection error: {e}") from e
                time.sleep(min(2 ** attempt, 8))
                continue
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                time.sleep(min(float(resp.headers.get("retry-after", 2 ** attempt)), 30))
                continue
            break
        assert resp is not None
        if resp.status_code != 200:
            raise LLMError(f"OpenAI API error {resp.status_code}: {resp.text[:300]}")
        j = resp.json()
        choice = (j.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        finish = choice.get("finish_reason")
        u = j.get("usage") or {}
        usage = {"input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens"),
                 "cost_usd": None, "seconds": round(time.time() - t0, 2)}
        return LLMResult(text=text, data=_try_json(text) if text else None, model=j.get("model", self.model),
                         stop_reason={"stop": "end_turn", "length": "max_tokens"}.get(finish, finish), usage=usage,
                         raw=j)


# ---------------------------------------------------------------- deterministic fake

Reply = Union[str, dict, list, LLMResult, Exception]


class FakeLLM(LLM):
    """Deterministic LLM for tests.

    ``FakeLLM([reply, reply, ...])`` returns the replies in order (the last one repeats);
    ``FakeLLM(handler)`` calls ``handler(system, user, call_index)`` for each call.
    A reply may be a string, a JSON-able value, an ``LLMResult`` or an exception to raise.
    Every call is recorded in ``.calls`` as a dict (system, user, images, json_schema, max_tokens).
    """

    name = "fake"
    supports_images = True

    def __init__(self, replies: Union[Sequence[Reply], Callable[[str, str, int], Reply], Reply, None] = None):
        self.calls: list[dict[str, Any]] = []
        if callable(replies):
            self._handler: Optional[Callable[[str, str, int], Reply]] = replies
            self._replies: list[Reply] = []
        else:
            self._handler = None
            if replies is None:
                replies = ["{}"]
            elif isinstance(replies, (str, dict, LLMResult, Exception)):
                replies = [replies]
            self._replies = list(replies)

    def complete(self, system: str, user: str, *, images: Optional[Sequence[ImageInput]] = None,
                 json_schema: Optional[dict] = None, max_tokens: int = 16000) -> LLMResult:
        i = len(self.calls)
        self.calls.append({"system": system, "user": user, "images": list(images or []),
                           "json_schema": json_schema, "max_tokens": max_tokens})
        reply = self._handler(system, user, i) if self._handler else self._replies[min(i, len(self._replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, LLMResult):
            return reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return LLMResult(text=text, data=_try_json(text), model="fake", stop_reason="end_turn",
                         usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "seconds": 0.0})


DEFAULT_LLM = "anthropic_api:claude-haiku-5-5"


def get_llm(spec: str = DEFAULT_LLM, **kwargs: Any) -> LLM:
    """An LLM from a spec (see the module docstring)."""
    from ..modelspec import split_options
    spec, options = split_options(spec)
    kind, _, model = spec.partition(":")
    model, _, effort = model.partition("#")
    if effort and kind in ("anthropic_api", "openai"):
        kwargs.setdefault("effort", effort)
    if kind == "anthropic_api":
        return AnthropicAPILLM(model or "haiku", extra=options, **kwargs)
    if kind == "openai":
        return OpenAILLM(model, base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
                         extra=options, **kwargs)
    if kind == "openai_compatible":
        name, _, base = model.partition("@")
        return OpenAILLM(name, base_url=base or "http://127.0.0.1:8000/v1", api_key_env=None,
                         token_field="max_tokens", extra=options, **kwargs)
    if kind == "claude_cli":
        return ClaudeCLILLM(model or "haiku", **kwargs)
    if kind == "fake":
        return FakeLLM(**kwargs)
    raise ValueError(f"unknown LLM spec: {spec!r}")


def ask_json(llm: LLM, system: str, user: str, *, images: Optional[Sequence[ImageInput]] = None,
             json_schema: Optional[dict] = None, max_tokens: int = 16000, retries: int = 1) -> tuple[Any, list[LLMResult]]:
    """Call the model until the reply parses as JSON (at most ``retries`` extra calls).

    Returns ``(data, results)``; ``data`` is ``None`` when every attempt failed to parse or the reply
    was truncated.
    """
    results: list[LLMResult] = []
    prompt = user
    for _ in range(retries + 1):
        res = llm.complete(system, prompt, images=images, json_schema=json_schema, max_tokens=max_tokens)
        results.append(res)
        if res.truncated:
            # cut off at max_tokens: whatever JSON can be salvaged from the fragment is not the answer, and
            # asking again would be cut off again. The caller decides (extract halves the segment).
            return None, results
        if res.data is not None:
            return res.data, results
        prompt = user + "\n\nYour previous reply was not valid JSON. Reply with one JSON value only, no prose, no code fences."
    return None, results
