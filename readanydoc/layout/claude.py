"""Claude as a GPU-free layout detector.

Asks the model for the regions that need special handling (tables; optionally forms and
handwriting) as absolute pixel boxes on the image it is shown. Rule from Claude's
vision-coordinate guidance: the image is pre-sized to the model's native limit (so the pixels the
model sees are the pixels we measure in), boxes are asked as [x1, y1, x2, y2] in pixels of that
image, the answer is JSON only. Boxes are validated, padded, normalised to 0-1 and cached on disk
with the call (``readers/live_cache.py``).

Spec strings: ``claude`` / ``claude:haiku`` (tables only), ``claude:haiku+forms`` (also form and
handwriting regions).
"""

from __future__ import annotations

import io
import json
import re
from typing import Optional

from ..model import BBox, RegionType
from ..readers.base import ImageInput, ReadContext, image_bytes
from .base import LayoutBlock, LayoutDetector, LayoutError

LAYOUT_PROMPT_FILE = (
    "Look at the document page image at {img}. The image is {w} pixels wide and {h} pixels high. "
    "Find every {what} on the page. For each one give its bounding box in pixel coordinates of this image, "
    "as [x1, y1, x2, y2] with the origin at the top-left corner, tight around the whole region including its "
    "header row, row labels and totals row. "
    'Answer with JSON only, no prose and no code fences, in this exact shape: '
    '{{"regions": [{{"type": "table", "box": [x1, y1, x2, y2]}}]}}. '
    "Allowed types: {types}. A table is a grid of rows and columns of values; a list, a paragraph or a single "
    "line of fields is not a table. If there are none, answer {{\"regions\": []}}."
)
TYPES = {"table": RegionType.TABLE, "form": RegionType.KEY_VALUE, "handwriting": RegionType.TEXT}


def parse_regions(text: str, width: int, height: int, pad: float = 0.012, min_area: float = 0.003,
                  max_regions: int = 12) -> list[LayoutBlock]:
    """Model answer -> validated blocks. Boxes outside the page, inverted, tiny or covering nearly the
    whole page with others inside are dropped or clamped; duplicates (IoU > 0.8) are merged."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise LayoutError("no JSON object in the layout answer")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LayoutError(f"layout answer is not valid JSON: {e}") from e
    out: list[LayoutBlock] = []
    for item in (data.get("regions") or [])[: max_regions * 2]:
        if not isinstance(item, dict):
            continue
        box, label = item.get("box"), str(item.get("type", "table")).lower()
        if label not in TYPES or not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            x1, y1, x2, y2 = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        if x1 < -0.05 * width or y1 < -0.05 * height or x2 > 1.05 * width or y2 > 1.05 * height:
            continue  # coordinates in another scale: do not guess
        bb = BBox.from_xyxy(x1 / width - pad, y1 / height - pad, x2 / width + pad, y2 / height + pad)
        if bb.area < min_area:
            continue
        if any(bb.iou(o.bbox) > 0.8 for o in out):
            continue
        out.append(LayoutBlock(bbox=bb, type=TYPES[label], native_label=label, score=None))
    out.sort(key=lambda b: (b.bbox.top, b.bbox.left))
    for i, b in enumerate(out):
        b.order = i
    return out[:max_regions]


class ClaudeLayout(LayoutDetector):
    name = "claude"
    needs_native_image = True  # the Layout stage hands over a native-size PNG, not the 150-dpi render

    def __init__(self, model: str = "haiku", forms: bool = False, reader=None):
        self.model, self.forms = model, forms
        self._reader = reader

    def detect(self, image: ImageInput, context: Optional[ReadContext] = None) -> list[LayoutBlock]:
        if image is None:
            raise LayoutError("claude layout needs a page image")
        from PIL import Image
        data = image_bytes(image)
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
        if self._reader is None:
            from ..readers.live_cache import LiveCacheReader
            self._reader = LiveCacheReader(self.model, "layout")
        types = ["table"] + (["form", "handwriting"] if self.forms else [])
        what = "table" if not self.forms else "table, fill-in form block and handwritten block"
        prompt = (LAYOUT_PROMPT_FILE.replace("{w}", str(w)).replace("{h}", str(h))
                  .replace("{what}", what).replace("{types}", ", ".join(types)))
        ctx = (context or ReadContext()).model_copy(update={"prompt": prompt})
        res = self._reader.read(data, None, ctx)
        self.last_usage = res.usage
        return parse_regions(res.text, w, h)
