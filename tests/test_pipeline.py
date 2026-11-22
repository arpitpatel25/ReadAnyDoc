import json

import pytest

from readanydoc import paths
from readanydoc.model import RegionType
from readanydoc.pipeline import (STAGE_ORDER, STAGES, Pipeline, PipelineConfig, Stage, StageConfig,
                               register_stage)
from readanydoc.pipeline.ground import parse_table_cells
from readanydoc.pipeline.mdblocks import split_markdown
from readanydoc.profiles import get_profile
from readanydoc.serialize import document_markdown, to_parse_response

from .conftest import needs_spike


def test_all_eight_stages_registered():
    Pipeline(PipelineConfig())
    assert STAGE_ORDER == ["intake", "triage", "layout", "read", "verify", "ground", "assemble", "shape"]
    for name in STAGE_ORDER:
        assert issubclass(STAGES[name], Stage) and STAGES[name].name == name


def test_split_markdown_keeps_every_character_of_each_block():
    md = ("# Title\nintro line\n\nPara one\nstill one.\n\n<table>\n<tr><td>a</td></tr>\n\n<tr><td>b</td></tr>\n"
          "</table>\n\n- item 1\n- item 2\n\n## Sub\n\n[Figure: a chart]\n")
    blocks = split_markdown(md)
    # every list item is its own block (Reducto's granularity); see tests/typing for the typing rules
    assert [b.type for b in blocks] == [RegionType.TITLE, RegionType.TEXT, RegionType.TEXT, RegionType.TABLE,
                                        RegionType.LIST_ITEM, RegionType.LIST_ITEM, RegionType.SECTION_HEADER,
                                        RegionType.FIGURE]
    assert blocks[3].text.count("<tr>") == 2  # blank line inside a table does not split it
    squash = lambda s: "".join(s.split())
    assert squash("".join(b.text for b in blocks)) == squash(md)


def test_split_markdown_types_labelled_furniture():
    md = ("<page-header>Journal of Things, Vol. 3</page-header>\nIntro text runs on.\n\n"
          "<line-number>12</line-number>\n\nBody <page-footer>broken tag\n\n<page-number>7</page-number>\n")
    blocks = split_markdown(md)
    assert [(b.type, b.text) for b in blocks] == [
        (RegionType.HEADER, "Journal of Things, Vol. 3"), (RegionType.TEXT, "Intro text runs on."),
        (RegionType.PAGE_NUMBER, "12"), (RegionType.TEXT, "Body broken tag"), (RegionType.PAGE_NUMBER, "7")]
    assert all("<page-" not in b.text and "<line-" not in b.text for b in blocks)


def test_parse_table_cells_spans():
    html = ('<table><tr><th rowspan="2">A</th><th colspan="2">B</th></tr>'
            "<tr><td>c</td><td>d</td></tr></table>")
    cells = parse_table_cells(html)
    assert [(c.row, c.col, c.rowspan, c.colspan, c.text) for c in cells] == [
        (0, 0, 2, 1, "A"), (0, 1, 1, 2, "B"), (1, 1, 1, 1, "c"), (1, 2, 1, 1, "d")]
    assert all(html[c.char_start:c.char_end] == c.text for c in cells)


@needs_spike
def test_end_to_end_on_five_cached_pages(haiku_docs, five_pages):
    assert len(haiku_docs) == 5
    for stem, doc in haiku_docs.items():
        page = doc.pages[0]
        assert set(doc.meta["stage_seconds"]) == set(STAGE_ORDER)
        assert page.kind in ("digital", "scanned") and page.words.get("ppocr")
        assert page.layout_regions, "layout stage produced nothing"
        assert page.regions and all(r.content for r in page.regions)
        assert [r.order for r in page.regions] == list(range(len(page.regions)))
        assert all(r.candidates for r in page.regions), "model output must be kept as a candidate"
        assert any(r.lines for r in page.regions), "ground stage produced no word citations"
        assert any(r.evidence for r in page.regions), "verify stage recorded no evidence"
        assert doc.chunks and len(doc.chunks) == 1
        for r in page.regions:
            for w in r.iter_words():
                assert r.content[w.char_start:w.char_end] == w.text


@needs_spike
def test_model_text_is_never_altered(five_pages):
    """Never make it worse: the scored text equals the raw model output, block for block."""
    cfg = get_profile("cached-haiku")
    cfg.parse.filter_blocks = ["Header", "Footer", "Page Number"]
    pipe = Pipeline(cfg)
    squash = lambda s: "".join(s.split())
    for stem in five_pages:
        raw = (paths.spike_dir() / "out" / "haiku" / f"{stem}.md").read_text()
        assert squash(document_markdown(pipe.run(stem))) == squash(raw)


@needs_spike
def test_glm_profile_runs(five_pages):
    pipe = Pipeline(get_profile("cached-glm"))
    for stem in five_pages[:2]:
        doc = pipe.run(stem)
        resp = to_parse_response(doc)
        assert resp["usage"]["usage_breakdown"]["parse_model"] == "R-1 Mini"
        assert resp["result"]["chunks"][0]["blocks"]


@needs_spike
@pytest.mark.parametrize("stage", [s for s in STAGE_ORDER if s != "intake"])
def test_each_stage_can_be_switched_off(stage, five_pages):
    doc = Pipeline(get_profile("cached-haiku").with_stage(stage, enabled=False)).run(five_pages[0])
    assert stage not in doc.meta["stage_seconds"]
    resp = to_parse_response(doc)  # the serializer must cope with whatever is missing
    assert resp["result"]["type"] == "full" and resp["usage"]["num_pages"] == 1
    if stage == "read":
        assert all(not b["content"] for c in resp["result"]["chunks"] for b in c["blocks"])


@needs_spike
def test_intake_cannot_be_switched_off(five_pages):
    doc = Pipeline(get_profile("cached-haiku").with_stage("intake", enabled=False)).run(five_pages[0])
    assert doc.pages


@needs_spike
def test_shape_modes(five_pages):
    stem = five_pages[0]
    base = get_profile("cached-haiku")
    block = base.model_copy(deep=True)
    block.parse.chunk_mode = "block"
    doc = Pipeline(block).run(stem)
    assert len(doc.chunks) == len(doc.pages[0].regions) > 1
    page = base.model_copy(deep=True)
    page.parse.chunk_mode = "page"
    page.parse.add_page_markers = True
    page.parse.filter_blocks = ["Text"]
    doc = Pipeline(page).run(stem)
    resp = to_parse_response(doc)
    chunk = resp["result"]["chunks"][0]
    assert chunk["content"].startswith("[[START OF PAGE 1]]") and chunk["content"].endswith("[[END OF PAGE 1]]")
    texts = [b for b in chunk["blocks"] if b["type"] == "Text"]
    assert texts and all(b["content"] not in chunk["content"] for b in texts)  # filtered from content only


@needs_spike
def test_segment_off_falls_back_to_page_level(five_pages):
    doc = Pipeline(get_profile("cached-haiku").with_stage("read", segment=False)).run(five_pages[0])
    assert len(doc.pages[0].regions) >= 1
    assert any("page-level content" in n.message for n in doc.notes)


@needs_spike
def test_table_cells_get_boxes():
    from readanydoc_research.bench import olm
    pipe = Pipeline(get_profile("cached-haiku"))
    found = 0
    for stem in olm.pages_by_cat()["table_tests"][:6]:
        for r in pipe.run(stem).pages[0].regions:
            if r.type == RegionType.TABLE and r.cells:
                found += sum(c.bbox is not None for c in r.cells)
    assert found > 20


@needs_spike
def test_real_pdf_path_and_image_inputs(five_pages, tmp_path):
    stem = five_pages[0]
    doc = Pipeline(get_profile("cached-haiku")).run(str(paths.bench_pdf(stem)))
    assert doc.file_type == "pdf" and doc.stem == stem and doc.pages[0].unit == "pt"
    img = Pipeline(get_profile("cached-haiku")).run(str(paths.bench_image(stem)))
    assert img.file_type == "png" and img.pages[0].unit == "px" and img.pages[0].kind == "scanned"
    assert img.pages[0].regions


def test_unknown_input_raises():
    from readanydoc.pipeline.intake import IntakeError
    with pytest.raises(IntakeError):
        Pipeline(PipelineConfig()).run("definitely_not_a_cached_stem")


@needs_spike
def test_stage_can_be_replaced_through_the_registry(five_pages):
    @register_stage("verify_stub_for_test")
    class Marker(Stage):
        def run(self, doc, ctx):
            doc.meta["marker"] = True
            return doc

    cfg = get_profile("cached-haiku")
    cfg.stages["verify"] = StageConfig(impl="verify_stub_for_test")
    assert Pipeline(cfg).run(five_pages[0]).meta.get("marker") is True
    del STAGES["verify_stub_for_test"]


@needs_spike
def test_cli_parse_prints_reducto_json(five_pages, capsys):
    from readanydoc.cli import main
    assert main(["parse", five_pages[0], "--profile", "cached-glm"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["response_type"] == "parse" and out["result"]["chunks"][0]["blocks"]


@needs_spike
def test_multi_page_pdf_with_page_range(five_pages, tmp_path):
    """A PDF that is not in the cache: intake/triage work, the cached reader reports what is missing."""
    import pypdfium2 as pdfium
    merged = pdfium.PdfDocument.new()
    for stem in five_pages[:3]:
        src = pdfium.PdfDocument(str(paths.bench_pdf(stem)))
        merged.import_pages(src)
    out = tmp_path / "merged.pdf"
    merged.save(str(out))
    cfg = get_profile("cached-haiku")
    cfg.parse.page_range = [2, 3]
    cfg.parse.chunk_mode = "page"
    doc = Pipeline(cfg).run(str(out))
    assert [p.original_page for p in doc.pages] == [2, 3] and doc.meta["total_pages"] == 3
    assert any(n.level == "error" and n.stage == "read" for n in doc.notes)
    resp = to_parse_response(doc)
    assert resp["usage"]["num_pages"] == 2 and len(resp["result"]["chunks"]) == 2


def test_shipped_profiles_are_the_benchmarked_configurations():
    """`engine` and `model-only` are written out in readanydoc/profiles.py; they must stay identical to the
    configurations that were benchmarked (engine-final-5 and vanilla-api in research/)."""
    from readanydoc.profiles import get_profile
    for shipped, measured in (("engine", "engine-final-5"), ("model-only", "vanilla-api")):
        a = get_profile(shipped).model_dump(exclude={"profile"})
        b = get_profile(measured).model_dump(exclude={"profile"})
        b["reader"] = b["reader"].replace("anthropic_api:haiku", "anthropic_api:claude-haiku-5-5")
        opts = b["stages"]["read"]["options"]
        if "handwriting_reader" in opts:
            opts["handwriting_reader"] = opts["handwriting_reader"].replace("anthropic_api:sonnet",
                                                                            "anthropic_api:claude-sonnet-5-5")
        assert a == b, shipped


def test_a_document_that_runs_over_its_time_limit_fails_cleanly():
    import time as _time

    import pytest

    from readanydoc.pipeline import Pipeline, PipelineTimeout
    from readanydoc.profiles import get_profile

    class Slow:
        name, enabled = "slow", True

        def run(self, doc, ctx):
            _time.sleep(2)
            return doc

    pipe = Pipeline(get_profile("model-only"))
    pipe.stages = [Slow()]
    with pytest.raises(PipelineTimeout):
        pipe.run("x.pdf", timeout=0.2)
