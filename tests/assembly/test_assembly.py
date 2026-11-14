import pytest

from readanydoc.model import BBox, Document, Line, Page, Region, RegionType, Word
from readanydoc.pipeline import PipelineConfig, PipelineContext
from readanydoc.pipeline.assemble import Assemble
from readanydoc.pipeline.assembly import headings, paragraphs
from readanydoc.pipeline.assembly import tables as TJ
from readanydoc.pipeline.shape import Shape
from readanydoc.pipeline.shaping.options import Options
from readanydoc.pipeline.shaping.tables import parse_table

from ..conftest import needs_spike
from . import evaluate, synth

X, T, S = RegionType.TEXT, RegionType.TABLE, RegionType.SECTION_HEADER


def table(header, rows, th=True):
    out = "<table>"
    if header:
        tag = "th" if th else "td"
        out += "<tr>" + "".join(f"<{tag}>{h}</{tag}>" for h in header) + "</tr>"
    out += "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return out + "</table>"


def make_doc(pages, boxes=False):
    doc = Document(source="x", file_type="pdf")
    for i, blocks in enumerate(pages):
        page = Page(index=i, original_page=i + 1)
        n = len(blocks)
        for k, (t, content) in enumerate(blocks):
            box = BBox(left=0.1, top=0.1 + 0.8 * k / n, width=0.8, height=0.8 / n, page=i) if boxes else None
            page.regions.append(Region(type=t, content=content, order=k, bbox=box))
        doc.pages.append(page)
    return doc


def run(doc, **opts):
    return Assemble(enabled=True, **opts).run(doc, PipelineContext(PipelineConfig()))


NUM = [["Alder", "1,200", "3.5%"], ["Birch", "950", "2.1%"], ["Cedar", "40", "0.4%"]]
HEAD = ["Item", "Units", "Share"]

# ---------------------------------------------------------------- tables


def judge(a, b):
    return TJ.judge(parse_table(a), parse_table(b))


def test_table_join_rules():
    a = table(HEAD, NUM)
    assert judge(a, table(HEAD, NUM)).drop_rows == 1 and judge(a, table(HEAD, NUM)).score >= 0.85
    missing = judge(a, table(None, NUM))
    assert missing.score >= 0.85 and missing.drop_rows == 0
    assert judge(a, table(["Item", "Units", "Price"], NUM)).score < 0.5  # one header cell differs: new table
    assert judge(a, table(["Name", "Qty", "Pct"], NUM)).score < 0.5
    assert judge(a, table(None, [["Alder", "1", "2", "3"]])).score == 0.0  # column count
    assert judge(a, table(None, [["north", "south", "east"], ["a", "b", "c"]])).score < 0.5  # types differ
    # a model that marks the first data row of a fragment as <th>: still data
    assert judge(a, table(NUM[0], NUM[1:])).score >= 0.85
    # text-only columns without boxes: not enough evidence, recorded low
    words = [["Alder", "red", "tall"], ["Birch", "white", "thin"]]
    weak = judge(table(["Tree", "Bark", "Shape"], words), table(None, words))
    assert 0.5 <= weak.score < 0.85


def test_merge_tables_records_fragments_and_keeps_content():
    a, b, c = table(HEAD, NUM), table(HEAD, NUM[:2]), table(None, NUM[:1])
    doc = make_doc([[(X, "Intro text."), (T, a)], [(T, b)], [(T, c), (X, "After.")]], boxes=True)
    run(doc, merge_tables=True)
    first, second, third = doc.pages[0].regions[1], doc.pages[1].regions[0], doc.pages[2].regions[0]
    assert [l.kind for l in doc.links] == ["table_continuation"] * 2
    merged = parse_table(first.meta["merged_content"])
    assert merged.n_rows == 1 + 3 + 2 + 1 and merged.header_rows == 1
    assert [m["content"] for m in first.meta["merged_tables"]] == [a, b, c]
    assert [m["bbox"]["page"] for m in first.meta["merged_tables"]] == [1, 2, 3]
    assert second.meta["merged_into"] == third.meta["merged_into"] == first.id
    assert (first.content, second.content, third.content) == (a, b, c)
    out = Shape(enabled=True, table_output_format="html").run(doc, PipelineContext(PipelineConfig()))
    chunk = out.chunks[0]
    assert chunk.content.count("<table>") == 1 and chunk.content.count("Alder") == 3
    assert second.id not in chunk.region_ids and third.id not in chunk.region_ids
    assert first.meta["shaped_content"] == first.meta["merged_content"]


def test_merge_is_off_by_default_and_follows_the_request():
    pages = [[(T, table(HEAD, NUM))], [(T, table(HEAD, NUM))]]
    doc = run(make_doc(pages))
    assert len(doc.links) == 1 and "merged_content" not in doc.pages[0].regions[0].meta
    doc = run(make_doc(pages), request={"formatting": {"merge_tables": True}})
    assert "merged_content" in doc.pages[0].regions[0].meta


def test_table_join_needs_adjacent_pages_and_boundary_position():
    a = table(HEAD, NUM)
    doc = run(make_doc([[(T, a), (X, "A closing paragraph.")], [(T, a)]]))
    assert not doc.links
    doc = make_doc([[(T, a)], [(T, a)]])
    doc.pages[1].original_page = 5  # page_range skipped pages
    assert not run(doc).links
    doc = run(make_doc([[(T, a)], [(X, "Table 2 (continued)"), (T, a)]]))
    assert len(doc.links) == 1

# ---------------------------------------------------------------- paragraphs


LONG = "The committee reviewed the proposal in detail and decided that the remaining budget should be moved to"


@pytest.mark.parametrize("end,start,high", [
    (LONG, "the next fiscal year without delay.", True),
    (LONG + " inter-", "national programmes.", True),
    (LONG + " the next year.", "the following points were raised.", False),
    (LONG, "The following points were raised.", False),
    (LONG, "- first item", False),
    ("Summary of results", "the next section explains them.", False),
    (LONG + " the next year:", "these were the reasons.", False),
])
def test_paragraph_judgement(end, start, high):
    score, _ = paragraphs.judge(end, start)
    assert (score >= 0.85) == high


def test_paragraph_links_skip_furniture_and_shape_joins_on_request():
    doc = make_doc([[(X, "Opening paragraph."), (X, LONG), (RegionType.FOOTER, "Confidential"),
                     (RegionType.PAGE_NUMBER, "1")],
                    [(RegionType.HEADER, "Report"), (X, "the next fiscal year."), (X, "Another one.")]])
    run(doc)
    link = [l for l in doc.links if l.kind == "paragraph_continuation"]
    assert len(link) == 1 and link[0].confidence.label == "high"
    ctx = PipelineContext(PipelineConfig())
    filt = ["Header", "Footer", "Page Number"]
    apart = Shape(enabled=True, filter_blocks=filt).run(doc, ctx).chunks[0].content
    assert LONG + "\n\nthe next fiscal year." in apart  # default: blocks stay apart
    joined = Shape(enabled=True, filter_blocks=filt, join_paragraphs=True).run(doc, ctx).chunks[0].content
    assert LONG + " the next fiscal year." in joined
    kept = Shape(enabled=True, join_paragraphs=True).run(doc, ctx).chunks[0].content
    assert LONG + "\n\nConfidential" in kept  # furniture shown between them: no join
    assert doc.pages[0].regions[1].content == LONG

# ---------------------------------------------------------------- headings


def test_numbering_depth():
    assert headings.numbering_depth("1 Introduction") == 1
    assert headings.numbering_depth("2.3 Methods") == 2 and headings.numbering_depth("2.3.1. Details") == 3
    for no in ("2024 results", "Introduction", "3 000 units sold", "12 people were asked about the new policy and"
               " most of them said that they liked it a lot"):
        assert headings.numbering_depth(no) is None


def test_heading_levels_from_numbering_and_markdown_rewrite():
    doc = make_doc([[(RegionType.TITLE, "# Annual report"), (S, "## 1 Scope"), (S, "## 1.1 Limits")],
                    [(S, "## 1.1.1 Details"), (S, "# 2 Method"), (S, "## Appendix A")]])
    run(doc)
    levels = [r.heading_level for r in doc.iter_regions()]
    assert levels == [1, 2, 3, 4, 2, 2]
    tree = doc.heading_tree
    assert len(tree) == 1 and [c.title for c in tree[0].children] == ["1 Scope", "2 Method", "Appendix A"]
    assert tree[0].children[0].children[0].children[0].title == "1.1.1 Details"
    out = Shape(enabled=True).run(doc, PipelineContext(PipelineConfig())).chunks[0].content
    assert "### 1.1 Limits" in out and "#### 1.1.1 Details" in out and "\n\n## 2 Method" in out
    assert doc.pages[0].regions[2].content == "## 1.1 Limits"
    flat = Shape(enabled=True, heading_levels=False).run(doc, PipelineContext(PipelineConfig())).chunks[0].content
    assert "\n\n## 1.1 Limits" in flat


def test_headings_without_evidence_are_left_alone():
    doc = make_doc([[(RegionType.TITLE, "# Paper"), (S, "## Background"), (S, "### Data"), (S, "## Results")]])
    run(doc)
    assert [r.heading_level for r in doc.iter_regions()] == [1, 2, 3, 2]
    assert not any("heading_level_source" in r.meta for r in doc.iter_regions())
    doc = make_doc([[(S, "## 3 apples and 4 pears"), (S, "## 1 lemon")]])  # numbers that do not count up
    run(doc)
    assert [r.heading_level for r in doc.iter_regions()] == [2, 2]
    assert run(make_doc([[(S, "## A")]]), headings=False).heading_tree == []


def test_heading_levels_from_font_size():
    doc = make_doc([[(S, "## Big"), (S, "## Small"), (S, "## Big again"), (S, "## Small again")]])
    for r, h in zip(doc.iter_regions(), (0.03, 0.02, 0.03, 0.02)):
        r.lines = [Line(words=[Word(text="w", bbox=BBox(left=0.1, top=0.1, width=0.1, height=h), bbox_source="pdf")])]
    run(doc)
    assert [r.heading_level for r in doc.iter_regions()] == [2, 3, 2, 3]
    assert run(make_doc([[(S, "## Big"), (S, "## Small")]]), heading_font_size=False).heading_tree[0].level == 2

# ---------------------------------------------------------------- furniture


def furniture_doc(n=4):
    pages = []
    for i in range(n):
        pages.append([(X, "ACME Corp annual report 2024"), (X, f"Body text of page {i} with its own words {i * 37}."),
                      (X, f"Total {i * 11}"), (X, f"Page {i + 1} of {n}")])
    doc = make_doc(pages)
    for p in doc.pages:
        tops = (0.03, 0.3, 0.6, 0.95)
        for r, top in zip(p.regions, tops):
            r.bbox = BBox(left=0.1, top=top, width=0.5, height=0.03, page=p.index)
    return doc


def test_repeated_furniture():
    doc = run(furniture_doc())
    assert [[r.type.value for r in p.regions] for p in doc.pages] == [["Header", "Text", "Text", "Page Number"]] * 4
    assert doc.pages[0].regions[0].meta["type_before_assemble"] == "Text"
    assert all(r.type == X for r in run(furniture_doc(2)).iter_regions())  # too few pages to tell
    assert all(r.type == X for r in run(furniture_doc(), repeated_furniture=False).iter_regions())


def test_repeated_numbers_in_the_body_or_not_counting_are_not_furniture():
    doc = furniture_doc()
    for p in doc.pages:
        p.regions[3].content = "42"  # same bare number on every page: data, not a page number
    run(doc)
    assert all(p.regions[3].type == X for p in doc.pages)
    assert all(p.regions[2].type == X for p in doc.pages)  # "Total N" sits mid-page

# ---------------------------------------------------------------- synthetic PDFs


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    return evaluate.evaluate(tmp_path_factory.mktemp("synth"), heading_style="per_page")


def test_synthetic_set_precision_recall(synthetic):
    res = synthetic
    assert res["documents"] >= 35 and res["paragraph"]["tp"] + res["paragraph"]["fn"] >= 10
    assert res["table"]["tp"] + res["table"]["fn"] >= 8
    for kind in ("paragraph", "table", "furniture"):
        assert res[kind]["precision"] >= 0.98, (kind, res["misses"])
    assert res["table"]["recall"] >= 0.85 and res["furniture"]["recall"] >= 0.95
    assert res["paragraph"]["recall"] >= 0.7  # a cut exactly at a sentence end cannot be seen from text
    hl = res["heading_levels"]
    assert hl["correct_after"] >= 0.95 * hl["n"] and hl["correct_after"] > hl["correct_before"]
    assert res["heading_edges"]["precision"] >= 0.95 and res["heading_edges"]["recall"] >= 0.95


@needs_spike
def test_synthetic_pdf_through_the_real_pipeline_stages(tmp_path):
    """Intake on a generated PDF: page_range keeps original page numbers; password and damage errors."""
    import pikepdf

    from readanydoc.pipeline import Pipeline
    from readanydoc.pipeline.intake import IntakeError
    t = synth.make_set(tmp_path)[0]
    cfg = PipelineConfig()
    cfg.parse.page_range = [2, 3, 99]
    doc = Pipeline(cfg).run(str(t.path))
    assert [(p.index, p.original_page) for p in doc.pages] == [(0, 2), (1, 3)] and doc.meta["total_pages"] == t.pages
    locked = tmp_path / "locked.pdf"
    with pikepdf.open(str(t.path)) as pdf:
        pdf.save(str(locked), encryption=pikepdf.Encryption(user="pw", owner="pw"))
    with pytest.raises(IntakeError, match="password"):
        Pipeline(PipelineConfig()).run(str(locked))
    cfg = PipelineConfig()
    cfg.parse.document_password = "pw"
    assert len(Pipeline(cfg).run(str(locked)).pages) == t.pages
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 nonsense")
    with pytest.raises(IntakeError) as e:
        Pipeline(PipelineConfig()).run(str(bad))
    assert "password" not in str(e.value)
