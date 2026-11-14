"""Cached layout: PaddleOCR-VL (PP-DocLayout boxes) and GLM-OCR pipeline JSON from pod_boxes.tgz."""

from __future__ import annotations

import json
from typing import Optional

from .. import paths
from ..model import BBox
from ..readers.base import ImageInput, ReadContext
from .base import LayoutBlock, LayoutDetector, LayoutError, map_label


class CachedPVLLayout(LayoutDetector):
    """``out_pvl/json/<stem>_res.json``: pixel boxes on the 150-dpi image, header/footer/number labels."""

    name = "cached:pvl"

    def detect(self, image: ImageInput = None, context: Optional[ReadContext] = None) -> list[LayoutBlock]:
        if context is None or not context.stem:
            raise LayoutError("cached layout needs context.stem")
        f = paths.pod_boxes_dir() / "out_pvl" / "json" / f"{context.stem}_res.json"
        if not f.exists():
            raise LayoutError(f"no cached PVL layout for {context.stem!r}")
        d = json.loads(f.read_text())
        w, h = d["width"], d["height"]
        scores = {tuple(round(v) for v in b["coordinate"]): b.get("score")
                  for b in (d.get("layout_det_res") or {}).get("boxes", [])}
        blocks = []
        for i, b in enumerate(d.get("parsing_res_list", [])):
            x0, y0, x1, y1 = b["block_bbox"]
            blocks.append(LayoutBlock(
                bbox=BBox.from_xyxy(x0, y0, x1, y1, context.page_index, w, h),
                type=map_label(b.get("block_label")), native_label=b.get("block_label"), order=i,
                score=scores.get(tuple(round(v) for v in b["block_bbox"])),
                text=b.get("block_content") or None))
        return blocks


class CachedGLMLayout(LayoutDetector):
    """``glm_json/<stem>.json``: boxes normalised to 0-1000; no header/footer labels (dropped upstream)."""

    name = "cached:glm"

    def detect(self, image: ImageInput = None, context: Optional[ReadContext] = None) -> list[LayoutBlock]:
        if context is None or not context.stem:
            raise LayoutError("cached layout needs context.stem")
        f = paths.pod_boxes_dir() / "glm_json" / f"{context.stem}.json"
        if not f.exists():
            raise LayoutError(f"no cached GLM layout for {context.stem!r}")
        pages = json.loads(f.read_text())
        blocks = []
        for i, b in enumerate(pages[0] if pages else []):
            if not b.get("bbox_2d"):
                continue
            x0, y0, x1, y1 = b["bbox_2d"]
            label = b.get("native_label") or b.get("label")
            blocks.append(LayoutBlock(bbox=BBox.from_xyxy(x0, y0, x1, y1, context.page_index, 1000, 1000),
                                      type=map_label(label), native_label=label,
                                      order=b.get("index", i), text=b.get("content") or None))
        return blocks
