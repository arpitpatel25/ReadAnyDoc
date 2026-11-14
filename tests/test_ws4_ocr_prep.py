"""Workstream 4 (final round): OCR word boxes, page preparation, zoom gate. No model call, no network."""
import shutil

import pytest
from PIL import Image, ImageDraw

from readanydoc.intake import prepare
from readanydoc.model import BBox, Page, Word
from readanydoc.pipeline.grounding import ocr
from readanydoc.pipeline.grounding.sources import source_priority, tesseract_words
from readanydoc.pipeline.reading import zoom_gate
from readanydoc.readers import WORD_PROVIDERS

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract binary not installed")
needs_ocr = pytest.mark.skipif(not ocr.available(), reason="optional OCR packages not installed")


def text_image(lines=("INVOICE NUMBER 48213", "Total amount due 1,250.00", "Payment within thirty days"), size=(900, 420)):
    from PIL import ImageFont
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=36)
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    for i, t in enumerate(lines):
        d.text((40, 40 + 110 * i), t, fill="black", font=font)
    return im


@needs_tesseract
def test_tesseract_reads_an_image_under_tmp(monkeypatch, tmp_path):
    """Leptonica rewrites paths that start with /tmp; the words used to fail with UnicodeDecodeError."""
    import tempfile
    monkeypatch.setenv("READANYDOC_CACHE_DIR", str(tmp_path / "cache"))
    with tempfile.TemporaryDirectory(dir="/tmp") as d:
        p = f"{d}/page.png"
        text_image().save(p)
        words = tesseract_words(p)
    assert any("INVOICE" in w.text.upper() for w in words)
    assert all(0 <= w.bbox.left <= 1 and 0 <= w.bbox.bottom <= 1.001 for w in words)


def test_rapidocr_words_are_tried_before_tesseract():
    w = [Word(text="a", bbox=BBox.from_xyxy(0, 0, 0.1, 0.1, 0))]
    page = Page(index=0, original_page=1, width=100, height=100)
    page.words = {"tesseract": w, "rapidocr": w, "pdf": w}
    page.text_layer_usable = True
    assert [n for n, _ in source_priority(page)] == ["pdf", "rapidocr", "tesseract"]
    page.text_layer_usable = False
    assert source_priority(page) == [("rapidocr", True), ("tesseract", True), ("pdf", False)]


def test_word_providers_are_registered():
    assert {"tesseract", "rapidocr", "tesseract+rapidocr"} <= set(WORD_PROVIDERS)


@needs_ocr
def test_ocr_lines_and_words(monkeypatch, tmp_path):
    monkeypatch.setenv("READANYDOC_CACHE_DIR", str(tmp_path / "cache"))
    p = tmp_path / "page.png"
    text_image().save(p)
    lines = ocr.ocr_lines(p, page_index=3)
    assert len(lines) == 3 and "48213" in lines[0].text
    words = ocr.ocr_words(p, page_index=3)
    assert [w.text for w in words if w.line == 0][-1] == "48213"
    assert all(w.bbox.page == 3 and 0 <= w.bbox.left < w.bbox.right <= 1 and w.confidence.score > 0.5 for w in words)
    assert lines[0].bbox.top < lines[1].bbox.top < lines[2].bbox.top
    assert (tmp_path / "cache" / "rapidocr").exists() and ocr.ocr_seconds(p) is not None
    # the same bytes at another path are served from the cache (keyed by content)
    q = tmp_path / "copy.png"
    q.write_bytes(p.read_bytes())
    monkeypatch.setattr(ocr, "run_image", lambda *a, **k: (_ for _ in ()).throw(AssertionError("not cached")))
    assert len(ocr.ocr_words(q)) == len(words)


@needs_ocr
@pytest.mark.parametrize("applied", [0, 90, 180, 270])
def test_image_turn_by_ocr(applied):
    im = prepare.turn_image(text_image(("The quick brown fox jumps over the lazy dog", "Pack my box with five dozen liquor jugs",
                                         "Sphinx of black quartz judge my vow", "How vexingly quick daft zebras jump"),
                                        size=(1200, 600)), applied)
    turn, info = prepare.image_turn_ocr(im)
    assert turn == (360 - applied) % 360, info
    assert prepare.turn_image(im, turn).size == (1200, 600)


def test_skew_angle_and_turn_image():
    pytest.importorskip("numpy")
    im = Image.new("RGB", (800, 1000), "white")
    d = ImageDraw.Draw(im)
    for y in range(100, 900, 40):
        d.rectangle([80, y, 720, y + 12], fill="black")
    a0, _ = prepare.skew_angle(im)
    assert abs(a0) < 0.3
    for skew in (-6, 3):
        a, gain = prepare.skew_angle(im.rotate(skew, fillcolor=(255, 255, 255)))
        assert abs(a + skew) <= 0.3 and gain > 1.3
    assert prepare.turn_image(im, 90).size == (1000, 800)


def _pdf_with_text(path, matrix):
    import pikepdf
    pdf = pikepdf.new()
    lines = b"".join(b"BT /F1 12 Tf %s Tm (This page was written sideways on purpose line %d) Tj ET\n" % (m, i)
                     for i, m in enumerate(matrix))
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page, MediaBox=[0, 0, 612, 792], Contents=pdf.make_stream(lines),
        Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))))
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    pdf.save(str(path))


def test_text_layer_turn_and_pdf_correction(monkeypatch, tmp_path):
    from readanydoc.pipeline import Pipeline, PipelineConfig, StageConfig
    monkeypatch.setenv("READANYDOC_CACHE_DIR", str(tmp_path / "cache"))
    upright, sideways = tmp_path / "up.pdf", tmp_path / "side.pdf"
    _pdf_with_text(upright, [b"1 0 0 1 72 %d" % (700 - 20 * i) for i in range(8)])
    _pdf_with_text(sideways, [b"0 1 -1 0 %d 72" % (100 + 20 * i) for i in range(8)])  # text runs bottom to top
    assert prepare.text_layer_turns(upright, [1]) == {1: 0}
    assert prepare.text_layer_turns(sideways, [1]) == {1: 90}
    off = {n: StageConfig(enabled=False) for n in ("layout", "read", "verify", "ground", "assemble", "shape")}
    cfg = PipelineConfig(words="tesseract", stages={**off, "triage": StageConfig(options={"prepare": "rotate"})})
    doc = Pipeline(cfg).run(str(sideways))
    page = doc.pages[0]
    assert page.properties["rotation_applied"] == 90 and (page.width, page.height) == (792.0, 612.0)
    assert doc.meta["path"] != str(sideways) and doc.meta["original_path"] == str(sideways)
    assert prepare.text_layer_turns(doc.meta["path"], [1]) == {1: 0}
    assert "sideways" in " ".join(w.text for w in page.words["pdf"])  # words read forwards now, not reversed
    same = Pipeline(cfg).run(str(upright))
    assert same.meta["path"] == str(upright) and "rotation_applied" not in same.pages[0].properties


def test_zoom_gate_rules():
    table = "<table><tr><td>Sales</td><td>1,234</td><td>5,678</td></tr><tr><td>Total</td><td>6,912</td><td>44</td></tr></table>"
    prose = "Dear Sir, on 12 March 1935 we received 250 crates."
    assert zoom_gate.table_number_count(table) == 4 and zoom_gate.table_number_count(prose) == 0
    assert zoom_gate.table_number_count("| a | 100 |\n|---|---|\n| b | 2,500 |") == 2
    page = Page(index=0, original_page=1, width=100, height=100)
    box = BBox.from_xyxy(0, 0, 0.1, 0.1, 0)
    page.words["tesseract"] = [Word(text=t, bbox=box) for t in ("Sales", "1,234", "5,678", "6,912", "44")]
    assert zoom_gate.unsupported_numbers(page, table)[:2] == (0, 4)
    run, info = zoom_gate.decide("ocr_disagree", page, table)
    assert not run and info["numbers_unsupported"] == 0
    page.words["tesseract"][1].text = "1,284"  # OCR and the page read differ on one number
    assert zoom_gate.decide("ocr_disagree", page, table)[0]
    assert zoom_gate.decide("table_numbers", page, table)[0] and not zoom_gate.decide("table_numbers", page, prose)[0]
    assert not zoom_gate.decide("table_and_disagree", page, prose)[0]
    assert zoom_gate.decide("anything else", page, prose)[0]  # unknown mode: strips run as before


def test_fill_provider_puts_pp_ocr_after_tesseract():
    assert "tesseract>rapidocr" in WORD_PROVIDERS
    w = [Word(text="a", bbox=BBox.from_xyxy(0, 0, 0.1, 0.1, 0))]
    page = Page(index=0, original_page=1, width=100, height=100)
    page.words = {"rapidocr_fill": w, "tesseract": w}
    assert [n for n, _ in source_priority(page)] == ["tesseract", "rapidocr_fill"]


def test_zoom_gate_legible_and_pre_read_gate():
    table = "<table><tr><td>Sales</td><td>1,234</td><td>5,678</td></tr><tr><td>Total</td><td>6,912</td><td>44</td></tr></table>"
    page = Page(index=0, original_page=1, width=100, height=100)
    assert zoom_gate.decide("table_legible", page, table)[0]
    assert not zoom_gate.decide("table_legible", page, table + "\n\nSigned [illegible]")[0]
    assert zoom_gate.pre_decide(page, 2) == (True, {"run": True, "ocr_source": "", "numbers": None, "min": 2})  # no OCR: unknown
    box = BBox.from_xyxy(0, 0, 0.1, 0.1, 0)
    page.words["tesseract"] = [Word(text=t, bbox=box) for t in ("Sales", "1,234", "5,678", "in", "7")]
    assert zoom_gate.pre_decide(page, 2)[0] and not zoom_gate.pre_decide(page, 3)[0]


def test_effort_route_rules():
    from readanydoc.pipeline.reading import effort_route
    box = BBox.from_xyxy(0, 0, 0.1, 0.1, 0)
    page = Page(index=0, original_page=1, width=100, height=100)
    assert not effort_route.decide(page)[0]  # nothing known about the page: never easy
    page.words["tesseract"] = [Word(text="word", bbox=box) for _ in range(60)] + [Word(text="1,250", bbox=box)]
    easy, info = effort_route.decide(page)
    assert easy and info["numbers"] == 1 and info["source"] == "tesseract"
    page.words["tesseract"] += [Word(text=f"{i},000", bbox=box) for i in range(10, 60)]
    assert not effort_route.decide(page)[0]  # a page full of numbers keeps the configured effort
    assert effort_route.spec_with_effort("anthropic_api:haiku#high@dev1", "medium") == "anthropic_api:haiku#medium@dev1"
    assert effort_route.spec_with_effort("anthropic_api:haiku#high", "medium") == "anthropic_api:haiku#medium"
    assert effort_route.spec_with_effort("claude_cli:haiku", "medium") == "claude_cli:haiku"


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_original_point_undoes_a_turn(turn):
    # where a point of the original page lands after the page is turned clockwise by `turn`
    fwd = {0: lambda x, y: (x, y), 90: lambda x, y: (1 - y, x), 180: lambda x, y: (1 - x, 1 - y),
           270: lambda x, y: (y, 1 - x)}[turn]
    for x, y in ((0.1, 0.2), (0.75, 0.4), (0.5, 0.95)):
        bx, by = prepare.original_point(*fwd(x, y), turn=turn)
        assert abs(bx - x) < 1e-9 and abs(by - y) < 1e-9
    # the pixel mapping agrees with what turn_image does to an image
    im = Image.new("L", (40, 100), 0)
    im.putpixel((4, 20), 255)
    out = prepare.turn_image(im, turn)
    px = next((x, y) for x in range(out.width) for y in range(out.height) if out.getpixel((x, y)) == 255)
    bx, by = prepare.original_point((px[0] + 0.5) / out.width, (px[1] + 0.5) / out.height, turn=turn)
    assert abs(bx - 4.5 / 40) < 1e-9 and abs(by - 20.5 / 100) < 1e-9


def test_boxes_return_to_the_original_page(monkeypatch, tmp_path):
    from readanydoc.model import Document, Region, RegionType
    page = Page(index=0, original_page=1, width=792.0, height=612.0, rotation=90)  # as left by a 90 degree correction
    page.properties.update(rotation_applied=90, skew_applied=0.0, renders_before_prepare={"200": "orig.png"})
    box = BBox.from_xyxy(0.1, 0.2, 0.5, 0.3, 0)  # a text line across the corrected page
    page.regions = [Region(id="r1", type=RegionType.TEXT, content="a line", bbox=box)]
    page.words = {"tesseract": [Word(text="a", bbox=BBox.from_xyxy(0.1, 0.2, 0.2, 0.3, 0))]}
    doc = Document(pages=[page], meta={"path": "fixed.pdf", "original_path": "given.pdf", "prepare": {"changed": True}})
    assert prepare.restore_original_frame(doc) == 1
    b = page.regions[0].bbox  # on the page as given the line runs bottom to top: a tall, narrow box
    assert (round(b.left, 3), round(b.top, 3), round(b.width, 3), round(b.height, 3)) == (0.2, 0.5, 0.1, 0.4)
    assert (page.width, page.height, page.rotation) == (612.0, 792.0, 0) and page.renders == {"200": "orig.png"}
    assert doc.meta["path"] == "given.pdf" and doc.meta["prepared_path"] == "fixed.pdf"
    assert prepare.restore_original_frame(doc) == 0  # only once
