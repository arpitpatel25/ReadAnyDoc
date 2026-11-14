"""Last touches on a parse response that need the server's file store.

``finish`` is called by the API layer right after the serializer, once per parsed document:

- page markers become blocks, as on Reducto: a ``Page Number`` block with a zero box for each
  ``[[START OF PAGE n]]`` / ``[[END OF PAGE n]]`` in the chunk it falls in;
- figure and table crops (``settings.return_images``) are put in the file store and linked from ``image_url``;
- chart data (advanced chart agent) is stored as JSON and linked from ``chart_data`` (a list with one link,
  the shape Reducto returns), with ``extra.is_chart``;
- ``pdf_url`` links to the PDF that was parsed (PDF inputs only);
- ``usage.usage_breakdown.charts`` counts the charts extracted.

``put(data: bytes, filename: str, content_type: str) -> url`` stores a file and returns its link.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

from ...model import Document
from . import chunking

Put = Callable[[bytes, str, str], str]
MAX_PDF_COPY = 100 * 1024 * 1024
MODEL_LABELS = {"r-1": "R-1", "r-1-mini": "R-1 Mini", "legacy": "Legacy"}


def _marker_block(text: str, page: int, original_page: Optional[int], model: str) -> dict[str, Any]:
    return {"type": "Page Number",
            "bbox": {"left": 0.0, "top": 0.0, "width": 0.0, "height": 0.0, "page": page, "original_page": original_page},
            "content": text, "image_url": None, "chart_data": None, "confidence": "high",
            "granular_confidence": {"extract_confidence": None, "parse_confidence": None},
            "extra": {"model": model, "page_marker": True}}


def finish(response: dict[str, Any], doc: Document, put: Optional[Put] = None,
           source: Optional[str] = None) -> dict[str, Any]:
    """Edit ``response`` (the serializer's output for ``doc``) in place and return it."""
    result = response.get("result") or {}
    chunks = result.get("chunks")
    if not isinstance(chunks, list) or not doc.chunks or len(chunks) != len(doc.chunks):
        return response
    index = {r.id: (r, p) for p in doc.pages for r in p.regions}
    original = {p.index: p.original_page for p in doc.pages}
    model = "r-1"
    charts = 0
    for ch, out in zip(doc.chunks, chunks):
        blocks = out.get("blocks") or []
        real = [rid for rid in ch.region_ids if rid in index and not index[rid][0].meta.get("merged_into")]
        if len(real) != len(blocks):
            continue  # not the serializer's layout: leave the chunk alone
        for rid, block in zip(real, blocks):
            region = index[rid][0]
            model = (block.get("extra") or {}).get("model") or model
            if put is not None and region.meta.get("image_path") and Path(region.meta["image_path"]).is_file():
                block["image_url"] = put(Path(region.meta["image_path"]).read_bytes(), f"{rid}.png", "image/png")
            if region.meta.get("is_chart"):
                block.setdefault("extra", {})["is_chart"] = True
            if region.meta.get("chart_data"):
                charts += 1
                if put is not None:
                    data = json.dumps(region.meta["chart_data"], ensure_ascii=False).encode()
                    block["chart_data"] = [put(data, f"{rid}_chart.json", "application/json")]
            if region.meta.get("chart_verification"):  # result of the consistency checks on the data
                block.setdefault("extra", {})["chart_verification"] = region.meta["chart_verification"]
                # Reducto's key: true when its verification pass accepted the data
                block["extra"]["chart_verified"] = region.meta["chart_verification"].get("status") != "flagged"
            if region.meta.get("figure_box"):
                block.setdefault("extra", {})["figure_located"] = True
        if any(rid.startswith("__page_") for rid in ch.region_ids):
            merged: list[dict[str, Any]] = []
            it = iter(blocks)
            for rid in ch.region_ids:
                if rid.startswith("__page_start_") or rid.startswith("__page_end_"):
                    start = rid.startswith("__page_start_")
                    page = int(rid.rsplit("_", 1)[1])
                    merged.append(_marker_block(chunking.marker_text(page + 1, start), page + 1,
                                                original.get(page), model))
                elif rid in index and not index[rid][0].meta.get("merged_into"):
                    merged.append(next(it))
            out["blocks"] = merged
    usage = response.get("usage") or {}
    if charts and isinstance(usage.get("usage_breakdown"), dict):
        usage["usage_breakdown"]["charts"] = charts
    ub = usage.get("usage_breakdown")
    if isinstance(ub, dict):
        if doc.meta.get("prompted_blocks"):
            ub["prompted_blocks"] = int(doc.meta["prompted_blocks"])
        label = MODEL_LABELS.get(doc.meta.get("requested_model"))
        if label:  # the model the request named, as Reducto reports it (which engine ran: usage.model_usage)
            ub["parse_model"] = label
            if label == "Legacy":  # Reducto bills legacy parses in credits, not pages
                ub["legacy_parse_credits"] = float(ub.get("parse_pages") or 0)
                ub["parse_pages"] = 0
    searchable = (doc.meta.get("searchable_pdf") or {}).get("path")
    if put is not None and searchable and Path(searchable).is_file():
        try:  # embed_pdf_metadata: pdf_url is the copy with the invisible text layer
            name = Path(source or doc.meta.get("path") or "document").stem or "document"
            response["pdf_url"] = put(Path(searchable).read_bytes(), f"{name}.searchable.pdf", "application/pdf")
        except OSError:
            pass
    if put is not None and response.get("pdf_url") is None and doc.file_type == "pdf":
        path = Path(source or doc.meta.get("path") or "")
        try:
            if path.is_file() and path.stat().st_size <= MAX_PDF_COPY and not doc.meta.get("converted_from"):
                response["pdf_url"] = put(path.read_bytes(), path.name if path.suffix else "document.pdf",
                                          "application/pdf")
        except OSError:
            pass
    return response
