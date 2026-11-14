"""Open layout detectors on CPU through ONNX Runtime. Spec: ``onnx:heron`` | ``onnx:ppdoclayout_plus``.

Both models are RT-DETR detectors exported to ONNX by their authors, Apache-2.0 code and weights:

* ``heron``: Docling's layout model (docling-project/docling-layout-heron-onnx, 171 MB, 640x640 input).
  17 labels, among them list items, forms, key-value regions and checkboxes.
* ``ppdoclayout_plus``: PP-DocLayout_plus-L (PaddlePaddle/PP-DocLayout_plus-L_onnx, 130 MB, 800x800 input).
  20 labels; no list item, form or key-value label.

Weights are looked for in ``READANYDOC_LAYOUT_MODELS`` (default ``~/.cache/readanydoc/layout_models/<name>/``) and
downloaded from Hugging Face on first use unless ``READANYDOC_LAYOUT_NO_DOWNLOAD=1``. Needs the ``layout`` extra
(onnxruntime, numpy). Nothing is imported until a detector is built.
"""

from __future__ import annotations

import io
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .. import localmodels
from ..model import BBox, RegionType
from ..readers.base import ImageInput, ReadContext
from .base import LayoutBlock, LayoutDetector, LayoutError, map_label

HERON_LABELS = ["caption", "footnote", "formula", "list_item", "page_footer", "page_header", "picture",
                "section_header", "table", "text", "title", "document_index", "code", "checkbox_selected",
                "checkbox_unselected", "form", "key_value_region"]
PP_LABELS = ["paragraph_title", "image", "text", "number", "abstract", "content", "figure_title", "formula", "table",
             "reference", "doc_title", "footnote", "header", "algorithm", "footer", "seal", "chart", "formula_number",
             "aside_text", "reference_content"]

# Docling labels -> Reducto block types (PP labels go through ``base.LABEL_MAP``)
HERON_MAP: dict[str, RegionType] = {
    "caption": RegionType.TEXT, "footnote": RegionType.TEXT, "formula": RegionType.TEXT,
    "list_item": RegionType.LIST_ITEM, "page_footer": RegionType.FOOTER, "page_header": RegionType.HEADER,
    "picture": RegionType.FIGURE, "section_header": RegionType.SECTION_HEADER, "table": RegionType.TABLE,
    "text": RegionType.TEXT, "title": RegionType.TITLE, "document_index": RegionType.TABLE, "code": RegionType.TEXT,
    "checkbox_selected": RegionType.TEXT, "checkbox_unselected": RegionType.TEXT, "form": RegionType.KEY_VALUE,
    "key_value_region": RegionType.KEY_VALUE,
}

MODELS = {
    "heron": {"repo": "docling-project/docling-layout-heron-onnx", "file": "model.onnx", "size": 640,
              "labels": HERON_LABELS, "bytes": 171220471},
    "ppdoclayout_plus": {"repo": "PaddlePaddle/PP-DocLayout_plus-L_onnx", "file": "inference.onnx", "size": 800,
                         "labels": PP_LABELS, "bytes": 129736329, "dir": "ppdoclayout_plus_l"},
}
# containers: a box of these labels may hold other boxes (a form holds its fields) and is never removed as a duplicate
CONTAINERS = {"form", "key_value_region", "picture", "image", "chart", "table", "document_index"}
MARKS = {"checkbox_selected", "checkbox_unselected"}


def models_dir() -> Path:
    return Path(os.environ.get("READANYDOC_LAYOUT_MODELS") or (Path.home() / ".cache" / "readanydoc" / "layout_models"))


def model_path(name: str) -> Path:
    spec = MODELS[name]
    return models_dir() / spec.get("dir", name) / spec["file"]


def available(name: str = "heron") -> bool:
    """True when the runtime is installed and the weights are on disk (no download is attempted)."""
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return name in MODELS and model_path(name).exists()


def _download(name: str) -> Path:
    spec = MODELS[name]
    path = model_path(name)
    if path.exists():
        return path
    if os.environ.get("READANYDOC_LAYOUT_NO_DOWNLOAD") == "1":
        raise LayoutError(f"layout weights missing: {path} (download {spec['repo']}/{spec['file']})")
    import sys

    import httpx
    print(f"readanydoc: downloading the layout model {name} (one time, 100-200 MB) to {path.parent}. "
          "This happens once; later runs start straight away.", file=sys.stderr, flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    url = f"https://huggingface.co/{spec['repo']}/resolve/main/{spec['file']}"
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=600.0) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
    except Exception as e:
        raise LayoutError(f"could not download layout weights from {url}: {e}") from e
    tmp.replace(path)
    return path


def _iou(a, b) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def _inside(a, b) -> float:
    """Share of box ``a`` that lies inside ``b``."""
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    area = (a[2] - a[0]) * (a[3] - a[1])
    return (iw * ih / area) if iw > 0 and ih > 0 and area > 0 else 0.0


def dedupe(dets: list[tuple[str, float, tuple[float, float, float, float]]], iou_at: float = 0.6,
           inside_at: float = 0.85) -> list[tuple[str, float, tuple[float, float, float, float]]]:
    """RT-DETR has no NMS and sometimes returns one block twice (two labels, or a box and a part of it).
    Highest score first; a later box is dropped when it overlaps a kept one (IoU) or lies inside a kept
    box that is not a container. A container is dropped only against another container."""
    kept: list[tuple[str, float, tuple[float, float, float, float]]] = []
    for lab, sc, bx in sorted(dets, key=lambda d: -d[1]):
        drop = False
        for klab, _, kbx in kept:
            both = (lab in CONTAINERS) == (klab in CONTAINERS)
            if lab in MARKS or klab in MARKS:
                both = lab in MARKS and klab in MARKS
            if not both:
                continue
            if _iou(bx, kbx) >= iou_at or _inside(bx, kbx) >= inside_at or _inside(kbx, bx) >= inside_at:
                drop = True
                break
        if not drop:
            kept.append((lab, sc, bx))
    return kept


def _groups(idx: list[int], boxes, axis: int, min_gap: float) -> list[list[int]]:
    """Runs of boxes that overlap along ``axis`` (0: x, columns; 1: y, bands), separated by clear gaps."""
    lo, hi = axis, axis + 2
    order = sorted(idx, key=lambda i: boxes[i][lo])
    groups, cur, end = [], [order[0]], boxes[order[0]][hi]
    for i in order[1:]:
        if boxes[i][lo] >= end + min_gap:
            groups.append(cur)
            cur = [i]
        else:
            cur.append(i)
        end = max(end, boxes[i][hi])
    groups.append(cur)
    return groups


def _column_gaps(idx: list[int], boxes, min_gap: float = 0.012) -> list[tuple[float, float]]:
    cols = _groups(idx, boxes, 0, min_gap)
    return [(max(boxes[i][2] for i in a), min(boxes[i][0] for i in b)) for a, b in zip(cols, cols[1:])]


def reading_order(boxes: list[tuple[float, float, float, float]]) -> list[int]:
    """Recursive XY cut on the boxes. Bands (cuts along y) come first; neighbouring bands that share a column
    gap are one multi-column area and are read column by column. Good enough for one to three columns."""
    def by_position(idx):
        return sorted(idx, key=lambda i: (boxes[i][1], boxes[i][0]))

    def cut(idx: list[int], depth: int, cols_first: bool = False, merge: bool = True) -> list[int]:
        if len(idx) <= 1 or depth > 14:
            return by_position(idx)
        if cols_first:
            cols = _groups(idx, boxes, 0, 0.012)
            if len(cols) > 1:
                return [j for g in cols for j in cut(g, depth + 1)]
            merge = False  # the bands of this area do not share a gap after all: read them one by one
        bands = _groups(idx, boxes, 1, 0.0)
        if len(bands) > 1:
            areas: list[list[list[int]]] = [[bands[0]]]
            for b in bands[1:]:
                prev = [i for x in areas[-1] for i in x]
                ga, gb = _column_gaps(prev, boxes), _column_gaps(b, boxes)
                shared = merge and any(min(a[1], c[1]) - max(a[0], c[0]) > 0.004 for a in ga for c in gb)
                if shared and _column_gaps(prev + b, boxes):
                    areas[-1].append(b)
                else:
                    areas.append([b])
            out = []
            for area in areas:
                flat = [i for b in area for i in b]
                out += cut(flat, depth + 1, cols_first=True, merge=merge and len(flat) < len(idx))
            return out
        cols = _groups(idx, boxes, 0, 0.012)
        if len(cols) > 1:
            return [j for g in cols for j in cut(g, depth + 1)]
        return by_position(idx)

    return cut(list(range(len(boxes))), 0) if boxes else []


class OnnxLayout(LayoutDetector):
    """One ONNX Runtime session per process and model. ``detect`` is thread-safe: the session is built and run on the
    process's layout worker thread (``readanydoc.localmodels.layout``), one call at a time."""

    needs_native_image = False
    _sessions: dict[str, object] = {}
    _lock = threading.Lock()

    def __init__(self, model: str = "heron", threshold: float = 0.3, threads: Optional[int] = None):
        if model not in MODELS:
            raise LayoutError(f"unknown ONNX layout model {model!r}; known: {sorted(MODELS)}")
        self.model = model
        self.name = f"onnx:{model}"
        self.threshold = float(os.environ.get("READANYDOC_LAYOUT_THRESHOLD", threshold))
        self.threads = threads or int(os.environ.get("READANYDOC_LAYOUT_THREADS", "4"))
        self.last_seconds: Optional[float] = None

    def _session(self):
        return localmodels.layout.call(self._session_on_worker)

    def _session_on_worker(self):
        with self._lock:
            s = self._sessions.get(self.model)
            if s is None:
                try:
                    import onnxruntime as ort
                except Exception as e:
                    raise LayoutError("the ONNX layout detector needs the 'layout' extra (onnxruntime, numpy)") from e
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = self.threads
                opts.log_severity_level = 3
                s = ort.InferenceSession(str(_download(self.model)), sess_options=opts,
                                         providers=["CPUExecutionProvider"])
                self._sessions[self.model] = s
            return s

    def raw(self, image: ImageInput) -> list[tuple[str, float, tuple[float, float, float, float]]]:
        """(native label, score, box as page fractions x0 y0 x1 y1), every detection above the threshold."""
        import numpy as np
        from PIL import Image
        if image is None:
            raise LayoutError("the ONNX layout detector needs a page image")
        im = Image.open(io.BytesIO(image)) if isinstance(image, bytes) else Image.open(image)
        im = im.convert("RGB")
        W, H = im.size
        spec = MODELS[self.model]
        S = spec["size"]
        arr = np.asarray(im.resize((S, S), Image.BILINEAR))
        sess = self._session()
        t0 = time.time()
        if self.model == "heron":
            feed = {"images": np.ascontiguousarray(arr.transpose(2, 0, 1)[None].astype(np.uint8)),
                    "orig_target_sizes": np.array([[10000, 10000]], dtype=np.int64)}
            labels, boxes, scores = localmodels.layout.call(sess.run, None, feed)
            dets = [(int(l), float(s), [float(v) / 10000.0 for v in b])
                    for l, b, s in zip(labels[0], boxes[0], scores[0])]
        else:
            x = (arr.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
            out = localmodels.layout.call(sess.run, None, {"image": np.ascontiguousarray(x),
                                                    "im_shape": np.array([[S, S]], dtype=np.float32),
                                                    "scale_factor": np.array([[1.0, 1.0]], dtype=np.float32)})[0]
            dets = [(int(r[0]), float(r[1]), [float(v) / S for v in r[2:6]]) for r in out]
        self.last_seconds = round(time.time() - t0, 3)
        names = spec["labels"]
        res = []
        for l, s, b in dets:
            if s < self.threshold or not 0 <= l < len(names):
                continue
            x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in b)
            if x1 - x0 <= 0.002 or y1 - y0 <= 0.002:
                continue
            res.append((names[l], s, (x0, y0, x1, y1)))
        return res

    def detect(self, image: ImageInput, context: Optional[ReadContext] = None) -> list[LayoutBlock]:
        dets = dedupe(self.raw(image))
        page = context.page_index if context else 0
        order = reading_order([d[2] for d in dets])
        blocks = []
        for k, i in enumerate(order):
            lab, sc, (x0, y0, x1, y1) = dets[i]
            t = HERON_MAP[lab] if self.model == "heron" else map_label(lab)
            blocks.append(LayoutBlock(bbox=BBox(left=x0, top=y0, width=x1 - x0, height=y1 - y0, page=page), type=t,
                                      native_label=lab, order=k, score=round(sc, 4)))
        return blocks
