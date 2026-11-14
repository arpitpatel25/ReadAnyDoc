"""Workstream B: tokenisation, alignment, Ground stage, citations, OCR data, confidence."""

import json

import jsonschema
import pytest

from readanydoc import paths
from readanydoc.model import BBox, Document, Page, Region, RegionType, Word
from readanydoc.pipeline.ground import Ground
from readanydoc.pipeline.grounding.align import align
from readanydoc.pipeline.grounding.tables import parse_pipe_table_cells, parse_table_cells
from readanydoc.pipeline.grounding.tokens import normalize, scan
from readanydoc.serialize import to_parse_response

from ..conftest import needs_spike

SPEC = paths.reference_dir() / "openapi.json"


# ---------- helpers ----------

def put(words, text, x, y, h=0.012, cw=0.008, gap=0.006, source="pdf", line=None):
    """Lay ``text`` out on one baseline starting at (x, y); returns the x after the last word."""
    for t in text.split():
        w = cw * len(t)
        words.append(Word(text=t, bbox=BBox(left=x, top=y, width=w, height=h), source=source, line=line))
        x += w + gap
    return x


def make_doc(words, regions, source="pdf", usable=True):
    page = Page(index=0, original_page=1, width=600, height=800, kind="digital" if source == "pdf" else "scanned",
                text_layer_usable=usable, words={source: words}, regions=regions)
    return Document(pages=[page])


def ground(doc, **opts):
    return Ground(tesseract="off", **opts).run(doc, None)


def boxes(region):
    return {w.text: w.bbox for w in region.iter_words() if w.bbox is not None}


# ---------- tokens ----------

def test_scan_masks_markup_and_keeps_offsets():
    c = '## **Total** due: <b>$1,575.00</b> &amp; see [note](http://x.y/z) \\frac{a}{b} <!-- hi there -->'
    toks, words = scan(c)
    assert [c[t.start:t.end] for t in toks] == ["Total", "due", "1", "575", "00", "see", "note", "a", "b"]
    texts = [c[w.start:w.end] for w in words]
    assert texts[:3] == ["Total", "due:", "$1,575.00"]
    assert all(c[t.start:t.end].lower() == t.norm for t in toks)


def test_scan_skips_reader_notes_and_splits_cjk():
    assert scan("*[Figure: a red bar chart of sales]*")[0] == []
    assert scan("word [illegible] next")[0][1].norm == "next"
    toks, words = scan("日本語 text")
    assert [t.norm for t in toks] == ["日", "本", "語", "text"] and len(words) == 2


def test_normalize_ligatures_accents_digits():
    assert normalize("ﬁnal") == "final" and normalize("Café") == "cafe" and normalize("٢٠٢٤") == "2024"


# ---------- alignment ----------

def test_align_hyphenation_fuzzy_and_numbers():
    rt = ["the", "administered", "dose", "was", "1", "234", "56", "reduction"]
    ct = ["the", "ad", "ministered", "dose", "was", "1", "234", "56", "reducrion"]
    m = align(rt, ct)
    assert m[1].cand == (1, 2) and m[1].kind == "merge"
    assert m[7].cand == (8,) and m[7].kind == "fuzzy"
    assert [m[i].cand for i in (4, 5, 6)] == [(5,), (6,), (7,)]


def test_align_never_pairs_different_numbers_or_short_tokens_by_similarity():
    assert align(["1234"], ["1235"]) == {}
    assert align(["of"], ["or"]) == {}
    assert align(["alpha", "house"], ["gamma", "delta"]) == {}


def test_align_slot_needs_matched_neighbours_on_one_line():
    rt, ct = ["paid", "to", "him", "today"], ["paid", "lo", "him", "today"]
    assert align(rt, ct, same_line=lambda a, b: True)[1].kind == "slot"
    assert 1 not in align(rt, ct, same_line=lambda a, b: False)


def test_align_residual_finds_text_out_of_order():
    rt = "first cell wraps here second cell also wraps".split()
    ct = "first cell second cell wraps here also wraps".split()
    m = align(rt, ct)
    assert len(m) == len(rt)
    assert sorted(c for x in m.values() for c in x.cand) == list(range(len(ct)))


def test_untrusted_source_gives_only_long_exact_runs():
    assert align(["alpha", "beta"], ["alpha", "beta"], strict=True) == {}
    assert len(align(["alpha", "beta", "gamma"], ["x", "alpha", "beta", "gamma"], strict=True)) == 3


# ---------- Ground stage on synthetic pages ----------

def test_two_columns_repeated_words_each_cited_once():
    words = []
    left = ["the quick brown fox jumps", "over the lazy dog today"]
    right = ["the quick brown cat sleeps", "under the lazy sun today"]
    for k in range(2):
        put(words, left[k], 0.05, 0.10 + 0.02 * k)
        put(words, right[k], 0.55, 0.10 + 0.02 * k)
    r1 = Region(content=" ".join(left), bbox=BBox(left=0.04, top=0.09, width=0.42, height=0.05),
                meta={"bbox_source": "layout"})
    r2 = Region(content=" ".join(right))  # no box at all
    doc = ground(make_doc(words, [r1, r2]))
    b1, b2 = boxes(r1), boxes(r2)
    assert len(b1) == len(set(left[0].split() + left[1].split()))
    assert all(b.left < 0.5 for b in b1.values()) and all(b.left > 0.5 for b in b2.values())
    assert r2.bbox is not None and r2.bbox.left > 0.5 and r2.meta["bbox_source"] == "ground"
    used = [(round(w.bbox.left, 4), round(w.bbox.top, 4)) for r in (r1, r2) for w in r.iter_words() if w.bbox]
    assert len(used) == len(set(used)) == 20
    assert doc.pages[0].properties["ground_coverage"] == 1.0


def test_text_absent_from_the_page_gets_no_box():
    words = []
    put(words, "invoice number 4471 dated march", 0.1, 0.1)
    r = Region(content="Invoice number 4471 dated March, payable immediately upon receipt.",
               bbox=BBox(left=0.05, top=0.09, width=0.9, height=0.04), meta={"bbox_source": "layout"})
    invented = Region(content="A paragraph the reader made up entirely by itself.")
    ground(make_doc(words, [r, invented]))
    cited = boxes(r)
    assert set(cited) == {"Invoice", "number", "4471", "dated", "March"}
    assert not boxes(invented) and invented.bbox is None
    assert invented.confidence.label == "low"


def test_tiny_repeated_block_is_placed_by_reading_order():
    words = []
    put(words, "3", 0.5, 0.05)                      # page number at the top
    put(words, "section one has some body text", 0.1, 0.20)
    put(words, "3", 0.1, 0.24)                      # the list marker we want
    put(words, "section two has other body text", 0.1, 0.28)
    regs = [Region(content="Section one has some body text"), Region(content="3"),
            Region(content="Section two has other body text")]
    ground(make_doc(words, regs))
    assert abs(regs[1].bbox.top - 0.24) < 1e-6


def test_table_cells_spans_and_wrapped_cells():
    words = []
    put(words, "Item", 0.10, 0.10); put(words, "Price", 0.50, 0.10)
    put(words, "Long name that", 0.10, 0.14); put(words, "12.50", 0.50, 0.14)
    put(words, "wraps below", 0.10, 0.16)
    put(words, "Short", 0.10, 0.20); put(words, "7.00", 0.50, 0.20)
    put(words, "Total", 0.10, 0.24)
    html = ("<table><tr><th>Item</th><th>Price</th></tr>"
            "<tr><td>Long name that wraps below</td><td>12.50</td></tr>"
            "<tr><td>Short</td><td>7.00</td></tr>"
            "<tr><td colspan=\"2\">Total</td></tr></table>")
    r = Region(type=RegionType.TABLE, content=html, bbox=BBox(left=0.08, top=0.09, width=0.6, height=0.18),
               meta={"bbox_source": "layout"})
    doc = make_doc(words, [r])
    ground(doc)
    cells = {c.text: c for c in r.cells}
    wrap = cells["Long name that wraps below"]
    assert wrap.bbox is not None and wrap.bbox.top < 0.141 and wrap.bbox.bottom > 0.17 and wrap.bbox.right < 0.45
    assert abs(cells["12.50"].bbox.left - 0.50) < 1e-6 and abs(cells["12.50"].bbox.top - 0.14) < 1e-6
    doc.meta["parse_options"] = {"return_citations": True}
    extra = to_parse_response(doc)["result"]["chunks"][0]["blocks"][0]["extra"]
    by_text = {c["text"]: c for c in extra["cell_citations"]}
    assert by_text["Total"]["row_idx"] == [3] and by_text["Total"]["col_idx"] == [0, 1]
    assert by_text["7.00"]["row_idx"] == [2] and by_text["7.00"]["col_idx"] == [1]
    for c in extra["word_citations"]:
        assert html[c["char_start"]:c["char_end"]] == c["text"] and "<" not in c["text"]


def test_table_cell_parsers():
    cells = parse_table_cells('<table><tr><td rowspan="2">a</td><td>b</td></tr><tr><td>c</td></tr></table>')
    assert [(c.row, c.col, c.rowspan) for c in cells] == [(0, 0, 2), (0, 1, 1), (1, 1, 1)]
    md = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    cells = parse_pipe_table_cells(md)
    assert [(c.row, c.col, c.text) for c in cells] == [(0, 0, "A"), (0, 1, "B"), (1, 0, "1"), (1, 1, "2")]
    assert all(md[c.char_start:c.char_end] == c.text for c in cells)


def test_ocr_source_fuzzy_and_non_latin():
    words = []
    put(words, "Tbe patient was adrnitted on Monday", 0.1, 0.1, source="ppocr", line=0)
    for k, ch in enumerate("東京都港区"):
        words.append(Word(text=ch, bbox=BBox(left=0.1 + 0.02 * k, top=0.2, width=0.018, height=0.018),
                          source="ppocr", line=1))
    r1 = Region(content="The patient was admitted on Monday")
    r2 = Region(content="**東京都港区**")
    ground(make_doc(words, [r1, r2], source="ppocr", usable=False))
    assert len(boxes(r1)) == 6
    assert [w.evidence[0].note for w in r1.iter_words()][3] == "fuzzy"
    assert len(boxes(r2)) == 5 and abs(boxes(r2)["港"].left - 0.16) < 1e-6
    assert r1.confidence.score < 1.0


# ---------- serializer ----------

def simple_doc():
    words = []
    put(words, "Hello wide world", 0.1, 0.1)
    return ground(make_doc(words, [Region(content="# Hello *wide* world"), Region(content="Nowhere to be found")]))


def test_citations_only_on_request_and_offsets_exact():
    doc = simple_doc()
    plain = to_parse_response(doc)
    assert plain["result"]["ocr"] is None
    assert "word_citations" not in plain["result"]["chunks"][0]["blocks"][0]["extra"]
    resp = to_parse_response(doc, return_citations=True, return_ocr_data=True)
    b0, b1 = resp["result"]["chunks"][0]["blocks"]
    assert [(c["text"], c["char_start"], c["char_end"]) for c in b0["extra"]["word_citations"]] == \
        [("Hello", 2, 7), ("wide", 9, 13), ("world", 15, 20)]
    assert set(b0["extra"]["word_citations"][0]["bbox"]) == {"left", "top", "width", "height", "page", "original_page"}
    assert b0["extra"]["bbox_source"] == "ground" and b0["confidence"] == "high"
    # no evidence: no citations, an estimate (the part of the page below the block before it), marked low
    assert b1["extra"]["word_citations"] == [] and b1["extra"]["bbox_source"] == "estimate:below_previous"
    assert b1["bbox"]["top"] >= b0["bbox"]["top"] + b0["bbox"]["height"] - 1e-6 and b1["bbox"]["width"] == 1.0
    assert b1["confidence"] == "low" and b1["granular_confidence"]["parse_confidence"] <= 0.5
    ocr = resp["result"]["ocr"]
    assert [w["text"] for w in ocr["words"]] == ["Hello", "wide", "world"] and len(ocr["lines"]) == 1
    assert ocr["words"][0]["rotation"] == 0 and ocr["words"][0]["chunk_index"] == 0


@pytest.mark.skipif(not SPEC.exists(), reason="reference/openapi.json not available")
def test_response_with_citations_and_ocr_validates():
    spec = json.loads(SPEC.read_text())
    schema = {"$ref": "#/components/schemas/ParseResponse", "components": spec["components"]}
    resp = to_parse_response(simple_doc(), return_citations=True, return_ocr_data=True)
    jsonschema.Draft202012Validator(schema).validate(resp)


def test_citation_reanchored_when_content_changes_after_grounding():
    doc = simple_doc()
    r = doc.pages[0].regions[0]
    r.content = "Title: Hello wide world"
    cites = to_parse_response(doc, return_citations=True)["result"]["chunks"][0]["blocks"][0]["extra"]["word_citations"]
    assert [c["text"] for c in cites] == ["Hello", "wide", "world"]
    assert all(r.content[c["char_start"]:c["char_end"]] == c["text"] for c in cites)


# ---------- cached bench pages ----------

@needs_spike
def test_dev_pages_coverage_and_correctness():
    from readanydoc_research.bench import ground as G
    from readanydoc_research.bench import olm
    from readanydoc.pipeline import Pipeline
    from readanydoc.profiles import get_profile
    pipe = Pipeline(get_profile("cached-haiku").with_stage("ground", tesseract="off"))
    stats, cov = {}, []
    for stem in olm.dev_subset():
        doc = pipe.run(stem)
        page = doc.pages[0]
        seen = set()
        for r in page.regions:
            for w in r.iter_words():
                assert r.content[w.char_start:w.char_end] == w.text
                if w.bbox is not None:
                    assert -1e-6 <= w.bbox.left and w.bbox.right <= 1 + 1e-6 and w.bbox.bottom <= 1 + 1e-6
            if r.bbox is not None:
                assert 0 <= r.bbox.left and r.bbox.right <= 1 + 1e-6 and r.bbox.area > 0
        if page.properties.get("text_layer_class") == "trustworthy":
            c = G.page_coverage(page)
            cov.append(c["cited"] / max(c["words"], 1))
            for k, v in G.digital_correctness(doc).items():
                stats[k] = stats.get(k, 0) + v
        del seen
    assert sum(cov) / len(cov) >= 0.95
    assert stats["unique_ok"] / stats["unique"] >= 0.99
    assert stats["any_ok"] / stats["any"] >= 0.97


@needs_spike
def test_ground_report_runs_on_two_pages():
    from readanydoc_research.bench import ground as G
    from readanydoc_research.bench import olm
    rep = G.ground_report("cached-haiku", olm.dev_subset()[:2], tesseract=False)
    assert rep["pages"] == 2 and "confidence" in rep and G.format_report(rep)
