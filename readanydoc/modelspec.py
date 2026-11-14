"""Model choice: from what a user asks for ("claude-haiku-5-5", effort="high", {...}) to the spec strings that
readers and task models are built from.

A spec is ``<adapter>:<model>[#<effort>][@<sample>][?<json options>]``, for example
``anthropic_api:claude-haiku-5-5#high?{"max_tokens": 32000}``. The options are extra fields of the provider's
request body and are sent as given, except the ones the pipeline itself owns (the messages, streaming and tools):
those are refused, because the pipeline would not be able to read the reply.
"""

from __future__ import annotations

import json
from typing import Any, Optional

# request fields the pipeline sets itself or must not receive (it reads one complete, non-streamed text reply)
RESERVED = frozenset({"model", "messages", "system", "stream", "tools", "tool_choice", "prompt", "input"})

ANTHROPIC_ALIASES = {"haiku", "sonnet", "opus"}
EFFORT_ADAPTERS = {"anthropic_api", "anthropic_batch", "openai"}  # adapters that take ``#effort``


class ModelSpecError(ValueError):
    pass


def split_options(spec: str) -> tuple[str, dict[str, Any]]:
    """``"anthropic_api:x#high?{...}"`` -> (``"anthropic_api:x#high"``, ``{...}``)."""
    base, sep, raw = spec.partition("?")
    if not sep:
        return spec, {}
    try:
        options = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ModelSpecError(f"model options in {spec!r} are not valid JSON: {e}") from e
    if not isinstance(options, dict):
        raise ModelSpecError(f"model options in {spec!r} must be a JSON object")
    return base, check_options(options)


def check_options(options: dict[str, Any]) -> dict[str, Any]:
    bad = sorted(RESERVED & set(options))
    if bad:
        raise ModelSpecError(f"model options {bad} are set by readanydoc itself and cannot be overridden")
    return options


def with_options(spec: str, options: Optional[dict[str, Any]]) -> str:
    if not options:
        return spec
    base, have = split_options(spec)
    return f"{base}?{json.dumps({**have, **check_options(dict(options))}, separators=(',', ':'), sort_keys=True)}"


def adapter_for(model: str) -> str:
    """The adapter for a bare model name. ``provider:model`` is taken as given."""
    if ":" in model:
        return model.partition(":")[0]
    name = model.lower()
    if name.startswith("claude") or name in ANTHROPIC_ALIASES:
        return "anthropic_api"
    if name.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    raise ModelSpecError(f"cannot tell the provider of model {model!r}; write it as <provider>:<model>, "
                         "for example anthropic_api:claude-haiku-5-5 or openai:gpt-5")


def make_spec(model: str, effort: Optional[str] = None, options: Optional[dict[str, Any]] = None) -> str:
    """A reader / task-model spec for a model name, an effort level and extra request options.

    >>> make_spec("claude-haiku-5-5", "high")
    'anthropic_api:claude-haiku-5-5#high'
    """
    options = dict(options or {})
    effort = options.pop("effort", None) or effort
    spec = model if ":" in model else f"{adapter_for(model)}:{model}"
    if effort and spec.partition(":")[0] in EFFORT_ADAPTERS:
        base, _, rest = spec.partition("?")
        spec = base.split("#")[0] + f"#{effort}" + (f"?{rest}" if rest else "")
    return with_options(spec, options)
