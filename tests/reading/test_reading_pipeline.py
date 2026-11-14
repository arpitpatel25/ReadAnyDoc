"""Workstream A integration tests on cached bench pages (skipped without the spike data)."""

import pytest

from readanydoc import paths
from readanydoc.model import BBox, RegionType
from readanydoc.pipeline import Pipeline
from readanydoc.profiles import get_profile
from readanydoc.readers.base import ReadContext, Reader, ReaderError, ReadResult
from readanydoc.readers.crop_cache import CropCacheReader, crop_key
from readanydoc.serialize import document_markdown, to_parse_response

from ..conftest import needs_spike


@pytest.fixture(scope="module")
def quality_docs(five_pages):
    pipe = Pipeline(get_profile("quality"))
    return {s: pipe.run(s) for s in five_pages}


@pytest.fixture(scope="module")
def cheap_docs(five_pages):
    pipe = Pipeline(get_profile("cheap"))
    return {s: pipe.run(s) for s in five_pages}


@needs_spike
def test_quality_profile_keeps_every_read_and_chooses_one(quality_docs):
    for stem, doc in quality_docs.items():
        page = doc.pages[0]
        reads = page.properties["reads"]
        assert [r["source"] for r in reads] == ["cached:haiku_hires", "cached:haiku"]
        assert sum(r["selected"] for r in reads) == 1
        assert all(r["content"] for r in reads), "no model output may be dropped"
        assert page.properties["read_selection"]["chosen"] in {r["source"] for r in reads}
        assert doc.meta["cost"]["est_cost_usd"] >= 0.016  # two strong page reads
        to_parse_response(doc)


@needs_spike
def test_quality_output_is_one_of_the_reads_outside_replaced_tables(quality_docs):
    """Selection never merges reads: every untouched block is verbatim from the chosen read."""
    squash = lambda s: "".join(s.split())
    for stem, doc in quality_docs.items():
        page = doc.pages[0]
        chosen = squash(next(r["content"] for r in page.properties["reads"] if r["selected"]))
        for r in page.regions:
            if r.meta.get("from_layout_only") or r.meta.get("table_selected") or r.meta.get("fusion_edits"):
                continue
            assert squash(r.content) in chosen
        for r in page.regions:
            if r.meta.get("table_selected"):
                assert any(c.meta.get("replaced") for c in r.candidates), "replaced table text must be kept"


@needs_spike
def test_cheap_profile_reports_cost_and_escalations(cheap_docs):
    for stem, doc in cheap_docs.items():
        cost = doc.meta["cost"]
        assert cost["est_cost_usd"] >= 0.0002
        esc = [u for u in doc.usage if u.get("escalation")]
        assert cost["page_escalations"] + cost["region_escalations"] == len(esc)
        for u in esc:
            assert u["reason"] and 0 < u["area"] <= 1.0
        for r in doc.pages[0].regions:
            if r.meta.get("escalated"):
                assert r.type == RegionType.TABLE and "<table" in r.content
                assert r.meta["tier"] == "strong"
        assert document_markdown(doc).strip()


@needs_spike
def test_cheap_without_crops_keeps_the_fast_read(five_pages, monkeypatch):
    """A missing crop read must never be an accidental model call or a lost table."""
    monkeypatch.delenv("READANYDOC_LIVE", raising=False)
    cfg = get_profile("cheap")
    cfg.stages["read"].options["escalate"]["table_reader"] = "crop_cache:no_such_model"
    cfg.stages["read"].options["escalate"]["pages"] = False
    cfg.stages["verify"].options["fusion"] = False
    cfg.parse.filter_blocks = ["Header", "Footer", "Page Number"]
    base = get_profile("cached-glm")
    base.parse.filter_blocks = ["Header", "Footer", "Page Number"]
    a, b = Pipeline(cfg), Pipeline(base)
    for stem in five_pages:
        doc = a.run(stem)
        assert document_markdown(doc) == document_markdown(b.run(stem))
        assert doc.meta["cost"]["region_escalations"] == 0


@needs_spike
def test_baseline_profiles_do_not_fuse(haiku_docs):
    for doc in haiku_docs.values():
        assert not any(r.meta.get("fusion_edits") for r in doc.pages[0].regions)
        assert doc.pages[0].properties["text_layer_class"] in {
            "none", "trustworthy", "ocr_layer", "stale_ocr", "garbage", "unchecked"}


@needs_spike
def test_pipeline_code_never_reads_the_bench_tests():
    """Guard against overfitting: reading code must not open the olmOCR test files."""
    root = paths.REPO_ROOT / "readanydoc"
    files = list((root / "pipeline").rglob("*.py")) + list((root / "readers").rglob("*.py"))
    assert files
    for f in files:
        src = f.read_text()
        assert ".jsonl" not in src and "olmbench" not in src and "bench.olm" not in src, f


class _Inner(Reader):
    name = "inner"

    def __init__(self):
        self.calls = 0

    def read(self, image=None, hint=None, context=None):
        self.calls += 1
        return ReadResult(text="<table><tr><td>a</td></tr></table>", stop_reason="end_turn")


def test_crop_cache_replays_and_never_calls_live_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("READANYDOC_SPIKE_DIR", str(tmp_path))
    monkeypatch.delenv("READANYDOC_LIVE", raising=False)
    box = BBox(left=0.1, top=0.2, width=0.5, height=0.25)
    ctx = ReadContext(stem="doc_pg1", bbox=box)
    assert crop_key("doc_pg1", box) == "doc_pg1__0100_0200_0600_0450"
    offline = CropCacheReader("haiku")
    assert not offline.needs_image
    with pytest.raises(ReaderError, match="no cached crop"):
        offline.read(None, RegionType.TABLE, ctx)
    with pytest.raises(ReaderError):
        offline.read(None, RegionType.TABLE, ReadContext(stem="doc_pg1"))  # page-level: not a crop
    live = CropCacheReader("haiku", live=True)
    live._inner = _Inner()
    first = live.read(b"png-bytes", RegionType.TABLE, ctx)
    assert live._inner.calls == 1 and not first.usage.cached
    again = offline.read(None, RegionType.TABLE, ctx)  # now replayed from disk, no call
    assert again.text == first.text and again.usage.cached and live._inner.calls == 1


class _Broken(Reader):
    name = "broken"
    needs_image = False

    def read(self, image=None, hint=None, context=None):
        return ReadResult(text="", stop_reason="end_turn")


@needs_spike
def test_broken_reader_falls_back_to_an_exact_text_layer(monkeypatch):
    """Empty model output on a born-digital page: the page still gets its text, flagged."""
    from readanydoc_research.bench import olm
    from readanydoc.pipeline.base import PipelineContext
    probe = Pipeline(get_profile("cached-glm").with_stage("read", enabled=False))
    stem = next(s for s in olm.dev_subset() if probe.run(s).pages[0].properties.get("text_layer_exact"))
    monkeypatch.setattr(PipelineContext, "reader", property(lambda self: _Broken()))
    doc = Pipeline(get_profile("cached-glm")).run(stem)
    page = doc.pages[0]
    assert page.regions and page.properties["reads"][0]["source"] == "pdf_text_layer"
    assert any("PDF text layer used instead" in n.message for n in doc.notes)
    first_words = [w.text for w in page.words["pdf"][:5]]
    assert all(w in document_markdown(doc) for w in first_words)


@needs_spike
def test_live_readers_get_a_native_size_page_image(five_pages, tmp_path, monkeypatch):
    """Pre-size to the model's limit (BUILD_SPEC section 3): no oversized image reaches a provider."""
    import io

    from PIL import Image

    from readanydoc.pipeline.read import Read
    from readanydoc.readers.sizing import fits
    monkeypatch.setenv("READANYDOC_CACHE_DIR", str(tmp_path))
    doc = Pipeline(get_profile("cached-glm").with_stage("read", enabled=False)).run(five_pages[1])

    class NeedsImage(_Broken):
        needs_image = True
    path = Read()._image(doc, doc.pages[0], NeedsImage())
    with Image.open(path) as im:
        assert fits(*im.size) and max(im.size) > 1500
