"""Reducto serializer: Document -> ParseResponse-shaped dict. Nothing else produces API output."""

from __future__ import annotations

from typing import Any, Optional

from ..model import Chunk, Document, Page, Region
from .citations import cell_citations, ocr_result, word_citations

_PARSE_MODEL = {"fast": "R-1 Mini", "strong": "R-1", "expert": "R-1"}
_MODEL_NAME = {"fast": "r-1-mini", "strong": "r-1", "expert": "r-1"}


def _bbox(region: Region, page: Page) -> tuple[dict[str, Any], str]:
    """(box, where it came from). Without word or layout evidence the box is an estimate recorded by
    Ground, else the whole page; both are marked in ``extra.bbox_source`` and force confidence low."""
    box = region.bbox
    est = region.meta.get("bbox_estimate") or region.meta.get("search_bbox")
    if box is not None:
        left, top, width, height = box.left, box.top, box.width, box.height
        source = region.meta.get("bbox_source") or "layout"
    elif est:
        left, top, width, height = est["left"], est["top"], est["width"], est["height"]
        source = "estimate:" + str(est.get("how") or "layout_shared")
    else:  # never located: the honest answer is "somewhere on this page"
        left, top, width, height = 0.0, 0.0, 1.0, 1.0
        source = "page"
    return ({"left": round(left, 4), "top": round(top, 4), "width": round(width, 4), "height": round(height, 4),
             "page": page.index + 1, "original_page": page.original_page}, source)


def _block(region: Region, page: Page, model_name: str, citations: bool = False) -> dict[str, Any]:
    bbox, bbox_source = _bbox(region, page)
    located = region.bbox is not None
    score = region.confidence.score
    if not located and score is not None:
        score = min(score, 0.5)
    extra: dict[str, Any] = {"model": model_name, "bbox_source": bbox_source}
    if region.meta.get("checks_listed"):  # Verify checks (pipeline/checks), present only with the flags switch
        extra["checks"] = region.meta["checks_listed"]
    if citations:
        extra["word_citations"] = word_citations(region, page)
        extra["cell_citations"] = cell_citations(region, page)
    out = {
        "type": region.type.value,
        "bbox": bbox,
        # shaped form (table format, heading level, links) unless citations need offsets into the raw content
        "content": region.content if citations else region.meta.get("shaped_content", region.content),
        "image_url": None,
        "chart_data": None,
        "confidence": region.confidence.label if located else "low",
        "granular_confidence": {"extract_confidence": None, "parse_confidence": score},
        "extra": extra,
    }
    if region.meta.get("merged_tables"):  # present only on merged tables, as in Reducto's ParseBlock
        out["merged_tables"] = region.meta["merged_tables"]
    return out


def _default_chunks(doc: Document) -> list[Chunk]:
    """Used only when the Shape stage did not run: one chunk, nothing filtered."""
    regions = [r for p in doc.pages for r in p.regions]
    content = "\n\n".join(r.content for r in regions if r.content.strip())
    return [Chunk(content=content, embed=content, region_ids=[r.id for r in regions])]


def to_parse_response(doc: Document, job_id: Optional[str] = None, duration: Optional[float] = None,
                      return_citations: Optional[bool] = None,
                      return_ocr_data: Optional[bool] = None) -> dict[str, Any]:
    """``return_citations`` / ``return_ocr_data`` (Reducto ``settings``) default to the values the
    pipeline recorded in ``doc.meta["parse_options"]``."""
    po = doc.meta.get("parse_options") or {}
    citations = bool(po.get("return_citations")) if return_citations is None else bool(return_citations)
    want_ocr = bool(po.get("return_ocr_data")) if return_ocr_data is None else bool(return_ocr_data)
    tier = doc.meta.get("tier", "strong")
    model_name = _MODEL_NAME.get(tier, "r-1")
    index = {r.id: (r, p) for p in doc.pages for r in p.regions}
    chunks = []
    chunk_of: dict[str, int] = {}
    for ci, ch in enumerate(doc.chunks or _default_chunks(doc)):
        blocks = [_block(*index[rid], model_name, citations) for rid in ch.region_ids
                  if rid in index and not index[rid][0].meta.get("merged_into")]
        for rid in ch.region_ids:
            chunk_of.setdefault(rid, ci)
        chunks.append({"content": ch.content, "embed": ch.embed, "enriched": ch.enriched,
                       "enrichment_success": ch.enriched is not None, "blocks": blocks})
    props = None
    if doc.properties and (doc.meta.get("parse_options") or {}).get("extract_document_properties"):
        keys = ("title", "author", "subject", "keywords", "creator", "producer", "last_modified_by",
                "created_at", "modified_at")
        props = {k: (str(doc.properties[k]) if doc.properties.get(k) is not None else None) for k in keys}
        if not any(props.values()):
            props = None
    n = len(doc.pages)
    return {
        "response_type": "parse",
        "job_id": job_id or doc.id,
        "duration": float(duration if duration is not None else doc.meta.get("duration", 0.0)),
        "pdf_url": None,
        "studio_link": None,
        "parse_mode": None,
        "usage": {
            "num_pages": n,
            "credits": None,
            "credit_breakdown": None,
            "page_billing_breakdown": None,
            "non_empty_cell_count": doc.meta.get("non_empty_cell_count"),
            "usage_breakdown": {"parse_model": _PARSE_MODEL.get(tier, "R-1"), "tier": "Default",
                                "parse_pages": n, "parse_native_pages": 0, "legacy_parse_credits": 0,
                                "ocr_pages": 0, "charts": 0, "prompted_blocks": 0},
        },
        "result": {"type": "full", "chunks": chunks, "ocr": ocr_result(doc, chunk_of) if want_ocr else None,
                   "custom": None},
        "document_properties": props,
    }


def document_markdown(doc: Document) -> str:
    """The document as one markdown string (chunk contents joined); what the bench scores."""
    return "\n\n".join(c["content"] for c in to_parse_response(doc)["result"]["chunks"] if c["content"])
