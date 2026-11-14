"""Fixtures for the task-layer tests: synthetic Documents, a cached bench table page, a fillable PDF."""

from __future__ import annotations

import pytest

from readanydoc.model import BBox, Confidence, Document, Line, Page, Region, RegionType, TableCell, Word
from readanydoc.textnorm import tokenize
from tests.conftest import HAVE_SPIKE

TABLE_STEM = "26f221a3e03a0084c486be718476250c9247_pg2_pg1"  # "Exhibitor List" table, digital, grounded


def make_region(content: str, rtype: str = "Text", page: int = 0, order: int = 0, top: float = 0.1,
                grounded: bool = True) -> Region:
    """A region whose words carry char offsets and (fake but distinct) boxes."""
    toks = tokenize(content)
    words = []
    for i, t in enumerate(toks):
        words.append(Word(text=content[t.start:t.end], char_start=t.start, char_end=t.end, source="test",
                          bbox=BBox(left=0.05 + 0.9 * i / max(len(toks), 1), top=top, width=0.8 / max(len(toks), 1),
                                    height=0.02, page=page) if grounded else None))
    region = Region(type=RegionType(rtype), order=order, content=content, text=content,
                    bbox=BBox(left=0.05, top=top, width=0.9, height=0.03, page=page) if grounded else None,
                    lines=[Line(text=content, words=words)] if words else [],
                    confidence=Confidence.of(0.97))
    if rtype == "Table":
        import re
        for i, m in enumerate(re.finditer(r"<t[dh][^>]*>(.*?)</t[dh]>", content, re.S)):
            region.cells.append(TableCell(row=i, col=0, text=m.group(1), char_start=m.start(1), char_end=m.end(1),
                                          bbox=BBox(left=0.1, top=top + 0.001 * i, width=0.2, height=0.001, page=page)))
    return region


def make_doc(pages: list[list], grounded: bool = True) -> Document:
    """``pages`` = list of pages; a page is a list of content strings or (type, content) pairs."""
    doc = Document(source="synthetic", file_type="pdf")
    for pi, blocks in enumerate(pages):
        page = Page(index=pi, original_page=pi + 1, width=612, height=792, kind="digital")
        for bi, b in enumerate(blocks):
            rtype, content = b if isinstance(b, tuple) else ("Text", b)
            page.regions.append(make_region(content, rtype, pi, bi, top=0.05 + 0.05 * bi, grounded=grounded))
        doc.pages.append(page)
    return doc


@pytest.fixture
def invoice_doc() -> Document:
    return make_doc([[
        ("Title", "# Invoice INV-2041"),
        "Billed to: Acme Robotics Ltd, 12 Harbour Road, Leeds",
        "Invoice date: 3 March 2025",
        ("Table", "<table><tr><th>Item</th><th>Qty</th><th>Amount</th></tr>"
                  "<tr><td>Servo motor</td><td>4</td><td>400.00</td></tr>"
                  "<tr><td>Controller board</td><td>2</td><td>1,100.00</td></tr>"
                  "<tr><td>Shipping</td><td>1</td><td>75.00</td></tr></table>"),
        "Total Due: $1,575.00",
        "Status: PAID",
    ]])


@pytest.fixture(scope="session")
def table_doc():
    if not HAVE_SPIKE:
        pytest.skip("cached spike artifacts not available")
    from readanydoc.pipeline import Pipeline
    from readanydoc.profiles import get_profile
    return Pipeline(get_profile("cached-haiku")).run(TABLE_STEM)


def build_form_pdf(path) -> None:
    """A one-page fillable PDF: 2 text fields, 1 multiline text, 1 checkbox, a 2-option radio group, 1 dropdown."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    helv = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    page.obj["/Resources"] = Dictionary(Font=Dictionary(F1=helv))
    labels = [(72, 705, "Full name:"), (72, 665, "Date of birth:"), (72, 625, "Comments:"),
              (100, 545, "I agree to the terms"), (72, 505, "Plan:"), (160, 505, "Basic"), (260, 505, "Premium"),
              (72, 465, "Country:")]
    ops = b"BT /F1 11 Tf 0 g\n"
    px = py = 0
    for x, y, text in labels:
        ops += f"{x - px} {y - py} Td ({text}) Tj\n".encode()
        px, py = x, y
    ops += b"ET"
    page.obj["/Contents"] = pdf.make_stream(ops)

    def xobj(data: bytes, w: float, h: float):
        s = pdf.make_stream(data)
        s["/Type"], s["/Subtype"], s["/BBox"] = Name.XObject, Name.Form, Array([0, 0, w, h])
        return s

    def button_ap(state: str):
        return Dictionary(N=Dictionary({"/" + state: xobj(b"q 0 G 1 w 2 2 m 10 10 l 2 10 m 10 2 l S Q", 12, 12),
                                        "/Off": xobj(b"", 12, 12)}))

    annots, fields = [], []

    def widget(rect, **kw):
        d = Dictionary(Type=Name.Annot, Subtype=Name.Widget, Rect=Array(rect), F=4, P=page.obj, **kw)
        ind = pdf.make_indirect(d)
        annots.append(ind)
        return ind

    da = String("/Helv 0 Tf 0 g")
    fields.append(widget([160, 700, 400, 718], FT=Name.Tx, T=String("full_name"), DA=da))
    fields.append(widget([160, 660, 300, 678], FT=Name.Tx, T=String("dob"), DA=da,
                         TU=String("Date of birth (MM/DD/YYYY)")))
    fields.append(widget([160, 580, 500, 640], FT=Name.Tx, T=String("comments"), DA=da, Ff=1 << 12))
    fields.append(widget([80, 542, 92, 554], FT=Name.Btn, T=String("agree"), V=Name.Off, AS=Name.Off,
                         AP=button_ap("Yes")))
    radio = pdf.make_indirect(Dictionary(FT=Name.Btn, T=String("plan"), Ff=1 << 15, V=Name.Off, Kids=Array([])))
    for x, state in ((145, "Basic"), (245, "Premium")):
        kid = widget([x, 502, x + 12, 514], Parent=radio, AS=Name.Off, AP=button_ap(state))
        radio.Kids.append(kid)
    fields.append(radio)
    fields.append(widget([160, 460, 300, 478], FT=Name.Ch, T=String("country"), Ff=1 << 17, DA=da,
                         Opt=Array([String("India"), String("Germany"), Array([String("US"), String("United States")])])))
    page.obj["/Annots"] = Array(annots)
    pdf.Root["/AcroForm"] = Dictionary(Fields=Array(fields), DA=da,
                                       DR=Dictionary(Font=Dictionary(Helv=helv)))
    pdf.save(str(path))
    pdf.close()


def build_flat_pdf(path) -> None:
    """The same labels with no form fields at all (a flat form)."""
    import pikepdf
    from pikepdf import Dictionary, Name

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    helv = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    page.obj["/Resources"] = Dictionary(Font=Dictionary(F1=helv))
    page.obj["/Contents"] = pdf.make_stream(b"BT /F1 11 Tf 72 705 Td (Full name: ______________) Tj ET")
    pdf.save(str(path))
    pdf.close()


@pytest.fixture
def form_pdf(tmp_path):
    p = tmp_path / "form.pdf"
    build_form_pdf(p)
    return p


@pytest.fixture
def flat_pdf(tmp_path):
    p = tmp_path / "flat.pdf"
    build_flat_pdf(p)
    return p
