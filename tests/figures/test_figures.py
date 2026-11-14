"""Figures: locating drawings on the page image, crops, figure prompts, chart data, response links."""
import json

import pytest
from PIL import Image, ImageDraw

from readanydoc.model import BBox, Document, Page, Region, RegionType
from readanydoc.pipeline import PipelineConfig, PipelineContext
from readanydoc.pipeline import figures
from readanydoc.pipeline.figures import locate
from readanydoc.pipeline.shape import Shape
from readanydoc.pipeline.shaping import merge, response
from readanydoc.pipeline.shaping.options import apply_request
from readanydoc.serialize import to_parse_response
from readanydoc.tasks.llm import FakeLLM

W, H = 800, 1000


def page_image(tmp_path, framed=False):
    """A white page: two text lines, a round drawing on the left, a bar drawing on the right; optionally a
    bordered box at the bottom with a small drawing and a caption inside."""
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    for y in (60, 90):  # "text": thin grey strokes, given to the locator as text boxes
        d.rectangle([80, y, 700, y + 12], fill=(60, 60, 60))
    d.ellipse([100, 200, 340, 440], fill=(40, 90, 160))
    for i, h in enumerate((120, 200, 160, 220)):
        d.rectangle([480 + i * 60, 440 - h, 520 + i * 60, 440], fill=(200, 90, 30))
    if framed:
        d.rectangle([80, 700, 720, 900], outline=(0, 0, 0), width=3)
        d.ellipse([110, 740, 230, 860], fill=(20, 20, 20))
        d.rectangle([300, 780, 660, 800], fill=(60, 60, 60))
    path = tmp_path / "page.png"
    im.save(path)
    return path, im


TEXT = [(0.1, 0.06, 0.775, 0.012), (0.1, 0.09, 0.775, 0.012)]
CAPTION = (0.375, 0.78, 0.45, 0.02)


def near(box, want, tol=0.03):
    return all(abs(a - b) <= tol for a, b in zip(box, want))


def test_drawings_are_found_and_text_is_not(tmp_path):
    _, im = page_image(tmp_path)
    blobs = [b for b in locate.ink_blobs(im, TEXT)]
    assert len(blobs) == 5 and all(b.top > 0.15 for b in blobs)  # the disc and four bars; the text lines were erased
    left, right = locate.figure_boxes(im, TEXT, 2)
    assert near(left, (0.125, 0.2, 0.3, 0.24)) and near(right, (0.6, 0.22, 0.275, 0.22))
    assert locate.figure_boxes(im, TEXT + [(0.1, 0.18, 0.8, 0.3)], 1) == [None]  # nothing drawn outside the text


def test_hints_pick_the_drawing_and_side_by_side_drawings_join(tmp_path):
    _, im = page_image(tmp_path)
    only_right = locate.figure_boxes(im, TEXT, 2, hints=[(0.6, 0.2, 0.3, 0.2), (0.1, 0.2, 0.3, 0.2)])
    assert near(only_right[0], (0.6, 0.22, 0.275, 0.22)) and near(only_right[1], (0.125, 0.2, 0.3, 0.24))
    one = locate.figure_boxes(im, TEXT, 1, hints=[(0.1, 0.2, 0.3, 0.25)])  # one description for both drawings
    assert near(one[0], (0.125, 0.2, 0.75, 0.24))


def test_a_bordered_box_with_text_is_a_frame_and_its_drawing_stands_alone(tmp_path):
    _, im = page_image(tmp_path, framed=True)
    boxes = locate.figure_boxes(im, TEXT + [CAPTION], 3)
    assert sum(b is not None for b in boxes) == 3
    inner = min((b for b in boxes if b), key=lambda b: b[2] * b[3])
    assert near(inner, (0.1375, 0.74, 0.15, 0.12))  # the drawing inside the box, not the box


def make_doc(tmp_path, framed=False):
    path, _ = page_image(tmp_path, framed)
    doc = Document(source=str(path), file_type="png")
    doc.meta["path"] = str(path)
    page = Page(index=0, original_page=1, width=W, height=H, unit="px")
    page.renders["150"] = str(path)
    def add(t, content, box=None):
        r = Region(type=t, content=content, order=len(page.regions))
        if box:
            r.bbox = BBox(left=box[0], top=box[1], width=box[2], height=box[3], page=0)
        page.regions.append(r)
        return r
    add(RegionType.TEXT, "First line of text.", TEXT[0])
    add(RegionType.TEXT, "Second line of text.", TEXT[1])
    add(RegionType.TEXT, "*[Figure: a blue disc]*")  # the reader's description, no box, typed Text
    add(RegionType.FIGURE, "[Chart: four orange bars]")
    add(RegionType.TABLE, "<table><tr><td>a</td><td>1</td></tr></table>", (0.1, 0.5, 0.5, 0.1))
    doc.pages.append(page)
    return doc


CHART = {"is_chart": True, "chart_type": "bar", "chart_title": "Output", "source": "",
         "axes": {"x": {"label": "Quarter", "type": "category"}, "y_primary": {"label": "Units", "min": 0, "max": 250}},
         "series": [{"label": "Plant A", "type": "bar", "corresponding_y": "primary", "color": "#c85a1e",
                     "data": [{"x": "Q1", "value": 120}, {"x": "Q2", "value": 200}]}], "estimated": True}


def test_is_figure():
    yes = ["*[Figure: black-and-white ornament]*", "[Immunize.org logo]", "[QR code: scan for PDF]",
           "*Two pie charts show September 2024 receipts and fiscal year receipts by source.*"]
    no = ["[illegible]", "[x] Yes [ ] No", "*Unaudited supplemental information*", "See the chart of accounts.",
          "[signature]"]
    assert all(figures.is_figure(Region(content=c)) for c in yes)
    assert not any(figures.is_figure(Region(content=c)) for c in no)
    assert figures.is_figure(Region(type=RegionType.FIGURE, content="anything"))


def test_figures_get_boxes_without_being_asked_and_no_model_is_called(tmp_path):
    doc = make_doc(tmp_path)
    stats = figures.process(doc, {}, llm=FakeLLM(RuntimeError("no call expected")), out_dir=tmp_path / "out")
    disc, bars = doc.pages[0].regions[2], doc.pages[0].regions[3]
    assert stats["figures"] == 2 and stats["located"] == 2 and stats["calls"] == 0
    assert near((disc.bbox.left, disc.bbox.top, disc.bbox.width, disc.bbox.height), (0.125, 0.2, 0.3, 0.24))
    assert bars.bbox.left > 0.55 and disc.type == RegionType.FIGURE and disc.meta["bbox_source"] == "figure_ink"
    assert "image_path" not in disc.meta and not (tmp_path / "out").exists()  # no crops unless asked


def test_return_images_prompt_and_chart_data(tmp_path):
    doc = make_doc(tmp_path)
    def reply(system, user, i):
        return CHART if "data chart" in user else "FIGURE: a described drawing."
    llm = FakeLLM(reply)
    req = {"settings": {"return_images": ["figure", "table"]},
           "enhance": {"agentic": [{"scope": "figure", "prompt": "Describe it.", "advanced_chart_agent": True}]}}
    cfg = apply_request(PipelineConfig(), req)
    out = Shape(enabled=True, figure_llm=llm, **cfg.stage("shape").options).run(doc, PipelineContext(cfg))
    disc, bars, table = out.pages[0].regions[2:5]
    assert all(Image.open(r.meta["image_path"]).size[0] > 20 for r in (disc, bars, table))
    assert len(llm.calls) == 2 and all(len(c["images"]) == 1 for c in llm.calls)  # one call per figure
    assert bars.meta["chart_data"]["series"][0]["data"][1] == {"x": "Q2", "value": 200} and bars.meta["is_chart"]
    assert "| Quarter | Plant A |" in bars.meta["figure_content"] and "| Q2 | 200 |" in bars.meta["figure_content"]
    assert out.meta["figures"]["charts"] == 2 and len([u for u in out.usage if u.get("task") == "figure"]) == 2
    assert "| Q1 | 120 |" in out.chunks[0].content and "First line of text." in out.chunks[0].content
    assert "password" not in json.dumps(out.meta)

    stored = {}
    def put(data, name, ctype):
        stored[name] = (data, ctype)
        return f"http://files.test/{name}"
    resp = response.finish(to_parse_response(out), out, put)
    blocks = resp["result"]["chunks"][0]["blocks"]
    fig = [b for b in blocks if b["type"] == "Figure"]
    assert len(fig) == 2 and all(b["image_url"].startswith("http://files.test/") for b in fig)
    assert fig[1]["chart_data"] == [f"http://files.test/{bars.id}_chart.json"] and fig[1]["extra"]["is_chart"]
    assert json.loads(stored[f"{bars.id}_chart.json"][0])["chart_title"] == "Output"
    assert [b for b in blocks if b["type"] == "Table"][0]["image_url"] and resp["usage"]["usage_breakdown"]["charts"] == 2
    assert stored[f"{disc.id}.png"][1] == "image/png" and resp["pdf_url"] is None  # an image input has no PDF


def test_not_a_chart_and_a_failing_call_leave_the_block_alone(tmp_path):
    doc = make_doc(tmp_path)
    llm = FakeLLM([{"is_chart": False}, RuntimeError("provider down")])
    stats = figures.process(doc, {"enhance": {"advanced_chart_agent": True}}, llm=llm, out_dir=tmp_path / "o")
    assert stats["charts"] == 0 and len(stats["errors"]) == 1
    assert not any(r.meta.get("figure_content") or r.meta.get("chart_data") for r in doc.iter_regions())


def test_page_markers_become_blocks(tmp_path):
    doc = make_doc(tmp_path)
    cfg = apply_request(PipelineConfig(), {"formatting": {"add_page_markers": True}})
    out = Shape(enabled=True, **cfg.stage("shape").options).run(doc, PipelineContext(cfg))
    resp = response.finish(to_parse_response(out), out)
    blocks = resp["result"]["chunks"][0]["blocks"]
    assert [b["content"] for b in (blocks[0], blocks[-1])] == ["[[START OF PAGE 1]]", "[[END OF PAGE 1]]"]
    assert blocks[0]["type"] == "Page Number" and blocks[0]["bbox"]["width"] == 0 and len(blocks) == 7
    assert resp["result"]["chunks"][0]["content"].startswith("[[START OF PAGE 1]]\n\nFirst line")


def test_merge_tables_like_reducto():
    T, X = RegionType.TABLE, RegionType.TEXT
    row = lambda *c: "<tr>" + "".join(f"<td>{x}</td>" for x in c) + "</tr>"  # noqa: E731
    doc = Document(source="x")
    spec = [[(T, "<table>" + row("h1", "h2", "h3") + row(1, 2, 3) + "</table>"), (X, "between"),
             (T, "<table><tbody>" + row(4, 5, 6) + "</tbody></table>")],
            [(T, "| h1 | h2 | h3 |\n| --- | --- | --- |\n| 7 | 8 | 9 |"), (T, "<table>" + row("a", "b") + "</table>")]]
    for i, blocks in enumerate(spec):
        page = Page(index=i, original_page=i + 1)
        for t, c in blocks:
            page.regions.append(Region(type=t, content=c, order=len(page.regions)))
        doc.pages.append(page)
    assert merge.merge_consecutive(doc) == 1
    head, second, third, other = [r for r in doc.iter_regions() if r.type == T]
    assert head.meta["merged_content"].count("<tr>") == 5 and "<tbody>" not in head.meta["merged_content"]
    assert [m["bbox"]["page"] for m in head.meta["merged_tables"]] == [1, 1, 2]
    assert second.meta["merged_into"] == head.id == third.meta["merged_into"] and "merged_into" not in other.meta
    cfg = apply_request(PipelineConfig(), {"formatting": {"merge_tables": True, "table_output_format": "html"}})
    out = Shape(enabled=True, figures=False, **cfg.stage("shape").options).run(doc, PipelineContext(cfg))
    resp = to_parse_response(out)
    tables = [b for b in resp["result"]["chunks"][0]["blocks"] if b["type"] == "Table"]
    assert len(tables) == 2 and len(tables[0]["merged_tables"]) == 3 and tables[0]["content"].count("<tr>") == 5
    off = apply_request(PipelineConfig(), {"formatting": {"merge_tables": False}})
    again = Shape(enabled=True, figures=False, **off.stage("shape").options).run(doc, PipelineContext(off))
    assert not any(r.meta.get("merged_into") or r.meta.get("merged_content") for r in again.iter_regions())


def test_table_summaries_for_embedding(monkeypatch):
    from readanydoc.pipeline.shaping import summarize
    llm = FakeLLM("This table shows two quarters of output for Plant A.")
    usage = []
    fn = summarize.make_summarizer("fake", usage, llm=llm)
    doc = Document(source="x")
    page = Page(index=0, original_page=1)
    page.regions.append(Region(type=RegionType.TABLE, content="<table><tr><th>Q</th><th>Units</th></tr><tr><td>Q1</td><td>120</td></tr></table>"))
    doc.pages.append(page)
    from readanydoc.pipeline.shape import Shaper
    from readanydoc.pipeline.shaping.options import Options
    ch = Shaper(doc, Options(embedding_optimized=True), summarizer=fn).chunks()[0]
    assert "| Q1 | 120 |" in ch.content and ch.embed == "This table shows two quarters of output for Plant A."
    assert usage[0]["task"] == "table_summary" and "120" in llm.calls[0]["user"] and not llm.calls[0]["images"]
    monkeypatch.delenv("READANYDOC_EMBED_LLM", raising=False)
    monkeypatch.delenv("READANYDOC_TASK_LLM", raising=False)
    assert summarize.default_spec("cached:haiku") is None  # offline profiles never call a model
    assert summarize.default_spec("anthropic_api:haiku#high") == "anthropic_api:haiku"
    monkeypatch.setenv("READANYDOC_EMBED_LLM", "off")
    assert summarize.default_spec("anthropic_api:haiku#high") is None


# ---------------------------------------------------------------- round 2: chart verification pass
def _chart(values, lo=0, hi=300, kind="bar"):
    return {"is_chart": True, "chart_type": kind, "chart_title": "Output",
            "axes": {"x": {"label": "Quarter"}, "y_primary": {"label": "Units", "min": lo, "max": hi}},
            "series": [{"label": "Plant A", "type": kind, "data": [{"x": f"Q{i + 1}", "value": v}
                                                                   for i, v in enumerate(values)]}]}


def test_chart_problems_are_found_without_a_model():
    ok = _chart([120, 200, 250])
    assert figures.chart_problems(ok) == []
    assert "outside the y-axis range" in figures.chart_problems(_chart([120, 2000, 250]))[0]
    assert "values are missing" in figures.chart_problems(_chart([None, None, 250]))[0]
    twice = _chart([1, 2])
    twice["series"][0]["data"][1]["x"] = "Q1"
    assert "more than once" in figures.chart_problems(twice)[0]
    assert "add up to 80" in figures.chart_problems(_chart([50, 30], kind="pie"))[0]
    assert figures.chart_problems(_chart([60, 40], kind="pie")) == []
    uneven = _chart([1, 2, 3, 4, 5, 6])
    uneven["series"] += [dict(uneven["series"][0], label="B"), {"label": "C", "type": "bar", "data": [{"x": "Q1", "value": 1}]}]
    assert "'C' (1) have far fewer points" in figures.chart_problems(uneven)[0]


def test_a_consistent_chart_costs_one_call_and_a_failed_check_one_more(tmp_path):
    png = tmp_path / "fig.png"
    Image.new("RGB", (60, 40), "white").save(png)
    want = {"chart": True, "prompt": None}
    llm = FakeLLM([_chart([120, 200, 250])])
    out = figures._ask(llm, want, str(png))
    assert len(llm.calls) == 1 and out["verification"] == {"status": "passed", "problems": []}
    llm = FakeLLM([_chart([120, 2000, 250]), _chart([120, 200, 250])])
    out = figures._ask(llm, want, str(png))
    assert len(llm.calls) == 2 and "outside the y-axis range" in llm.calls[1]["user"]
    assert out["verification"]["status"] == "corrected" and out["chart"]["series"][0]["data"][1]["value"] == 200
    assert len(out["usage"]) == 2
    llm = FakeLLM([_chart([120, 2000, 250]), _chart([120, 2000, 250])])   # the second read is no better
    out = figures._ask(llm, want, str(png))
    assert out["verification"]["status"] == "flagged" and out["chart"]["series"][0]["data"][1]["value"] == 2000


# ---------------------------------------------------------------- round 2: Figure blocks from layout pictures
def _layout_page():
    from readanydoc.model import BBox, Page, Region, RegionType
    page = Page(index=0, original_page=1, width=612, height=792)
    def reg(t, content, top, h=0.05, left=0.1, w=0.8):
        return Region(type=RegionType(t), content=content, bbox=BBox(left=left, top=top, width=w, height=h, page=0))
    page.regions = [reg("Title", "# Statement", 0.12), reg("Figure", "[Figure: a pie chart]", 0.30, h=0.2, w=0.4),
                    reg("Table", "<table><tr><td>a</td></tr></table>", 0.55, h=0.2), reg("Text", "Closing line.", 0.80)]
    def det(label, score, x0, y0, x1, y1):
        return {"id": "d", "label": label, "score": score, "box": [x0, y0, x1, y1]}
    page.properties["layout_detections"] = [
        det("picture", 0.93, 0.08, 0.03, 0.25, 0.09),      # a logo above the title: new block, first in order
        det("picture", 0.90, 0.12, 0.31, 0.48, 0.49),      # the pie chart the reader already described
        det("picture", 0.88, 0.60, 0.60, 0.66, 0.64),      # an icon inside the table
        det("picture", 0.85, 0.70, 0.88, 0.90, 0.96),      # a stamp at the bottom: new block, last
        det("picture", 0.60, 0.72, 0.89, 0.88, 0.95),      # nested double of the stamp
        det("picture", 0.30, 0.50, 0.03, 0.60, 0.08),      # not confident enough
        det("picture", 0.95, 0.40, 0.10, 0.405, 0.105),    # a speck
        det("text", 0.99, 0.10, 0.80, 0.90, 0.85)]
    return page


def test_layout_pictures_become_figure_blocks_only_where_no_block_accounts_for_them():
    from readanydoc.pipeline.figures import layout_pictures
    page = _layout_page()
    new = layout_pictures.add(page, [(0.1, 0.30, 0.4, 0.2)])
    assert [(round(r.bbox.left, 2), round(r.bbox.top, 2)) for r in new] == [(0.08, 0.03), (0.7, 0.88)]
    assert [r.type.value for r in page.regions] == ["Figure", "Title", "Figure", "Table", "Text", "Figure"]
    assert [r.order for r in page.regions] == list(range(6))
    logo = page.regions[0]
    assert logo.content == "" and logo.meta["from_layout"] and logo.meta["bbox_source"] == "layout"
    assert abs(logo.bbox.width - 0.17) < 1e-6 and abs(logo.bbox.height - 0.06) < 1e-6 and logo.confidence.label == "high"
    assert layout_pictures.add(page, [(0.1, 0.30, 0.4, 0.2), (0.08, 0.03, 0.17, 0.06), (0.7, 0.88, 0.2, 0.08)]) == []


def test_a_described_figure_that_was_not_found_takes_the_picture_box_instead_of_a_second_block():
    from readanydoc.pipeline.figures import layout_pictures
    page = _layout_page()
    seal = page.regions[1]                       # the reader's "[Figure: ...]" with a guessed box, not located
    seal.bbox = seal.bbox.model_copy(update={"left": 0.5, "top": 0.02, "width": 0.3, "height": 0.03})
    new = layout_pictures.add(page, [], unlocated=[seal])
    assert (round(seal.bbox.left, 2), round(seal.bbox.top, 2)) == (0.08, 0.03)       # the nearest free picture (the logo)
    assert seal.meta["bbox_source"] == "layout" and seal.meta["bbox_before_layout"] == [0.5, 0.02, 0.3, 0.03]
    assert seal.content == "[Figure: a pie chart]"                                   # the description stays
    assert [(round(r.bbox.left, 2), round(r.bbox.top, 2)) for r in new] == [(0.12, 0.31), (0.7, 0.88)]
    assert sum(r.type.value == "Figure" for r in page.regions) == 3                  # 1 described + 2 new, not 4


def test_layout_figures_switch_through_the_shape_stage(tmp_path):
    doc = make_doc(tmp_path)
    page = doc.pages[0]
    before = [r.type.value for r in page.regions]
    page.properties["layout_detections"] = [{"id": "d0", "label": "picture", "score": 0.9,
                                             "box": [0.70, 0.90, 0.90, 0.97]}]
    cfg = apply_request(PipelineConfig(), {"settings": {"return_images": ["figure"]}})
    opts = cfg.stage("shape").options
    off = Shape(enabled=True, figure_llm=FakeLLM(RuntimeError("no call")), **opts).run(doc, PipelineContext(cfg))
    assert len(off.pages[0].regions) == len(before)                        # off by default: no block is added
    assert not any(r.meta.get("from_layout") for r in off.pages[0].regions)
    llm = FakeLLM("A round blue seal with the words CITY OF TESTING.")
    on = Shape(enabled=True, figure_llm=llm, layout_figures=True, layout_figures_describe=True, **opts).run(
        doc, PipelineContext(cfg))
    added = [r for r in on.pages[0].regions if r.meta.get("from_layout")]
    assert len(added) == 1 and added[0].type.value == "Figure" and len(on.pages[0].regions) == len(before) + 1
    assert added[0].content == "A round blue seal with the words CITY OF TESTING." and len(llm.calls) == 1
    assert Image.open(added[0].meta["image_path"]).size[0] > 20                # return_images gets a crop
    assert "CITY OF TESTING" in on.chunks[0].content and on.meta["figures"]["from_layout"] == 1
    resp = response.finish(to_parse_response(on), on, lambda data, name, ctype: f"http://files.test/{name}")
    fig = [b for b in resp["result"]["chunks"][0]["blocks"] if b["type"] == "Figure"][-1]
    assert fig["image_url"] == f"http://files.test/{added[0].id}.png" and abs(fig["bbox"]["left"] - 0.70) < 1e-6
    again = Shape(enabled=True, figure_llm=llm, layout_figures=True, **opts).run(on, PipelineContext(cfg))
    assert len([r for r in again.pages[0].regions if r.meta.get("from_layout")]) == 1   # not added twice
    assert [r for r in again.pages[0].regions if r.meta.get("from_layout")][0].content == ""   # no description asked


def test_small_picture_on_the_line_of_a_running_head_is_not_a_figure():
    """Round 2, combine step: an icon beside the chapter name belongs to the running head."""
    from types import SimpleNamespace
    from readanydoc.pipeline.figures import layout_pictures as lp
    dets = [{"label": "page_header", "score": 0.9, "box": [0.30, 0.040, 0.80, 0.065]},
            {"label": "picture", "score": 0.9, "box": [0.10, 0.038, 0.16, 0.068]},     # icon on the header line, 0.18%
            {"label": "picture", "score": 0.9, "box": [0.10, 0.060, 0.20, 0.120]},     # logo below it: 8% of its height
            {"label": "picture", "score": 0.9, "box": [0.05, 0.030, 0.45, 0.070]},     # banner on the line, 1.6%: large
            {"label": "picture", "score": 0.9, "box": [0.60, 0.500, 0.66, 0.530]}]     # small picture in the body
    page = SimpleNamespace(properties={"layout_detections": dets}, regions=[], index=0)
    tops = lambda cs: sorted(round(c["box"][1], 3) for c in cs)
    assert tops(lp.candidates(page)) == [0.03, 0.06, 0.5]              # only the icon on the header line is out
    assert 0.038 not in tops(lp.candidates(page)) and lp.on_running_line((0.10, 0.038, 0.06, 0.03), dets, 0.5)
    assert not lp.on_running_line((0.10, 0.060, 0.10, 0.06), dets, 0.5)       # logo next to, not on, the line
    assert not lp.on_running_line((0.05, 0.030, 0.40, 0.04), dets, 0.5)       # 1.6% of the page: a figure
    assert lp.on_running_line((0.10, 0.038, 0.06, 0.03), dets, 0.0) is False   # rule off
    assert 0.038 in tops(lp.candidates(SimpleNamespace(properties={"layout_detections": dets[1:2] + dets[4:]}, regions=[], index=0)))
