"""Table summaries for ``retrieval.embedding_optimized``.

Reducto puts a short natural-language summary of each table in the chunk's ``embed`` text ("This table shows
..."). ``make_summarizer(spec)`` returns a hook for ``embed.table_embed`` that asks the task model for the same
thing: one text-only call per table, made only when the request sets ``embedding_optimized``. The table itself
(``content``) is never changed. When the call fails the deterministic ``header: value`` fallback is used.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

from .tables import TableGrid

SYSTEM = ("You write one short paragraph that summarises a table for a search index. Start with 'This table'. "
          "Name what the table is about, its row and column categories, and the most important figures exactly "
          "as written. Two to four sentences. No markdown, no HTML, no preamble.")
MAX_TABLE_CHARS = 24000


def make_summarizer(spec: str, usage_sink: Optional[list[dict[str, Any]]] = None, llm=None) -> Callable:
    """Hook ``(grid, content, context) -> text | None`` backed by ``readanydoc.tasks.llm``."""
    holder = {"llm": llm}

    def summarise(grid: TableGrid, content: str, context: dict) -> Optional[str]:
        if holder["llm"] is None:
            from ...tasks.llm import get_llm
            holder["llm"] = get_llm(spec)
        table = content if len(content) <= MAX_TABLE_CHARS else content[:MAX_TABLE_CHARS]
        cache = os.environ.get("READANYDOC_FIGURE_CACHE")
        f = None
        if cache:
            key = hashlib.sha1(f"{SYSTEM}|{table}|{spec}".encode()).hexdigest()[:24]
            f = Path(cache) / f"table_{key}.json"
            if f.exists():
                d = json.loads(f.read_text())
                if usage_sink is not None:
                    usage_sink.append({**d.get("usage", {}), "cached": True, "task": "table_summary"})
                return d["text"]
        res = holder["llm"].complete(SYSTEM, "Table:\n\n" + table, max_tokens=1500)
        text = (res.text or "").strip()
        usage = {"model": res.model, **{k: res.usage.get(k) for k in ("input_tokens", "output_tokens", "cost_usd")}}
        if usage_sink is not None:
            usage_sink.append({**usage, "task": "table_summary"})
        if not text or "<table" in text.lower():
            return None
        if f is not None:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"text": text, "usage": usage}))
        return text

    return summarise


def default_spec(reader_spec: Optional[str]) -> Optional[str]:
    """The model for summaries: READANYDOC_EMBED_LLM (``off`` disables), else the task model, else the page
    reader's provider. None for cached (offline) profiles: no model is called there."""
    env = os.environ.get("READANYDOC_EMBED_LLM")
    if env:
        return None if env.lower() in ("off", "none", "0") else env
    r = reader_spec or ""
    if r.startswith(("cached", "crop_cache", "live_cache")):
        return None
    if os.environ.get("READANYDOC_TASK_LLM"):
        return os.environ["READANYDOC_TASK_LLM"]
    if "anthropic_api" in r or r.startswith("api_cache"):
        return "anthropic_api:haiku"
    if r.startswith("claude_cli"):
        return "claude_cli:haiku"
    return None
