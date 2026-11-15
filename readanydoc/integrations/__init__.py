"""Adapters for LLM frameworks. Each module imports its framework only when used.

    from readanydoc.integrations.langchain import ReadAnyDocLoader      # pip install "readanydoc[langchain]"
    from readanydoc.integrations.llamaindex import ReadAnyDocReader     # pip install "readanydoc[llamaindex]"
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Optional, Union

SPLITS = ("page", "document", "block")


def chunks(path: Union[str, Path], split: str = "page", **parse_kwargs: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    """Parse ``path`` and yield (text, metadata) per page, per block or for the whole document.

    Metadata: ``source``, ``page`` (1-based, page and block splits), ``block_type`` and ``bbox`` (block split),
    ``confidence`` (block split), and ``warnings`` on the first chunk."""
    import readanydoc
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    result = readanydoc.parse(path, **parse_kwargs)
    base = {"source": str(path)}
    warnings = list(result.warnings)

    def meta(extra: dict[str, Any]) -> dict[str, Any]:
        nonlocal warnings
        m = {**base, **extra}
        if warnings:
            m["warnings"] = warnings
            warnings = []
        return m

    if split == "document":
        yield result.markdown, meta({"pages": result.pages})
        return
    if split == "block":
        for b in result.blocks:
            if not b.content.strip():
                continue
            extra: dict[str, Any] = {"block_type": b.type}
            if b.bbox is not None:
                extra["page"] = b.bbox.page
                extra["bbox"] = [b.bbox.left, b.bbox.top, b.bbox.width, b.bbox.height]
            if b.confidence:
                extra["confidence"] = b.confidence
            yield b.content, meta(extra)
        return
    pages: dict[int, list[str]] = {}
    for b in result.blocks:
        if b.content.strip():
            pages.setdefault(b.bbox.page if b.bbox is not None else 1, []).append(b.content)
    for page in sorted(pages):
        yield "\n\n".join(pages[page]), meta({"page": page})
