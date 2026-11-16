"""Figures: where they are, their images, and what the request asks to know about them.

Runs at the start of the Shape stage. Figure blocks always get the box of the drawing found on the page
(no model call). The rest happens only when the request asks for it:

- ``settings.return_images`` with ``figure`` / ``table``: the block's region is cut out of the page image and
  saved; the API layer puts the file in the server's file store and writes its link to ``image_url``.
- ``enhance.agentic[{scope: "figure", prompt}]``: one model call per figure with the crop and the prompt; the
  answer is what the Figure block shows.
- ``enhance.agentic[{scope: "figure", advanced_chart_agent: true}]`` or ``enhance.advanced_chart_agent``: one
  model call per figure; a chart comes back as series data (``chart_data``: a link to a JSON file) and the block
  shows the legend and a data table. Not a chart: the block is left as it was.

A figure block is a region of type Figure, or a block that is nothing but the reader's bracketed description
("[Company logo]", "*[Figure: bar chart of ...]*"). Boxes come from ``locate`` (ink that is not text).
Nothing here touches text the reader transcribed. Model calls are recorded in ``doc.usage`` (task "figure").
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from ... import paths
from ...model import BBox, Document, Page, Region, RegionType
from . import locate

RENDER_W = 640  # page image width used to find figures
CROP_DPI = 150
_PLACEHOLDER = re.compile(r"^\s*(\*{1,2}|_)?\[[^\[\]\n]{3,400}\](\*{1,2}|_)?\s*$")
_ITALIC_LINE = re.compile(r"^\s*([*_])(?![*_\s])[^*_\n]{12,500}\1\s*$")  # one whole block in italics
_NOT_FIGURE = re.compile(r"^\W*(illegible|signature|signed|redacted|blank|x| )\W*$", re.I)
_FIGURE_WORD = re.compile(r"\b(logo|figure|image|photo|picture|chart|graph|diagram|illustration|seal|emblem|"
                          r"icon|qr code|barcode|map|drawing|plot|stamp|crest|coat of arms)\b", re.I)

_DESCRIBES = re.compile(r"\b(pie|bar|line|stacked|area|scatter)?\s*(charts?|graphs?|diagrams?|plots?|maps?|"
                        r"illustrations?|photographs?|photos?|logos?)\s+(shows?|showing|of|depicts?|depicting|"
                        r"displays?|illustrates?|compares?|plots?)\b", re.I)

CHART_SYSTEM = ("You read charts from document images and return their data. Reply with one JSON object only, "
                "no prose and no code fences.")
CHART_PROMPT = (
    "The image is one figure cut from a document page. If it is a data chart (bar, line, area, pie, scatter, "
    "combination), return its data. Read values from printed data labels where they exist, otherwise estimate "
    "them from the axis. Return this JSON object:\n"
    '{"is_chart": true, "chart_type": "bar|stacked_bar|line|area|pie|scatter|combo|other", "chart_title": str, '
    '"source": str, "axes": {"x": {"label": str, "type": "category|date|number"}, '
    '"y_primary": {"label": str, "min": number|null, "max": number|null}}, '
    '"series": [{"label": str, "type": "bar|line|area|pie|scatter", "corresponding_y": "primary|secondary", '
    '"color": "#rrggbb"|null, "data": [{"x": str, "value": number|null}]}], "estimated": true|false}\n'
    "For a pie chart use one series whose data points are the slices. Use null for a value you cannot read. "
    '"estimated" is true when values were read off the axis rather than from printed labels. '
    'If the image is not a data chart (a logo, a photo, a diagram, a seal), return {"is_chart": false}.')
DESCRIBE_SYSTEM = ("You look at one figure cut from a document page and answer the instruction about it. "
                   "Reply with the answer only, as plain Markdown.")


def wants(request: Optional[dict[str, Any]]) -> dict[str, Any]:
    """What the parse request asks of this module."""
    req = request or {}
    settings, enhance = req.get("settings") or {}, req.get("enhance") or {}
    images = {str(x) for x in settings.get("return_images") or []}
    prompt, chart = None, bool(enhance.get("advanced_chart_agent"))
    for entry in enhance.get("agentic") or []:
        if isinstance(entry, dict) and entry.get("scope") == "figure":
            prompt = entry.get("prompt") or prompt
            chart = chart or bool(entry.get("advanced_chart_agent"))
    return {"figure_images": "figure" in images, "table_images": "table" in images, "prompt": prompt, "chart": chart,
            "any": bool(images & {"figure", "table"}) or bool(prompt) or chart}


def is_figure(region: Region) -> bool:
    if region.type == RegionType.FIGURE:
        return True
    if region.type not in (RegionType.TEXT, RegionType.TITLE, RegionType.SECTION_HEADER):
        return False
    c = region.content
    if _PLACEHOLDER.match(c):
        inner = c.strip().strip("*_").strip("[]")
        return not _NOT_FIGURE.match(inner) and bool(_FIGURE_WORD.search(inner))
    # the reader's one-line description of a drawing, written as a whole block in italics
    return region.type == RegionType.TEXT and bool(_ITALIC_LINE.match(c)) and bool(_DESCRIBES.search(c))


# ---------------------------------------------------------------- page images

def page_image(doc: Document, page: Page, width: Optional[int] = None, dpi: Optional[int] = None):
    """The page as a PIL image, at a pixel width or at a resolution. None when it cannot be drawn."""
    from PIL import Image
    src = doc.meta.get("path")
    try:
        if doc.file_type == "pdf" and src:
            from ..intake import render_page
            use = dpi or max(int(round((width or RENDER_W) / max(page.width or 612.0, 1.0) * 72)), 20)
            return render_page(src, page.original_page or page.index + 1, dpi=use,
                               password=doc.meta.get("_password")).convert("RGB")
        for path in list(page.renders.values()) + ([src] if src else []):
            if path and Path(path).exists():
                im = Image.open(path)
                if getattr(im, "n_frames", 1) > 1:
                    im.seek((page.original_page or 1) - 1)
                im = im.convert("RGB")
                if width and im.width > width:
                    im = im.resize((width, max(int(im.height * width / im.width), 1)))
                return im
    except Exception:
        return None
    return None


def pdf_words(doc: Document, page: Page) -> list[locate.Box]:
    """Word boxes of the PDF text layer (empty for scans and images)."""
    src = doc.meta.get("path")
    if doc.file_type != "pdf" or not src:
        return []
    try:
        import pdfplumber
        with pdfplumber.open(src, password=doc.meta.get("_password") or None) as pdf:
            p = pdf.pages[(page.original_page or page.index + 1) - 1]
            w, h = float(p.width), float(p.height)
            return [(x["x0"] / w, x["top"] / h, (x["x1"] - x["x0"]) / w, (x["bottom"] - x["top"]) / h)
                    for x in p.extract_words()]
    except Exception:
        return []


def _box_of(region: Region) -> Optional[locate.Box]:
    b = region.bbox
    if b is None or (b.width > 0.95 and b.height > 0.95):
        return None
    return (b.left, b.top, b.width, b.height)


def _hint(region: Region) -> Optional[locate.Box]:
    est = region.meta.get("bbox_estimate") or region.meta.get("search_bbox")
    if est:
        return (est["left"], est["top"], est["width"], est["height"])
    return _box_of(region)


LEGEND_REACH = 0.06  # a legend or axis title this far above or below a drawing, within its width, is part of it


def _grow(box: locate.Box, words: list[locate.Box], blocked: list[locate.Box],
          spare: Optional[list[locate.Box]] = None) -> locate.Box:
    """Take in what belongs to a drawing but is not part of its main blob: words no text block claims (axis
    labels, legend text) and small unmatched ink (legend swatches). Distances are taken from the drawing
    itself, so labels do not chain down the page."""
    left, top, right, bottom = box[0], box[1], box[0] + box[2], box[1] + box[3]
    def claimed(w):
        cx, cy = w[0] + w[2] / 2, w[1] + w[3] / 2
        return any(b[0] <= cx <= b[0] + b[2] and b[1] <= cy <= b[1] + b[3] for b in blocked)
    loose = [w for w in words if not claimed(w)] + [s for s in (spare or []) if not claimed(s)]
    l0, t0, r0, b0 = left, top, right, bottom
    for w in loose:
        wl, wt, wr, wb = w[0], w[1], w[0] + w[2], w[1] + w[3]
        dx = max(wl - r0, l0 - wr, 0.0)
        dy = max(wt - b0, t0 - wb, 0.0)
        within = wl >= l0 - 0.1 and wr <= r0 + 0.02  # in the drawing's own column (swatches sit a little left)
        if max(dx, dy) <= locate.MERGE_GAP or (within and dx == 0.0 and dy <= LEGEND_REACH) \
                or (within and dy <= LEGEND_REACH and dx <= 0.1):
            left, top, right, bottom = min(left, wl), min(top, wt), max(right, wr), max(bottom, wb)
    return (max(left, 0.0), max(top, 0.0), min(right, 1.0) - max(left, 0.0), min(bottom, 1.0) - max(top, 0.0))


def locate_figures(doc: Document, page: Page, figures: list[Region]) -> dict[str, locate.Box]:
    """Box of each figure block of the page that a drawing could be matched to."""
    image = page_image(doc, page, width=RENDER_W)
    if image is None or not figures:
        return {}
    words = pdf_words(doc, page)
    fig_ids = {r.id for r in figures}
    # text to take off the page: the words each block was grounded on (a block's own box can be loose); a
    # block without word boxes counts with its box when that is small
    blocks: list[locate.Box] = []
    for r in page.regions:
        if r.id in fig_ids:
            continue
        got = [(w.bbox.left, w.bbox.top, w.bbox.width, w.bbox.height) for w in r.iter_words() if w.bbox is not None]
        box = _box_of(r) if r.meta.get("bbox_source") != "page" else None
        if r.type == RegionType.TABLE and box is not None:
            blocks.append(box)  # a table's rules and cells are never a picture
        elif got:
            blocks.extend(got)
        elif box is not None and box[2] * box[3] <= 0.08:
            blocks.append(box)
    spare: list[locate.Box] = []
    found = locate.figure_boxes(image, words + blocks, len(figures), [_hint(r) for r in figures], spare=spare)
    out = {}
    for region, box in zip(figures, found):
        if box is not None:
            claimed = [b for r in page.regions if r.id not in fig_ids and r.meta.get("bbox_source") != "page"
                       for b in [_box_of(r)] if b is not None and b[2] * b[3] <= 0.08]
            out[region.id] = _grow(box, words, claimed + blocks, spare)  # blocks: words text blocks stand on
    return out


def crop(doc: Document, page: Page, box: locate.Box, out: Path, pad: float = 0.006) -> Optional[str]:
    image = page_image(doc, page, dpi=CROP_DPI)
    if image is None:
        return None
    w, h = image.size
    left, top = max(box[0] - pad, 0.0), max(box[1] - pad, 0.0)
    right, bottom = min(box[0] + box[2] + pad, 1.0), min(box[1] + box[3] + pad, 1.0)
    if right - left <= 0 or bottom - top <= 0:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    image.crop((int(left * w), int(top * h), max(int(right * w), int(left * w) + 1),
                max(int(bottom * h), int(top * h) + 1))).save(out)
    return str(out)


# ---------------------------------------------------------------- model calls

def llm_spec(reader_spec: Optional[str] = None) -> str:
    """READANYDOC_FIGURE_LLM, else the task model, else the provider the page reader uses."""
    spec = os.environ.get("READANYDOC_FIGURE_LLM") or os.environ.get("READANYDOC_TASK_LLM")
    if spec:
        return spec
    r = reader_spec or ""
    if "anthropic_api" in r or r.startswith("api_cache"):
        return "anthropic_api:haiku"
    return "claude_cli:haiku"


def _cached_call(llm, system: str, user: str, image_path: str, max_tokens: int) -> tuple[str, dict[str, Any]]:
    """One call, replayed from disk when READANYDOC_FIGURE_CACHE is set and the same call was made before."""
    data = Path(image_path).read_bytes()
    cache = os.environ.get("READANYDOC_FIGURE_CACHE")
    f = None
    if cache:
        key = hashlib.sha1(data + f"|{system}|{user}|{getattr(llm, 'name', '')}".encode()).hexdigest()[:24]
        f = Path(cache) / f"{key}.json"
        if f.exists():
            d = json.loads(f.read_text())
            return d["text"], {**d.get("usage", {}), "cached": True}
    res = llm.complete(system, user, images=[data], max_tokens=max_tokens)
    usage = {"model": res.model, **{k: res.usage.get(k) for k in ("input_tokens", "output_tokens", "cost_usd")}}
    if f is not None and res.text.strip():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"text": res.text, "usage": usage}))
    return res.text, usage


def chart_markdown(data: dict[str, Any]) -> str:
    """Legend and data table for a chart, the way the Figure block shows it."""
    lines = []
    if data.get("chart_title"):
        lines += [f"# {data['chart_title']}", ""]
    series = [s for s in data.get("series") or [] if isinstance(s, dict)]
    axes = data.get("axes") or {}
    y = (axes.get("y_primary") or {}).get("label")
    lines += ["## Legend", ""]
    for s in series:
        axis = f"; {s.get('corresponding_y') or 'primary'} y-axis" + (f" ({y})" if y else "")
        lines.append(f"- **{s.get('label') or 'Series'}** — {s.get('type') or data.get('chart_type') or 'series'}"
                     + (axis if (s.get("type") or data.get("chart_type")) != "pie" else ""))
    xs: list[str] = []
    for s in series:
        for pt in s.get("data") or []:
            x = str(pt.get("x") if pt.get("x") is not None else pt.get("date") or "")
            if x not in xs:
                xs.append(x)
    def cell(v):
        return "" if v is None else str(v)
    lines += ["", "## Data table", "", "| " + " | ".join([(axes.get("x") or {}).get("label") or "X-axis point"]
                                                         + [str(s.get("label") or "") for s in series]) + " |",
              "|" + "---|" * (len(series) + 1)]
    for x in xs:
        row = [x]
        for s in series:
            hit = next((pt for pt in s.get("data") or []
                        if str(pt.get("x") if pt.get("x") is not None else pt.get("date") or "") == x), None)
            row.append(cell(hit.get("value")) if hit else "")
        lines.append("| " + " | ".join(row) + " |")
    if data.get("estimated"):
        lines += ["", "Values are estimated from the axis."]
    return "\n".join(lines)


def chart_problems(data: dict[str, Any]) -> list[str]:
    """Deterministic consistency checks on chart data (no model call). Empty list = nothing found."""
    out: list[str] = []
    series = [s for s in data.get("series") or [] if isinstance(s, dict)]
    def num(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    axis = ((data.get("axes") or {}).get("y_primary") or {})
    lo, hi = num(axis.get("min")), num(axis.get("max"))
    kinds = {str(s.get("type") or data.get("chart_type") or "") for s in series}
    lengths = {}
    for s in series:
        label = str(s.get("label") or "series")
        pts = [p for p in s.get("data") or [] if isinstance(p, dict)]
        xs = [str(p.get("x")) for p in pts]
        vals = [num(p.get("value")) for p in pts]
        if not pts:
            out.append(f"series '{label}' has no data points")
            continue
        lengths[label] = len(pts)
        if len(set(xs)) < len(xs):
            out.append(f"series '{label}' lists the same x value more than once")
        missing = sum(v is None for v in vals)
        if missing > len(vals) / 2:
            out.append(f"series '{label}': {missing} of {len(vals)} values are missing")
        if (s.get("corresponding_y") or "primary") == "primary" and lo is not None and hi is not None and hi > lo:
            span = hi - lo
            off = [v for v in vals if v is not None and not (lo - 0.05 * span <= v <= hi + 0.05 * span)]
            if off:
                out.append(f"series '{label}': {len(off)} value(s) lie outside the y-axis range {lo:g} to {hi:g} "
                           f"(e.g. {off[0]:g})")
    if kinds == {"pie"} and len(series) == 1:
        vals = [num(p.get("value")) for p in series[0].get("data") or [] if isinstance(p, dict)]
        vals = [v for v in vals if v is not None]
        total = sum(vals)
        if vals and all(0 <= v <= 100 for v in vals) and 50 < total and not (97 <= total <= 103):
            out.append(f"the pie slices look like percentages but add up to {total:g}, not 100")
    elif len(set(lengths.values())) > 1 and not kinds & {"scatter"}:
        same_axis = {lab: n for lab, n in lengths.items()}
        most = max(set(same_axis.values()), key=list(same_axis.values()).count)
        short = [f"'{lab}' ({n})" for lab, n in same_axis.items() if n < most * 0.8]
        if short:
            out.append(f"series {', '.join(short)} have far fewer points than the others ({most})")
    return out


def _ask(llm, want: dict[str, Any], image_path: str) -> dict[str, Any]:
    """The model's answers for one figure: {"content", "chart", "usage": [..]}."""
    out: dict[str, Any] = {"usage": []}
    if want["chart"]:
        from ...tasks.llm import parse_json
        text, usage = _cached_call(llm, CHART_SYSTEM, CHART_PROMPT, image_path, 12000)
        out["usage"].append(usage)
        try:
            data = parse_json(text)
        except Exception:
            data = None
        if isinstance(data, dict) and data.get("is_chart") and data.get("series"):
            data.pop("is_chart", None)
            # verification pass. Free checks on the data; one more read only when a check fails.
            problems = chart_problems(data)
            verification = {"status": "passed", "problems": []}
            if problems:
                fix_prompt = (CHART_PROMPT + "\n\nA first reading of this chart had these problems:\n- "
                              + "\n- ".join(problems) + "\nRead the chart again carefully and return the "
                              "corrected JSON object.")
                text2, usage2 = _cached_call(llm, CHART_SYSTEM, fix_prompt, image_path, 12000)
                out["usage"].append(usage2)
                try:
                    data2 = parse_json(text2)
                except Exception:
                    data2 = None
                if isinstance(data2, dict) and data2.get("is_chart") and data2.get("series") \
                        and len(chart_problems(data2)) < len(problems):
                    data2.pop("is_chart", None)
                    left = chart_problems(data2)
                    data, verification = data2, {"status": "corrected" if not left else "flagged",
                                                 "problems": left, "first_read_problems": problems}
                else:  # the second read is no better: keep the first and say what is wrong with it
                    verification = {"status": "flagged", "problems": problems}
            out["verification"] = verification
            out["chart"] = data
            out["content"] = chart_markdown(data)
            return out
    if want["prompt"]:
        text, usage = _cached_call(llm, DESCRIBE_SYSTEM, str(want["prompt"]), image_path, 4000)
        out["usage"].append(usage)
        if text.strip():
            out["content"] = text.strip()
    return out


# ---------------------------------------------------------------- entry point

def clear(doc: Document) -> None:
    for r in doc.iter_regions():
        for k in ("image_path", "figure_content", "chart_data", "is_chart", "figure_box", "chart_verification"):
            r.meta.pop(k, None)


def process(doc: Document, request: Optional[dict[str, Any]], reader_spec: Optional[str] = None,
            llm=None, out_dir: Optional[Path] = None, password: Optional[str] = None,
            layout_figures: bool = False, describe: bool = False, layout_running: float = 0.5) -> dict[str, Any]:
    """Locate, crop and (when asked) question the figures of ``doc``. Returns counts for the stage note.

    ``layout_figures``: also emit a Figure block for every picture the layout model found that no
    block accounts for; ``describe`` asks the model for one sentence about each of those (one call each)."""
    want = wants(request)
    clear(doc)
    for page in doc.pages:  # blocks added by an earlier run of this stage on the same document
        page.regions = [r for r in page.regions if not r.meta.get("from_layout")]
    stats = {"figures": 0, "located": 0, "figure_images": 0, "table_images": 0, "calls": 0, "charts": 0}
    layout_figures = layout_figures and any(p.properties.get("layout_detections") for p in doc.pages)
    if not want["any"] and not layout_figures and not any(is_figure(r) for r in doc.iter_regions()):
        return stats
    want = {**want, "layout_figures": layout_figures, "describe": describe, "layout_running": layout_running}
    doc.meta["_password"] = password  # for rendering only; removed again below, never stored
    try:
        return _process(doc, want, stats, reader_spec, llm, out_dir)
    finally:
        doc.meta.pop("_password", None)


def _process(doc: Document, want: dict[str, Any], stats: dict[str, Any], reader_spec: Optional[str], llm,
             out_dir: Optional[Path]) -> dict[str, Any]:
    out_dir = Path(out_dir) if out_dir else paths.cache_dir() / "figures" / doc.id
    # whether a figure without a box loses anything the request asked for (an image, a description, chart data)
    stats["boxes_needed"] = bool(want.get("figure_images") or want.get("prompt") or want.get("chart"))
    jobs: list[tuple[Region, str]] = []
    describe_jobs: list[tuple[Region, str]] = []
    for page in doc.pages:
        figures = [r for r in page.regions if is_figure(r) and not r.meta.get("merged_into")]
        stats["figures"] += len(figures)
        boxes = locate_figures(doc, page, figures) if figures else {}  # figure blocks always get their box
        for r in figures:
            box = boxes.get(r.id)
            if box is None:
                continue
            stats["located"] += 1
            r.meta["figure_box"] = [round(v, 4) for v in box]
            # the reader's description is not text on the page, so a box grounded on its words is a guess;
            # the drawing found on the page is the evidence for where the figure is
            r.bbox = BBox(left=box[0], top=box[1], width=box[2], height=box[3], page=page.index)
            r.meta["bbox_source"] = "figure_ink"
            if r.type != RegionType.FIGURE:  # a described drawing that is really on the page is a Figure block
                r.meta["type_before_figure"] = r.type.value
                r.type = RegionType.FIGURE
            if not (want["figure_images"] or want["prompt"] or want["chart"]):
                continue
            path = crop(doc, page, box, out_dir / f"{r.id}.png")
            if path:
                if want["figure_images"]:
                    r.meta["image_path"] = path
                    stats["figure_images"] += 1
                if want["prompt"] or want["chart"]:
                    jobs.append((r, path))
        if want.get("layout_figures"):
            from . import layout_pictures
            have = [tuple(r.meta["figure_box"]) for r in figures if r.meta.get("figure_box")]
            lost = [r for r in figures if not r.meta.get("figure_box")]
            added = layout_pictures.add(page, have, unlocated=lost, running=float(want.get("layout_running", 0.5)))
            for r in lost:  # a described figure that took a picture's box: located after all
                if r.meta.get("figure_box"):
                    stats["located"] += 1
                    stats["located_by_layout"] = stats.get("located_by_layout", 0) + 1
                    if r.type != RegionType.FIGURE:
                        r.meta["type_before_figure"] = r.type.value
                        r.type = RegionType.FIGURE
                    if want["figure_images"] or want["prompt"] or want["chart"]:
                        path = crop(doc, page, tuple(r.meta["figure_box"]), out_dir / f"{r.id}.png")
                        if path and want["figure_images"]:
                            r.meta["image_path"] = path
                            stats["figure_images"] += 1
                        if path and (want["prompt"] or want["chart"]):
                            jobs.append((r, path))
            for r in added:
                stats["figures"] += 1
                stats["located"] += 1
                stats["from_layout"] = stats.get("from_layout", 0) + 1
                box = tuple(r.meta["figure_box"])
                asked = want["prompt"] or want["chart"]
                if not (want["figure_images"] or asked or want.get("describe")):
                    continue
                path = crop(doc, page, box, out_dir / f"{r.id}.png")
                if path:
                    if want["figure_images"]:
                        r.meta["image_path"] = path
                        stats["figure_images"] += 1
                    if asked:
                        jobs.append((r, path))
                    elif want.get("describe"):
                        describe_jobs.append((r, path))
        if want["table_images"]:
            for r in page.regions:
                box = _box_of(r) if r.type == RegionType.TABLE else None
                if box is not None and r.meta.get("bbox_source") != "page":
                    path = crop(doc, page, box, out_dir / f"{r.id}.png", pad=0.004)
                    if path:
                        r.meta["image_path"] = path
                        stats["table_images"] += 1
    if describe_jobs:
        from . import layout_pictures
        try:
            if llm is None:
                from ...tasks.llm import get_llm
                llm = get_llm(llm_spec(reader_spec))
            def say(job):
                try:
                    return layout_pictures.describe(llm, job[1])
                except Exception as e:
                    return None, {"error": f"{type(e).__name__}: {e}"}
            with ThreadPoolExecutor(max_workers=4) as ex:
                said = list(ex.map(say, describe_jobs))
            for (r, _path), (text, usage) in zip(describe_jobs, said):
                if "error" not in usage:
                    stats["calls"] += 1
                    doc.usage.append({**usage, "task": "figure_describe"})
                if text:
                    r.content = text
                    r.meta["figure_content"] = text
        except Exception as e:  # descriptions are extras
            stats.setdefault("errors", []).append(f"{type(e).__name__}: {e}"[:200])
    if jobs:
        if llm is None:
            from ...tasks.llm import get_llm
            llm = get_llm(llm_spec(reader_spec))
        def run(job):
            try:
                return _ask(llm, want, job[1])
            except Exception as e:  # a failed figure call never fails the parse
                return {"usage": [], "error": f"{type(e).__name__}: {e}"}
        with ThreadPoolExecutor(max_workers=4) as ex:
            answers = list(ex.map(run, jobs))
        for (r, path), ans in zip(jobs, answers):
            for u in ans["usage"]:
                stats["calls"] += 1
                doc.usage.append({**u, "task": "figure"})
            if ans.get("error"):
                stats.setdefault("errors", []).append(ans["error"][:200])
            if ans.get("chart"):
                r.meta["chart_data"] = ans["chart"]
                r.meta["is_chart"] = True
                if ans.get("verification"):
                    r.meta["chart_verification"] = ans["verification"]
                stats["charts"] += 1
            if ans.get("content"):
                r.meta["figure_content"] = ans["content"]
    return stats
