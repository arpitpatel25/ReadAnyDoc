"""The ``embed`` field: chunk text tuned for retrieval (``retrieval.embedding_optimized``).

Reducto replaces each table with a natural-language summary written by an LLM. This module gives
the hook for that (``set_table_summarizer`` or the Shape option ``table_summarizer="pkg.mod:func"``)
and a deterministic fallback that needs no model: every body row becomes ``header: value`` pairs.
Nothing here calls a model.
"""

from __future__ import annotations

import importlib
from typing import Callable, Optional

from .tables import TableGrid

# (grid, table content as shown, context) -> summary text, or None to use the fallback
TableSummarizer = Callable[[TableGrid, str, dict], Optional[str]]
_summarizer: Optional[TableSummarizer] = None


def set_table_summarizer(fn: Optional[TableSummarizer]) -> None:
    global _summarizer
    _summarizer = fn


def load_summarizer(spec: Optional[str]) -> Optional[TableSummarizer]:
    """``"package.module:function"`` -> callable."""
    if not spec:
        return None
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


def column_headers(g: TableGrid) -> tuple[list[str], int]:
    """(one label per column, number of header rows). Stacked header rows are joined with ' / '."""
    n = g.header_rows
    if n == 0 and g.n_rows > 1:
        n = 1  # no <th>: read the first row as the header, as a markdown table would
    if n >= g.n_rows:
        return [""] * g.n_cols, 0
    rows = g.matrix(fill_merged=True)[:n]
    labels = []
    for k in range(g.n_cols):
        parts: list[str] = []
        for r in rows:
            t = r[k].replace("\n", " ").strip()
            if t and t not in parts:
                parts.append(t)
        labels.append(" / ".join(parts))
    return labels, n


def linearise_table(g: TableGrid) -> str:
    """Deterministic fallback: one sentence per body row, ``header: value; header: value.``"""
    labels, n = column_headers(g)
    lines = []
    if g.caption:
        lines.append(g.caption.replace("\n", " ") + ".")
    for row in g.matrix(fill_merged=True)[n:]:
        pairs = []
        for label, value in zip(labels, row):
            value = value.replace("\n", " ").strip()
            if not value:
                continue
            pairs.append(f"{label}: {value}" if label else value)
        if pairs:
            lines.append("; ".join(pairs) + ".")
    return "\n".join(lines)


def table_embed(g: TableGrid, content: str, context: Optional[dict] = None,
                summarizer: Optional[TableSummarizer] = None) -> tuple[str, str]:
    """(embed text, how it was made: ``summarizer`` | ``fallback``)."""
    fn = summarizer or _summarizer
    if fn is not None:
        try:
            text = fn(g, content, dict(context or {}))
        except Exception:  # a failing hook must not lose the table
            text = None
        if text and text.strip():
            return text.strip(), "summarizer"
    return linearise_table(g), "fallback"
