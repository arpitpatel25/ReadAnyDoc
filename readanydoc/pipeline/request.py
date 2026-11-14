"""Reducto parse options (the ``retrieval`` / ``formatting`` / ``settings`` / ``spreadsheet`` / ``enhance`` parts of a
request) applied to a pipeline configuration. Shared by the HTTP API and the Python library."""

from __future__ import annotations

from typing import Any, Optional

from .base import PipelineConfig


MAX_OPEN_RANGE = 20_000


class PageRangeError(ValueError):
    """settings.page_range is not a valid range of 1-based page numbers."""


def page_numbers(spec: Any) -> Optional[list[int]]:
    """Reducto's page_range forms -> sorted 1-based page numbers (None = all pages)."""
    if spec is None:
        return None
    items = spec if isinstance(spec, list) else [spec]
    if not items:
        return None
    pages: set[int] = set()
    for it in items:
        if isinstance(it, bool):
            raise PageRangeError("Invalid page range")
        if isinstance(it, int):
            lo, hi = it, it
        elif isinstance(it, dict):
            lo = 1 if it.get("start") is None else it["start"]
            hi = (max(lo, 1) + MAX_OPEN_RANGE) if it.get("end") is None else it["end"]
        elif isinstance(it, str):
            parts = it.replace(" ", "").split("-")
            if not all(p.isdigit() for p in parts) or len(parts) > 2:
                continue  # sheet names etc.: not a page number
            lo, hi = int(parts[0]), int(parts[-1])
        else:
            raise PageRangeError("Invalid page range")
        if lo < 1 or hi < lo:
            raise PageRangeError(f"Invalid page range: start={lo}, end={hi} (pages are 1-indexed, start <= end)")
        pages.update(range(lo, min(hi, lo + MAX_OPEN_RANGE) + 1))
    return sorted(pages) or None


def apply_parse_request(cfg: PipelineConfig, parsing: dict[str, Any], page_range: Any = None) -> PipelineConfig:
    st = parsing.get("settings") or {}
    # spending cap per page (settings.page_cap_usd / page_cap_tokens): a request can lower the profile's cap,
    # never raise it
    for key in ("page_cap_usd", "page_cap_tokens"):
        want, have = st.get(key), cfg.stage("read").options.get(key)
        if want is not None:
            cfg = cfg.with_stage("read", **{key: min(want, have) if have else want})
    retrieval = parsing.get("retrieval") or {}
    chunking = retrieval.get("chunking") or {}
    formatting = parsing.get("formatting") or {}
    po = cfg.parse
    po.chunk_mode = chunking.get("chunk_mode", "disabled")
    po.chunk_size = chunking.get("chunk_size")
    po.chunk_overlap = chunking.get("chunk_overlap", 0)
    po.filter_blocks = list(retrieval.get("filter_blocks") or [])
    po.add_page_markers = bool(formatting.get("add_page_markers"))
    po.table_output_format = formatting.get("table_output_format", "dynamic")
    po.page_range = page_numbers(st.get("page_range") if st.get("page_range") is not None else page_range)
    po.document_password = st.get("document_password")
    po.extract_document_properties = bool(st.get("extract_document_properties"))
    po.return_citations = bool(st.get("return_citations"))
    po.return_ocr_data = bool(st.get("return_ocr_data"))
    # intake / assemble / shape stages read the raw request (merge_tables, formatting.include, spreadsheet.*, embed)
    from .shaping.options import apply_request
    return apply_request(cfg, parsing)
