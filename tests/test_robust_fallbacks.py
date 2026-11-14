"""0.1.1 fixes: refused pages fall back, table re-reads are bounded, long lists are checked, usage names models."""
import time

from readanydoc.model import Confidence, Document, Page, Word
from readanydoc.pipeline import PipelineConfig, StageConfig
from readanydoc.pipeline.base import PipelineContext
from readanydoc.pipeline.read import Read, ocr_page_text
from readanydoc.readers.base import Reader, ReadResult, Usage
from readanydoc.tasks import FakeLLM, extract
from readanydoc.tasks.common import sum_usage
from readanydoc.tasks.extract import _rows_in, short_segments
from tests.tasks.conftest import make_doc


# ---- 1. content-filter refusals
class Fake(Reader):
    needs_image = False

    def __init__(self, name, text, **kw):
        self.name, self.text, self.kw, self.calls = name, text, kw, 0

    def read(self, image, hint=None, context=None):
        self.calls += 1
        return ReadResult(text=self.text, usage=Usage(model=self.name, input_tokens=100, output_tokens=5), **self.kw)


def ocr_page():
    p = Page(index=0, kind="scanned")
    p.words["tesseract"] = [Word(text=t, line=ln, confidence=Confidence.of(0.9), source="tesseract")
                            for ln, t in [(0, "Patient"), (0, "record"), (1, "Dose"), (1, "40"), (1, "mg")]]
    return p


def run_read(first, second, monkeypatch, **options):
    cfg = PipelineConfig(reader="x", stages={"read": StageConfig(options=options)})
    stage = Read(**cfg.stage("read").options)
    monkeypatch.setattr(Read, "_reader", lambda self, spec: second)
    ctx = PipelineContext(cfg)
    ctx._reader = first
    doc = Document(source="x.pdf")
    doc.file_type = "pdf"
    doc.pages = [ocr_page()]
    return stage.run(doc, ctx)


REFUSE = dict(stop_reason="refusal", refused=True)


def test_ocr_page_text_keeps_lines():
    assert ocr_page_text(ocr_page()) == "Patient record\nDose 40 mg"
    assert ocr_page_text(Page(index=0)) == ""


def test_refused_page_uses_ocr_text_and_is_marked(monkeypatch):
    doc = run_read(Fake("haiku", "", **REFUSE), Fake("unused", "x"), monkeypatch)
    p = doc.pages[0]
    assert "Dose 40 mg" in p.regions[0].content
    assert p.properties["ocr_fallback"] and all(r.meta.get("ocr_fallback") for r in p.regions)
    assert any("plain OCR text used instead" in n.message for n in doc.notes)
    assert any(e.get("escalation") == "refused" for e in doc.usage)  # refused calls are still counted


def test_refused_page_goes_to_a_second_model_when_configured(monkeypatch):
    second = Fake("sonnet", "Patient record\nDose 40 mg")
    doc = run_read(Fake("haiku", "", **REFUSE), second, monkeypatch, refusal_reader="anthropic_api:sonnet")
    assert second.calls == 1
    assert not doc.pages[0].properties.get("ocr_fallback")
    assert doc.pages[0].regions[0].content.startswith("Patient record")


def test_second_model_also_refuses_then_ocr(monkeypatch):
    doc = run_read(Fake("haiku", "", **REFUSE), Fake("sonnet", "", **REFUSE), monkeypatch,
                   refusal_reader="anthropic_api:sonnet")
    assert doc.pages[0].properties["ocr_fallback"]


# ---- 2. table re-reads: page time budget
def test_region_reads_stop_at_the_deadline(monkeypatch):
    from readanydoc.pipeline.regions import crops

    def slow(doc, page, box, prompt, spec, tag, pad=0.0):
        time.sleep(0.5 if tag == "slow" else 0)
        return ReadResult(text=tag)
    monkeypatch.setattr(crops, "read_crop", slow)
    doc, page = Document(source="x.pdf"), Page(index=0)
    t = time.monotonic()
    out = crops.read_many(doc, page, [((0, 0, 1, 1), "p", "fast"), ((0, 0, .5, .5), "p", "slow")], "x",
                          deadline=time.monotonic() + 0.2)
    assert time.monotonic() - t < 0.45
    assert out[0].text == "fast" and out[1] is None
    assert page.properties["region_reads_timed_out"] == 1


# ---- 3. long lists: rows checked against the parsed tables
def table(n):
    return "<table><tr><th>Name</th></tr>" + "".join(f"<tr><td>row {i}</td></tr>" for i in range(n)) + "</table>"


def test_row_counting():
    assert _rows_in(table(12)) == 12
    assert _rows_in("| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |") == 2


def test_short_segment_is_found_relative_to_the_others():
    parts = [({"items": list(range(20))}, {}), ({"items": list(range(3))}, {}), ({"items": list(range(19))}, {})]
    opts = {"row_check_min_rows": 10, "row_check_ratio": 0.5}
    assert short_segments(parts, [20, 20, 20], ("items",), opts) == [1]
    assert short_segments(parts, [5, 5, 5], ("items",), opts) == []  # too few rows to judge


SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string"}}}}


def test_lost_rows_are_extracted_again_and_kept_when_more():
    doc = make_doc([[("Table", table(15))], [("Table", table(15))]])

    def reply(system, user, i):
        if "do not stop early" in user:
            return {"items": [f"row {k}" for k in range(15)]}
        return {"items": ["row 0", "row 1"]}  # the first answer loses most rows
    llm = FakeLLM(reply)
    out = extract.run(doc, {"instructions": {"schema": SCHEMA}, "settings": {"array_extract": True}},
                      llm=llm, options={"segment_chars": 10**6})
    assert len(out["result"][0]["items"]) >= 15
    assert len(llm.calls) == 3  # first answer, then the two halves


def test_a_gap_that_remains_is_a_warning():
    doc = make_doc([[("Table", table(15))], [("Table", table(15))]])
    out = extract.run(doc, {"instructions": {"schema": SCHEMA}, "settings": {"array_extract": True}},
                      llm=FakeLLM([{"items": ["row 0"]}]), options={"segment_chars": 10**6})
    assert any("some items may be missing" in w for w in out.get("warnings", []))


# ---- 4. usage names the model, not the task
def test_task_usage_carries_the_model_name():
    llm = FakeLLM([{"items": []}])
    res = llm.complete("s", "u")
    assert sum_usage([res]).get("model") == res.model
