import pytest

from readanydoc.model import Document, Page, Region, RegionType
from readanydoc.pipeline import ParseOptions, Pipeline, PipelineConfig, PipelineContext
from readanydoc.pipeline.shape import Shape, build_chunks
from readanydoc.pipeline.shaping import chunking as C
from readanydoc.pipeline.shaping.options import Options, apply_request, resolve
from readanydoc.pipeline.shaping.page_range import PageRangeError, resolve_page_range

TABLE = "<table>" + "".join(f"<tr><td>row {i}</td><td>{i * 7}</td><td>note {i}</td></tr>" for i in range(60)) + "</table>"
SENT = "The quick brown fox jumps over the lazy dog near the river bank today. "


def make_doc(pages):
    doc = Document(source="x")
    for i, blocks in enumerate(pages):
        page = Page(index=i, original_page=i + 1)
        for t, content in blocks:
            page.regions.append(Region(type=t, content=content, order=len(page.regions)))
        doc.pages.append(page)
    return doc


def long_doc():
    S, X, H, T = RegionType.SECTION_HEADER, RegionType.TEXT, RegionType.TITLE, RegionType.TABLE
    return make_doc([
        [(H, "# Title"), (S, "## 1 Intro"), (X, SENT * 6), (X, SENT * 5), (S, "## 2 Methods"), (X, SENT * 30)],
        [(X, SENT * 4), (T, TABLE), (S, "## 3 Results"), (X, SENT * 3), (RegionType.FOOTER, "footer text")],
        [(S, "## 4 End"), (X, SENT * 2)],
    ])


def chunks(doc, **kw):
    return build_chunks(doc, Options(**kw))


def test_disabled_page_block():
    doc = long_doc()
    assert len(chunks(doc)) == 1
    pages = chunks(doc, chunk_mode="page")
    # a page that starts under a heading of an earlier page repeats it with "(cont.)" (Reducto does the same)
    assert len(pages) == 3 and pages[1].content.startswith("# Title (cont.)\n\n## 2 Methods (cont.)\n\n")
    assert pages[2].content.startswith("# Title (cont.)\n\n## 4 End")
    blocks = chunks(doc, chunk_mode="block")
    assert len(blocks) == sum(len(p.regions) for p in doc.pages)
    assert all(len(c.region_ids) == 1 for c in blocks)
    assert blocks[0].content == "# Title" and blocks[1].content == "# Title (cont.)\n\n## 1 Intro"
    assert blocks[2].content.startswith("# Title (cont.)\n\n## 1 Intro (cont.)\n\nThe quick")
    plain = build_chunks(doc, Options(chunk_mode="block"), heading_context=False)
    assert plain[2].content.startswith("The quick")


def test_section_and_page_sections():
    doc = long_doc()
    sec = chunks(doc, chunk_mode="section")
    # every heading starts a chunk, a lone title too; later chunks carry the title as context
    assert [c.content.split("\n\n")[0] for c in sec] == ["# Title"] + ["# Title (cont.)"] * 4
    assert [c.content.split("\n\n")[1] for c in sec[1:]] == ["## 1 Intro", "## 2 Methods", "## 3 Results", "## 4 End"]
    assert sec[0].content == "# Title"
    ps = chunks(doc, chunk_mode="page_sections")
    assert len(ps) == 6  # page 1: title + 2 sections, page 2: lead-in + section 3, page 3: 1
    ids_per_page = [{r.id for r in p.regions} for p in doc.pages]
    assert all(any(set(c.region_ids) <= ids for ids in ids_per_page) for c in ps)


def test_heading_levels_in_context():
    S, X, H = RegionType.SECTION_HEADER, RegionType.TEXT, RegionType.TITLE
    doc = make_doc([[(H, "# T"), (S, "## A"), (S, "### A.1"), (X, "one"), (S, "### A.2"), (X, "two"), (S, "## B"), (X, "three")]])
    out = [c.content for c in chunks(doc, chunk_mode="section")]
    assert out == ["# T", "# T (cont.)\n\n## A", "# T (cont.)\n\n## A (cont.)\n\n### A.1\n\none",
                   "# T (cont.)\n\n## A (cont.)\n\n### A.2\n\ntwo", "# T (cont.)\n\n## B\n\nthree"]


def test_variable_band_and_atomic_tables():
    doc = long_doc()
    out = build_chunks(doc, Options(chunk_mode="variable"), heading_context=False)
    table = [c for c in out if "<table>" in c.content]
    assert len(table) == 1 and table[0].content.count("<tr>") == 60  # never split, may exceed the band
    assert not any(c.content.rstrip().split("\n")[-1].startswith("#") for c in out)  # no dangling heading
    joined = " ".join(c.content for c in out)
    assert joined.count("The quick brown fox") == 50  # nothing lost, nothing duplicated
    every = [rid for c in out for rid in c.region_ids]
    assert set(every) == {r.id for r in doc.iter_regions()}


def test_variable_follows_reducto_examples():
    """Block sizes and groupings Reducto returned (bofa page, chunk_size 500; brk page, default size)."""
    S, X, H, T, F = (RegionType.SECTION_HEADER, RegionType.TEXT, RegionType.TITLE, RegionType.TABLE,
                     RegionType.FIGURE)
    def blocks(spec):
        return [(t, ("## " if t == S else "# " if t == H else "") + "x" * n) for t, n in spec]
    bofa = make_doc([blocks([(F, 106), (X, 35), (X, 68), (S, 35), (X, 170), (H, 28), (X, 37), (X, 40), (S, 22),
                             (T, 370), (F, 87), (S, 15), (X, 109), (X, 27), (RegionType.FOOTER, 59)])])
    assert [len(c.region_ids) for c in chunks(bofa, chunk_mode="variable", chunk_size=500)] == [8, 3, 4]
    assert [len(c.region_ids) for c in chunks(bofa, chunk_mode="variable")] == [15]
    brk = make_doc([blocks([(H, 49), (S, 54), (S, 15), (S, 45), (T, 1859), (S, 52), (T, 2058), (X, 52), (X, 328),
                            (S, 78), (T, 1061), (RegionType.FOOTER, 4)])])
    assert [len(c.region_ids) for c in chunks(brk, chunk_mode="variable")] == [3, 2, 2, 2, 3]
    assert [len(c.region_ids) for c in chunks(brk, chunk_mode="variable", chunk_size=500)] == [3, 2, 2, 2, 2, 1]


def test_variable_custom_size_and_sentence_cuts():
    doc = make_doc([[(RegionType.TEXT, SENT * 40)]])
    out = chunks(doc, chunk_mode="variable", chunk_size=400)
    assert len(out) > 4 and all(len(c.content) <= 500 for c in out)
    assert all(c.content.endswith(".") for c in out)
    assert all(c.region_ids == [doc.pages[0].regions[0].id] for c in out)
    # a block a little over the bound stays whole, as on Reducto (708 characters at chunk_size 500)
    whole = make_doc([[(RegionType.TEXT, SENT * 10)]])
    assert len(chunks(whole, chunk_mode="variable", chunk_size=500)) == 1


def test_overlap():
    doc = make_doc([[(RegionType.TEXT, SENT * 40)]])
    plain = chunks(doc, chunk_mode="variable", chunk_size=400)
    over = chunks(doc, chunk_mode="variable", chunk_size=400, chunk_overlap=50)
    assert over[0].content == plain[0].content + plain[1].content[:50]
    for i in range(1, len(plain) - 1):  # 50 characters of each neighbour, as Reducto returns it
        assert over[i].content == plain[i - 1].content[-50:] + plain[i].content + plain[i + 1].content[:50]
    assert over[-1].content == plain[-2].content[-50:] + plain[-1].content


def test_filter_blocks_markers_and_embed():
    doc = long_doc()
    one = chunks(doc, filter_blocks=["Footer", "Table"], add_page_markers=True)[0]
    assert "footer text" not in one.content and "<table>" not in one.content
    real = [r for r in one.region_ids if not r.startswith("__page_")]
    assert len(real) == sum(len(p.regions) for p in doc.pages)  # still in blocks
    assert one.region_ids[0] == "__page_start_0" and one.region_ids[-1] == "__page_end_2"  # markers become blocks
    sec = chunks(doc, chunk_mode="section", add_page_markers=True)
    assert sec[0].content == "[[START OF PAGE 1]]\n\n# Title"  # a marker sits where its page starts, once
    assert sum(c.content.count("[[START OF PAGE 2]]") for c in sec) == 1
    for n in (1, 2, 3):
        assert one.content.count(f"[[START OF PAGE {n}]]") == 1 and one.content.count(f"[[END OF PAGE {n}]]") == 1
    assert one.content.index("[[END OF PAGE 1]]") < one.content.index("[[START OF PAGE 2]]")
    emb = chunks(doc, embedding_optimized=True)[0]
    assert "<table>" in emb.content and "<table>" not in emb.embed
    assert "row 0; 0; note 0." not in emb.embed and "row 0: row 1; 0: 7; note 0: note 1." in emb.embed


def test_embed_hook_and_fallback_with_headers():
    from readanydoc.pipeline.shaping import embed
    html = "<table><tr><th>Name</th><th>Qty</th></tr><tr><td>a</td><td>1</td></tr></table>"
    doc = make_doc([[(RegionType.TABLE, html)]])
    assert chunks(doc, embedding_optimized=True)[0].embed == "Name: a; Qty: 1."
    embed.set_table_summarizer(lambda grid, content, ctx: f"A table with {grid.n_rows} rows.")
    try:
        assert chunks(doc, embedding_optimized=True)[0].embed == "A table with 2 rows."
        embed.set_table_summarizer(lambda *a: 1 / 0)
        assert chunks(doc, embedding_optimized=True)[0].embed == "Name: a; Qty: 1."
    finally:
        embed.set_table_summarizer(None)


def test_table_formats_reach_chunks_and_block_meta():
    html = "<table><tr><th>Name</th><th>Qty</th></tr><tr><td>a</td><td>1</td></tr></table>"
    doc = make_doc([[(RegionType.TABLE, html)]])
    r = doc.pages[0].regions[0]
    assert chunks(doc)[0].content.startswith("| Name | Qty |") and r.meta["shaped_content"].startswith("| Name")
    assert chunks(doc, table_output_format="html")[0].content == html and "shaped_content" not in r.meta
    assert chunks(doc, table_output_format="csv")[0].content == "Name,Qty\na,1"
    assert r.content == html  # the canonical content is never rewritten


def test_shape_stage_reads_request_and_options():
    doc = long_doc()
    cfg = apply_request(PipelineConfig(), {"retrieval": {"chunking": {"chunk_mode": "section"},
                                                         "filter_blocks": ["Footer"], "embedding_optimized": True},
                                           "formatting": {"table_output_format": "md", "add_page_markers": True},
                                           "settings": {"document_password": "secret"}})
    assert "document_password" not in cfg.stage("shape").options["request"]["settings"]
    assert cfg.parse.document_password == "secret"
    sc = cfg.stage("shape")
    out = Shape(enabled=True, **sc.options).run(doc, PipelineContext(cfg))
    assert len(out.chunks) == 5 and "| row 0 | 0 | note 0 |" in out.chunks[2].content
    assert out.meta["parse_options"]["chunk_mode"] == "section" and out.meta["parse_options"]["embedding_optimized"]
    off = Shape(enabled=True, dynamic_tables=False).run(long_doc(), PipelineContext(PipelineConfig()))
    assert "<table>" in off.chunks[0].content


def test_resolve_defaults_match_reducto():
    o = resolve(ParseOptions())
    assert (o.chunk_mode, o.table_output_format, o.merge_tables, o.embedding_optimized) == ("disabled", "dynamic", False, False)
    assert o.size_band == (750, 1000, 1250)
    assert o.spreadsheet.clustering == "accurate" and o.spreadsheet.split.rows == 50
    o = resolve(None, {"spreadsheet": {"split_large_tables": {"size": {"row": 10, "column": 4}}, "clustering": "fast"}})
    assert (o.spreadsheet.split.rows, o.spreadsheet.split.cols, o.spreadsheet.clustering) == (10, 4, "fast")


def test_page_range_forms():
    assert resolve_page_range(None, 5) == [1, 2, 3, 4, 5]
    assert resolve_page_range({"start": 2, "end": 4}, 5) == [2, 3, 4]
    assert resolve_page_range({"start": 4}, 5) == [4, 5]
    assert resolve_page_range([{"start": 1, "end": 2}, {"start": 4, "end": 9}], 5) == [1, 2, 4, 5]
    assert resolve_page_range([5, 1, 1, 9], 5) == [1, 5]
    assert resolve_page_range(["2-3"], 5) == [2, 3]
    assert resolve_page_range(["Costs", "2"], 3, ["Summary", "Costs", "2"]) == [2, 3]
    for bad in ({"start": 0}, {"start": 3, "end": 2}, [True], ["nope"]):
        with pytest.raises(PageRangeError):
            resolve_page_range(bad, 5, ["a"] if bad == ["nope"] else None)


def test_split_text_never_loses_text():
    text = (SENT * 12).strip()
    parts = C.split_text(text, 300, 375)
    assert " ".join(parts) == text and all(len(p) <= 375 for p in parts)
    blob = "x" * 2000
    assert "".join(C.split_text(blob, 300, 375)) == blob
