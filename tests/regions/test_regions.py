"""Workstream 1 (final round): layout regions. No model call and no network in any test."""

import pytest

from readanydoc.layout.onnx import dedupe, reading_order
from readanydoc.model import BBox, Document, Page, Region, RegionType
from readanydoc.pipeline import Pipeline, PipelineContext
from readanydoc.pipeline.regions import Switches, complete, forms, structure, switches, tables
from readanydoc.pipeline.regions.match import _nest, detections, form_regions, top_level
from readanydoc.profiles import get_profile


def det(i, label, type_, box, score=0.9, order=None):
    return {"id": f"d{i}", "label": label, "type": type_, "score": score, "box": list(box),
            "order": i if order is None else order, "inside": None}


def region(type_, content, box=None):
    bbox = BBox(left=box[0], top=box[1], width=box[2] - box[0], height=box[3] - box[1]) if box else None
    return Region(type=type_, content=content, text=content, bbox=bbox)


def page_with(regions, dets):
    p = Page(index=0, width=612, height=792)
    p.regions = regions
    p.properties["layout_detections"] = dets
    return p


# ---------------------------------------------------------------------------------------------- switches
def test_switches_parse_and_default_off(monkeypatch):
    assert not Switches.parse(None).any() and not Switches.parse("").any()
    s = Switches.parse("types,boxes,tables=broken,complete,deny=Key Value>Text|Header>Text")
    assert (s.types, s.boxes, s.tables, s.complete, s.forms, s.assemble) == ("layout", True, "broken", True, None, False)
    assert s.deny == frozenset({"Key Value>Text", "Header>Text"})
    assert Switches.parse({"tables": "grid", "forms": True}).forms == "on" and Switches.parse("forms=notables").forms == "notables"
    with pytest.raises(ValueError):
        Switches.parse("tables=whatever")
    with pytest.raises(ValueError):
        Switches.parse("nonsense")
    monkeypatch.delenv("READANYDOC_REGIONS", raising=False)
    ctx = PipelineContext(get_profile("layout-api"))
    assert not switches(ctx).any()
    monkeypatch.setenv("READANYDOC_REGIONS", "types")
    assert switches(ctx).types == "layout"


def test_layout_api_profile_is_vanilla_plus_hooks(monkeypatch):
    monkeypatch.delenv("READANYDOC_REGIONS", raising=False)
    a, v = get_profile("layout-api"), get_profile("vanilla-api")
    assert a.stage("read").options == v.stage("read").options and a.reader == v.reader
    assert a.stage("ground").options == v.stage("ground").options and a.stage("ground").impl == "ground_regions"
    pipe = Pipeline(a)
    assert type(pipe.stages[5]).__name__ == "GroundRegions"
    doc = Document(source="x")
    doc.pages.append(Page(index=0))
    pipe.stages[2].run(doc, pipe.ctx)  # detect_only with no switch: the detector is not built
    assert pipe.ctx._layout is None and "layout_detections" not in doc.pages[0].properties


# ---------------------------------------------------------------------------------------------- detector helpers
def test_dedupe_keeps_containers_and_drops_doubles():
    dets = [("text", 0.9, (0.1, 0.1, 0.5, 0.2)), ("list_item", 0.6, (0.1, 0.1, 0.5, 0.21)),      # same block twice
            ("form", 0.8, (0.05, 0.05, 0.9, 0.5)),                                                 # holds the text
            ("checkbox_selected", 0.7, (0.12, 0.12, 0.14, 0.14)),                                  # a mark inside the text
            ("text", 0.5, (0.2, 0.12, 0.4, 0.18))]                                                 # part of the first
    kept = dedupe(dets)
    assert [k[0] for k in kept] == ["text", "form", "checkbox_selected"]


def test_reading_order_two_columns_under_a_title():
    boxes = [(0.55, 0.2, 0.9, 0.5), (0.1, 0.05, 0.9, 0.1), (0.1, 0.2, 0.45, 0.5), (0.1, 0.55, 0.45, 0.9),
             (0.55, 0.55, 0.9, 0.9)]
    assert reading_order(boxes) == [1, 2, 3, 0, 4]


def test_nesting_and_accessors():
    dets = [det(0, "form", "Key Value", (0.1, 0.1, 0.9, 0.6)), det(1, "text", "Text", (0.15, 0.15, 0.5, 0.2)),
            det(2, "key_value_region", "Key Value", (0.12, 0.3, 0.8, 0.5)), det(3, "checkbox_selected", "Text", (0.2, 0.35, 0.22, 0.37)),
            det(4, "text", "Text", (0.1, 0.7, 0.9, 0.8), score=0.35)]
    _nest(dets)
    assert dets[1]["inside"] == "d0" and dets[2]["inside"] == "d0" and dets[3]["inside"] == "d2" and dets[4]["inside"] is None
    p = page_with([], dets)
    assert [d["id"] for d in form_regions(p)] == ["d0"]
    assert [d["id"] for d in top_level(detections(p))] == ["d0", "d4"]
    assert detections(p, min_score=0.5)[-1]["id"] == "d3"


# ---------------------------------------------------------------------------------------------- a: structure
def test_structure_types_and_boxes():
    regs = [region(RegionType.TEXT, "1. First item of a list", (0.1, 0.1, 0.6, 0.14)),
            region(RegionType.HEADER, "Annual report", (0.1, 0.02, 0.4, 0.05)),
            region(RegionType.TABLE, "<table><tr><td>a</td></tr></table>", (0.1, 0.3, 0.9, 0.5)),
            region(RegionType.TEXT, "one line of a long paragraph", (0.1, 0.6, 0.9, 0.62)),
            region(RegionType.TEXT, "not located")]
    dets = [det(0, "list_item", "List Item", (0.1, 0.1, 0.62, 0.145)), det(1, "section_header", "Section Header", (0.1, 0.02, 0.41, 0.05)),
            det(2, "text", "Text", (0.1, 0.3, 0.9, 0.5)), det(3, "text", "Text", (0.1, 0.58, 0.9, 0.7))]
    p = page_with(regs, dets)
    out = structure.apply(p, dets, types="layout", boxes=True)
    assert [r.type for r in regs] == [RegionType.LIST_ITEM, RegionType.SECTION_HEADER, RegionType.TABLE, RegionType.TEXT, RegionType.TEXT]
    assert regs[1].heading_level == 2 and regs[1].meta["type_before_regions"] == "Header"
    assert regs[0].bbox.right == pytest.approx(0.62) and regs[0].meta["bbox_source"] == "layout"
    assert regs[3].bbox.height == pytest.approx(0.02)          # a part of a larger region keeps its own box
    assert regs[3].meta["layout_detection"]["how"] == "part"
    assert out["types_changed"] == 2 and out["boxes_changed"] == 3


def test_structure_deny_and_no_switch():
    regs = [region(RegionType.KEY_VALUE, "Name: Ann", (0.1, 0.1, 0.6, 0.14))]
    dets = [det(0, "text", "Text", (0.1, 0.1, 0.6, 0.14))]
    p = page_with(regs, dets)
    structure.apply(p, dets, types=None, boxes=False)
    assert regs[0].type == RegionType.KEY_VALUE
    structure.apply(p, dets, types="layout", deny=frozenset({"Key Value>Text"}))
    assert regs[0].type == RegionType.KEY_VALUE
    structure.apply(p, dets, types="layout")
    assert regs[0].type == RegionType.TEXT


def test_structure_merge_needs_one_type():
    regs = [region(RegionType.TEXT, "a\nb", (0.1, 0.1, 0.9, 0.3))]
    same = [det(0, "list_item", "List Item", (0.1, 0.1, 0.9, 0.19)), det(1, "list_item", "List Item", (0.1, 0.2, 0.9, 0.3))]
    structure.apply(page_with(regs, same), same, types="layout")
    assert regs[0].type == RegionType.LIST_ITEM
    regs = [region(RegionType.TEXT, "a\nb", (0.1, 0.1, 0.9, 0.3))]
    mixed = [det(0, "list_item", "List Item", (0.1, 0.1, 0.9, 0.19)), det(1, "section_header", "Section Header", (0.1, 0.2, 0.9, 0.3))]
    structure.apply(page_with(regs, mixed), mixed, types="layout")
    assert regs[0].type == RegionType.TEXT


# ---------------------------------------------------------------------------------------------- b: tables
GOOD = "<table><tr><th>Item</th><th>2023</th><th>2024</th></tr><tr><td>Rent</td><td>1,200</td><td>1,300</td></tr><tr><td>Total</td><td>2,400</td><td>2,650</td></tr></table>"
BROKEN = "<table><tr><th>Item</th><th>2023</th><th>2024</th></tr><tr><td>Rent</td><td>1,200 1,300</td></tr><tr><td>Total</td><td>2,400</td><td>2,650</td></tr></table>"
WRONG = GOOD.replace("1,300", "1,800")


def test_grid_consistent_matches_devset_rule():
    assert tables.grid_consistent(GOOD) is True and tables.grid_consistent(BROKEN) is False
    assert tables.grid_consistent("<table><tr><td>x</td></tr></table>") is None
    span = "<table><tr><td rowspan='2'>a</td><td>b</td></tr><tr><td>c</td></tr><tr><td colspan=2>d</td></tr></table>"
    assert tables.grid_consistent(span) is True


def test_table_rules():
    ev = ["1200", "1300", "2400", "2650", "2023", "2024", "1200", "1300", "2400", "2650"]
    assert tables.decide("grid", BROKEN, GOOD, ev)[0] is True
    assert tables.decide("broken", BROKEN, GOOD, ev)[0] is True
    assert tables.decide("broken", WRONG, GOOD, ev)[0] is False            # the page-read grid is fine
    assert tables.decide("grid", WRONG, GOOD, ev)[0] is True               # consistent grid is enough for this rule
    assert tables.decide("numbers", WRONG, GOOD, ev)[0] is True            # and the numbers agree better
    assert tables.decide("numbers", GOOD, WRONG, ev)[0] is False           # a worse crop never wins
    assert tables.decide("numbers", WRONG, GOOD, ev[:3])[0] is False       # too little evidence
    assert tables.decide("grid", GOOD, BROKEN, ev)[0] is False             # the crop grid must be consistent
    assert tables.decide("grid", GOOD, GOOD, ev)[0] is False               # same table
    assert tables.decide("grid", GOOD, "<table><tr><td>Rent</td></tr><tr><td>x</td></tr></table>", ev)[0] is False
    assert tables.decide("grid", GOOD, GOOD.replace("1,300", "[illegible]"), ev)[0] is False
    assert tables.decide("broken_numbers", BROKEN, WRONG, ev)[0] is False


def test_table_pairs_by_box_and_single_fallback():
    r = region(RegionType.TABLE, GOOD, (0.1, 0.3, 0.9, 0.5))
    d = det(0, "table", "Table", (0.09, 0.29, 0.91, 0.51))
    assert tables.pairs(page_with([r], [d])) == [(d, r)]
    unplaced = region(RegionType.TABLE, GOOD)
    assert tables.pairs(page_with([unplaced], [d])) == [(d, unplaced)]
    two = [region(RegionType.TABLE, GOOD), region(RegionType.TABLE, GOOD)]
    assert tables.pairs(page_with(two, [d])) == []


# ---------------------------------------------------------------------------------------------- c: forms
def test_form_guard():
    old = "Name Ann Lee\n\nDate 3 May 1999\n\nAmount 12.50"
    new = "Name: Ann Lee\nDate: 3 May 1999\nAmount: 12.50"
    assert forms.decide(old, new, [])[0] is True
    assert forms.decide(old, "Name: Ann Lee\nDate: 3 May 1999", [])[0] is False               # lost words
    assert forms.decide(new, new, [])[0] is False                                              # nothing gained
    assert forms.decide(old, new + "\n" + "extra words " * 20, [])[0] is False                 # bloat
    assert forms.kv_lines("<table><tr><td>a: b</td></tr></table>\nc: d") == 1


# ---------------------------------------------------------------------------------------------- e: completeness
def test_missing_regions_only_where_nothing_is_located():
    regs = [region(RegionType.TEXT, "First paragraph of the page", (0.1, 0.1, 0.9, 0.2))]
    dets = [det(0, "text", "Text", (0.1, 0.1, 0.9, 0.2)), det(1, "text", "Text", (0.1, 0.4, 0.9, 0.5)),
            det(2, "table", "Table", (0.1, 0.6, 0.9, 0.9)), det(3, "text", "Text", (0.2, 0.65, 0.5, 0.7)),
            det(4, "text", "Text", (0.1, 0.92, 0.9, 0.95), score=0.52)]
    _nest(dets)
    p = page_with(regs, dets)
    assert [d["id"] for d in complete.missing(p)] == ["d1"]   # d0 covered, d3 inside a table, d4 weak and no words


# ---------------------------------------------------------------------------------------------- the stage
def test_ground_regions_without_switch_is_ground(monkeypatch):
    monkeypatch.delenv("READANYDOC_REGIONS", raising=False)
    from readanydoc.pipeline.ground import Ground
    from readanydoc.pipeline.regions import GroundRegions

    def build():
        d = Document(source="x")
        pg = Page(index=0, width=612, height=792)
        pg.regions = [region(RegionType.TEXT, "Hello world"), region(RegionType.TABLE, GOOD)]
        pg.properties["layout_detections"] = [det(0, "section_header", "Section Header", (0.1, 0.1, 0.5, 0.2))]
        d.pages.append(pg)
        return d
    ctx = PipelineContext(get_profile("layout-api"))
    a = GroundRegions(tesseract="off").run(build(), ctx)
    b = Ground(tesseract="off").run(build(), ctx)
    assert [(r.type, r.content, r.bbox) for r in a.pages[0].regions] == [(r.type, r.content, r.bbox) for r in b.pages[0].regions]


def test_onnx_detector_smoke():
    from readanydoc.layout import make_layout
    from readanydoc.layout import onnx as lo
    if not lo.available("heron"):
        pytest.skip("layout extra or heron weights not installed")
    from PIL import Image, ImageDraw
    import io
    im = Image.new("RGB", (850, 1100), "white")
    dr = ImageDraw.Draw(im)
    for y in range(120, 400, 22):
        dr.line((80, y, 770, y), fill="black", width=6)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    blocks = make_layout("onnx:heron").detect(buf.getvalue())
    assert all(0 <= b.bbox.left <= 1 and 0 <= b.bbox.bottom <= 1.0001 for b in blocks)


def test_split_blocks_cuts_only_at_line_breaks_between_regions():
    from readanydoc.model import Line, Word

    def word(text, start, box):
        return Word(text=text, char_start=start, char_end=start + len(text),
                    bbox=BBox(left=box[0], top=box[1], width=box[2] - box[0], height=box[3] - box[1]))
    content = "Report 2003\nFirst paragraph here\nsecond line"
    r = region(RegionType.TEXT, content, (0.1, 0.05, 0.9, 0.3))
    r.lines = [Line(words=[word("Report", 0, (0.1, 0.05, 0.2, 0.07)), word("2003", 7, (0.21, 0.05, 0.3, 0.07)),
                           word("First", 12, (0.1, 0.2, 0.2, 0.22)), word("paragraph", 18, (0.21, 0.2, 0.4, 0.22)),
                           word("here", 28, (0.41, 0.2, 0.5, 0.22)), word("second", 33, (0.1, 0.23, 0.2, 0.25)),
                           word("line", 40, (0.21, 0.23, 0.3, 0.25))])]
    dets = [det(0, "page_header", "Header", (0.09, 0.04, 0.5, 0.08)), det(1, "text", "Text", (0.09, 0.19, 0.9, 0.26))]
    p = page_with([r], dets)
    assert structure.split_blocks(p, dets) == 1
    assert [x.content for x in p.regions] == ["Report 2003", "First paragraph here\nsecond line"]
    assert p.regions[1].meta["search_bbox"]["top"] == pytest.approx(0.19)
    one_line = region(RegionType.TEXT, "Report 2003 First paragraph", (0.1, 0.05, 0.9, 0.3))
    one_line.lines = [Line(words=[word("Report", 0, (0.1, 0.05, 0.2, 0.07)), word("First", 12, (0.1, 0.2, 0.2, 0.22))])]
    p2 = page_with([one_line], dets)
    assert structure.split_blocks(p2, dets) == 0 and len(p2.regions) == 1      # no line break: never cut inside a line


# ---------------------------------------------------------------------------------------------- guarded types
def test_starts_like_list_item():
    yes = ["- item", "1. first", "(a) clause", "**II.4.6** The contractor must", "<sup>2</sup> Amounts reflect",
           "3\\.\tThe Defendant had", "**A–1** Suppose that", "• Optional", "*1. $L(U,V)$", "[12] Smith et al."]
    no = ["The Court granted certiorari", "*Held*: The Administrative Procedure Act", "sessions without the consent",
          "Total revenue was 12.5 million", ""]
    assert all(structure.starts_like_list_item(t) for t in yes)
    assert not any(structure.starts_like_list_item(t) for t in no)


def test_guarded_type_changes():
    g = structure.guarded
    sure, unsure = {"label": "text", "score": 0.97}, {"label": "text", "score": 0.8}
    assert g("Text", "List Item", sure, "match", "(a) the clause") and not g("Text", "List Item", sure, "match", "A paragraph.")
    assert not g("Title", "Section Header", sure, "match", "# Closing Disclosure") and g("Title", "Header", sure, "match", "x")
    assert g("Text", "Section Header", unsure, "match", "Results") and not g("List Item", "Section Header", sure, "match", "x")
    assert g("List Item", "Text", sure, "match", "(1) text") and not g("List Item", "Text", unsure, "match", "(1) text")
    assert not g("Key Value", "Text", sure, "part", "REGION:") and not g("Header", "Text", sure, "match", "x")
    assert g("Footer", "Text", unsure, "match", "x") and g("Text", "Header", unsure, "match", "x")
    assert not g("Text", "Key Value", sure, "match", "x")


def test_structure_guarded_mode_keeps_reader_title_and_unmarked_text():
    p = page_with([region(RegionType.TITLE, "# Closing Disclosure", (0.1, 0.05, 0.5, 0.08)),
                   region(RegionType.TEXT, "The Court granted certiorari.", (0.1, 0.2, 0.9, 0.3)),
                   region(RegionType.TEXT, "(b) the second clause", (0.1, 0.4, 0.9, 0.5)),
                   region(RegionType.TEXT, "Annual report 2023", (0.1, 0.01, 0.5, 0.03))],
                  [det(0, "section_header", "Section Header", (0.1, 0.05, 0.5, 0.08)),
                   det(1, "list_item", "List Item", (0.1, 0.2, 0.9, 0.3)), det(2, "list_item", "List Item", (0.1, 0.4, 0.9, 0.5)),
                   det(3, "page_header", "Header", (0.1, 0.01, 0.5, 0.03))])
    out = structure.apply(p, p.properties["layout_detections"], "guarded")
    assert [r.type for r in p.regions] == [RegionType.TITLE, RegionType.TEXT, RegionType.LIST_ITEM, RegionType.HEADER]
    assert out["transitions"] == {"Text>List Item": 1, "Text>Header": 1}
    assert Switches.parse("types=guarded,boxes").types == "guarded"

def test_split_blocks_guards_forms_and_unsure_same_type_boxes():
    from readanydoc.model import Line, Word

    def word(text, start, box):
        return Word(text=text, char_start=start, char_end=start + len(text),
                    bbox=BBox(left=box[0], top=box[1], width=box[2] - box[0], height=box[3] - box[1]))

    def block(type_, content):
        a, _, b = content.partition("\n")
        r = region(type_, content, (0.1, 0.1, 0.9, 0.3))
        r.lines = [Line(words=[word(a.split()[0], 0, (0.1, 0.11, 0.2, 0.13)), word(a.split()[-1], len(a) - len(a.split()[-1]), (0.3, 0.11, 0.4, 0.13)),
                               word(b.split()[0], len(a) + 1, (0.1, 0.21, 0.2, 0.23)), word(b.split()[-1], len(content) - len(b.split()[-1]), (0.3, 0.21, 0.4, 0.23))])]
        return r
    two = lambda s0, s1, inside=None: [dict(det(0, "text", "Text", (0.09, 0.1, 0.9, 0.14), score=s0), inside=inside),
                                       dict(det(1, "text", "Text", (0.09, 0.2, 0.9, 0.24), score=s1), inside=inside)]
    cut = lambda r, dets: structure.split_blocks(page_with([r], dets), dets)
    assert cut(block(RegionType.TEXT, "It was started by a man who earned\nmoney with music in the streets."), two(0.88, 0.69)) == 0
    assert cut(block(RegionType.TEXT, "It was started by a man who earned\nmoney with music in the streets."), two(0.9, 0.9)) == 1
    assert cut(block(RegionType.TEXT, "The first paragraph ends here.\nThe second one starts here."), two(0.88, 0.69)) == 1
    assert cut(block(RegionType.TEXT, "first clause goes here\n(b) second clause goes here"), two(0.88, 0.69)) == 1
    assert cut(block(RegionType.KEY_VALUE, "NAME OF ACCOUNT:\nJohn Smith and Co."), two(0.9, 0.9)) == 0     # a field stays whole
    form = [dict(det(9, "form", "Key Value", (0.05, 0.05, 0.95, 0.5)), inside=None)]
    assert cut(block(RegionType.TEXT, "NAME OF ACCOUNT:\nJohn Smith and Co."), form + two(0.9, 0.9, inside="d9")) == 0


def test_layout_best_profile_presets_the_kept_switches(monkeypatch):
    monkeypatch.delenv("READANYDOC_REGIONS", raising=False)
    cfg = get_profile("layout-best-api")
    s = Switches.parse(cfg.stage("ground").options["regions"])
    assert (s.types, s.boxes, s.tables, s.forms, s.split, s.assemble, s.complete) == ("guarded", True, "broken", "notables", False, False, False)
    assert cfg.stage("layout").options["detect_only"] is True and cfg.layout == "onnx:heron"
