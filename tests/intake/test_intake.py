"""Digital-native formats, images and PDF extras. Every input file is generated in the test."""

import json

import pytest

from readanydoc.intake import sniff
from readanydoc.intake.errors import CellCountExceeded
from readanydoc.model import RegionType
from readanydoc.pipeline import Pipeline, PipelineConfig
from readanydoc.pipeline.intake import IntakeError
from readanydoc.pipeline.shaping.options import apply_request
from readanydoc.pipeline.shaping.tables import parse_table
from readanydoc.serialize import to_parse_response

from ..conftest import needs_spike


def parse(path, request=None, **stage_opts):
    cfg = apply_request(PipelineConfig(), request or {"formatting": {"table_output_format": "html"}})
    for stage, opts in stage_opts.items():
        cfg = cfg.with_stage(stage, **opts)
    return Pipeline(cfg).run(str(path))


def types(doc, page=0):
    return [r.type.value for r in doc.pages[page].regions]

# ---------------------------------------------------------------- DOCX


@pytest.fixture()
def docx_file(tmp_path):
    import docx
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    d = docx.Document()
    d.core_properties.title = "Quarterly plan"
    d.core_properties.author = "Jo Doe"
    d.add_heading("Quarterly plan", 0)
    d.add_heading("Scope", 1)
    p = d.add_paragraph("Plain and ")
    p.add_run("bold").bold = True
    p.add_run(" text with a ")
    # hyperlink
    rid = d.part.relate_to("https://example.com/x", "http://schemas.openxmlformats.org/officeDocument/2006/"
                           "relationships/hyperlink", is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rid)
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "link"
    run.append(t)
    link.append(run)
    p._p.append(link)
    # tracked change: "old" deleted, "new" inserted
    q = d.add_paragraph("Price is ")
    dele = OxmlElement("w:del")
    dele.set(qn("w:id"), "1")
    dele.set(qn("w:author"), "Ed")
    r1 = OxmlElement("w:r")
    dt = OxmlElement("w:delText")
    dt.text = "10"
    r1.append(dt)
    dele.append(r1)
    ins = OxmlElement("w:ins")
    ins.set(qn("w:id"), "2")
    ins.set(qn("w:author"), "Ed")
    r2 = OxmlElement("w:r")
    it = OxmlElement("w:t")
    it.text = "12"
    r2.append(it)
    ins.append(r2)
    q._p.append(dele)
    q._p.append(ins)
    d.add_paragraph("first", style="List Bullet")
    d.add_paragraph("second", style="List Bullet")
    tb = d.add_table(rows=3, cols=3)
    for r, row in enumerate([["Name", "Q1", "Q2"], ["Merged", "", "5"], ["b", "3", "4"]]):
        for c, v in enumerate(row):
            tb.cell(r, c).text = v
    tb.cell(1, 0).merge(tb.cell(1, 1))
    c = d.add_paragraph("Commented sentence.")
    d.add_comment(c.runs, text="Check this number", author="Rev")
    d.add_page_break()
    d.add_heading("Next", 1)
    d.add_paragraph("Second page text.")
    path = tmp_path / "plan.docx"
    d.save(str(path))
    return path


def test_docx_structure(docx_file):
    doc = parse(docx_file)
    assert doc.file_type == "docx" and doc.meta["native"] and len(doc.pages) == 2
    assert types(doc)[:4] == ["Title", "Section Header", "Text", "Text"]
    r = doc.pages[0].regions
    assert r[0].content == "# Quarterly plan" and r[1].content == "## Scope"
    assert r[2].content == "Plain and **bold** text with a [link](https://example.com/x)"  # links show by default
    assert r[3].content == "Price is 12"  # changes read as accepted
    assert [x.content for x in r if x.type == RegionType.LIST_ITEM] == ["- first", "- second"]
    grid = parse_table(next(x.content for x in r if x.type == RegionType.TABLE))
    assert grid.matrix()[1] == ["Merged", "Merged", "5"] and grid.header_rows == 1
    assert "Comment" not in types(doc)
    assert types(doc, 1) == ["Section Header", "Text"]
    assert all(x.confidence.label == "high" and x.bbox is not None and x.meta["bbox_estimated"]
               for x in doc.iter_regions())
    assert [n.title for n in doc.heading_tree[0].children] == ["Scope", "Next"]
    assert not any(n.level == "error" for n in doc.notes)


def test_docx_include_and_properties(docx_file):
    req = {"formatting": {"include": ["change_tracking", "hyperlinks", "comments"]},
           "settings": {"extract_document_properties": True}}
    doc = parse(docx_file, req)
    r = doc.pages[0].regions
    assert r[2].content.endswith("[link](https://example.com/x)")
    assert r[3].content == "Price is <change><s>10</s><u>12</u></change>"
    comment = [x for x in r if x.type == RegionType.COMMENT]
    assert len(comment) == 1 and comment[0].content == "Rev: Check this number"
    resp = to_parse_response(doc)
    assert resp["document_properties"]["title"] == "Quarterly plan" and resp["document_properties"]["author"] == "Jo Doe"
    assert resp["usage"]["num_pages"] == 2
    blocks = [b for c in resp["result"]["chunks"] for b in c["blocks"]]
    assert {b["bbox"]["page"] for b in blocks} == {1, 2}


def test_docx_page_range(docx_file):
    doc = parse(docx_file, {"settings": {"page_range": {"start": 2}}})
    assert [(p.index, p.original_page) for p in doc.pages] == [(0, 2)] and doc.meta["total_pages"] == 2
    assert all(r.bbox.page == 0 for r in doc.iter_regions())

# ---------------------------------------------------------------- spreadsheets


@pytest.fixture()
def xlsx_file(tmp_path):
    import openpyxl
    from openpyxl.styles import PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws["A1"] = "Sales report"
    rows = [["Region", "Units", "Price"], ["North", 10, 2.5], ["South", 20, 3.0], ["East", 30, 0.125]]
    for r, row in enumerate(rows, start=3):
        for c, v in enumerate(row, start=1):
            ws.cell(r, c, v)
    ws["C7"] = "=SUM(C4:C6)"
    ws["A4"].fill = PatternFill("solid", fgColor="FFFF00")
    ws["F3"], ws["G3"], ws["F4"], ws["G4"] = "Key", "Value", "rate", 0.5
    ws["G4"].number_format = "0%"
    ws.row_dimensions[5].hidden = True
    big = wb.create_sheet("Big")
    big.append(["id", "value"])
    for i in range(120):
        big.append([i, i * 2])
    hidden = wb.create_sheet("Secret")
    hidden["A1"], hidden["B1"] = "k", "v"
    hidden.sheet_state = "hidden"
    path = tmp_path / "book.xlsx"
    wb.save(str(path))
    return path


def test_xlsx_fast_clustering_and_cells(xlsx_file):
    doc = parse(xlsx_file, {"spreadsheet": {"clustering": "fast"}, "formatting": {"table_output_format": "html"}}, intake={"sheet_titles": False})
    assert doc.file_type == "xlsx" and len(doc.pages) == 3 and doc.meta["non_empty_cell_count"] == 262
    assert [p.properties["sheet_name"] for p in doc.pages] == ["Sales", "Big", "Secret"]
    sales = doc.pages[0].regions
    assert [r.type.value for r in sales] == ["Text", "Table", "Table"]
    assert sales[0].content == "Sales report" and sales[0].meta["range"] == "A1:A1"
    main = sales[1]
    grid = parse_table(main.content)
    assert grid.matrix()[0] == ["Region", "Units", "Price"] and grid.header_rows == 1
    assert grid.matrix()[2] == ["South", "20", "3"] and grid.matrix()[3] == ["East", "30", "0.125"]
    assert main.meta["range"] == "A3:C7" and main.meta["sheet"] == "Sales"
    assert main.meta["cell_refs"]["1,0"] == "A4" and main.meta["cell_refs"]["0,2"] == "C3"
    cell = next(c for c in main.cells if (c.row, c.col) == (1, 1))
    assert cell.text == "10" and main.content[cell.char_start:cell.char_end] == "10"
    assert 0 < cell.bbox.left < cell.bbox.left + cell.bbox.width <= 1
    assert parse_table(sales[2].content).matrix() == [["Key", "Value"], ["rate", "50%"]]
    big = doc.pages[1].regions
    assert len(big) == 3 and [parse_table(r.content).n_rows for r in big] == [51, 51, 21]
    assert all(parse_table(r.content).matrix()[0] == ["id", "value"] for r in big)  # header repeated
    assert big[1].meta["range"] == "A52:B101"
    assert any("accurate" in n.message for n in parse(xlsx_file).notes)


def test_xlsx_options(xlsx_file):
    doc = parse(xlsx_file, {"spreadsheet": {"clustering": "disabled", "split_large_tables": {"enabled": False},
                                           "include": ["formula", "cell_colors"],
                                           "exclude": ["hidden_sheets", "hidden_rows"]},
                            "formatting": {"table_output_format": "html"}}, intake={"sheet_titles": False})
    assert [p.properties["sheet_name"] for p in doc.pages] == ["Sales", "Big"]
    assert [p.original_page for p in doc.pages] == [1, 2]
    sales = doc.pages[0].regions
    assert len(sales) == 1 and sales[0].type == RegionType.TABLE and sales[0].meta["range"] == "A1:G7"
    html = sales[0].content
    assert 'data-formula="=SUM(C4:C6)"' in html and 'style="background-color:#FFFF00"' in html
    assert "South" not in html  # hidden row 5 excluded
    assert len(doc.pages[1].regions) == 1 and parse_table(doc.pages[1].regions[0].content).n_rows == 121
    by_name = parse(xlsx_file, {"settings": {"page_range": ["Big"]}}, intake={"sheet_titles": False})
    assert [(p.index, p.original_page) for p in by_name.pages] == [(0, 2)]
    with pytest.raises(IntakeError, match="no sheet named"):
        parse(xlsx_file, {"settings": {"page_range": ["Nope"]}}, intake={"sheet_titles": False})


def test_xlsx_max_cell_count_is_a_422(xlsx_file):
    with pytest.raises(CellCountExceeded) as e:
        parse(xlsx_file, {"spreadsheet": {"max_cell_count": 100}})
    from readanydoc.api.errors import from_exception
    api = from_exception(e.value)
    assert api.status == 422 and api.name == "CELL_COUNT_EXCEEDED" and "max_cell_count=100" in api.message
    assert parse(xlsx_file, {"spreadsheet": {"max_cell_count": 262}}).pages


def test_xlsx_merged_cells_and_column_split(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Group", None, "Other", "D", "E"])
    ws.append(["a", "b", "c", "d", "e"])
    ws.merge_cells("A1:B1")
    path = tmp_path / "m.xlsx"
    wb.save(str(path))
    doc = parse(path, {"spreadsheet": {"clustering": "fast"}, "formatting": {"table_output_format": "html"}}, intake={"sheet_titles": False})
    assert '<th colspan="2">Group</th>' in doc.pages[0].regions[0].content
    doc = parse(path, {"spreadsheet": {"clustering": "fast", "split_large_tables": {"size": {"row": None, "column": 3}}},
                       "formatting": {"table_output_format": "html"}}, intake={"sheet_titles": False})
    assert [parse_table(r.content).n_cols for r in doc.pages[0].regions] == [3, 2]


def test_csv_and_txt(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("name;amount\nÄpfel;1,5\n\"semi;colon\";2\n", encoding="utf-8")
    doc = parse(p)
    assert doc.file_type == "csv" and doc.meta["delimiter"] == ";"
    assert parse_table(doc.pages[0].regions[0].content).matrix() == [["name", "amount"], ["Äpfel", "1,5"],
                                                                       ["semi;colon", "2"]]
    t = tmp_path / "n.txt"
    t.write_bytes("First paragraph\nstill first.\n\nSecond café.\fPage two.".encode("cp1252"))
    doc = parse(t)
    assert doc.file_type == "txt" and len(doc.pages) == 2 and doc.meta["encoding"] == "cp1252"
    assert [r.content for r in doc.pages[0].regions] == ["First paragraph\nstill first.", "Second café."]
    resp = to_parse_response(doc)
    assert resp["result"]["chunks"][0]["content"].endswith("Page two.")

# ---------------------------------------------------------------- PPTX, EML


def test_pptx(tmp_path):
    import pptx
    from pptx.util import Inches
    prs = pptx.Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Roadmap"
    s.placeholders[1].text = "First point\nSecond point"
    tb = s.shapes.add_table(2, 2, Inches(1), Inches(5), Inches(4), Inches(1)).table
    for (r, c), v in {(0, 0): "K", (0, 1): "V", (1, 0): "a", (1, 1): "1"}.items():
        tb.cell(r, c).text = v
    s.notes_slide.notes_text_frame.text = "Say hello"
    prs.slides.add_slide(prs.slide_layouts[5]).shapes.title.text = "Second"
    path = tmp_path / "deck.pptx"
    prs.save(str(path))
    doc = parse(path)
    assert doc.file_type == "pptx" and len(doc.pages) == 2
    assert types(doc) == ["Title", "List Item", "Table"]
    assert doc.pages[0].regions[1].content == "- First point\n- Second point"
    assert parse_table(doc.pages[0].regions[2].content).matrix() == [["K", "V"], ["a", "1"]]
    box = doc.pages[0].regions[2].bbox
    assert abs(box.left - 0.1) < 0.01 and "bbox_estimated" not in doc.pages[0].regions[2].meta  # real geometry
    assert doc.pages[1].regions[0].content == "# Second"
    notes = parse(path, {"formatting": {"include": ["comments"]}})
    assert notes.pages[0].regions[-1].type == RegionType.COMMENT and notes.pages[0].regions[-1].content == "Say hello"


def test_eml(tmp_path):
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Ann <ann@example.com>", "bob@example.com", "Offer letter"
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0100"
    m.set_content("Hello Bob,\n\nPlease find the offer attached.\n")
    m.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="offer.pdf")
    path = tmp_path / "mail.eml"
    path.write_bytes(m.as_bytes())
    doc = parse(path, {"settings": {"extract_document_properties": True}})
    assert doc.file_type == "eml" and types(doc) == ["Title", "Key Value", "Text", "Text", "Text"]
    r = doc.pages[0].regions
    assert r[0].content == "# Offer letter" and "**From:** Ann <ann@example.com>" in r[1].content
    assert r[-1].content == "Attachments: offer.pdf" and doc.meta["attachments"] == ["offer.pdf"]
    assert to_parse_response(doc)["document_properties"]["created_at"] == "2024-01-01T10:00:00+01:00"
    html = tmp_path / "h.eml"
    html.write_text("From: a@b.c\nSubject: H\nContent-Type: text/html\n\n<p>One</p><p>Two <b>bold</b></p>")
    assert [x.content for x in parse(html).pages[0].regions][-2:] == ["One", "Two bold"]

# ---------------------------------------------------------------- images, legacy, sniffing


@needs_spike
def test_images(tmp_path):
    from PIL import Image
    frames = [Image.new("RGB", (200 + 10 * i, 100), "white") for i in range(3)]
    tif = tmp_path / "scan.tiff"
    frames[0].save(tif, save_all=True, append_images=frames[1:])
    doc = parse(tif, {"settings": {"page_range": [1, 3]}})
    assert doc.file_type == "tiff" and [(p.original_page, p.width) for p in doc.pages] == [(1, 200.0), (3, 220.0)]
    assert doc.meta["total_pages"] == 3 and all(p.unit == "px" and p.renders for p in doc.pages)
    for ext, fmt in (("png", "PNG"), ("jpg", "JPEG"), ("gif", "GIF"), ("bmp", "BMP")):
        p = tmp_path / f"wrong_name_{ext}.dat"  # type comes from the bytes, not the name
        frames[0].save(p, format=fmt)
        assert sniff(p) == ext and len(parse(p).pages) == 1


def test_unsupported_formats_give_a_415_style_error(tmp_path, monkeypatch):
    from readanydoc.api.errors import from_exception
    monkeypatch.setenv("READANYDOC_SOFFICE", str(tmp_path / "no-such-soffice"))  # no converter, no decoder
    monkeypatch.setenv("READANYDOC_NO_TEXTUTIL", "1")
    monkeypatch.setenv("READANYDOC_HEIC_DECODER", "none")
    cases = {"old.doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64, "old.xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
             "old.rtf": b"{\\rtf1 hello}", "pic.heic": b"\0\0\0\x18ftypheic" + b"\0" * 32, "thing.xyz": b"\x01\x02\x03"}
    for name, data in cases.items():
        p = tmp_path / name
        p.write_bytes(data)
        with pytest.raises(IntakeError) as e:
            parse(p)
        api = from_exception(e.value)
        assert (api.status, api.name) == (415, "DOCUMENT_UNSUPPORTED"), name
        assert "not supported" in api.message and name.rsplit(".", 1)[1] in api.message
    for name, data in (("broken.docx", b"PK\x03\x04 not a real zip"), ("mail.msg", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")):
        bad = tmp_path / name
        bad.write_bytes(data)
        with pytest.raises(IntakeError) as e:
            parse(bad)
        assert from_exception(e.value).name == "DOCUMENT_CORRUPT", name


def _fake_tool(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def test_legacy_formats_go_through_the_converter(tmp_path, monkeypatch, docx_file, xlsx_file):
    """LibreOffice is not needed for the test: a stand-in with soffice's command line copies a prepared file."""
    from readanydoc.intake import legacy
    log = tmp_path / "calls.log"
    fake = _fake_tool(tmp_path / "soffice", f"""
echo "$@" >> {log}
while [ $# -gt 0 ]; do
  case "$1" in --convert-to) fmt="$2"; shift;; --outdir) out="$2"; shift;; esac
  src="$1"; shift
done
name=$(basename "$src"); name="${{name%.*}}"
case "$fmt" in docx) cp {docx_file} "$out/$name.docx";; xlsx) cp {xlsx_file} "$out/$name.xlsx";; *) exit 3;; esac
""")
    monkeypatch.setenv("READANYDOC_SOFFICE", str(fake))
    assert legacy.converter_for("doc") == ("soffice", str(fake)) and legacy.converter_for("pdf") is None
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64
    for name, data, kind in (("memo.doc", ole, "docx"), ("memo.rtf", b"{\\rtf1 hi}", "docx"), ("book.xls", ole, "xlsx"),
                             ("note.odt", None, "docx")):
        p = tmp_path / name
        if data is None:  # an OpenDocument text file is a zip with a mimetype entry
            import zipfile
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
                z.writestr("content.xml", "<x/>")
        else:
            p.write_bytes(data)
        doc = parse(p)
        assert doc.file_type == kind and doc.meta["converted_from"] == str(p), name
        assert sum(len(pg.regions) for pg in doc.pages) > 0, name
    calls = log.read_text()
    assert calls.count("--headless") == 4 and "--convert-to docx" in calls and "--convert-to xlsx" in calls
    assert "UserInstallation" in calls  # private profile: a running LibreOffice must not block the conversion
    _fake_tool(tmp_path / "soffice", "exit 1\n")  # a converter that fails is a damaged-file error, not a crash
    with pytest.raises(IntakeError) as e:
        parse(tmp_path / "memo.doc")
    assert "conversion" in str(e.value)
    ppt = tmp_path / "deck.ppt"
    ppt.write_bytes(ole)
    monkeypatch.setenv("READANYDOC_SOFFICE", str(tmp_path / "gone"))
    with pytest.raises(IntakeError) as e:  # textutil (macOS) never takes presentations or spreadsheets
        parse(ppt)
    assert "LibreOffice" in str(e.value)
    # round 2: the message says exactly what to install and what is wrong
    msg = str(e.value)
    assert "File type 'ppt' is not supported" in msg and ".ppt files are converted to PPTX" in msg
    assert f"READANYDOC_SOFFICE points to {str(tmp_path / 'gone')!r}, which does not exist" in msg
    assert "set READANYDOC_SOFFICE to the full path of soffice" in msg and "convert the file to PPTX or PDF" in msg


@pytest.mark.parametrize("platform, command", [("darwin", "brew install --cask libreoffice"),
                                               ("linux", "apt-get install -y libreoffice-core"),
                                               ("win32", "winget install TheDocumentFoundation.LibreOffice")])
def test_missing_libreoffice_message_names_the_install_command(monkeypatch, platform, command):
    import sys

    from readanydoc.intake import legacy
    monkeypatch.delenv("READANYDOC_SOFFICE", raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    why, hint = legacy.install_advice("xls")
    assert ".xls files are converted to XLSX with LibreOffice (the 'soffice' program), which was not found" in why
    assert command in hint and ("textutil" in why) == (platform == "darwin")
    assert "textutil" not in legacy.install_advice("doc")[0]


def test_legacy_file_without_any_converter_is_a_415_with_the_advice(tmp_path, monkeypatch):
    from readanydoc.intake import legacy
    monkeypatch.setattr(legacy, "find_soffice", lambda: None)
    monkeypatch.setattr(legacy, "find_textutil", lambda: None)
    for kind in ("doc", "xls", "ppt", "rtf", "odt"):
        with pytest.raises(IntakeError) as e:
            legacy.convert(tmp_path / f"a.{kind}", kind)
        assert "is not supported" in str(e.value) and "Install LibreOffice on the server:" in str(e.value)


def test_textutil_reads_rtf_when_libreoffice_is_absent(tmp_path, monkeypatch):
    import shutil
    if not shutil.which("textutil"):
        pytest.skip("textutil is a macOS tool")
    monkeypatch.setenv("READANYDOC_SOFFICE", str(tmp_path / "gone"))
    p = tmp_path / "letter.rtf"
    p.write_text(r"{\rtf1\ansi{\fonttbl{\f0 Helvetica;}}\f0 Quarterly report\par\par Revenue rose to $18.4 million.\par}")
    doc = parse(p)
    text = " ".join(r.content for pg in doc.pages for r in pg.regions)
    assert doc.file_type == "docx" and "Quarterly report" in text and "$18.4 million" in text
    junk = tmp_path / "junk.doc"
    junk.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    with pytest.raises(IntakeError):  # not a Word file inside: refused instead of converted to nonsense
        parse(junk)


@needs_spike
def test_heic_goes_through_a_decoder(tmp_path, monkeypatch):
    from PIL import Image
    png = tmp_path / "page.png"
    Image.new("RGB", (120, 80), "white").save(png)
    fake = _fake_tool(tmp_path / "heif-convert", f'cp {png} "$2"\n')
    monkeypatch.setenv("READANYDOC_HEIC_DECODER", str(fake))
    heic = tmp_path / "photo.heic"
    heic.write_bytes(b"\0\0\0\x18ftypheic" + b"\0" * 32)
    doc = parse(heic)
    assert doc.file_type == "png" and len(doc.pages) == 1 and (doc.pages[0].width, doc.pages[0].height) == (120.0, 80.0)
    assert doc.meta["converted_from"] == str(heic)
    _fake_tool(tmp_path / "heif-convert", "exit 2\n")
    with pytest.raises(IntakeError) as e:
        parse(heic)
    assert "damaged" in str(e.value)


class _FakeOle:
    """Just enough of olefile.OleFileIO for the MSG reader."""

    def __init__(self, streams, storages=()):
        self.streams, self.storages = streams, storages

    def exists(self, name):
        return name in self.streams

    def openstream(self, name):
        import io
        return io.BytesIO(self.streams[name])

    def listdir(self, streams=True, storages=False):
        return [[s] for s in self.storages]

    def close(self):
        pass


def test_msg_reader(tmp_path, monkeypatch):
    import sys
    import types
    u = lambda s: s.encode("utf-16-le")  # noqa: E731
    ticks = (int((__import__("datetime").datetime(2025, 3, 17, 9, 30) - __import__("datetime").datetime(1601, 1, 1))
                 .total_seconds()) * 10_000_000)
    props = b"\0" * 32 + (0x00390040).to_bytes(4, "little") + b"\0" * 4 + ticks.to_bytes(8, "little")
    streams = {"__substg1.0_0037001F": u("Annual report"), "__substg1.0_1000001F": u("Dear members,\r\n\r\nRevenue rose.\r\n"),
               "__substg1.0_0C1A001F": u("Dana Whitlock"), "__substg1.0_5D01001F": u("dana@example.org"),
               "__substg1.0_0E04001F": u("members@example.org"), "__properties_version1.0": props,
               "__attach_version1.0_#00000000/__substg1.0_3707001F": u("shipments.csv")}
    fake = types.SimpleNamespace(OleFileIO=lambda path: _FakeOle(streams, ["__attach_version1.0_#00000000"]),
                                 isOleFile=lambda path: True)
    monkeypatch.setitem(sys.modules, "olefile", fake)
    p = tmp_path / "mail.msg"
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    doc = parse(p)
    blocks = [(r.type.value, r.content) for pg in doc.pages for r in pg.regions]
    assert blocks[0] == ("Title", "# Annual report")
    assert blocks[1] == ("Key Value", "**From:** Dana Whitlock <dana@example.org>\n**To:** members@example.org\n"
                                      "**Date:** 2025-03-17 09:30:00+0000")
    assert [c for _, c in blocks[2:]] == ["Dear members,", "Revenue rose.", "Attachments: shipments.csv"]
    assert doc.file_type == "msg" and doc.properties["title"] == "Annual report"


def test_page_range_outside_the_document_is_a_400(tmp_path):
    from readanydoc.api.errors import from_exception
    from PIL import Image
    img = tmp_path / "one.png"
    Image.new("RGB", (100, 100), "white").save(img)
    with pytest.raises(IntakeError) as e:
        parse(img, {"settings": {"page_range": {"start": 5, "end": 6}}})
    api = from_exception(e.value)
    assert (api.status, api.name) == (400, "INVALID_CONFIG") and "No pages found in the requested range" in api.message


def test_sniff(tmp_path, docx_file, xlsx_file):
    assert sniff(docx_file) == "docx" and sniff(xlsx_file) == "xlsx"
    renamed = tmp_path / "renamed.bin"
    renamed.write_bytes(docx_file.read_bytes())
    assert sniff(renamed) == "docx"  # zip container content decides, not the extension
    for name, want in (("a.xlsm", "xlsx"), ("a.tsv", "csv"), ("a.rtf", "rtf"), ("a.md", "txt")):
        p = tmp_path / name
        p.write_text("x\ty\n")
        assert sniff(p) == want

# ---------------------------------------------------------------- PDF extras


@needs_spike
def test_pdf_properties_links_and_comments(tmp_path):
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    from readanydoc.intake.pdf import pdf_date, read_annotations
    from readanydoc.model import BBox, Line, Region, Word
    from readanydoc.pipeline import PipelineContext
    from readanydoc.pipeline.assemble import Assemble
    from readanydoc.pipeline.shape import Shape
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(600, 800))
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    page.Resources = Dictionary(Font=Dictionary(F1=font))
    page.Contents = pdf.make_stream(b"BT /F1 12 Tf 60 700 Td (See our website for details) Tj ET")
    page.Annots = Array([
        pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array([90, 695, 140, 712]),
                                     A=Dictionary(S=Name.URI, URI=String("https://example.com")))),
        pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Text, Rect=Array([300, 600, 320, 620]),
                                     Contents=String("Please verify"), T=String("Ann")))])
    pdf.docinfo["/Title"] = "Spec sheet"
    pdf.docinfo["/Author"] = "QA"
    pdf.docinfo["/CreationDate"] = "D:20240131120000+01'00'"
    path = tmp_path / "annotated.pdf"
    pdf.save(str(path))
    assert pdf_date("D:20240131120000+01'00'") == "2024-01-31T12:00:00+01:00"
    assert pdf_date("D:20240131") == "2024-01-31T00:00:00+00:00" and pdf_date("garbage") is None
    ann = read_annotations(path, [1])[1]
    assert ann["links"][0]["url"] == "https://example.com" and abs(ann["links"][0]["bbox"]["left"] - 0.15) < 1e-6
    req = {"formatting": {"include": ["hyperlinks", "comments"]}, "settings": {"extract_document_properties": True}}
    doc = parse(path, req)
    assert doc.properties["title"] == "Spec sheet" and doc.properties["created_at"] == "2024-01-31T12:00:00+01:00"
    assert to_parse_response(doc)["document_properties"]["author"] == "QA"
    assert doc.pages[0].properties["annotations"]["comments"][0]["text"] == "Please verify"
    comments = [r for r in doc.pages[0].regions if r.type == RegionType.COMMENT]
    assert len(comments) == 1 and comments[0].content == "Ann: Please verify" and comments[0].bbox is not None
    assert "annotations" not in parse(path).pages[0].properties  # only read when asked for
    # link attachment: a grounded text block whose words sit under the link rectangle
    text = "See our website for details"
    region = Region(type=RegionType.TEXT, content=text)
    words, pos = [], 0
    for i, w in enumerate(text.split()):
        start = text.index(w, pos)
        pos = start + len(w)
        words.append(Word(text=w, char_start=start, char_end=pos, bbox_source="pdf",
                          bbox=BBox(left=0.10 + 0.045 * i, top=0.115, width=0.04, height=0.015)))
    region.lines = [Line(words=words)]
    doc.pages[0].regions = [region]
    cfg = apply_request(PipelineConfig(), req)
    ctx = PipelineContext(cfg)
    doc = Assemble(enabled=True, **cfg.stage("assemble").options).run(doc, ctx)
    doc = Shape(enabled=True, **cfg.stage("shape").options).run(doc, ctx)
    assert "[our website](https://example.com)" in doc.chunks[0].content
    assert region.content == text and region.meta["shaped_content"].startswith("See [our website]")


def test_native_documents_need_no_model_and_no_cached_data(docx_file, monkeypatch):
    """Parked pages are invisible to triage..ground, and the reader is not even built."""
    monkeypatch.setenv("READANYDOC_SPIKE_DIR", "/nonexistent-readanydoc-spike")
    doc = parse(docx_file)
    assert doc.pages and doc.chunks and json.loads(doc.to_json())["meta"].get("_native_pages") is None
    assert not doc.usage and all(r.source == "docx" for r in doc.iter_regions())
    off = parse(docx_file, assemble={"enabled": False})
    assert off.pages and off.chunks  # Shape restores the pages when Assemble is off


def test_xlsx_sheets_start_with_their_name(xlsx_file):
    doc = parse(xlsx_file, {"spreadsheet": {"clustering": "fast"}})
    first = [p.regions[0] for p in doc.pages]
    assert [(r.type.value, r.content) for r in first] == [("Title", "# Sales"), ("Title", "# Big"), ("Title", "# Secret")]
    assert all(r.bbox.width == 0 and r.meta["sheet_title"] for r in first)  # no place on the sheet, as on Reducto


def test_bmp_gets_a_png_render_for_the_reader(tmp_path):
    from PIL import Image
    bmp = tmp_path / "scan.bmp"
    Image.new("RGB", (64, 48), "white").save(bmp)
    doc = parse(bmp, read={"enabled": False}, triage={"enabled": False}, ground={"enabled": False})
    render = next(iter(doc.pages[0].renders.values()))
    assert doc.file_type == "bmp" and render.endswith(".png") and Image.open(render).size == (64, 48)


def test_encrypted_pdf_gets_a_decrypted_working_copy(tmp_path):
    import pikepdf
    import pypdfium2 as pdfium
    from PIL import Image
    plain = tmp_path / "plain.pdf"
    Image.new("RGB", (200, 260), "white").save(plain)
    locked = tmp_path / "locked.pdf"
    with pikepdf.open(plain) as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(user="secret", owner="owner"))
    off = {"enabled": False}
    with pytest.raises(IntakeError) as e:
        parse(locked, read=off, triage=off, ground=off)
    assert "password" in str(e.value)
    doc = parse(locked, {"settings": {"document_password": "secret"}}, read=off, triage=off, ground=off)
    assert len(doc.pages) == 1 and doc.meta["path"] != str(locked)
    pdfium.PdfDocument(doc.meta["path"]).close()  # opens without a password
    assert "secret" not in doc.to_json()  # the password is not stored with the document
