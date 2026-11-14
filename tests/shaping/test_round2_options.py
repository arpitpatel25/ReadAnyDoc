"""Round 2 options: table / text prompts, searchable PDF (embed_pdf_metadata), settings.model label."""

from __future__ import annotations

import pdfplumber
import pikepdf
import pytest
from PIL import Image, ImageDraw

from readanydoc.model import BBox, Document, Line, Page, Region, RegionType, Word
from readanydoc.pipeline.shaping import agentic, response, searchable
from readanydoc.tasks.llm import FakeLLM
from tests.tasks.conftest import make_doc, make_region

TABLE = ('<table><tr><th>Item</th><th colspan="2">Amount</th></tr>'
         "<tr><td>Fees &amp; charges</td><td>$1,200</td><td>$ 30</td></tr>"
         "<tr><td>Total</td><td><b>$1,230</b></td><td></td></tr></table>")
REQ_TABLE = {"enhance": {"agentic": [{"scope": "table", "mode": "max", "prompt": "No dollar signs."}]}}
REQ_TEXT = {"enhance": {"agentic": [{"scope": "text", "prompt": "Dates as YYYY-MM-DD."}]}}


def test_wants_reads_only_table_and_text_prompts():
    req = {"enhance": {"agentic": [{"scope": "figure", "prompt": "x"}, {"scope": "table", "prompt": " "},
                                   {"scope": "text", "prompt": "Dates as ISO"}]}}
    assert agentic.wants(req) == {"table": None, "text": "Dates as ISO"}
    assert agentic.wants({}) == {"table": None, "text": None} == agentic.wants(None)


def test_table_prompt_changes_cells_in_place_and_keeps_the_grid():
    doc = make_doc([[("Table", TABLE), "Paid on 3/4/2024"]])
    region = doc.pages[0].regions[0]
    llm = FakeLLM({"changes": [{"cell": 3, "text": "1,200"}, {"cell": 4, "text": "30"}, {"cell": 6, "text": "1,230"},
                               {"cell": 2, "text": "Fees & charges"}, {"cell": 99, "text": "x"}]})
    stats = agentic.process(doc, REQ_TABLE, llm=llm)
    assert stats["tables"] == 1 and stats["cells_changed"] == 3 and stats["calls"] == 1
    assert region.content == TABLE.replace("$1,200", "1,200").replace("$ 30", "30").replace("<b>$1,230</b>", "1,230")
    assert 'colspan="2"' in region.content and "Fees &amp; charges" in region.content   # untouched cells byte for byte
    user = llm.calls[0]["user"]
    assert "Instruction: No dollar signs." in user and "c3 | r2 c2 | $1,200" in user and llm.calls[0]["images"] == []
    assert [c.text for c in region.cells][3:5] == ["1,200", "30"]                      # citations stay in step
    assert region.content[region.cells[3].char_start:region.cells[3].char_end] == "1,200"
    assert doc.pages[0].regions[1].content == "Paid on 3/4/2024"                       # text scope not asked
    assert doc.usage[-1]["task"] == "agentic_table" and doc.meta["prompted_blocks"] == 0


def test_table_prompt_bad_answers_leave_the_table_as_read():
    for reply in ("not json", {"table": "<p>no table</p>"}, {"changes": []}, RuntimeError("down")):
        doc = make_doc([[("Table", TABLE)]])
        stats = agentic.process(doc, REQ_TABLE, llm=FakeLLM([reply]))
        assert doc.pages[0].regions[0].content == TABLE and stats["cells_changed"] == 0


def test_table_prompt_may_replace_the_table_when_the_structure_must_change():
    doc = make_doc([[("Table", TABLE)]])
    new = "<table><tr><th>Item</th><th>Amount</th></tr><tr><td>Total</td><td>1,230</td></tr></table>"
    agentic.process(doc, REQ_TABLE, llm=FakeLLM({"table": new}))
    assert doc.pages[0].regions[0].content == new and doc.pages[0].regions[0].cells == []


def test_text_prompt_is_one_call_per_page_and_rewrites_are_dropped():
    long = "Signed on March 5, 2024 in Pune. " + "The parties agree to the terms set out in the schedule below. " * 4
    doc = make_doc([[("Key Value", "Date of birth: 3/4/1990"), long, ("Table", TABLE), ("Signature", "J. Doe"),
                     ("Footer", "for July 31, 2019 to October 24, 2019"), "Short line."],
                    ["Nothing to change here."]])
    def handler(system, user, i):
        if i == 0:
            return {"changes": [{"block": 0, "text": "Date of birth: 1990-03-04"},
                                {"block": 1, "text": "A completely different paragraph about something else. " * 5},
                                {"block": 2, "text": "for 2019-07-31 to 2019-10-24"},
                                {"block": 3, "text": "Short line, now with a long invented continuation added to it."}]}
        return {"changes": []}
    llm = FakeLLM(handler)
    stats = agentic.process(doc, REQ_TEXT, llm=llm)
    kv, text, table, sig, footer, short = doc.pages[0].regions
    assert kv.content == "Date of birth: 1990-03-04" and kv.meta["agentic_original"] == "Date of birth: 3/4/1990"
    assert footer.content == "for 2019-07-31 to 2019-10-24"        # a short line may change in most characters
    assert text.content == long and table.content == TABLE          # a rewrite of a long block is dropped
    assert short.content == "Short line."                           # and so is one that grows a short block 5x
    assert stats["text_blocks_changed"] == 2 and stats["calls"] == 2 and len(stats["notes"]) == 2
    assert "[b0] Key Value" in llm.calls[0]["user"] and "J. Doe" not in llm.calls[0]["user"]   # signatures, tables not sent
    assert "<table" not in llm.calls[0]["user"] and doc.meta["prompted_blocks"] == 2


def test_no_prompt_or_no_live_model_means_no_call(monkeypatch):
    doc = make_doc([[("Table", TABLE)]])
    llm = FakeLLM()
    assert agentic.process(doc, {"enhance": {"agentic": [{"scope": "table"}]}}, llm=llm)["calls"] == 0 and not llm.calls
    monkeypatch.delenv("READANYDOC_TASK_LLM", raising=False)
    monkeypatch.delenv("READANYDOC_EMBED_LLM", raising=False)
    stats = agentic.process(doc, REQ_TABLE, reader_spec="cached:haiku")
    assert stats["calls"] == 0 and "live model" in stats["notes"][0] and doc.pages[0].regions[0].content == TABLE


# ---------------------------------------------------------------- searchable PDF
WORDS = [("INVOICE", 0.10, 0.10, 0.30, 0.05), ("Total", 0.10, 0.50, 0.12, 0.03), ("1,234.50", 0.60, 0.50, 0.20, 0.03),
         ("Zoë", 0.10, 0.70, 0.08, 0.03), ("नमस्ते", 0.30, 0.70, 0.15, 0.03)]


def _scan_doc(path, file_type="pdf", n_pages=1):
    doc = Document(source=str(path), file_type=file_type, meta={"path": str(path)})
    for i in range(n_pages):
        page = Page(index=i, original_page=i + 1, width=612, height=792, kind="scanned", text_layer_usable=False)
        words = [Word(text=t, bbox=BBox(left=l, top=tp, width=w, height=h, page=i), char_start=0, char_end=len(t))
                 for t, l, tp, w, h in WORDS]
        page.regions.append(Region(type=RegionType.TEXT, content=" ".join(w.text for w in words),
                                   bbox=BBox(left=0.05, top=0.05, width=0.9, height=0.75, page=i),
                                   lines=[Line(text="x", words=words)]))
        page.words["tesseract"] = [Word(text="Footer", bbox=BBox(left=0.4, top=0.9, width=0.1, height=0.02, page=i)),
                                   Word(text="lnvoice", bbox=BBox(left=0.1, top=0.1, width=0.3, height=0.05, page=i))]
        doc.pages.append(page)
    return doc


def _image_only_pdf(path, rotate=0, pages=1):
    im = Image.new("RGB", (612, 792), "white")
    ImageDraw.Draw(im).rectangle((60, 80, 240, 120), fill="black")
    im.save(path, "PDF", resolution=72, save_all=True, append_images=[im] * (pages - 1))
    if rotate:
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            pdf.pages[0].Rotate = rotate
            pdf.save(path)
    return path


def _read_words(path, page=0):
    with pdfplumber.open(path) as pdf:
        p = pdf.pages[page]
        return {w["text"]: (w["x0"] / p.width, w["top"] / p.height, w["x1"] / p.width, w["bottom"] / p.height)
                for w in p.extract_words()}, (p.extract_text() or "")


def test_searchable_pdf_has_an_invisible_layer_with_our_words_at_their_boxes(tmp_path):
    src = _image_only_pdf(tmp_path / "scan.pdf")
    assert _read_words(src)[1] == ""
    stats = searchable.build(_scan_doc(src), tmp_path / "out" / "searchable.pdf")
    assert stats["pages_with_layer"] == 1 and stats["words"] == 6 and stats["path"]   # 5 grounded + "Footer"
    got, text = _read_words(stats["path"])
    for t, left, top, w, h in WORDS:
        x0, y0, x1, y1 = got[t]
        assert abs(x0 - left) < 0.01 and abs(x1 - (left + w)) < 0.02, (t, got[t])       # same place, same width
        assert abs((y0 + y1) / 2 - (top + h / 2)) < 0.02, (t, got[t])
    assert "Zoë" in text and "नमस्ते" in text                 # any script, not only Latin-1
    assert "Footer" in got and "lnvoice" not in got           # OCR words only outside the blocks we grounded
    with pikepdf.open(stats["path"]) as pdf:
        streams = b"".join(c.read_bytes() for c in pdf.pages[0].Contents)
        assert b"3 Tr" in streams                              # invisible: the page looks the same
    import pypdfium2 as pdfium
    a, b = (pdfium.PdfDocument(str(p))[0].render(scale=0.5).to_pil().tobytes() for p in (src, stats["path"]))
    assert a == b


@pytest.mark.parametrize("rotate", [90, 180, 270])
def test_searchable_layer_follows_a_rotated_page(tmp_path, rotate):
    src = _image_only_pdf(tmp_path / "scan.pdf", rotate=rotate)
    stats = searchable.build(_scan_doc(src), tmp_path / "s.pdf")
    import ctypes

    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(stats["path"])
    page = pdf[0]
    w, h = (int(round(v)) for v in page.get_size())           # as viewed (the rotation applied)
    tp = page.get_textpage()
    found = tp.search("INVOICE").get_next()
    assert found is not None
    xs, ys = [], []
    for i in range(found[0], found[0] + found[1]):            # character boxes -> where a viewer shows them
        l, b_, r, t = tp.get_charbox(i)
        for x, y in ((l, b_), (r, t)):
            dx, dy = ctypes.c_int(), ctypes.c_int()
            pdfium.raw.FPDF_PageToDevice(page.raw, 0, 0, w, h, 0, x, y, ctypes.byref(dx), ctypes.byref(dy))
            xs.append(dx.value / w)
            ys.append(dy.value / h)
    # the word was placed at left 0.10-0.40, top 0.10-0.15 of the page as the reader saw it
    assert abs(min(xs) - 0.10) < 0.02 and abs(max(xs) - 0.40) < 0.03, (min(xs), max(xs))
    assert 0.08 < min(ys) and max(ys) < 0.17 and abs(max(ys) - 0.15) < 0.02, (min(ys), max(ys))


def test_pages_with_a_text_layer_are_left_alone_and_nothing_is_written_without_need(tmp_path):
    src = _image_only_pdf(tmp_path / "scan.pdf", pages=2)
    doc = _scan_doc(src, n_pages=2)
    doc.pages[0].text_layer_usable = True
    stats = searchable.build(doc, tmp_path / "s.pdf")
    assert stats["pages_with_layer"] == 1 and stats["skipped"] == {"page already has a text layer": 1}
    assert _read_words(stats["path"], 0)[1] == "" and "INVOICE" in _read_words(stats["path"], 1)[1]
    doc.pages[1].text_layer_usable = True
    stats = searchable.build(doc, tmp_path / "t.pdf")
    assert stats["path"] is None and not (tmp_path / "t.pdf").exists()


def test_words_sharing_a_box_are_joined_and_doubles_are_dropped(tmp_path):
    doc = _scan_doc(_image_only_pdf(tmp_path / "scan.pdf"))
    region = doc.pages[0].regions[0]
    box = BBox(left=0.5, top=0.2, width=0.2, height=0.03, page=0)
    region.content = "Employer's name"
    region.lines = [Line(text="x", words=[Word(text="Employer", bbox=box, char_start=0, char_end=8),
                                          Word(text="s", bbox=box, char_start=9, char_end=10),
                                          Word(text="name", bbox=BBox(left=0.72, top=0.2, width=0.1, height=0.03, page=0),
                                               char_start=11, char_end=15)])]
    twin = region.model_copy(deep=True)                       # a second block grounded on the same words
    doc.pages[0].regions.append(twin)
    doc.pages[0].words["tesseract"] = [Word(text="Emp1oyer's", bbox=box)]   # OCR's version of a word we placed
    words, source = searchable.page_words(doc.pages[0])
    assert [t for t, _ in words] == ["Employer's", "name"] and source == "grounded"
    got, _ = _read_words(searchable.build(doc, tmp_path / "s.pdf")["path"])
    assert set(got) == {"Employer's", "name"}


def test_a_page_with_an_unusable_text_layer_is_redrawn_before_the_new_layer(tmp_path):
    src = tmp_path / "old_ocr.pdf"
    with pikepdf.open(_image_only_pdf(tmp_path / "scan.pdf")) as pdf:
        page = pdf.pages[0]
        font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                    BaseFont=pikepdf.Name.Helvetica))
        name = page.add_resource(font, pikepdf.Name.Font)
        page.contents_add(pdf.make_stream(f"BT 3 Tr {name} 30 Tf 61 700 Td (lNV0lCE garbage) Tj ET".encode()))
        pdf.save(src)
    assert "lNV0lCE" in _read_words(src)[1]
    doc = _scan_doc(src)
    doc.pages[0].words["pdf"] = [Word(text="lNV0lCE", bbox=BBox(left=0.1, top=0.1, width=0.3, height=0.05, page=0))]
    stats = searchable.build(doc, tmp_path / "s.pdf")
    got, text = _read_words(stats["path"])
    assert stats["pages_rasterised"] == 1 and "lNV0lCE" not in text and "garbage" not in text
    assert "INVOICE" in got and abs(got["INVOICE"][0] - 0.10) < 0.01
    import pypdfium2 as pdfium
    im = pdfium.PdfDocument(stats["path"])[0].render(scale=1).to_pil().convert("L")
    assert im.getpixel((150, 100)) < 50 and im.getpixel((400, 400)) > 200    # the picture of the page is still there


def test_image_input_becomes_a_searchable_pdf(tmp_path):
    png = tmp_path / "scan.png"
    Image.new("RGB", (850, 1100), "white").save(png)
    doc = _scan_doc(png, file_type="image")
    doc.pages[0].renders["150"] = str(png)
    stats = searchable.build(doc, tmp_path / "s.pdf", dpi=100)
    got, text = _read_words(stats["path"])
    assert "INVOICE" in text and abs(got["Total"][0] - 0.10) < 0.01
    with pikepdf.open(stats["path"]) as pdf:
        assert [round(float(v)) for v in pdf.pages[0].mediabox] == [0, 0, 612, 792]   # 850 px at 100 dpi


def test_response_reports_the_requested_model_searchable_pdf_and_prompted_blocks(tmp_path):
    doc = make_doc([["hello"]])
    from readanydoc.pipeline.shape import build_chunks
    from readanydoc.pipeline.base import ParseOptions
    from readanydoc.serialize import to_parse_response
    doc.chunks = build_chunks(doc, ParseOptions())
    pdf = _image_only_pdf(tmp_path / "s.pdf")
    stored = {}
    def put(data, name, ctype):
        stored[name] = data
        return "http://x/" + name
    for model, label, pages, legacy in (("r-1-mini", "R-1 Mini", 1, 0), ("legacy", "Legacy", 0, 1.0), ("r-1", "R-1", 1, 0)):
        doc.meta.update(requested_model=model, prompted_blocks=2, searchable_pdf={"path": str(pdf)})
        out = response.finish(to_parse_response(doc), doc, put, source="/in/scan.pdf")
        ub = out["usage"]["usage_breakdown"]
        assert (ub["parse_model"], ub["parse_pages"], ub["legacy_parse_credits"]) == (label, pages, legacy)
        assert ub["prompted_blocks"] == 2 and out["pdf_url"] == "http://x/scan.searchable.pdf"
    assert stored["scan.searchable.pdf"] == pdf.read_bytes()


def test_mini_and_legacy_map_to_the_cheaper_profile_of_the_configured_engine(monkeypatch):
    from readanydoc.api.service import profile_for
    for k in ("READANYDOC_PROFILE", "READANYDOC_PROFILE_R_1_MINI", "READANYDOC_PROFILE_LEGACY", "READANYDOC_MINI_SAME_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    assert (profile_for(None), profile_for("r-1-mini")) == ("engine", "model-only")
    monkeypatch.setenv("READANYDOC_PROFILE", "engine-final")
    assert [profile_for(m) for m in ("r-1", "r-1-mini", "legacy")] == ["engine-final", "vanilla-api", "vanilla-api"]
    monkeypatch.setenv("READANYDOC_PROFILE_R_1_MINI", "engine-api")           # an explicit choice wins
    assert profile_for("r-1-mini") == "engine-api" and profile_for("legacy") == "vanilla-api"
    monkeypatch.delenv("READANYDOC_PROFILE_R_1_MINI")
    monkeypatch.setenv("READANYDOC_MINI_SAME_PROFILE", "1")                   # one engine for all three
    assert profile_for("r-1-mini") == "engine-final"
    monkeypatch.setenv("READANYDOC_PROFILE", "cached-haiku")                  # unknown to the map: unchanged
    monkeypatch.delenv("READANYDOC_MINI_SAME_PROFILE")
    assert profile_for("legacy") == "cached-haiku"


def test_mini_tier_reads_at_medium_effort_unless_a_reader_is_named(monkeypatch):
    from readanydoc.api.service import pipeline_config
    for k in ("READANYDOC_PROFILE_R_1_MINI", "READANYDOC_PROFILE_LEGACY", "READANYDOC_MINI_SAME_PROFILE", "READANYDOC_READER",
              "READANYDOC_READER_MINI", "READANYDOC_STAGE_OPTIONS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("READANYDOC_PROFILE", "engine-final")
    assert pipeline_config({}).reader == "anthropic_api:haiku#high"
    mini = pipeline_config({"settings": {"model": "r-1-mini"}})
    assert (mini.profile, mini.reader) == ("vanilla-api", "anthropic_api:haiku#medium")
    assert pipeline_config({"settings": {"model": "legacy"}}).reader == "anthropic_api:haiku#medium"
    monkeypatch.setenv("READANYDOC_READER_MINI", "anthropic_api:haiku#low")
    assert pipeline_config({"settings": {"model": "r-1-mini"}}).reader == "anthropic_api:haiku#low"
    monkeypatch.delenv("READANYDOC_READER_MINI")
    monkeypatch.setenv("READANYDOC_MINI_SAME_PROFILE", "1")
    same = pipeline_config({"settings": {"model": "r-1-mini"}})
    assert (same.profile, same.reader) == ("engine-final", "anthropic_api:haiku#high")
