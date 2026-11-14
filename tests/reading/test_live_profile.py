"""Workstream A: live profile plumbing without any model call (fake inner reader)."""

import pytest

from readanydoc.pipeline import Pipeline
from readanydoc.pipeline.grounding.sources import tesseract_available
from readanydoc.profiles import get_profile
from readanydoc.readers import make_reader, make_word_provider
from readanydoc.readers.base import Reader, ReaderError, ReadResult
from readanydoc.readers.live_cache import LiveCacheReader
from readanydoc.serialize import to_parse_response

from ..conftest import needs_spike


class Fake(Reader):
    name = "fake"

    def __init__(self, texts):
        self.texts, self.calls = list(texts), 0

    def read(self, image=None, hint=None, context=None):
        self.calls += 1
        return ReadResult(text=self.texts[min(self.calls - 1, len(self.texts) - 1)], stop_reason="end_turn")


def test_live_cache_keys_by_image_prompt_and_sample(tmp_path, monkeypatch):
    monkeypatch.setenv("READANYDOC_CALL_CACHE", str(tmp_path))
    monkeypatch.delenv("READANYDOC_NO_LIVE", raising=False)
    inner = Fake(["first", "second"])
    a, b = LiveCacheReader("haiku", "1", inner=inner), LiveCacheReader("haiku", "2", inner=inner)
    assert a.read(b"img").text == "first" and inner.calls == 1
    again = a.read(b"img")
    assert again.text == "first" and again.usage.cached and inner.calls == 1  # replayed
    assert b.read(b"img").text == "second" and inner.calls == 2  # another sample = an independent read
    assert a.read(b"other image").text == "second" and inner.calls == 3
    monkeypatch.setenv("READANYDOC_NO_LIVE", "1")
    assert a.read(b"img").text == "first"
    with pytest.raises(ReaderError):
        a.read(b"never seen")
    assert make_reader("live_cache:haiku#2").name == "live_cache:haiku#2"
    assert make_word_provider("tesseract").name == "tesseract"


def test_empty_live_output_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("READANYDOC_CALL_CACHE", str(tmp_path))
    inner = Fake(["", "good"])
    r = LiveCacheReader("haiku", "1", inner=inner)
    assert r.read(b"img").text == "" and r.read(b"img").text == "good" and inner.calls == 2


@needs_spike
@pytest.mark.skipif(not tesseract_available(), reason="tesseract binary not installed")
def test_live_quality_profile_on_a_plain_pdf_path(tmp_path, monkeypatch, five_pages):
    """Any PDF path, no cached adapters, no layout: two reads, selection, Reducto-shaped output."""
    import shutil

    from readanydoc import paths
    from readanydoc.readers import READERS
    monkeypatch.setenv("READANYDOC_CALL_CACHE", str(tmp_path / "calls"))
    pdf = tmp_path / "some_document.pdf"  # not a bench stem
    shutil.copy(paths.bench_pdf(five_pages[0]), pdf)
    raw = (paths.spike_dir() / "out" / "haiku_hires" / f"{five_pages[0]}.md").read_text()
    truncated = raw[: len(raw) // 3]
    fakes = {"1": Fake([truncated]), "2": Fake([raw])}
    monkeypatch.setitem(READERS, "live_cache",
                        lambda arg: LiveCacheReader("haiku", arg.partition("#")[2], inner=fakes[arg.partition("#")[2]]))
    from readanydoc.pipeline.read import Read
    monkeypatch.setattr(Read, "_readers", {})
    doc = Pipeline(get_profile("live-quality")).run(str(pdf))
    page = doc.pages[0]
    assert page.words.get("tesseract"), "live OCR evidence missing"
    reads = page.properties["reads"]
    assert [r["source"] for r in reads] == ["live_cache:haiku#1", "live_cache:haiku#2"]
    assert reads[1]["selected"], "the complete read must win over the truncated one"
    content = to_parse_response(doc)["result"]["chunks"][0]["content"]
    assert "".join(content.split()) == "".join(raw.split())
