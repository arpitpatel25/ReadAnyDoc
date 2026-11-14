"""PPTX via python-pptx: one slide = one page, shapes keep their real position on the slide."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..model import BBox, Page, RegionType
from .common import esc, finish_pages, iso, make_region, new_page
from .errors import IntakeError, damaged


def _shapes(container):
    for sh in container:
        if sh.shape_type is not None and str(sh.shape_type).startswith("GROUP"):
            yield from _shapes(sh.shapes)
        else:
            yield sh


def _box(sh, w: float, h: float, page: int) -> Optional[BBox]:
    try:
        if sh.left is None or sh.width is None:
            return None
        return BBox.from_xyxy(sh.left, sh.top, sh.left + sh.width, sh.top + sh.height, page, w, h)
    except Exception:
        return None


def _table(tbl) -> str:
    out = ["<table>"]
    for i, row in enumerate(tbl.rows):
        out.append("<tr>")
        for cell in row.cells:
            if cell.is_spanned:
                continue
            attrs = ""
            if cell.is_merge_origin:
                if cell.span_height > 1:
                    attrs += f' rowspan="{cell.span_height}"'
                if cell.span_width > 1:
                    attrs += f' colspan="{cell.span_width}"'
            tag = "th" if i == 0 and tbl.first_row and len(tbl.rows) > 1 else "td"
            out.append(f"<{tag}{attrs}>{esc(cell.text.strip()).replace(chr(10), '<br>')}</{tag}>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def load_pptx(path: Path, include: Optional[list[str]] = None) -> tuple[list[Page], dict, dict]:
    import pptx
    try:
        prs = pptx.Presentation(str(path))
    except Exception as e:
        raise damaged("PPTX", e) from e
    w, h = float(prs.slide_width or 9144000), float(prs.slide_height or 6858000)
    pages: list[Page] = []
    for i, slide in enumerate(prs.slides):
        page = new_page(i, i + 1, width=round(w / 12700, 2), height=round(h / 12700, 2))
        title_shape = None
        try:
            title_shape = slide.shapes.title
        except Exception:
            pass
        items = []
        for sh in _shapes(slide.shapes):
            box = _box(sh, w, h, i)
            key = (box.top if box else 1.0, box.left if box else 0.0)
            if title_shape is not None and sh.shape_id == title_shape.shape_id:
                key = (-1.0, 0.0)
            items.append((key, sh, box))
        for _, sh, box in sorted(items, key=lambda t: t[0]):
            if getattr(sh, "has_table", False) and sh.has_table:
                page.regions.append(make_region(RegionType.TABLE, _table(sh.table), "pptx", fmt="html", bbox=box))
            elif getattr(sh, "has_text_frame", False) and sh.has_text_frame:
                is_title = title_shape is not None and sh.shape_id == title_shape.shape_id
                paras = [p for p in sh.text_frame.paragraphs if "".join(r.text for r in p.runs).strip()]
                if not paras:
                    continue
                if is_title:
                    text = " ".join("".join(r.text for r in p.runs).strip() for p in paras)
                    page.regions.append(make_region(RegionType.TITLE, f"# {text}", "pptx", level=1, bbox=box))
                    continue
                placeholder = getattr(sh, "is_placeholder", False) and sh.is_placeholder
                bullets = placeholder and len(paras) > 1
                lines = []
                for p in paras:
                    text = "".join(r.text for r in p.runs).strip().replace("\x0b", "\n")
                    lines.append("  " * int(p.level or 0) + f"- {text}" if bullets else text)
                rtype = RegionType.LIST_ITEM if bullets else RegionType.TEXT
                page.regions.append(make_region(rtype, "\n".join(lines), "pptx", bbox=box))
            elif str(sh.shape_type).startswith("PICTURE"):
                alt = (getattr(sh, "name", "") or "").strip()
                try:
                    descr = sh._element.xpath(".//p:cNvPr/@descr")
                    alt = (descr[0] if descr and descr[0] else alt).strip()
                except Exception:
                    pass
                page.regions.append(make_region(RegionType.FIGURE, f"[Image: {alt}]" if alt else "[Image]", "pptx",
                                                bbox=box))
        if "comments" in (include or []) and slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
            if notes:
                page.regions.append(make_region(RegionType.COMMENT, notes, "pptx", speaker_notes=True))
        pages.append(page)
    props = {}
    try:
        cp = prs.core_properties
        props = {"title": cp.title, "author": cp.author, "subject": cp.subject, "keywords": cp.keywords,
                 "last_modified_by": cp.last_modified_by, "created_at": iso(cp.created),
                 "modified_at": iso(cp.modified)}
    except Exception:
        pass
    return finish_pages(pages), {k: v for k, v in props.items() if v}, {}
