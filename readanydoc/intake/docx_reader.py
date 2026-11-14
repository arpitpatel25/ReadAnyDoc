"""DOCX via python-docx: paragraphs, headings, lists, tables, hyperlinks, comments, tracked changes.

No rendering: pages follow the page breaks stored in the file (explicit breaks and Word's
``lastRenderedPageBreak`` marks); a file without any is one page. Block boxes are estimated.

``formatting.include``:
- ``change_tracking``: insertions as ``<u>``, deletions as ``<s>``, each run of changes wrapped in
  ``<change>``. Without it the text reads as if every change were accepted.
- ``hyperlinks``: ``[text](url)``. ``comments``: a Comment block after the paragraph it is anchored to.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from ..model import Page, Region, RegionType
from .common import esc, finish_pages, iso, make_region, new_page
from .errors import IntakeError, damaged

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _q(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _on(el, name: str) -> bool:
    """A toggle property (<w:b/>, <w:b w:val="0"/>) on a run's rPr."""
    rpr = el.find(_q("rPr"))
    if rpr is None:
        return False
    node = rpr.find(_q(name))
    if node is None:
        return False
    return node.get(_q("val"), "true") not in ("0", "false", "none")


class _Ctx:
    def __init__(self, doc, include: list[str]):
        self.doc = doc
        self.track = "change_tracking" in include
        self.links = "hyperlinks" in include
        self.want_comments = "comments" in include
        self.rels = {rid: rel.target_ref for rid, rel in doc.part.rels.items() if rel.is_external}
        self.comments: dict[str, tuple[str, str]] = {}
        self.page_break = False
        self.seen_comments: list[str] = []
        self.counters: dict[tuple[str, int], int] = {}
        self.stats = {"insertions": 0, "deletions": 0, "hyperlinks": 0, "comments": 0}
        if self.want_comments:
            self._load_comments()

    def _load_comments(self) -> None:
        for rel in self.doc.part.rels.values():
            if rel.reltype.endswith("/comments") and not rel.is_external:
                from lxml import etree
                root = etree.fromstring(rel.target_part.blob)
                for c in root.iter(_q("comment")):
                    text = "\n".join("".join(t.text or "" for t in p.iter(_q("t"))) for p in c.iter(_q("p")))
                    self.comments[c.get(_q("id"))] = (c.get(_q("author")) or "", text.strip())


def _run_text(run, ctx: _Ctx, deleted: bool = False) -> str:
    out = []
    for ch in run:
        tag = ch.tag
        if tag == _q("t") or (deleted and tag == _q("delText")):
            out.append(ch.text or "")
        elif tag == _q("tab"):
            out.append("\t")
        elif tag in (_q("br"), _q("cr")):
            if ch.get(_q("type")) == "page":
                ctx.page_break = True
            else:
                out.append("\n")
        elif tag == _q("lastRenderedPageBreak"):
            ctx.page_break = True
        elif tag == _q("noBreakHyphen"):
            out.append("-")
        elif tag == _q("commentReference") and ctx.want_comments:
            ctx.seen_comments.append(ch.get(_q("id")))
    return "".join(out)


def _styled(text: str, run) -> str:
    if not text.strip():
        return text
    lead = text[:len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    core = text.strip()
    if _on(run, "b"):
        core = f"**{core}**"
    if _on(run, "i"):
        core = f"*{core}*"
    if _on(run, "strike") or _on(run, "dstrike"):
        core = f"<s>{core}</s>"
    if _on(run, "u"):
        core = f"<u>{core}</u>"
    return lead + core + trail


def _inline(el, ctx: _Ctx, plain: bool = False) -> list[tuple[str, str]]:
    """Children of a paragraph-like element -> [(kind, text)], kind in text | ins | del."""
    parts: list[tuple[str, str]] = []
    for ch in el:
        tag = ch.tag
        if tag == _q("r"):
            t = _run_text(ch, ctx)
            if t:
                parts.append(("text", t if plain else _styled(t, ch)))
        elif tag == _q("ins"):
            for kind, t in _inline(ch, ctx, plain):
                parts.append(("ins" if kind == "text" else kind, t))
        elif tag == _q("del"):
            t = "".join(_run_text(r, ctx, deleted=True) for r in ch.iter(_q("r")))
            if t:
                parts.append(("del", t))
        elif tag == _q("hyperlink"):
            inner = "".join(t for k, t in _inline(ch, ctx, plain) if k != "del")
            url = ctx.rels.get(ch.get(f"{{{R}}}id") or "")
            if url and ctx.links and inner.strip() and not plain:
                ctx.stats["hyperlinks"] += 1
                parts.append(("text", f"[{inner.strip()}]({url})"))
            elif inner:
                parts.append(("text", inner))
        elif tag in (_q("smartTag"), _q("sdt"), _q("sdtContent"), _q("fldSimple"), _q("moveTo")):
            parts.extend(_inline(ch, ctx, plain))
        elif tag == _q("commentReference") and ctx.want_comments:
            ctx.seen_comments.append(ch.get(_q("id")))
    return parts


def _join(parts: list[tuple[str, str]], ctx: _Ctx) -> str:
    out: list[str] = []
    i = 0
    while i < len(parts):
        kind, text = parts[i]
        if kind == "text":
            out.append(text)
            i += 1
            continue
        group = []
        while i < len(parts) and parts[i][0] in ("ins", "del"):
            group.append(parts[i])
            i += 1
        if ctx.track:
            body = ""
            for k, t in group:
                ctx.stats["insertions" if k == "ins" else "deletions"] += 1
                body += f"<u>{t}</u>" if k == "ins" else f"<s>{t}</s>"
            out.append(f"<change>{body}</change>")
        else:
            out.append("".join(t for k, t in group if k == "ins"))
    text = "".join(out)
    return re.sub(r"\*\*\*\*|(?<=\S)\*\*\*\*(?=\S)", "", text)  # adjacent bold runs: "**a****b**" -> "**ab**"


def _style(p) -> tuple[str, str]:
    ppr = p.find(_q("pPr"))
    if ppr is None:
        return "", ""
    st = ppr.find(_q("pStyle"))
    return (st.get(_q("val")) if st is not None else "") or "", ""


def _num(p) -> Optional[tuple[str, int]]:
    ppr = p.find(_q("pPr"))
    if ppr is None:
        return None
    npr = ppr.find(_q("numPr"))
    if npr is None:
        return None
    nid, lvl = npr.find(_q("numId")), npr.find(_q("ilvl"))
    if nid is None or nid.get(_q("val")) == "0":
        return None
    return nid.get(_q("val")), int(lvl.get(_q("val"))) if lvl is not None else 0


def _table_html(tbl, ctx: _Ctx) -> str:
    rows = [tr for tr in tbl.iter(_q("tr")) if tr.getparent() is tbl or tr.getparent().getparent() is tbl]
    grid: list[list[dict[str, Any]]] = []
    for tr in rows:
        line = []
        trpr = tr.find(_q("trPr"))
        head = trpr is not None and trpr.find(_q("tblHeader")) is not None
        for tc in tr.findall(_q("tc")) + [c for s in tr.findall(_q("sdt")) for c in s.iter(_q("tc"))]:
            tcpr = tc.find(_q("tcPr"))
            span, vmerge = 1, None
            if tcpr is not None:
                gs = tcpr.find(_q("gridSpan"))
                if gs is not None:
                    span = max(int(gs.get(_q("val"), "1") or 1), 1)
                vm = tcpr.find(_q("vMerge"))
                if vm is not None:
                    vmerge = vm.get(_q("val"), "continue")
            paras = []
            for el in tc:
                if el.tag == _q("p"):
                    t = _join(_inline(el, ctx), ctx).strip()
                    if t:
                        paras.append(esc(t) if "<" not in t else t)
                elif el.tag == _q("tbl"):  # nested table: flattened to text
                    paras.append(esc(" ".join("".join(x.text or "" for x in c.iter(_q("t")))
                                              for c in el.iter(_q("tc")))))
            line.append({"text": "<br>".join(paras), "colspan": span, "vmerge": vmerge, "rowspan": 1, "head": head})
        grid.append(line)
    # vertical merges: a "continue" cell extends the cell above it in the same grid column
    above: dict[int, dict[str, Any]] = {}
    for line in grid:
        col = 0
        for cell in line:
            if cell["vmerge"] == "continue" and col in above:
                above[col]["rowspan"] += 1
                cell["skip"] = True
            else:
                above[col] = cell
            col += cell["colspan"]
    look = None
    tblpr = tbl.find(_q("tblPr"))
    if tblpr is not None:
        look = tblpr.find(_q("tblLook"))
    first_header = look is not None and (look.get(_q("firstRow")) == "1"
                                         or (look.get(_q("val")) or "").upper() in ("04A0", "01E0", "00A0", "0020"))
    out = ["<table>"]
    for i, line in enumerate(grid):
        out.append("<tr>")
        for cell in line:
            if cell.get("skip"):
                continue
            tag = "th" if cell["head"] or (i == 0 and first_header and len(grid) > 1) else "td"
            attrs = (f' rowspan="{cell["rowspan"]}"' if cell["rowspan"] > 1 else "") + \
                    (f' colspan="{cell["colspan"]}"' if cell["colspan"] > 1 else "")
            out.append(f"<{tag}{attrs}>{cell['text']}</{tag}>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _heading_level(style_id: str, style_name: str) -> Optional[int]:
    for s in (style_name, style_id):
        m = re.match(r"^heading\s*(\d)$", s.strip(), re.I)
        if m:
            return int(m.group(1))
    return None


def load_docx(path: Path, include: Optional[list[str]] = None) -> tuple[list[Page], dict, dict]:
    import docx
    try:
        d = docx.Document(str(path))
    except Exception as e:
        raise damaged("DOCX", e) from e
    ctx = _Ctx(d, include or [])
    names = {}
    try:
        names = {s.style_id: (s.name or "") for s in d.styles}
    except Exception:
        pass
    has_title = any((names.get(_style(p)[0], "") or "").lower() == "title" for p in d.element.body.iter(_q("p")))
    pages: list[list[Region]] = [[]]

    def emit(region: Region) -> None:
        pages[-1].append(region)

    def flush_comments() -> None:
        for cid in ctx.seen_comments:
            author, text = ctx.comments.get(cid, ("", ""))
            if text:
                ctx.stats["comments"] += 1
                emit(make_region(RegionType.COMMENT, f"{author}: {text}" if author else text, "docx", comment_id=cid))
        ctx.seen_comments = []

    def walk(container) -> None:
        for el in container:
            if el.tag == _q("p"):
                ppr = el.find(_q("pPr"))
                if ppr is not None and ppr.find(_q("pageBreakBefore")) is not None and pages[-1]:
                    pages.append([])
                ctx.page_break = False
                text = _join(_inline(el, ctx), ctx)
                sid = _style(el)[0]
                sname = names.get(sid, sid)
                level = _heading_level(sid, sname)
                body = text.strip()
                if body:
                    if sname.lower() == "title":
                        emit(make_region(RegionType.TITLE, f"# {body}", "docx", level=1, style=sname))
                    elif level:
                        lv = min(level + (1 if has_title else 0), 6)
                        emit(make_region(RegionType.SECTION_HEADER if lv > 1 else RegionType.TITLE,
                                         "#" * lv + " " + body, "docx", level=lv, style=sname))
                    elif _num(el) is not None or sname.lower().startswith("list"):
                        num = _num(el)
                        lvl = num[1] if num else 0
                        ordered = "number" in sname.lower()
                        marker = "-"
                        if ordered:
                            key = (num[0] if num else sname, lvl)
                            ctx.counters[key] = ctx.counters.get(key, 0) + 1
                            marker = f"{ctx.counters[key]}."
                        emit(make_region(RegionType.LIST_ITEM, "  " * lvl + f"{marker} {body}", "docx", style=sname))
                    else:
                        emit(make_region(RegionType.TEXT, body, "docx", style=sname or None))
                flush_comments()
                if ctx.page_break:
                    pages.append([])
                    ctx.page_break = False
            elif el.tag == _q("tbl"):
                emit(make_region(RegionType.TABLE, _table_html(el, ctx), "docx", fmt="html"))
                flush_comments()
                ctx.page_break = False
            elif el.tag == _q("sdt"):
                content = el.find(_q("sdtContent"))
                if content is not None:
                    walk(content)

    walk(d.element.body)
    while len(pages) > 1 and not pages[-1]:
        pages.pop()
    out: list[Page] = []
    for i, regions in enumerate(pages):
        page = new_page(i, i + 1)
        page.regions = regions
        out.append(page)
    try:  # running header / footer of the first section, once per page
        sec = d.sections[0]
        head = "\n".join(p.text.strip() for p in sec.header.paragraphs if p.text.strip())
        foot = "\n".join(p.text.strip() for p in sec.footer.paragraphs if p.text.strip())
        for page in out:
            if head:
                page.regions.insert(0, make_region(RegionType.HEADER, head, "docx"))
            if foot:
                page.regions.append(make_region(RegionType.FOOTER, foot, "docx"))
        w, h = sec.page_width, sec.page_height
        if w and h:
            for page in out:
                page.width, page.height = float(w.pt), float(h.pt)
    except Exception:
        pass
    props = {}
    try:
        cp = d.core_properties
        props = {"title": cp.title, "author": cp.author, "subject": cp.subject, "keywords": cp.keywords,
                 "last_modified_by": cp.last_modified_by, "created_at": iso(cp.created),
                 "modified_at": iso(cp.modified)}
        for rel in d.part.package.rels.values():
            if rel.reltype.endswith("/extended-properties"):
                m = re.search(rb"<Application>([^<]+)</Application>", rel.target_part.blob)
                if m:
                    props["creator"] = m.group(1).decode("utf-8", "replace")
    except Exception:
        pass
    return finish_pages(out), {k: v for k, v in props.items() if v}, {"docx": ctx.stats}
