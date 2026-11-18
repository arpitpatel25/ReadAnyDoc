"""What each model can take and what it costs, from public data, without spending a token.

Sources, in order:

1. ``READANYDOC_MODEL_LIMITS``: a JSON file of your own, ``{"models": {"<model id>": {"max_input": ..., ...}}}``
   (same fields as below), for private or fine-tuned models, or to override a public value;
2. ``readanydoc/data/model_limits.json``: a snapshot of LiteLLM's public model list (MIT licence; refreshed at
   release with ``scripts/refresh_model_limits.py``), about 4,300 chat models from every major provider;
3. for an OpenAI-compatible server (vLLM, OpenRouter, LM Studio...): the context size its ``/models`` route
   reports (a free request, no tokens).

Fields: ``max_input`` (context window), ``max_output``, ``price_jump`` (input tokens above which the whole request
is billed at the higher rate), prices per token ``in``, ``out``, ``cache_read``, ``cache_write`` and their
``*_above`` rates. A model none of the sources knows is an error that names the setting to give: ReadAnyDoc does
not guess a model's limits.
"""

from __future__ import annotations

import json
import os
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

ENV = "READANYDOC_MODEL_LIMITS"
SNAPSHOT = Path(__file__).resolve().parent / "data" / "model_limits.json"
CHARS_PER_TOKEN = 3.0  # tokens are estimated from characters without a call; 3 over-counts on purpose (safe side)
_server_cache: dict[tuple, Optional[dict]] = {}
_lock = threading.Lock()


class UnknownModel(Exception):
    pass


@lru_cache(maxsize=1)
def _snapshot() -> dict:
    try:
        return json.loads(SNAPSHOT.read_text()).get("models", {})
    except (OSError, ValueError):
        return {}


def _user_file() -> dict:
    path = os.environ.get(ENV)
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text()).get("models", {})
    except (OSError, ValueError) as e:
        raise UnknownModel(f"{ENV}={path} could not be read: {e}") from e


def _candidates(model: str) -> list[str]:
    m = model.strip()
    out = [m, m.lower()]
    if "/" in m:  # "anthropic/claude-haiku-5-5", "openai/gpt-5"
        out += [m.split("/", 1)[1], m.split("/", 1)[1].lower()]
    out += [re.sub(r"-\d{8}$", "", x) for x in list(out)]  # a dated model id: "claude-haiku-4-5-20251001"
    return list(dict.fromkeys(out))


def lookup(model: str) -> tuple[Optional[dict], str]:
    """(limits, where they came from) for a model id, or (None, "") when no file knows it."""
    user = _user_file()
    snap = _snapshot()
    for c in _candidates(model):
        if c in user:
            base = snap.get(c, {})
            return {**base, **user[c]}, f"your file ({ENV})"
    for c in _candidates(model):
        if c in snap:
            return dict(snap[c]), "public model list (LiteLLM snapshot)"
    return None, ""


def from_server(base_url: str, model: str, api_key: Optional[str] = None) -> Optional[dict]:
    """The context size an OpenAI-compatible server reports for ``model`` on GET /models (no tokens)."""
    key = (base_url, model)
    with _lock:
        if key in _server_cache:
            return _server_cache[key]
    found = None
    try:
        import httpx
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        r = httpx.get(base_url.rstrip("/") + "/models", headers=headers, timeout=10)
        for m in (r.json().get("data") or []) if r.status_code == 200 else []:
            if m.get("id") == model:
                ctx = m.get("context_length") or m.get("max_model_len") or m.get("context_window")
                out = (m.get("top_provider") or {}).get("max_completion_tokens") or m.get("max_output_tokens")
                if ctx:
                    found = {"max_input": int(ctx), **({"max_output": int(out)} if out else {})}
                break
    except Exception:  # noqa: BLE001 - a lookup only
        found = None
    with _lock:
        _server_cache[key] = found
    return found


def for_llm(llm: Any) -> tuple[dict, str]:
    """(limits, source) for an LLM adapter. A test double may carry its own ``context_limits``."""
    own = getattr(llm, "context_limits", None)
    if isinstance(own, dict):
        return dict(own), "set on the model adapter"
    model = getattr(llm, "model", None) or ""
    found, source = lookup(model) if model else (None, "")
    if found is not None:
        return found, source
    base = getattr(llm, "base_url", None)
    if base and model:
        key = os.environ.get(getattr(llm, "api_key_env", "") or "") if getattr(llm, "api_key_env", None) else None
        found = from_server(base, model, key)
        if found is not None:
            return found, f"reported by the server ({base})"
    raise UnknownModel(
        f"the limits of model {model or getattr(llm, 'name', '?')!r} are not known: it is not in the public model "
        f"list and its server does not report them. Give them with the extract option part_tokens (largest page "
        f"part, in tokens), or in a JSON file named by {ENV} "
        '({"models": {"<model id>": {"max_input": <context tokens>, "max_output": <output tokens>}}}).')


def part_budget(limits: dict, reserve: int = 6000) -> int:
    """Largest document part (tokens) for one call: under the price jump and the context window, leaving room
    for the instructions, the field list and the schema (``reserve`` plus 15%)."""
    caps = [x for x in (limits.get("price_jump"), limits.get("max_input")) if isinstance(x, (int, float)) and x > 0]
    if not caps:
        raise UnknownModel("no context size for this model")
    cap = int(min(caps))
    return max(int(cap * 0.85) - reserve, cap // 2)


def estimate_tokens(text_chars: int) -> int:
    return int(text_chars / CHARS_PER_TOKEN) + 1


def cost(limits: Optional[dict], usage: dict) -> Optional[float]:
    """USD of one call from its usage block, with the higher rate above the price jump (cached tokens count)."""
    if not limits or "in" not in limits or "out" not in limits:
        return None
    inp = usage.get("input_tokens") or 0
    read = usage.get("cache_read_input_tokens") or 0
    write = usage.get("cache_creation_input_tokens") or 0
    out = usage.get("output_tokens") or 0
    above = bool(limits.get("price_jump")) and inp + read + write > limits["price_jump"]
    rate = lambda k: limits.get(f"{k}_above" if above and f"{k}_above" in limits else k)  # noqa: E731
    c = inp * rate("in") + out * rate("out")
    c += read * (rate("cache_read") if rate("cache_read") is not None else rate("in") * 0.1)
    c += write * (rate("cache_write") if rate("cache_write") is not None else rate("in") * 1.25)
    return round(c, 6)
