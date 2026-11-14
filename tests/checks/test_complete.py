from readanydoc.model import BBox, Confidence, Document, Page, Region, RegionType, Word
from readanydoc.pipeline.checks import complete as C
from readanydoc.pipeline.checks.common import PageChecks


def W(text, x, y, line, source="pdf", conf=None, w=None, h=0.012):
    return Word(text=text, bbox=BBox(left=x, top=y, width=w or 0.012 * len(text), height=h, page=0), source=source,
                line=line, confidence=Confidence.of(conf) if conf is not None else Confidence())


def line(words, y, line_no, x=0.1, **kw):
    out, cx = [], x
    for t in words.split():
        out.append(W(t, cx, y, line_no, **kw))
        cx += 0.012 * len(t) + 0.008
    return out


def page_with(pdf_words, ocr_words, contents, exact=True):
    page = Page(index=0, width=612, height=792, kind="digital" if exact else "scanned")
    if pdf_words:
        page.words["pdf"] = pdf_words
    if ocr_words:
        page.words["tesseract"] = ocr_words
    page.text_layer_usable = exact
    page.properties["text_layer_exact"] = exact
    page.regions = [Region(type=RegionType.TEXT, content=c, text=c) for c in contents]
    return page


def checks(page, **options):
    doc = Document(source="x.pdf", stem="x", file_type="pdf", pages=[page])
    return PageChecks(doc=doc, page=page, reader_spec="none", ground_options={"tesseract": "off"}, options=options)


BODY = "The quick brown fox jumps over the lazy dog near the river bank today"


def test_segments_split_at_wide_gaps():
    ws = line("Annual Report", 0.03, 0) + line("Page 7", 0.03, 0, x=0.8)
    segs = C.pdf_segments(page_with(ws, None, []))
    assert [s.text for s in segs] == ["Annual Report", "Page 7"] and segs[0].band == "top"


def test_doubled_glyph_words_are_dropped():
    ws = line("Annual Report", 0.03, 0) + line("Annual Report", 0.03, 0)
    assert [s.text for s in C.pdf_segments(page_with(ws, None, []))] == ["Annual Report"]


def test_dropped_header_is_restored_from_the_text_layer():
    header = line("Consolidated Statement 2023", 0.03, 0)
    body = line(BODY, 0.3, 1)
    page = page_with(header + body, [w.model_copy(update={"source": "tesseract"}) for w in header + body], [BODY])
    n = C.restore_from_text_layer(checks(page))
    assert n == 1 and page.regions[0].type == RegionType.HEADER and page.regions[0].content == "Consolidated Statement 2023"
    assert page.regions[0].meta["checks"][0]["status"] == "restored" and page.regions[1].content == BODY


def test_hidden_text_is_not_restored():
    header = line("Invisible watermark text", 0.03, 0)
    body = line(BODY, 0.3, 1)
    page = page_with(header + body, [w.model_copy(update={"source": "tesseract"}) for w in body], [BODY])  # OCR does not see it
    assert C.restore_from_text_layer(checks(page)) == 0 and len(page.regions) == 1


def test_untrusted_layer_and_present_text_are_left_alone():
    header = line("Consolidated Statement 2023", 0.03, 0)
    body = line(BODY, 0.3, 1)
    ocr = [w.model_copy(update={"source": "tesseract"}) for w in header + body]
    page = page_with(header + body, ocr, [BODY], exact=False)
    assert C.restore_from_text_layer(checks(page)) == 0
    page = page_with(header + body, ocr, ["**Consolidated** Statement, 2023", BODY])
    assert C.restore_from_text_layer(checks(page)) == 0 and len(page.regions) == 2


def test_reordered_body_text_is_not_called_missing():
    cell = line("fox river dog", 0.5, 2)
    body = line(BODY, 0.3, 1)
    read, toks = C.norm(BODY), set(C.norm(BODY).split())
    seg = C.pdf_segments(page_with(cell, None, []))[0]
    assert seg.band is None and not C.absent(seg, read, toks)


def test_glued_or_doubled_text_is_not_simple():
    assert C.simple(C.Seg("Annual Report 2023", (0.1, 0.02, 0.4, 0.04)))
    assert not C.simple(C.Seg("AAnnnnuuaall RReeppoorrtt", (0.1, 0.02, 0.4, 0.04)))
    assert not C.simple(C.Seg("thisisaverylonggluedwordwithoutanyspacesatall", (0.1, 0.02, 0.4, 0.04)))
    assert not C.simple(C.Seg("∑ ∫ α → β ≤ γ", (0.1, 0.3, 0.4, 0.32)))


def test_ocr_candidates_need_confident_words():
    ws = line("Federal Reserve Bulletin", 0.03, 0, source="tesseract", conf=0.93) + \
         line("x;~ |l", 0.5, 1, source="tesseract", conf=0.2)
    page = page_with(None, ws, [BODY], exact=False)
    segs = C.ocr_segments(page)
    assert [s.text for s in segs] == ["Federal Reserve Bulletin"]


class FakeChecks(PageChecks):
    answer: str = ""

    def ask(self, box, prompt, tag, zoom=None):
        self.calls += 1
        return self.answer


def scan_page():
    header = line("Federal Reserve Bulletin", 0.03, 0, source="tesseract", conf=0.93)
    body = line(BODY, 0.3, 1, source="tesseract", conf=0.95)
    return page_with(None, header + body, [BODY], exact=False)


def test_scan_line_is_written_by_the_model_not_by_ocr():
    page = scan_page()
    pc = FakeChecks(doc=Document(source="x.png", stem="x", file_type="png", pages=[page]), page=page, reader_spec="none",
                    ground_options={"tesseract": "off"})
    pc.answer = "FEDERAL RESERVE BULLETIN\n\nSomething unrelated the pointer never showed"
    assert C.restore_scan(pc) == 1 and pc.calls == 1
    assert page.regions[0].content == "FEDERAL RESERVE BULLETIN" and page.regions[0].type == RegionType.HEADER
    assert len(page.regions) == 2  # the unrelated crop line is not inserted


def test_scan_crop_that_disagrees_with_ocr_inserts_nothing():
    page = scan_page()
    pc = FakeChecks(doc=Document(source="x.png", stem="x", file_type="png", pages=[page]), page=page, reader_spec="none",
                    ground_options={"tesseract": "off"})
    pc.answer = "Completely different words here"
    assert C.restore_scan(pc) == 0 and len(page.regions) == 1


def test_scan_with_many_unmatched_lines_is_skipped():
    ws = []
    for i in range(15):
        ws += line(f"Unmatched noise line number{i} here", 0.1 + i * 0.05, i, source="tesseract", conf=0.9)
    page = page_with(None, ws, [BODY], exact=False)
    pc = FakeChecks(doc=Document(source="x.png", stem="x", file_type="png", pages=[page]), page=page, reader_spec="none",
                    ground_options={"tesseract": "off"})
    assert C.restore_scan(pc) == 0 and pc.calls == 0 and "completeness_skipped" in page.properties


def test_margins_only_option_is_parsed():
    from readanydoc.pipeline import checks
    spec = checks.parse_spec("complete=scan;complete_margins_only=true")
    assert spec["complete"] == ["scan"] and spec["complete_margins_only"] is True
