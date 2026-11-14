"""Stage 8, Shape: chunking, table formats, filter_blocks, page markers, embed.

Writes ``Document.chunks``; the Reducto serializer turns those into the API response. Region
content is never rewritten: when a block must be shown differently (table format, merged table,
document-level heading level, hyperlinks) the shaped form goes to ``region.meta["shaped_content"]``
and into the chunk text. See ``pipeline/shaping/`` for the pieces.

Stage options (``StageConfig.options``), all optional:
- ``request``: the raw Reducto parse request (see ``shaping.options.apply_request``).
- ``dynamic_tables`` (default True): let ``table_output_format="dynamic"`` turn simple tables into
  markdown. Off = dynamic keeps the reader's HTML.
- ``heading_levels`` (default True): show the document-level heading levels Assemble computed.
- ``join_paragraphs`` (default False): print a paragraph that Assemble linked across a page break
  as one paragraph (only links Assemble marked high-confidence).
- ``table_summarizer``: ``"pkg.mod:func"`` hook for embedding-optimized table summaries.
- ``llm_table_summaries`` (default True): with ``embedding_optimized`` and a live model provider, ask the
  model for each table's summary (``shaping/summarize.py``); off = the deterministic fallback.
- ``figures`` (default True): locate figure blocks on the page; crop / question them when the request asks.
- ``figure_llm``: an ``readanydoc.tasks.llm.LLM`` instance for figure calls (tests).
- ``heading_context`` (default True): chunks that start under a heading they do not contain repeat it
  with " (cont.)", as Reducto does in every chunk mode except ``disabled``.
- any ``shaping.options.Options`` field (``chunk_mode``, ``table_output_format``, ...) as an override.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..model import Chunk, Document, Page, Region, RegionType
from .base import ParseOptions, PipelineContext, Stage, register_stage
from .shaping import chunking, embed as embed_mod, merge as merge_mod, tables
from .shaping.options import Options, resolve

_HEADING_PREFIX = re.compile(r"^(\s{0,3})(#{1,6})(\s+)")
CHUNK_MODES = ("disabled", "page", "block", "section", "page_sections", "variable")


def _reducto_box(box, page: Page) -> Optional[dict[str, Any]]:
    if box is None:
        return None
    return {"left": round(box.left, 4), "top": round(box.top, 4), "width": round(box.width, 4),
            "height": round(box.height, 4), "page": page.index + 1, "original_page": page.original_page}


def apply_links(content: str, links: list[dict[str, Any]]) -> str:
    """Wrap character spans in markdown links. Spans are offsets into ``content``; overlaps are skipped."""
    out, last_start = content, len(content) + 1
    for ln in sorted(links, key=lambda d: d["start"], reverse=True):
        s, e, url = int(ln["start"]), int(ln["end"]), str(ln.get("url") or "")
        if not url or not (0 <= s < e <= len(content)) or e > last_start:
            continue
        text = content[s:e]
        if "](" in text or "<" in text or "\n" in text:
            continue
        out = out[:s] + f"[{text}]({url})" + out[e:]
        last_start = s
    return out


def heading_content(content: str, level: Optional[int]) -> str:
    """Rewrite the leading ``#`` run of a heading to ``level`` (1-6). Text is untouched."""
    if not level:
        return content
    level = min(max(int(level), 1), 6)
    m = _HEADING_PREFIX.match(content)
    if m:
        return content if len(m.group(2)) == level else m.group(1) + "#" * level + m.group(3) + content[m.end():]
    return content


class Shaper:
    """Computes what each block shows, then chunks."""

    def __init__(self, doc: Document, opts: Options, *, dynamic_tables: bool = True, heading_levels: bool = True,
                 join_paragraphs: bool = False, summarizer=None, heading_context: bool = True):
        self.doc = doc
        self.opts = opts
        self.dynamic_tables = dynamic_tables
        self.heading_levels = heading_levels
        self.join_paragraphs = join_paragraphs
        self.summarizer = summarizer
        self.heading_context = heading_context
        self.notes: list[tuple[str, str]] = []
        self.table_formats: dict[str, int] = {}

    # ---- one block ----
    def block(self, region: Region, page: Page) -> tuple[str, str]:
        """(content to show, embed text) for one region, ignoring filter_blocks."""
        base = region.content
        if region.type == RegionType.TABLE:
            return self._table(region, page)
        if region.meta.get("figure_content"):  # the answer to the request's figure prompt / chart extraction
            return region.meta["figure_content"], region.meta["figure_content"]
        shown = base
        if region.meta.get("links") and "hyperlinks" in self.opts.include:
            shown = apply_links(shown, region.meta["links"])
        if self.heading_levels and region.type in (RegionType.TITLE, RegionType.SECTION_HEADER) \
                and region.meta.get("heading_level_source"):
            shown = heading_content(shown, region.heading_level)
        return shown, shown

    def _table(self, region: Region, page: Page) -> tuple[str, str]:
        source = region.meta.get("merged_content") or region.content
        fmt = self.opts.table_output_format
        if fmt == "dynamic" and not self.dynamic_tables:
            fmt = "html" if tables.is_html_table(source) else "md"
        grid = tables.parse_table(source)
        if grid is None:
            return source, source
        boxes = None
        if fmt == "jsonbbox":
            boxes = {(c.row, c.col): _reducto_box(c.bbox, page) for c in region.cells if c.bbox is not None}
            if not boxes:
                self.notes.append(("warning", f"table {region.id}: no cell boxes, jsonbbox written as json"))
        shown, used = tables.convert_table(source, fmt, boxes)
        self.table_formats[used] = self.table_formats.get(used, 0) + 1
        emb = shown
        if self.opts.embedding_optimized:
            emb, how = embed_mod.table_embed(grid, shown, {"region_id": region.id, "page": page.index + 1},
                                             self.summarizer)
            region.meta["embed_source"] = how
        return shown, emb

    # ---- all blocks ----
    def pieces(self) -> list[chunking.Piece]:
        filtered = set(self.opts.filter_blocks)
        joins: dict[str, str] = {}
        if self.join_paragraphs:
            for link in self.doc.links:
                if link.kind == "paragraph_continuation" and link.confidence.label == "high":
                    joins[link.to_region] = link.from_region
        out = []
        for page in self.doc.pages:
            for region in page.regions:
                absorbed = bool(region.meta.get("merged_into"))
                shown, emb = ("", "") if absorbed else self.block(region, page)
                if not absorbed:
                    if shown != region.content:
                        region.meta["shaped_content"] = shown
                    else:
                        region.meta.pop("shaped_content", None)
                hide = region.type.value in filtered or not shown.strip()
                out.append(chunking.Piece(region_id=region.id, page=page.index, type=region.type.value,
                                          text="" if hide else shown, embed="" if hide else emb,
                                          in_blocks=not absorbed, joins_prev=joins.get(region.id)))
        return out

    def chunks(self) -> list[Chunk]:
        mode = self.opts.chunk_mode if self.opts.chunk_mode in CHUNK_MODES else "disabled"
        out = chunking.chunk_pieces(self.pieces(), mode, [p.index for p in self.doc.pages],
                                    add_page_markers=self.opts.add_page_markers, band=self.opts.size_band,
                                    overlap=self.opts.chunk_overlap, heading_context=self.heading_context)
        for ch in out:  # Reducto r-1 returns the chunk text again as "enriched", enrichment_success true
            ch.enriched = ch.content
        return out


def build_chunks(doc: Document, opts: ParseOptions | Options, **flags: Any) -> list[Chunk]:
    """Chunks for ``doc``. Accepts the spine's ParseOptions or the full shaping Options."""
    full = opts if isinstance(opts, Options) else resolve(opts)
    return Shaper(doc, full, **flags).chunks()


@register_stage("shape")
class Shape(Stage):
    def run(self, doc: Document, ctx: PipelineContext) -> Document:
        from .intake import restore_native_pages
        restore_native_pages(doc)
        overrides = {k: v for k, v in self.options.items()
                     if k in Options.__dataclass_fields__ and k != "spreadsheet"}
        opts = resolve(ctx.config.parse, self.opt("request"), overrides)
        if opts.chunk_mode not in CHUNK_MODES:
            doc.note(self.name, f"unknown chunk_mode {opts.chunk_mode!r}; using 'disabled'", "warning")
        if opts.table_output_format not in tables.FORMATS:
            doc.note(self.name, f"unknown table_output_format {opts.table_output_format!r}; using 'dynamic'",
                     "warning")
            opts.table_output_format = "dynamic"
        request = self.opt("request") or {}
        from . import figures
        if self.opt("figures", True):
            try:
                enhance = request.get("enhance") or {}
                stats = figures.process(doc, request, reader_spec=ctx.config.reader, llm=self.opt("figure_llm"),
                                        password=ctx.config.parse.document_password,
                                        layout_figures=bool(self.opt("layout_figures", False)),
                                        layout_running=float(self.opt("layout_figures_running", 0.5)),
                                        describe=bool(self.opt("layout_figures_describe", False))
                                        and enhance.get("summarize_figures", True) is not False)
                doc.meta["figures"] = stats
                if stats["figures"] and stats["located"] < stats["figures"]:
                    doc.note(self.name, f"figures: {stats['located']} of {stats['figures']} figure blocks could be "
                                        "located on the page; the others have no image", "warning")
            except Exception as e:  # images and chart data are extras: never fail the parse for them
                doc.note(self.name, f"figures not processed ({type(e).__name__}: {e})", "warning")
        # custom table / text prompts (one text call per table, one per page; only when asked)
        from .shaping import agentic, searchable
        if self.opt("agentic_prompts", True) and any(agentic.wants(request).values()):
            try:
                stats = agentic.process(doc, request, reader_spec=ctx.config.reader, llm=self.opt("agentic_llm"))
                doc.meta["agentic"] = {k: v for k, v in stats.items() if k != "notes"}
                for msg in stats["notes"][:10]:
                    doc.note(self.name, msg, "warning")
            except Exception as e:
                doc.note(self.name, f"agentic prompts not applied ({type(e).__name__}: {e})", "warning")
        model = (request.get("settings") or {}).get("model")
        if model in ("r-1", "r-1-mini", "legacy"):
            doc.meta["requested_model"] = model  # the response reports the model that was asked for
        if self.opt("searchable_pdf", True) and searchable.wanted(request):
            try:
                from .. import paths
                st = request.get("settings") or {}
                stats = searchable.build(doc, paths.cache_dir() / "figures" / doc.id / "searchable.pdf",
                                         dpi=st.get("embed_pdf_metadata_dpi"),
                                         password=ctx.config.parse.document_password)
                doc.meta["searchable_pdf"] = stats
                if not stats["path"]:
                    why = ", ".join(f"{k}: {v}" for k, v in stats["skipped"].items()) or "no page image"
                    doc.note(self.name, f"embed_pdf_metadata: no text layer was added ({why})")
            except Exception as e:
                doc.note(self.name, f"embed_pdf_metadata failed ({type(e).__name__}: {e})", "warning")
        if opts.merge_tables:
            n = merge_mod.merge_consecutive(doc)
            if n:
                doc.note(self.name, f"merge_tables: {n} run(s) of same-width tables merged")
        else:
            merge_mod.clear(doc)
        summarizer = None
        if self.opt("table_summarizer"):
            try:
                summarizer = embed_mod.load_summarizer(self.opt("table_summarizer"))
            except Exception as e:
                doc.note(self.name, f"table_summarizer not loaded ({e}); using the deterministic fallback", "warning")
        if summarizer is None and embed_mod._summarizer is None and opts.embedding_optimized \
                and self.opt("llm_table_summaries", True) and not doc.meta.get("native"):
            from .shaping import summarize
            spec = summarize.default_spec(ctx.config.reader)
            if spec:  # one text call per table, only because the request asked for embedding-optimised output
                summarizer = summarize.make_summarizer(spec, doc.usage)
        shaper = Shaper(doc, opts, dynamic_tables=bool(self.opt("dynamic_tables", True)),
                        heading_levels=bool(self.opt("heading_levels", True)),
                        join_paragraphs=bool(self.opt("join_paragraphs", False)), summarizer=summarizer,
                        heading_context=bool(self.opt("heading_context", True)))
        doc.chunks = shaper.chunks()
        for level, msg in shaper.notes:
            doc.note(self.name, msg, level)
        if opts.embedding_optimized and summarizer is None and embed_mod._summarizer is None \
                and any(r.meta.get("embed_source") == "fallback" for r in doc.iter_regions()):
            doc.note(self.name, "embedding_optimized: no table summarizer configured; tables in 'embed' are "
                                "linearised as header: value pairs")
        po = ctx.config.parse.model_dump(exclude={"document_password"})
        po.update({"chunk_mode": opts.chunk_mode, "chunk_size": opts.chunk_size, "chunk_overlap": opts.chunk_overlap,
                   "filter_blocks": list(opts.filter_blocks), "add_page_markers": opts.add_page_markers,
                   "table_output_format": opts.table_output_format,
                   "extract_document_properties": opts.extract_document_properties,
                   "embedding_optimized": opts.embedding_optimized, "merge_tables": opts.merge_tables,
                   "include": list(opts.include)})
        doc.meta["parse_options"] = po
        doc.meta["table_formats"] = shaper.table_formats
        return doc
