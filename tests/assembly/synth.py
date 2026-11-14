"""Synthetic multi-page PDFs with known ground truth for the assembly stage.

``make_set(dir)`` writes real PDFs (pikepdf content streams, standard Helvetica) and returns, for
each, the ground truth: which page breaks cut a paragraph, which cut a table, the true heading
levels, and the running header / footer text. ``read_pdf`` then plays the part of a per-page model:
it reads each page on its own from the text layer (pdfplumber) and knows nothing about the other
pages, so a table fragment is just a table and heading levels are only locally meaningful.

The set is small and written by the same person as the rules it tests; treat its numbers as a
regression guard, not as a measure of quality on real documents.
"""

from __future__ import annotations

import html
import random
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name

from readanydoc.model import BBox, Confidence, Document, Line, Page, Region, RegionType, Word

W, H = 612.0, 792.0
LEFT, RIGHT, TOP, BOTTOM = 72.0, 540.0, 720.0, 80.0
BODY, LEADING = 10.0, 14.0
SIZES = {1: 20.0, 2: 15.0, 3: 12.0}
WORDS = ("system market report value policy growth method result sample energy cost region network model "
         "process figure period capital demand supply review control signal volume margin output ratio "
         "factor change effect design survey budget index sector stage theory record").split()
NAMES = "Alder Birch Cedar Dogwood Elm Fir Ginkgo Hazel Ivy Juniper Larch Maple Oak Pine Rowan Spruce".split()


@dataclass
class Truth:
    name: str
    path: Path
    pages: int = 0
    para_joins: set = field(default_factory=set)  # page index i: a paragraph runs from page i to i+1
    table_joins: set = field(default_factory=set)
    headings: list = field(default_factory=list)  # (title, true level)
    header: str = ""
    footer: str = ""
    kinds: dict = field(default_factory=dict)  # page index -> description of the break (for debugging)


class _Canvas:
    def __init__(self, header: str, footer: str):
        self.pages: list[list[str]] = []
        self.header, self.footer = header, footer
        self.y = 0.0
        self.new_page()

    def new_page(self) -> None:
        self.pages.append([])
        n = len(self.pages)
        if self.header:
            self.text(LEFT, 756, self.header, 9)
        if self.footer:
            self.text(LEFT, 40, self.footer, 9)
        self.text(520, 40, f"Page {n}", 9)
        self.y = TOP

    def text(self, x: float, y: float, s: str, size: float, bold: bool = False) -> None:
        s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        self.pages[-1].append(f"BT /{'F2' if bold else 'F1'} {size:g} Tf {x:.2f} {y:.2f} Td ({s}) Tj ET")

    def line(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.pages[-1].append(f"0.5 w {x0:.2f} {y0:.2f} m {x1:.2f} {y1:.2f} l S")

    def room(self) -> float:
        return self.y - BOTTOM

    def save(self, path: Path) -> None:
        pdf = pikepdf.new()
        fonts = Dictionary(
            F1=pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                            Encoding=Name.WinAnsiEncoding)),
            F2=pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/Helvetica-Bold"),
                                            Encoding=Name.WinAnsiEncoding)))
        for ops in self.pages:
            page = pdf.add_blank_page(page_size=(W, H))
            page.Resources = Dictionary(Font=fonts)
            page.Contents = pdf.make_stream("\n".join(ops).encode("latin-1"))
        pdf.save(str(path))


def _sentence(rng: random.Random, n: int | None = None) -> str:
    words = [rng.choice(WORDS) for _ in range(n or rng.randint(8, 16))]
    return " ".join(words).capitalize() + "."


def _wrap(text: str, width: int = 84) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def _table_rows(rng: random.Random, cols: int, rows: int, numeric: bool) -> tuple[list[str], list[list[str]]]:
    header = ["Item"] + [rng.choice(["Units", "Cost", "Share", "Total", "Rate", "Count", "Owner", "Site"]) + f" {k}"
                         for k in range(1, cols)]
    body = []
    for _ in range(rows):
        row = [rng.choice(NAMES)]
        for _k in range(1, cols):
            row.append(f"{rng.randint(10, 9999):,}" if numeric else rng.choice(WORDS).capitalize())
        body.append(row)
    return header, body


class _Writer:
    def __init__(self, truth: Truth, rng: random.Random):
        self.t, self.rng = truth, rng
        self.c = _Canvas(truth.header, truth.footer)

    @property
    def page(self) -> int:
        return len(self.c.pages) - 1

    def gap(self, pts: float = 10) -> None:
        self.c.y -= pts

    def heading(self, title: str, level: int) -> None:
        size = SIZES[level]
        if self.c.room() < size + 3 * LEADING:
            self.c.new_page()
        self.c.y -= size + 4
        self.c.text(LEFT, self.c.y, title, size, bold=True)
        self.c.y -= 8
        self.t.headings.append((title, level))

    def paragraph(self, sentences: int, allow_split: bool = True) -> None:
        lines = _wrap(" ".join(_sentence(self.rng) for _ in range(sentences)))
        if not allow_split and self.c.room() < len(lines) * LEADING:
            self.c.new_page()
        for i, ln in enumerate(lines):
            if self.c.room() < LEADING:
                if i > 0:
                    self.t.para_joins.add(self.page)
                    self.t.kinds[self.page] = "paragraph cut" + (" at a sentence end" if lines[i - 1].endswith(".") else "")
                self.c.new_page()
            self.c.y -= LEADING
            self.c.text(LEFT, self.c.y, ln, BODY)
        self.gap()

    def table(self, cols: int, rows: int, numeric: bool = True, repeat_header: bool = True,
              allow_split: bool = True) -> None:
        header, body = _table_rows(self.rng, cols, rows, numeric)
        rh = 18.0
        width = (RIGHT - LEFT) / cols
        if self.c.room() < 3 * rh or (not allow_split and self.c.room() < (rows + 1) * rh):
            self.c.new_page()

        def draw(cells: list[str], bold: bool) -> None:
            top = self.c.y
            self.c.y -= rh
            for k, text in enumerate(cells):
                self.c.text(LEFT + k * width + 4, self.c.y + 5, text, BODY, bold=bold)
            self.c.line(LEFT, top, RIGHT, top)
            self.c.line(LEFT, self.c.y, RIGHT, self.c.y)
            for k in range(cols + 1):
                self.c.line(LEFT + k * width, top, LEFT + k * width, self.c.y)

        draw(header, True)
        for i, row in enumerate(body):
            if self.c.room() < rh:
                self.t.table_joins.add(self.page)
                self.t.kinds[self.page] = f"table cut, header {'repeated' if repeat_header else 'missing'}, " \
                                          f"{'numeric' if numeric else 'text'} columns"
                self.c.new_page()
                self.c.y -= 6
                if repeat_header:
                    draw(header, True)
            draw(row, False)
        self.gap(14)

    def fill_to(self, remaining: float) -> None:
        """Add short paragraphs until about ``remaining`` points are left on the page."""
        while self.c.room() > remaining + 3 * LEADING:
            lines = int(min((self.c.room() - remaining) // LEADING - 1, 4))
            if lines < 1:
                break
            self.paragraph_lines(lines)

    def paragraph_lines(self, n: int) -> None:
        text = ""
        while len(_wrap(text)) < n:
            text = (text + " " + _sentence(self.rng)).strip()
        lines = _wrap(text)[:n]
        if not lines[-1].endswith("."):
            lines[-1] = lines[-1].rstrip(",") + "."
        for ln in lines:
            self.c.y -= LEADING
            self.c.text(LEFT, self.c.y, ln, BODY)
        self.gap()

    def break_page(self) -> None:
        self.c.new_page()


def make_set(out_dir: Path, seed: int = 20261111) -> list[Truth]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    truths: list[Truth] = []

    def doc(name: str, numbered: bool = True) -> _Writer:
        t = Truth(name=name, path=out_dir / f"{name}.pdf", header=f"{name.title()} Quarterly Review",
                  footer="Confidential draft")
        w = _Writer(t, rng)
        w.heading(f"{name.title()} annual report", 1)
        w._numbered = numbered  # type: ignore[attr-defined]
        w._sec = 0  # type: ignore[attr-defined]
        return w

    def section(w: _Writer, sub: int = 0) -> None:
        w._sec += 1  # type: ignore[attr-defined]
        title = " ".join(rng.choice(WORDS) for _ in range(2)).capitalize()
        w.heading(f"{w._sec} {title}" if w._numbered else title, 2)  # type: ignore[attr-defined]
        w.paragraph(rng.randint(2, 4), allow_split=False)
        for k in range(1, sub + 1):
            title = " ".join(rng.choice(WORDS) for _ in range(2)).capitalize()
            w.heading(f"{w._sec}.{k} {title}" if w._numbered else title, 3)  # type: ignore[attr-defined]
            w.paragraph(rng.randint(2, 3), allow_split=False)

    def finish(w: _Writer) -> None:
        while len(w.c.pages) < 3:  # furniture needs 3 pages
            w.break_page()
            section(w)
        w.t.pages = len(w.c.pages)
        w.c.save(w.t.path)
        truths.append(w.t)

    # A. paragraphs cut by a page break (positive) ------------------------------------------
    for i in range(16):
        w = doc(f"para{i}", numbered=i % 2 == 0)
        section(w, sub=rng.randint(0, 2))
        w.fill_to(rng.choice([40, 55, 70]))
        w.paragraph(rng.randint(10, 14))
        section(w, sub=1)
        finish(w)
    # B. paragraph ends with the page; the next page starts a new paragraph (negative) ---------
    for i in range(6):
        w = doc(f"parabreak{i}", numbered=i % 2 == 1)
        section(w, sub=1)
        w.paragraph(3, allow_split=False)
        w.break_page()
        w.paragraph(3, allow_split=False)
        section(w)
        finish(w)
    # C. tables cut by a page break (positive): header repeated / missing, numeric / text ------
    k = 0
    for repeat in (True, False):
        for numeric in (True, False):
            for cols in (3, 5):
                w = doc(f"table{k}", numbered=k % 2 == 0)
                section(w)
                w.fill_to(rng.choice([120, 160]))
                w.table(cols, rng.randint(14, 22), numeric=numeric, repeat_header=repeat)
                w.paragraph(2, allow_split=False)
                section(w, sub=1)
                finish(w)
                k += 1
    # D. a table ends the page and a different table starts the next one (negative) ------------
    for i in range(6):
        w = doc(f"twotables{i}", numbered=i % 2 == 0)
        section(w)
        cols = rng.choice([3, 4])
        w.table(cols, rng.randint(4, 7), numeric=i % 2 == 0, allow_split=False)
        w.break_page()
        w.table(cols if i < 4 else cols + 1, rng.randint(4, 7), numeric=i % 3 != 0, allow_split=False)
        w.paragraph(2, allow_split=False)
        section(w)
        finish(w)
    # E. table at the page end, plain paragraph on the next page (negative for both) -----------
    for i in range(3):
        w = doc(f"tablethentext{i}")
        section(w)
        w.table(4, 6, allow_split=False)
        w.break_page()
        w.paragraph(4, allow_split=False)
        section(w, sub=2)
        finish(w)
    return truths


# ---------------------------------------------------------------- the per-page "model"

def _lines(words: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if out and abs(out[-1][0]["top"] - w["top"]) < 3:
            out[-1].append(w)
        else:
            out.append([w])
    return out


def _region(rtype: RegionType, content: str, words: list[dict], page: int, level=None) -> Region:
    box = BBox.from_xyxy(min(w["x0"] for w in words), min(w["top"] for w in words), max(w["x1"] for w in words),
                         max(w["bottom"] for w in words), page, W, H)
    r = Region(type=rtype, content=content, text=content, bbox=box, heading_level=level, source="synthetic",
               confidence=Confidence(score=1.0, label="high"))
    r.lines = [Line(words=[Word(text=w["text"], bbox_source="pdf",
                                bbox=BBox.from_xyxy(w["x0"], w["top"], w["x1"], w["bottom"], page, W, H))
                           for w in words])]
    return r


def read_pdf(path: Path, heading_style: str = "flat") -> Document:
    """One Document from the PDF, read page by page with no cross-page knowledge.

    ``heading_style``: ``flat`` writes every section heading as ``##`` (and the largest type on
    page 1 as ``#``); ``per_page`` ranks heading sizes within each page (largest = ``#``), which is
    how a model that sees one page at a time behaves.
    """
    import logging

    import pdfplumber
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    doc = Document(source=str(path), stem=Path(path).stem, file_type="pdf")
    with pdfplumber.open(str(path)) as pdf:
        for pi, pg in enumerate(pdf.pages):
            page = Page(index=pi, original_page=pi + 1, width=W, height=H, kind="digital", text_layer_usable=True)
            words = pg.extract_words(extra_attrs=["size", "fontname"])
            tables = pg.find_tables()
            items: list[tuple[float, Region]] = []
            used: set[int] = set()
            for tb in tables:
                x0, top, x1, bottom = tb.bbox
                inside = [i for i, w in enumerate(words) if x0 - 1 <= w["x0"] and w["x1"] <= x1 + 1
                          and top - 1 <= w["top"] and w["bottom"] <= bottom + 1]
                used.update(inside)
                rows = []
                for row in tb.rows:
                    cells = []
                    bold = True
                    for cb in row.cells:
                        if cb is None:
                            cells.append("")
                            continue
                        cw = [words[i] for i in inside if cb[0] - 1 <= words[i]["x0"] and words[i]["x1"] <= cb[2] + 1
                              and cb[1] - 1 <= words[i]["top"] and words[i]["bottom"] <= cb[3] + 1]
                        bold = bold and all("Bold" in w["fontname"] for w in cw)
                        cells.append(" ".join(w["text"] for w in sorted(cw, key=lambda w: w["x0"])))
                    rows.append((cells, bold))
                body = "".join("<tr>" + "".join(f"<{'th' if b else 'td'}>{html.escape(c)}</{'th' if b else 'td'}>"
                                                for c in cells) + "</tr>" for cells, b in rows)
                items.append((top, _region(RegionType.TABLE, f"<table>{body}</table>", [words[i] for i in inside], pi)))
            rest = [w for i, w in enumerate(words) if i not in used]
            lines = _lines(rest)
            head_sizes = sorted({round(l[0]["size"], 1) for l in lines if l[0]["size"] > BODY + 1}, reverse=True)
            para: list[list[dict]] = []

            def flush() -> None:
                if para:
                    ws = [w for l in para for w in l]
                    text = " ".join(" ".join(w["text"] for w in l) for l in para)
                    items.append((para[0][0]["top"], _region(RegionType.TEXT, text, ws, pi)))
                    para.clear()

            for ln in lines:
                size = round(ln[0]["size"], 1)
                text = " ".join(w["text"] for w in ln)
                if size > BODY + 1:
                    flush()
                    if heading_style == "per_page":
                        level = head_sizes.index(size) + 1
                    else:
                        level = 1 if (pi == 0 and size == max(head_sizes) and size >= SIZES[1]) else 2
                    rtype = RegionType.TITLE if level == 1 else RegionType.SECTION_HEADER
                    items.append((ln[0]["top"], _region(rtype, "#" * level + " " + text, ln, pi, level)))
                elif size < BODY - 0.5:  # header / footer / page number line: its own block, typed Text
                    flush()
                    groups: list[list[dict]] = []
                    for w in ln:
                        if groups and w["x0"] - groups[-1][-1]["x1"] < 40:
                            groups[-1].append(w)
                        else:
                            groups.append([w])
                    for g in groups:
                        items.append((g[0]["top"], _region(RegionType.TEXT, " ".join(w["text"] for w in g), g, pi)))
                else:
                    if para and ln[0]["top"] - para[-1][0]["top"] > LEADING + 4:
                        flush()
                    para.append(ln)
            flush()
            page.regions = [r for _, r in sorted(items, key=lambda t: t[0])]
            for k, r in enumerate(page.regions):
                r.order = k
            doc.pages.append(page)
    return doc


def body_size(doc: Document) -> float:
    hs = [w.bbox.height for r in doc.iter_regions() if r.type == RegionType.TEXT for w in r.iter_words()]
    return statistics.median(hs) if hs else 0.0
