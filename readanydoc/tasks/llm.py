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

``json_schema`` is advisory for the CLI and OpenAI adapters (it is described in the prompt by the caller). The
Anthropic API adapter sends it as a structured-output format whenever it fits the API's limits
(``api_schema``; ``structured_outputs=False`` turns this off) and falls back to no format when the API cannot
compile it. Callers always validate the reply themselves, so no adapter is trusted to enforce the schema.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import threading
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


_KEY = re.compile(r'"[^"\n]{0,200}"\s*:')  # a JSON object key: its presence outside a value means JSON was cut


def _unique_keys(pairs: list) -> dict:
    """``object_pairs_hook``: a key written twice in one object means a value landed in the wrong object (a
    bracket closed it early), and plain JSON parsing would silently keep only the last one."""
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key {k!r} in one object")
        out[k] = v
    return out


_DECODER = json.JSONDecoder(object_pairs_hook=_unique_keys)


def _loads(text: str) -> Any:
    return _DECODER.decode(text)


def _scan_brackets(t: str) -> list[tuple[int, str]]:
    """Positions of the structural brackets of ``t`` (outside JSON strings)."""
    out, in_str, esc = [], False, False
    for i, c in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "{}[]":
            out.append((i, c))
    return out


def repair_brackets(t: str) -> Optional[Any]:
    """Repair the mistake models make in long nested JSON: a closing bracket of the wrong kind, such as an
    extra ``}`` inside a list (``[{...}}, ...]``).

    Only a closer that cannot close the open bracket is removed, and only when the whole remaining text is one
    valid JSON value. An extra closer that does match (``{"a": {"b": 1}}, "c": 2}``) is not repaired: which
    object ``"c"`` belonged to is a guess. Even so, the same text can come from a missing opening bracket
    (``[[], null]]``), where the repair reshapes the answer: callers must check the result against the schema
    they asked for before using it. ``parse_json`` never repairs.
    """
    starts = [i for i, c in _scan_brackets(t) if c in "{["]
    if not starts:
        return None
    span = t[starts[0]:]
    drop, stack = set(), []
    for i, c in _scan_brackets(span):
        if c in "{[":
            stack.append(c)
        elif not stack:
            return None  # closes more than was opened: ambiguous
        elif stack[-1] == ("{" if c == "}" else "["):
            stack.pop()
        else:
            drop.add(i)
    if not drop or stack:
        return None
    fixed = "".join(ch for i, ch in enumerate(span) if i not in drop).rstrip()
    try:
        val, end = _DECODER.raw_decode(fixed)
    except ValueError:
        return None
    rest = fixed[end:]
    return val if not _KEY.search(rest) and not any(c in rest for c in "{}[]") else None


def _debris(before: str, after: str) -> bool:
    """Whether the text around a parsed JSON value holds pieces of the answer: an object key, an opening
    bracket before the value that is never closed (the value sits inside a larger, broken one), or a closing
    bracket after it with no opening one (the value was closed early by a bracket too many)."""
    if _KEY.search(before) or _KEY.search(after):
        return True
    depth = 0
    for _, c in _scan_brackets(before):  # brackets inside quoted strings do not count
        depth += 1 if c in "{[" else -1
    if depth > 0:
        return True
    depth = 0
    for _, c in _scan_brackets(after):
        depth += 1 if c in "{[" else -1
        if depth < 0:
            return True
    return False


def parse_json(text: str) -> Any:
    """Parse a model reply as JSON. Accepts bare JSON, fenced JSON, or JSON surrounded by prose.

    Never returns a fragment of a broken reply: a piece of JSON is accepted only when nothing around it is
    part of the answer (``_debris``), and an object with the same key twice is broken JSON too (``_unique_keys``).
    Broken JSON is never repaired here (see ``repair_brackets``).
    Raises ``ValueError`` when no complete JSON value can be found.
    """
    t = (text or "").strip()
    if not t:
        raise ValueError("empty reply")
    try:
        return _loads(t)
    except ValueError:
        pass
    for m in _FENCE.finditer(t):
        try:
            return _loads(m.group(1).strip())
        except ValueError:
            continue
    dec = _DECODER
    best: Any = None
    best_span = (-1, -1)
    for m in re.finditer(r"[\[{]", t):
        try:
            val, end = dec.raw_decode(t, m.start())
        except ValueError:
            continue
        if end - m.start() > best_span[1] - best_span[0]:
            best, best_span = val, (m.start(), end)
        if end >= len(t) - 1:
            break
    if best_span[0] >= 0 and not _debris(t[:best_span[0]], t[best_span[1]:]):
        return best
    if best_span[0] >= 0:
        raise ValueError("reply is broken JSON: only a fragment parses")
    raise ValueError("no JSON found in reply")


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


# ---------------------------------------------------------------- structured outputs (JSON schema -> API format)

# Anthropic's published limits for one request with output_config.format (docs: structured outputs,
# "Schema complexity limits"). A schema over them is sent without a format instead of failing.
SO_MAX_OPTIONAL = 24
SO_MAX_UNIONS = 16
_SO_FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
_SO_DROP = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength",
            "maxItems", "uniqueItems", "pattern", "default", "examples", "discriminator", "$schema", "$id",
            "$comment", "readOnly", "writeOnly", "deprecated", "contentMediaType", "contentEncoding"}
_SO_REFUSE = {"not", "if", "then", "else", "patternProperties", "dependentSchemas", "dependentRequired",
              "propertyNames", "prefixItems", "contains", "unevaluatedProperties", "unevaluatedItems", "$ref",
              "$defs", "definitions", "$dynamicRef"}


class _Unsupported(Exception):
    pass


def api_schema(schema: Any) -> Optional[dict]:
    """The structured-output version of a (``$ref``-free) JSON Schema, or None when it cannot be one.

    Every object is closed (``additionalProperties: false``) and nothing is required except what the caller
    marked required, so the model can always leave out a value the document does not have instead of being
    forced to invent one. Constraints the API does not support are dropped (callers validate the reply
    anyway). None when the schema uses a feature the API cannot express, has an object without declared
    properties, or exceeds the published limits on optional fields and union types.
    """
    counts = {"optional": 0, "unions": 0}

    def go(s: Any) -> Any:
        if not isinstance(s, dict):
            raise _Unsupported("non-object schema node")
        if _SO_REFUSE & set(s):
            raise _Unsupported(f"unsupported keyword: {sorted(_SO_REFUSE & set(s))[0]}")
        out: dict = {}
        for k, v in s.items():
            if k in _SO_DROP:
                continue
            if k == "format":
                if v in _SO_FORMATS:
                    out[k] = v
            elif k in ("anyOf", "oneOf"):
                counts["unions"] += 1
                out["anyOf"] = [go(x) for x in v]
            elif k == "allOf":
                out[k] = [go(x) for x in v]
            elif k == "properties":
                out[k] = {name: go(sub) for name, sub in v.items()}
            elif k == "items":
                out[k] = go(v)
            elif k == "additionalProperties":
                continue  # always closed below: undeclared fields are dropped by the caller anyway
            elif k == "minItems":
                if v in (0, 1):
                    out[k] = v
            elif k == "type" and isinstance(v, list):
                if len(v) > 1:
                    counts["unions"] += 1
                out[k] = v
            else:
                out[k] = v
        types = out.get("type") if isinstance(out.get("type"), list) else [out.get("type")]
        if "object" in types:
            props = out.get("properties")
            if not props:
                raise _Unsupported("object without declared properties")
            out["additionalProperties"] = False
            req = [r for r in out.get("required", []) if r in props]
            out["required"] = req
            counts["optional"] += len(props) - len(req)
        if "array" in types and "items" not in out:
            raise _Unsupported("array without items")
        return out

    try:
        res = go(schema)
    except _Unsupported:
        return None
    if counts["optional"] > SO_MAX_OPTIONAL or counts["unions"] > SO_MAX_UNIONS:
        return None
    return res


# ---------------------------------------------------------------- Anthropic Messages API

_FORMAT_LOCK = threading.Lock()
_FORMAT_VERDICTS: Optional[dict] = None


def _format_cache_path() -> Path:
    base = os.environ.get("READANYDOC_CACHE_DIR") or os.path.join(os.path.expanduser("~"), ".cache", "readanydoc")
    return Path(base) / "structured_formats.json"


def _format_key(model: str, schema_key: str) -> str:
    import hashlib
    return hashlib.sha256(f"{model}\n{schema_key}".encode()).hexdigest()[:32]


def _format_verdicts() -> dict:
    """Whether the API compiled a schema, kept on disk: {hash of model and schema: true/false}."""
    global _FORMAT_VERDICTS
    with _FORMAT_LOCK:
        if _FORMAT_VERDICTS is None:
            try:
                _FORMAT_VERDICTS = json.loads(_format_cache_path().read_text())
            except (OSError, ValueError):
                _FORMAT_VERDICTS = {}
        return _FORMAT_VERDICTS


def _save_format_verdict(key: str, ok: bool) -> None:
    verdicts = _format_verdicts()
    with _FORMAT_LOCK:
        verdicts[key] = ok
        try:
            path = _format_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(verdicts))
            tmp.replace(path)
        except OSError:
            pass  # a cache only


class AnthropicAPILLM(LLM):
    supports_images = True
    parallel_safe = True  # stateless HTTP calls: callers may run several at once

    def __init__(self, model: str = "haiku", base_url: str = "https://api.anthropic.com",
                 effort: Optional[str] = None, timeout: float = 600.0, max_retries: int = 2,
                 api_version: str = "2023-06-01", client: Optional[httpx.Client] = None,
                 api_key_env: str = "ANTHROPIC_API_KEY", structured_outputs: Union[bool, str] = "auto",
                 extra: Optional[dict] = None):
        self.model = MODELS.get(model, model)
        self.extra = dict(extra or {})
        self.name = f"anthropic_api:{self.model}"
        self.base_url = base_url.rstrip("/")
        self.effort = effort if effort is not None else (os.environ.get("READANYDOC_TASK_EFFORT") or "high")
        self.max_retries = max_retries
        self.api_version = api_version
        self.api_key_env = api_key_env
        # "auto": use structured outputs when the schema fits the API's limits; False: never; True: same as auto
        self.structured_outputs = structured_outputs
        self._rejected_formats: set[str] = set()  # schemas the API refused to compile: sent without a format
        self._accepted_formats: set[str] = set()  # schemas the API compiled (format_ok)
        self._client = client or httpx.Client(timeout=timeout)

    def output_format(self, json_schema: Optional[dict]) -> Optional[dict]:
        if json_schema is None or not self.structured_outputs:
            return None
        if json.dumps(json_schema, sort_keys=True) in self._rejected_formats:
            return None
        schema = api_schema(json_schema)
        return {"type": "json_schema", "schema": schema} if schema is not None else None

    def format_ok(self, json_schema: dict) -> bool:
        """Whether the API compiles ``json_schema`` as a structured output. Besides its published limits the API
        caps the compiled grammar's size, which depends on nesting and enums and is not published, so a schema is
        tried once with a one-token request (the API then keeps the grammar for 24 hours; the answer is kept here
        for this process). False when the schema is over the published limits. A network error counts as "yes":
        the real call falls back to no format on a refusal anyway."""
        if not self.structured_outputs or api_schema(json_schema) is None:
            return False
        key = json.dumps(json_schema, sort_keys=True)
        if key in self._rejected_formats:
            return False
        if key in self._accepted_formats:
            return True
        known = _format_verdicts().get(_format_key(self.model, key))
        if known is not None:  # checked before, in this or an earlier run (the same schema on many documents)
            (self._accepted_formats if known else self._rejected_formats).add(key)
            return known
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            return True
        body = {"model": self.model, "max_tokens": 1, "messages": [{"role": "user", "content": "{}"}],
                "output_config": {"format": {"type": "json_schema", "schema": api_schema(json_schema)}}}
        try:
            resp = self._client.post(f"{self.base_url}/v1/messages", json=body,
                                     headers={"x-api-key": api_key, "anthropic-version": self.api_version,
                                              "content-type": "application/json"})
        except httpx.HTTPError:
            return True
        if resp.status_code == 400:
            self._rejected_formats.add(key)
            _save_format_verdict(_format_key(self.model, key), False)
            return False
        if resp.status_code == 200:
            self._accepted_formats.add(key)
            _save_format_verdict(_format_key(self.model, key), True)
            u = resp.json().get("usage") or {}
            _log_usage({"t": round(time.time(), 2), "model": self.model, "event": "format_check",
                        "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                        "cost_usd": api_cost(self.model, u)})
        return True

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
        fmt = self.output_format(json_schema)
        if fmt is not None:
            out_cfg["format"] = fmt
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
        if resp.status_code == 400 and "format" in (body.get("output_config") or {}):
            # the API would not compile this schema (too complex, or a feature it does not take): remember it
            # and ask again without a format; the reply is checked against the schema by the caller anyway
            self._rejected_formats.add(json.dumps(json_schema, sort_keys=True))
            _log_usage({"t": round(time.time(), 2), "model": self.model, "event": "format_rejected",
                        "detail": resp.text[:200]})
            return self.complete(system, user, images=images, json_schema=json_schema, max_tokens=max_tokens)
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

    def output_format(self, json_schema: Optional[dict]) -> Optional[dict]:
        if json_schema is None or not self.structured_outputs:
            return None
        if json.dumps(json_schema, sort_keys=True) in self._rejected_formats:
            return None
        schema = api_schema(json_schema)
        return {"type": "json_schema", "schema": schema} if schema is not None else None

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
             json_schema: Optional[dict] = None, max_tokens: int = 16000, retries: int = 1,
             repair: Optional[Callable[[str], Any]] = None) -> tuple[Any, list[LLMResult]]:
    """Call the model until the reply parses as JSON (at most ``retries`` extra calls).

    ``repair(text)`` is tried on a reply that does not parse (and was not cut off) before asking again; it
    returns the repaired value, or None. Returns ``(data, results)``; ``data`` is ``None`` when every attempt
    failed to parse or the reply was truncated.
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
        if repair is not None and not res.refused:
            fixed = repair(res.text or "")
            if fixed is not None:
                return fixed, results
        prompt = user + "\n\nYour previous reply was not valid JSON. Reply with one JSON value only, no prose, no code fences."
    return None, results
