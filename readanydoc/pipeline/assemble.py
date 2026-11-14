"""Stage 7, Assemble: whole-document structure.

What it does (each behind an option; nothing here edits ``region.content``):
- ``repeated_furniture`` (default on): running headers / footers / page numbers by repetition
  across pages (3+ pages). Old type kept in ``meta["type_before_assemble"]``.
- ``headings`` (default on): one heading tree for the document; ``heading_levels`` (default on)
  also normalises levels from numbering patterns and, with ``heading_font_size`` (default on),
  PDF-layer type size. Old level kept in ``meta["heading_level_before"]``.
- ``paragraph_links`` / ``table_links`` (default on): cross-page continuation links in
  ``Document.links`` with a confidence; only ``high`` links are ever acted on.
- ``merge_tables`` (default: the request's ``formatting.merge_tables``, else off): merge accepted
  table continuations; the merged HTML and the original fragments go to the first block's meta.
- annotations: with ``formatting.include`` containing ``hyperlinks`` / ``comments``, PDF link
  annotations are attached to the words they cover and PDF comments become Comment blocks.
Joined paragraphs are printed as one by Shape (``shape.join_paragraphs``, default off).
"""

from __future__ import annotations

from ..model import Document
from .assembly import annotations, furniture, headings, paragraphs
from .assembly import tables as table_joins
from .base import PipelineContext, Stage, register_stage
from .shaping.options import resolve


@register_stage("assemble")
class Assemble(Stage):
    def run(self, doc: Document, ctx: PipelineContext) -> Document:
        from .intake import restore_native_pages
        restore_native_pages(doc)
        opts = resolve(ctx.config.parse, self.opt("request"))
        native = bool(doc.meta.get("native"))
        if self.opt("repeated_furniture", True) and not native and len(doc.pages) >= 3:
            changed = furniture.detect_furniture(
                doc, min_pages=int(self.opt("furniture_min_pages", 3)),
                min_share=float(self.opt("furniture_min_share", 0.3)))
            kept = [c for c in changed if c.get("kept")]
            if len(changed) > len(kept):
                doc.note(self.name, f"retyped {len(changed) - len(kept)} repeated edge blocks as page furniture")
            if self.opt("restore_body", True):
                kept += furniture.restore_body(doc, changed)
            if kept:
                doc.note(self.name, f"{len(kept)} block(s) at page edges kept as content (captions, continued "
                                    "headings, sentences running over the page break)")
        if "comments" in opts.include and annotations.add_comments(doc):
            doc.note(self.name, "PDF comment annotations added as Comment blocks")
        if "hyperlinks" in opts.include and not native:
            annotations.attach_links(doc)
        if self.opt("headings", True):
            headings.apply(doc, normalise=bool(self.opt("heading_levels", True)) and not native,
                           use_font_size=bool(self.opt("heading_font_size", True)),
                           unify=bool(self.opt("heading_types", True)))
        else:
            doc.heading_tree = []
        doc.links = []
        table_joins.clear_merges(doc)
        if self.opt("table_links", True) and doc.file_type not in ("xlsx", "xlsm", "csv"):
            joins = table_joins.find_table_continuations(doc)
            doc.links.extend(link for link, _ in joins)
            merge = self.opt("merge_tables")
            if merge is None:
                merge = opts.merge_tables
            if merge and joins:
                n = table_joins.merge_tables(doc, joins)
                if n:
                    doc.note(self.name, f"merge_tables: {n} table(s) merged across pages")
        if self.opt("paragraph_links", True) and doc.file_type not in ("xlsx", "xlsm", "csv"):
            doc.links.extend(paragraphs.find_paragraph_continuations(doc))
        return doc
