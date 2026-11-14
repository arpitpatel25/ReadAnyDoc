"""Handwriting route (workstream D): detection from run-time signals, the read stage's use of it, token usage."""
from readanydoc.model import Confidence, Document, Page, Word
from readanydoc.pipeline import PipelineConfig, StageConfig
from readanydoc.pipeline.base import PipelineContext
from readanydoc.pipeline.read import Read
from readanydoc.pipeline.reading import handwriting
from readanydoc.readers import usage as usage_mod
from readanydoc.readers.base import Reader, ReadResult, Usage


def page(kind="scanned", ocr=(), conf=0.9, exact=False):
    p = Page(index=0, kind=kind)
    p.properties["text_layer_exact"] = exact
    if ocr:
        p.words["tesseract"] = [Word(text=t, confidence=Confidence.of(conf), source="tesseract") for t in ocr]
    return p


PRINT = "The quick brown fox jumps over the lazy dog near the old river bank today".split()


def test_illegible_mark_on_a_scan_routes():
    s = handwriting.detect(page(ocr=PRINT), "Received of John [illegible] the sum of ten dollars")
    assert s.route and s.illegible == 1 and "illegible" in s.reason


def test_digital_page_is_never_routed():
    assert not handwriting.detect(page(kind="digital"), "name [illegible] [illegible]").route
    assert not handwriting.detect(page(exact=True), "name [illegible]").route


def test_printed_scan_without_marks_is_not_routed():
    s = handwriting.detect(page(ocr=PRINT, conf=0.92), " ".join(PRINT))
    assert not s.route and s.ocr_agreement == 1.0


def test_ocr_that_cannot_read_the_page_routes():
    garbage = ["lk", "wv~", "ot", "Jn", "mm", "oo", "e", "tt"]
    read = "Springfield June 4th 1861 one day after date I promise to pay William Garr twenty dollars"
    s = handwriting.detect(page(ocr=garbage, conf=0.3), read)
    assert s.route and s.ocr_confidence == 0.3 and s.ocr_agreement == 0.0


def test_low_agreement_alone_or_low_confidence_alone_does_not_route():
    read = "Springfield June 4th 1861 one day after date I promise to pay William Garr twenty dollars"
    assert not handwriting.detect(page(ocr=["lk", "wv", "ot", "Jn", "mm", "oo"], conf=0.8), read).route  # OCR sure of itself
    assert not handwriting.detect(page(ocr=read.split(), conf=0.3), read).route  # OCR agrees with the read


def test_other_scripts_do_not_route_on_the_ocr_signal():
    read = "郵便振替払込請求書兼受領証 口座記号 口座番号 金額 料金 加入者名 ご依頼人 おところ おなまえ 様"
    s = handwriting.detect(page(ocr=["lk", "wv~", "ot", "Jn", "mm", "oo"], conf=0.3), read)
    assert not s.route


def test_image_input_counts_as_a_scan():
    p = page(kind=None, ocr=PRINT)
    assert handwriting.detect(p, "pay [illegible]", is_pdf=False).route
    assert not handwriting.detect(p, "pay [illegible]", is_pdf=True).route


class Fake(Reader):
    needs_image = False

    def __init__(self, name, text, **kw):
        self.name, self.text, self.kw, self.calls = name, text, kw, 0

    def read(self, image, hint=None, context=None):
        self.calls += 1
        return ReadResult(text=self.text, usage=Usage(model=self.name, input_tokens=100, output_tokens=50), **self.kw)


def run_read(first, second, monkeypatch, **options):
    cfg = PipelineConfig(reader="x", stages={"read": StageConfig(options={"handwriting_route": True, **options})})
    stage = Read(**cfg.stage("read").options)
    monkeypatch.setattr(Read, "_reader", lambda self, spec: second)
    ctx = PipelineContext(cfg)
    ctx._reader = first
    doc = Document(source="x.pdf")
    doc.file_type = "pdf"
    doc.pages = [page(ocr=PRINT)]
    return stage.run(doc, ctx)


def test_route_uses_the_second_read_and_keeps_the_first_as_a_candidate(monkeypatch):
    first = Fake("claude-haiku-5-5", "Received of John [illegible] ten dollars")
    second = Fake("claude-sonnet-5-5", "Received of John Whitcomb ten dollars")
    doc = run_read(first, second, monkeypatch)
    p = doc.pages[0]
    assert p.regions[0].content == "Received of John Whitcomb ten dollars"
    assert p.properties["handwriting_route"]["used"] is True
    reads = {r["source"]: r for r in p.properties["reads"]}
    assert reads["claude-sonnet-5-5"]["selected"] and reads["claude-haiku-5-5"]["tier"] == "superseded"
    mu = usage_mod.summarize(doc.usage)
    assert set(mu["models"]) == {"claude-haiku-5-5", "claude-sonnet-5-5"} and mu["calls"] == 2
    assert usage_mod.cost_usd(mu) == round((100 * 0.10 + 50 * 0.50 + 100 * 2 + 50 * 10) / 1e6, 6)


def test_route_keeps_the_first_read_when_the_second_fails_or_is_short(monkeypatch):
    first = Fake("claude-haiku-5-5", "Received of John [illegible] the sum of ten dollars in full of account")
    for bad in (Fake("claude-sonnet-5-5", "", stop_reason="refusal", refused=True), Fake("claude-sonnet-5-5", "Received"),
                Fake("claude-sonnet-5-5", "Received of John W the sum", stop_reason="max_tokens", truncated=True)):
        doc = run_read(first, bad, monkeypatch, max_attempts=1)
        assert "[illegible]" in doc.pages[0].regions[0].content
        assert doc.pages[0].properties["handwriting_route"]["used"] is False


def test_route_off_or_no_signal_makes_no_second_call(monkeypatch):
    second = Fake("claude-sonnet-5-5", "x")
    run_read(Fake("claude-haiku-5-5", " ".join(PRINT)), second, monkeypatch)
    run_read(Fake("claude-haiku-5-5", "pay [illegible]"), second, monkeypatch, handwriting_route=False)
    assert second.calls == 0


def test_engine_api_profile_has_the_route_behind_an_option():
    from readanydoc.profiles import get_profile
    o = get_profile("engine-api").stage("read").options
    assert o["handwriting_route"] is True and o["handwriting_reader"] == "anthropic_api:sonnet"
    assert "handwriting_route" not in get_profile("vanilla-api").stage("read").options


def test_usage_summary_counts_tokens_per_model_and_prices_them():
    mu = usage_mod.summarize([
        {"model": "claude-haiku-5-5", "input_tokens": 1000, "output_tokens": 2000},
        {"model": "claude-haiku-5-5", "input_tokens": 1000, "output_tokens": 0, "cached": True},
        {"model": "claude-sonnet-5-5", "input_tokens": 5000, "output_tokens": 1000},
        {"reader": "cached:haiku"},  # no tokens: not a model call
    ])
    assert mu["calls"] == 3 and mu["cached_calls"] == 1 and mu["input_tokens"] == 7000
    assert usage_mod.cost_usd(mu) == round((2000 * 0.1 + 2000 * 0.5 + 5000 * 2 + 1000 * 10) / 1e6, 6)
    assert usage_mod.cost_usd(usage_mod.summarize([{"model": "gpt-x", "input_tokens": 1, "output_tokens": 1}])) is None
    both = usage_mod.merge(mu, mu)
    assert both["calls"] == 6 and both["models"]["claude-sonnet-5-5"]["output_tokens"] == 2000


def test_api_reader_spec_with_sample_is_disk_cached(tmp_path, monkeypatch):
    from readanydoc.readers import make_reader
    monkeypatch.setenv("READANYDOC_CALL_CACHE", str(tmp_path))
    r = make_reader("anthropic_api:haiku#high@t1")
    assert r.name == "anthropic_api:claude-haiku-5-5" and r._inner.effort == "high"
    inner = Fake("x", "hello")
    r._inner = inner
    assert r.read(b"\x89PNG1").text == "hello" and r.read(b"\x89PNG1").usage.cached and inner.calls == 1
    assert not hasattr(make_reader("anthropic_api:haiku#high"), "_inner")


# ---------------------------------------------------------------------------------------------- round 4: print witness
def _with_ppocr(p, n, conf):
    p.words["rapidocr_fill"] = [Word(text=f"w{k}", confidence=Confidence.of(conf), source="rapidocr_fill") for k in range(n)]
    return p


GARBAGE = ["lk", "wv~", "ot", "Jn", "mm", "oo", "e", "tt"]
LETTER = "Springfield June 4th 1861 one day after date I promise to pay William Garr twenty dollars"


def test_print_witness_is_off_by_default():
    s = handwriting.detect(_with_ppocr(page(ocr=GARBAGE, conf=0.3), 80, 0.99), LETTER)
    assert s.route and s.print_confidence is None


def test_printed_table_that_tesseract_cannot_read_is_not_routed():
    # a dense printed table: Tesseract fails (0.3), PP-OCR reads it as print (0.98 over 80 words)
    s = handwriting.detect(_with_ppocr(page(ocr=GARBAGE, conf=0.3), 80, 0.98), LETTER, print_ocr=True)
    assert not s.route and s.print_confidence == 0.98 and "print" in s.reason
    s = handwriting.detect(_with_ppocr(page(ocr=GARBAGE, conf=0.3), 80, 0.98), "Total [illegible] 12 34 56", print_ocr=True)
    assert not s.route and s.illegible == 1


def test_handwriting_is_still_routed_with_the_print_witness():
    for conf in (0.70, 0.86, 0.913, 0.941):       # PP-OCR on the handwritten business pages: 0.70 to 0.94
        s = handwriting.detect(_with_ppocr(page(ocr=GARBAGE, conf=0.3), 200, conf), LETTER, print_ocr=True)
        assert s.route and s.print_confidence == conf
    # a page with a handful of printed words says nothing about the rest of it
    s = handwriting.detect(_with_ppocr(page(ocr=GARBAGE, conf=0.3), 7, 1.0), "Paid [illegible] by cheque", print_ocr=True)
    assert s.route
    # no second OCR: as before
    assert handwriting.detect(page(ocr=GARBAGE, conf=0.3), LETTER, print_ocr=True).route


def test_print_witness_never_adds_a_route():
    s = handwriting.detect(_with_ppocr(page(ocr=PRINT, conf=0.92), 80, 0.5), " ".join(PRINT), print_ocr=True)
    assert not s.route


def test_engine_final_4_differs_from_3_full_only_by_the_two_round_4_switches():
    from readanydoc.profiles import get_profile
    a, b = get_profile("engine-final-3-full"), get_profile("engine-final-4")
    ra, rb = dict(a.stage("read").options), dict(b.stage("read").options)
    assert rb.pop("handwriting_print_ocr") is True and "handwriting_print_ocr" not in ra
    assert rb.pop("forms").split(",") == ra.pop("forms").split(",") + ["kv_guard"]
    assert ra == rb
    for name in ("triage", "layout", "verify", "ground", "shape"):
        assert a.stage(name).options == b.stage(name).options
    assert (a.reader, a.layout, a.words, a.tier) == (b.reader, b.layout, b.words, b.tier)
