"""Stage 3, Layout + reading order: typed, boxed regions per page (content is filled by Read).

Option ``detect_only``: the detector's regions are stored as plain dicts in
``page.properties["layout_detections"]`` and nothing else changes, so Read and Ground behave exactly as
without a layout stage. They are used after grounding by ``pipeline/regions`` when one of its switches is on;
with every switch off the detector is not even loaded.
"""

from __future__ import annotations

from ..model import Candidate, Document, Region
from ..readers.base import ReadContext
from .base import PipelineContext, Stage, register_stage


@register_stage("layout")
class Layout(Stage):
    def run(self, doc: Document, ctx: PipelineContext) -> Document:
        if self.opt("detect_only", False):
            from .regions import detect_pages, switches_on
            if switches_on(ctx):
                detect_pages(doc, ctx)
            return doc
        for page in doc.pages:
            image = page.renders.get("150")
            if self.opt("only_untrusted", False) and page.properties.get("text_layer_exact"):
                continue  # a born-digital page with a checked text layer needs no table repair
            try:
                if getattr(ctx.layout, "needs_native_image", False):  # model detectors
                    from ..readers.sizing import render_pdf_native, resize_image_native
                    if doc.file_type == "pdf":
                        image = render_pdf_native(doc.meta["path"], page.original_page)
                    else:
                        src = image or next(iter(page.renders.values()), None) or doc.meta.get("path")
                        image = resize_image_native(src) if src else None
                blocks = ctx.layout.detect(image, ReadContext(stem=doc.stem, page_index=page.index))
                usage = getattr(ctx.layout, "last_usage", None)
                if usage is not None:
                    doc.usage.append({"page": page.index, "reader": f"layout:{ctx.layout.name}",
                                      **usage.model_dump(exclude_none=True)})
            except Exception as e:
                if isinstance(e, ImportError) or "onnxruntime" in str(e):
                    doc.note(self.name, "the layout model is not installed (pip install 'readanydoc[engine]'); block "
                                        "types, boxes, forms and headers/footers come from the page read only",
                             "warning")
                    break
                doc.note(self.name, f"page {page.index}: layout unavailable ({e})", "warning")
                continue
            regions = []
            for i, b in enumerate(sorted(blocks, key=lambda b: b.order)):
                r = Region(type=b.type, bbox=b.bbox, order=i, native_label=b.native_label,
                           source=f"layout:{ctx.layout.name}")
                if b.score is not None:
                    r.meta["layout_score"] = b.score
                if b.text:  # the detector pipeline's own read: kept as a candidate, never as content
                    r.candidates.append(Candidate(source=f"layout:{ctx.layout.name}", content=b.text))
                regions.append(r)
            page.regions = regions
            page.layout_regions = [r.model_copy(deep=True) for r in regions]
        return doc


from . import regions as _regions  # noqa: E402,F401  (registers the ``ground_regions`` implementation)
