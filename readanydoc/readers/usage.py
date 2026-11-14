"""Model token usage of one request, summed per model.

``Document.usage`` has one entry per model call (``model``, ``input_tokens``, ``output_tokens``).
The API returns the sum as ``usage.model_usage`` so that a client, or the test harness, can compute
the cost of a request from tokens and its own price list.
"""

from __future__ import annotations

from typing import Any, Iterable

# USD per 1M tokens (input, output), by model family
PRICES = {"haiku": (0.10, 0.50), "sonnet": (2.0, 10.0), "opus": (4.0, 20.0)}


def family(model: str) -> str:
    m = (model or "").lower()
    return next((f for f in PRICES if f in m), m or "unknown")


def summarize(entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """{"calls", "input_tokens", "output_tokens", "cached_calls", "models": {model: {...same...}}}.
    Calls replayed from a disk cache are counted with the tokens of the original call (what the
    request costs without the cache) and also counted in ``cached_calls``."""
    models: dict[str, dict[str, int]] = {}
    entries_list = list(entries)
    for e in entries_list:
        if e.get("input_tokens") is None and e.get("output_tokens") is None:
            continue
        m = models.setdefault(str(e.get("model") or e.get("reader") or (f"task:{e['task']}" if e.get("task") else "unknown")),
                              {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_calls": 0})
        m["calls"] += int(e.get("calls") or 1)
        m["input_tokens"] += int(e.get("input_tokens") or 0)
        m["output_tokens"] += int(e.get("output_tokens") or 0)
        m["cached_calls"] += 1 if e.get("cached") else 0
        if e.get("batch"):  # Message Batches API: these tokens are billed at half price
            m["batch_calls"] = m.get("batch_calls", 0) + 1
            m["batch_input_tokens"] = m.get("batch_input_tokens", 0) + int(e.get("input_tokens") or 0)
            m["batch_output_tokens"] = m.get("batch_output_tokens", 0) + int(e.get("output_tokens") or 0)
    total = {k: sum(m[k] for m in models.values()) for k in ("calls", "input_tokens", "output_tokens", "cached_calls")}
    out = {**total, "models": models}
    steps = step_seconds(entries_list)
    if steps:
        out["steps"] = steps
    return out


def step_seconds(entries: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Seconds of the model calls by pipeline step:
    {step: {"calls", "seconds" (sum), "seconds_max" (longest call)}}. A step is the entry's ``escalation``
    ("page" when it has none; "page:<reason>" for a second page read such as the handwriting route; strip sets
    are "zoom_strip:za" / "zoom_strip:zb"; region reads are "region:table" / "region:form" / "region:formula"
    ("region:table_late" for a table read that was not started with the page read, "region:form_pre"
    for a form read that was, "region:form_unused" for one that was started and not used), or
    "region" for older kinds). Calls of one step run together, so a page waits for about the longest
    call of each step; a replayed call counts with the seconds of the original call. Entries without ``seconds``
    (readers that do not time their calls) are left out."""
    steps: dict[str, dict[str, Any]] = {}
    for e in entries:
        sec = e.get("seconds")
        if not isinstance(sec, (int, float)) or (e.get("input_tokens") is None and e.get("output_tokens") is None):
            continue
        name = str(e.get("escalation") or "page")
        if name in ("page", "zoom_strip", "zoom_strip_unused") and e.get("reason"):
            name = f"{name}:{e['reason']}"
        elif name == "region" and e.get("reason"):
            # region reads by kind. Table-only reads start with the page read (prefetch),
            # form reads come after grounding, so they add to the page's waiting time in different places.
            reason = str(e["reason"])
            kind = ("table" if reason.startswith(("tread", "table_prefetch")) else "form" if reason.startswith("form:")
                    else "form_unused" if reason.startswith("form_prefetch_unused")
                    else "formula" if reason.startswith("formula") else "")
            if kind == "form" and e.get("prefetched"):
                kind = "form_pre"  # started with the page read (not after grounding)
            if kind == "table" and reason.startswith("tread") and e.get("prefetched") is False:
                kind = "table_late"  # a table read made by the table step itself, after the page read
            name = f"region:{kind}" if kind else name
        st = steps.setdefault(name, {"calls": 0, "seconds": 0.0, "seconds_max": 0.0})
        st["calls"] += 1
        st["seconds"] = round(st["seconds"] + float(sec), 2)
        st["seconds_max"] = round(max(st["seconds_max"], float(sec)), 2)
    return steps


def merge(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    """Sum of two ``summarize`` results."""
    out = summarize([])
    for part in (a, b):
        for name, m in ((part or {}).get("models") or {}).items():
            o = out["models"].setdefault(name, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_calls": 0})
            for k in set(o) | set(m):
                o[k] = o.get(k, 0) + int(m.get(k) or 0)
    for k in ("calls", "input_tokens", "output_tokens", "cached_calls"):
        out[k] = sum(m[k] for m in out["models"].values())
    return out


def cost_usd(model_usage: dict[str, Any] | None, prices: dict[str, tuple[float, float]] | None = None) -> float | None:
    """Cost from tokens; None when a model has no known price."""
    prices = prices or PRICES
    if not model_usage or not model_usage.get("models"):
        return None
    total = 0.0
    for name, m in model_usage["models"].items():
        f = family(name)
        if f not in prices:
            return None
        pi, po = prices[f]
        total += (m.get("input_tokens", 0) * pi + m.get("output_tokens", 0) * po) / 1e6
        total -= 0.5 * (m.get("batch_input_tokens", 0) * pi + m.get("batch_output_tokens", 0) * po) / 1e6
    return round(total, 6)
