"""Round 2, workstream A: table switches (hygiene, joins, acceptance rules, spec), formulas, zoom regions.
No model call: readers are replaced by fakes where a read is needed."""
import pytest

from readanydoc.model import BBox, Page, Region, RegionType, TableCell
from readanydoc.pipeline import tables
from readanydoc.pipeline.reading import formulas, zoom, zoom_regions
from readanydoc.pipeline.tables import evidence as ev
from readanydoc.pipeline.tables import hygiene, reread

T3 = "<table><tr><th>Item</th><th>M</th><th>NM</th></tr><tr><td>Fish</td><td>16.00</td><td>17.50</td></tr><tr><td>Parma</td><td>16.00</td><td>17.50</td></tr></table>"


def box(l, t, r, b):
    return BBox(left=l, top=t, width=r - l, height=b - t, page=0)


# ---------------------------------------------------------------- spec
def test_spec_off_by_default_and_parsed(monkeypatch):
    monkeypatch.delenv("READANYDOC_TABLES", raising=False)
    assert tables.spec_from(None) == {}
    s = tables.parse_spec("reread=missed+all;accept=content;clean=1;merge=cont;words_at=0.9;formulas=tag+matrix")
    assert s == {"reread": ["missed", "all"], "accept": "content", "clean": True, "merge": "cont", "words_at": 0.9,
                 "formulas": ["tag", "matrix"]}
    monkeypatch.setenv("READANYDOC_TABLES", "clean=1")
    assert tables.spec_from("merge=layout") == {"clean": True}  # the environment wins
    for bad in ("reread=everything", "accept=trust", "merge=always", "prompt=nice", "formulas=all"):
        with pytest.raises(ValueError):
            tables.parse_spec(bad)


def test_profile_engine_final_has_no_table_switch():
    from readanydoc.profiles import get_profile
    assert "tables" not in get_profile("engine-final").stage("ground").options
    assert "zoom_regions" not in get_profile("engine-final").stage("read").options


def test_profile_engine_final_r2a_is_engine_final_plus_switches():
    from readanydoc.profiles import get_profile
    a, b = get_profile("engine-final"), get_profile("engine-final-r2a")
    spec = tables.parse_spec(b.stage("ground").options["tables"])
    assert spec["reread"] == ["missed", "broken", "all"] and spec["accept"] == "content" and spec["clean"] and spec["prefetch"]
    assert b.stage("read").options["zoom_regions"] == "gate"
    for st in ("triage", "layout", "verify", "shape"):
        assert a.stage(st).options == b.stage(st).options
    ro = dict(b.stage("read").options); ro.pop("zoom_regions")
    go = dict(b.stage("ground").options); go.pop("tables")
    assert ro == a.stage("read").options and go == a.stage("ground").options


# ---------------------------------------------------------------- hygiene
def test_clean_removes_picture_descriptions_only():
    t = ("<table><tr><td>[Icon: green car with double arrow]</td><td>*[Figure: FDA logo]*</td><td>![logo](x.png)</td>"
         "<td>[x] yes [ ] no</td><td>[illegible]</td><td>[signature]</td><td>see [1] and [Type= discrete]</td></tr></table>")
    out = hygiene.clean_table(t)
    assert out.count("<td></td>") == 3
    for kept in ("[x] yes [ ] no", "[illegible]", "[signature]", "see [1] and [Type= discrete]"):
        assert kept in out


def test_clean_lists_and_paragraphs_become_lines():
    t = "<table><tr><td><ul><li>true</li>\n<li>false</li></ul></td><td><p>one</p>\n<p>two</p></td><td><ol><li>a</li><li>b</li></ol></td></tr></table>"
    out = hygiene.clean_table(t)
    assert "<td>• true<br>• false</td>" in out and "<td>one<br>two</td>" in out and "<td>1. a<br>2. b</td>" in out
    assert "<ul" not in out and "<li" not in out and "<p>" not in out


def test_clean_touches_nothing_outside_tables_and_is_idempotent():
    text = "Intro [Figure: a chart]\n\n- item\n\n" + T3
    assert hygiene.clean_content(text) == text
    once = hygiene.clean_table("<table><tr><td><ul><li>a</li></ul>[Icon: x]</td></tr></table>")
    assert hygiene.clean_table(once) == once


def _two_tables(second_header: bool, gap=0.02):
    a = Region(type=RegionType.TABLE, content=T3, bbox=box(0.1, 0.1, 0.9, 0.4))
    hdr = "<tr><th>Item</th><th>M</th><th>NM</th></tr>" if second_header else "<tr><th>General</th><th></th><th></th></tr>"
    b = Region(type=RegionType.TABLE, content="<table>" + hdr + "<tr><td>Roast</td><td>17.00</td><td>18.50</td></tr></table>",
               bbox=box(0.1, 0.4 + gap, 0.9, 0.6))
    return Page(index=0, regions=[a, b], properties={"layout_detections": []})


def test_merge_layout_needs_one_table_region():
    page = _two_tables(False)
    assert hygiene.merge_page(page, "layout") == []  # no table region around both
    page.properties["layout_detections"] = [{"id": "d0", "label": "table", "score": 0.9, "box": [0.08, 0.08, 0.92, 0.62]}]
    rep = hygiene.merge_page(page, "layout")
    assert len(rep) == 1 and len(page.regions) == 1
    assert page.regions[0].content.count("<tr>") == 5 and "<td>General</td>" in page.regions[0].content
    assert ev.shape(page.regions[0].content).consistent is True


def test_merge_layout_not_when_each_table_has_its_own_region():
    page = _two_tables(False)
    page.properties["layout_detections"] = [{"id": "d0", "label": "table", "score": 0.9, "box": [0.08, 0.08, 0.92, 0.41]},
                                            {"id": "d1", "label": "table", "score": 0.9, "box": [0.08, 0.41, 0.92, 0.62]}]
    assert hygiene.merge_page(page, "layout") == []


def test_merge_cont_only_without_own_header_and_directly_below():
    assert len(hygiene.merge_page(_two_tables(False), "cont")) == 1
    assert hygiene.merge_page(_two_tables(True), "cont") == []  # a second table with its own column header stays
    assert hygiene.merge_page(_two_tables(False, gap=0.2), "cont") == []  # far below: another table
    page = _two_tables(False)
    page.regions[1].content = "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"
    assert hygiene.merge_page(page, "cont") == []  # other column count


# ---------------------------------------------------------------- evidence and rules
def _cells(grid):
    """grid: rows of (left, right) per column; rows are 0.05 high."""
    out = []
    for r, row in enumerate(grid):
        for c, (l, rt) in enumerate(row):
            out.append(TableCell(row=r, col=c, text="x", bbox=box(l, 0.1 + 0.05 * r, rt, 0.14 + 0.05 * r)))
    return out


def test_fit_sees_a_value_under_the_wrong_header():
    good = _cells([[(0.1, 0.3), (0.4, 0.5), (0.6, 0.7)]] * 4)
    assert ev.fit(good).cols == 1.0 and ev.fit(good).rows == 1.0 and ev.fit(good).located == 1.0
    bad = _cells([[(0.1, 0.3), (0.4, 0.5), (0.6, 0.7)]] * 3 + [[(0.1, 0.3), (0.6, 0.7), (0.4, 0.5)]])
    assert ev.fit(bad).cols < 1.0


def test_content_compares_numbers_and_words_not_markup():
    other = T3.replace("<th>", "<td>").replace("</th>", "</td>").replace("<td>Fish</td>", "<td><b>Fish</b></td>")
    c = ev.content(T3, other)
    assert c.lowest == 1.0
    assert ev.content(T3, T3.replace("17.50", "17.80", 1)).number_recall < 1.0


def test_decide_table_content_rule():
    f = ev.Fit(cells=9, located=1.0, cols=1.0, rows=1.0, checked=9)
    regrid = "<table><tr><th>Item</th><th colspan=\"2\">M NM</th></tr><tr><td>Fish</td><td>16.00</td><td>17.50</td></tr><tr><td>Parma</td><td>16.00</td><td>17.50</td></tr></table>"
    ok, why, _ = reread.decide_table("table", T3, regrid, f, f, "content", {})
    assert ok, why
    ok, why, _ = reread.decide_table("table", T3, regrid.replace("17.50", "17.80", 1), f, f, "content", {})
    assert not ok and "numbers" in why  # a digit never changes on trust
    ok, why, _ = reread.decide_table("table", T3, regrid.replace("Parma", "Palma"), f, f, "content", {})
    assert not ok and "words" in why
    ragged = regrid.replace("<td>Parma</td>", "")
    assert not reread.decide_table("table", T3, ragged, f, f, "content", {})[0]
    assert not reread.decide_table("table", T3, T3, f, f, "content", {})[0]  # same table: nothing to do
    assert reread.decide_table("table", T3, regrid.replace("17.50", "17.80", 1), f, f, "blind", {})[0]  # reference rule only


def test_decide_table_fit_and_strict_rules():
    regrid = T3.replace("<th>M</th><th>NM</th>", "<th colspan=\"2\">M NM</th>")
    good, worse = ev.Fit(9, 1.0, 1.0, 1.0, 9), ev.Fit(9, 1.0, 0.7, 1.0, 9)
    assert reread.decide_table("table", T3, regrid, good, good, "fit", {})[0]
    assert not reread.decide_table("table", T3, regrid, good, worse, "fit", {})[0]
    assert not reread.decide_table("table", T3, regrid, good, good, "strict", {})[0]
    assert reread.decide_table("table", T3, regrid, worse, good, "strict", {})[0]


MENU = ("<table><tr><td>Fish and Chips with salad</td><td>M: $16.00</td><td>NM: $17.50</td></tr>"
        "<tr><td>Chicken Parma with ham</td><td>M: $16.00</td><td>NM: $17.50</td></tr>"
        "<tr><td>Crumbed Calamari with chips</td><td>M: $17.00</td><td>NM: $18.50</td></tr></table>")
MENU_TEXT = ("**Fish and Chips** with salad\nM: $16.00 NM: $17.50\n\n**Chicken Parma** with ham\nM: $16.00 NM: $17.50\n\n"
             "**Crumbed Calamari** with chips\nM: $17.00 NM: $18.50")


def test_decide_missed_accepts_a_real_grid_with_the_same_text():
    f = ev.Fit(cells=9, located=1.0, cols=1.0, rows=0.7, checked=9)
    ok, why, d = reread.decide_missed(MENU_TEXT, MENU, f, {})
    assert ok, (why, d)


def test_decide_missed_never_turns_text_into_a_table():
    f = ev.Fit(cells=9, located=1.0, cols=1.0, rows=1.0, checked=9)
    assert not reread.decide_missed(MENU_TEXT, "", f, {})[0]  # the model said NOT_A_TABLE
    para = "word " * 40
    prose = f"<table><tr><td>{para}</td><td>{para}</td></tr><tr><td>{para}</td><td>{para}</td></tr><tr><td>{para}</td><td>{para}</td></tr></table>"
    ok, why, _ = reread.decide_missed((para + "\n\n") * 6, prose, f, {})
    assert not ok and "paragraph" in why
    one_col = "<table><tr><td>a b c</td></tr><tr><td>d e f</td></tr><tr><td>g h i</td></tr></table>"
    assert not reread.decide_missed("a b c d e f g h i", one_col, f, {})[0]
    assert not reread.decide_missed(MENU_TEXT + "\n\nA whole paragraph that the table does not hold at all, lost.", MENU, f, {})[0]
    assert not reread.decide_missed(MENU_TEXT, MENU.replace("Fish and Chips", "Fish and Chips and ten invented words that are not on the page"), f, {})[0]
    askew = ev.Fit(cells=9, located=1.0, cols=0.5, rows=1.0, checked=9)
    assert not reread.decide_missed(MENU_TEXT, MENU, askew, {})[0]  # cells do not stand in columns on the page
    unseen = ev.Fit(cells=9, located=0.2, cols=1.0, rows=1.0, checked=2)
    assert not reread.decide_missed(MENU_TEXT, MENU, unseen, {})[0]  # cells not found on the page: no evidence


def test_decide_dropped_needs_ocr_support():
    from collections import Counter
    f = ev.Fit(cells=9, located=0.9, cols=1.0, rows=1.0, checked=9)
    words = ev.tokens(MENU)
    assert reread.decide_dropped(MENU, words, Counter(), f, {})[0]
    assert not reread.decide_dropped(MENU, ["other", "words", "entirely", "seen", "by", "ocr", "here"], Counter(), f, {})[0]
    assert not reread.decide_dropped("", words, Counter(), f, {})[0]


def test_find_missed_only_where_the_read_has_text_and_no_table():
    det = {"id": "d0", "label": "table", "score": 0.9, "box": [0.1, 0.1, 0.9, 0.5]}
    txt = [Region(type=RegionType.TEXT, content="Fish and Chips M: $16.00 NM: $17.50 salad", bbox=box(0.1, 0.1 + 0.1 * k, 0.9, 0.2 + 0.1 * k))
           for k in range(3)]
    page = Page(index=0, regions=txt, properties={"layout_detections": [det]})
    got = reread.find(page, {"missed"})
    assert [c.kind for c in got] == ["missed"] and got[0].idx == [0, 1, 2]
    page.regions = [Region(type=RegionType.TABLE, content=T3, bbox=box(0.1, 0.1, 0.9, 0.5))]
    assert reread.find(page, {"missed"}) == []  # the read has a table there
    page.regions = txt[:2] + [Region(type=RegionType.FOOTER, content="Curried Sausages M: $18.00 NM: $19.50 mash", bbox=box(0.1, 0.3, 0.9, 0.4))]
    assert len(reread.find(page, {"missed"})) == 1  # a last row that Ground typed as furniture does not hide the table
    page.regions = [Region(type=RegionType.KEY_VALUE, content="Name: x " * 5, bbox=box(0.1, 0.1, 0.9, 0.5))]
    assert reread.find(page, {"missed"}) == []  # form fields are not turned into a table
    page.regions = txt
    page.properties["layout_detections"] = [dict(det, score=0.3)]
    assert reread.find(page, {"missed"}) == []  # an unsure detection is not evidence
    assert len(reread.find(page, {"missed"}, {"missed_table_score": 0.2})) == 1  # stress-test option only
    page.properties["layout_detections"] = [dict(det, label="text")]
    assert reread.find(page, {"missed"}) == []


# ---------------------------------------------------------------- formulas
def test_formula_tag_only_at_the_end_of_a_displayed_formula():
    assert formulas.tag_numbers(r"$$f'(x)^2 \leq 2f(x)f''(x). \quad (2)$$") == r"$$f'(x)^2 \leq 2f(x)f''(x). \tag{2}$$"
    assert formulas.tag_numbers(r"$$a = f(1)$$") == r"$$a = f(1)$$"  # an argument, not a number at the margin
    assert formulas.tag_numbers("see equation (2)") == "see equation (2)"
    assert formulas.tag_numbers(r"$$a=b \tag{1}$$") == r"$$a=b \tag{1}$$"


def test_formula_symbols_guard():
    old = "<table><tr><td>1</td><td>0</td><td>$-\\frac{4}{3}$</td></tr><tr><td>0</td><td>1</td><td>0</td></tr></table>"
    new = r"$$\begin{bmatrix} 1 & 0 & -\frac{4}{3} \\ 0 & 1 & 0 \end{bmatrix}$$"
    assert formulas.same_symbols(old, new)[0]
    assert not formulas.same_symbols(old, new.replace("4", "7"))[0]
    assert not formulas.same_symbols(old, new.replace("0 & 1 & 0", "0 & 1 & 0 & x"))[0]


# ---------------------------------------------------------------- zoom regions
def test_zoom_regions_off_is_the_whole_page_cut():
    page = Page(index=0, properties={"layout_detections": [{"label": "table", "score": 0.9, "box": [0.1, 0.2, 0.9, 0.5]}]})
    assert zoom_regions.boxes(page, 3, None) == zoom.strip_boxes(3)
    assert zoom_regions.boxes(Page(index=0), 3, "tables") == zoom.strip_boxes(3)  # no table region: whole page
    assert zoom_regions.boxes(Page(index=0), 3, "tables_only") == []  # ... or no strips at all


def test_zoom_regions_two_sets_are_different_cuts_inside_the_table():
    page = Page(index=0, properties={"layout_detections": [{"label": "table", "score": 0.9, "box": [0.1, 0.2, 0.9, 0.5]},
                                                           {"label": "text", "score": 0.9, "box": [0.1, 0.6, 0.9, 0.9]}]})
    a, b = zoom_regions.boxes(page, 3, "tables"), zoom_regions.boxes(page, 4, "tables")
    assert len(a) == 1 and len(b) == 2 and a != b
    for x0, y0, x1, y1 in a + b:
        assert 0.07 <= x0 and x1 <= 0.93 and 0.18 <= y0 and y1 <= 0.52
    assert zoom_regions.coverage(page) < 0.35
    tall = Page(index=0, properties={"layout_detections": [{"label": "table", "score": 0.9, "box": [0.05, 0.05, 0.95, 0.95]}]})
    assert len(zoom_regions.boxes(tall, 3, "tables")) == 3 and len(zoom_regions.boxes(tall, 4, "tables")) == 4


# ---------------------------------------------------------------- prefetch
class _FakeReader:
    name = "fake"

    def __init__(self):
        self.calls = []

    def read(self, image, hint=None, context=None):
        from readanydoc.readers.base import ReadResult, Usage
        self.calls.append((image, context.prompt))
        return ReadResult(text=T3, stop_reason="end_turn", usage=Usage(input_tokens=10, output_tokens=20))


def _prefetch_setup(monkeypatch):
    from readanydoc.model import Document
    from readanydoc.pipeline.regions import crops
    fake = _FakeReader()
    monkeypatch.setattr(crops, "reader_for", lambda spec: fake)
    monkeypatch.setattr(crops, "crop_png", lambda doc, page, box, pad=0.012, zoom=None, max_dpi=400.0: repr(tuple(box)).encode())
    doc = Document(source="x.png")
    page = Page(index=0, properties={"layout_detections": [
        {"id": "d0", "label": "table", "score": 0.9, "box": [0.1, 0.1, 0.9, 0.3]},
        {"id": "d1", "label": "table", "score": 0.9, "box": [0.1, 0.5, 0.9, 0.7]},
        {"id": "d2", "label": "text", "score": 0.9, "box": [0.1, 0.8, 0.9, 0.9]}]})
    return fake, doc, page


def test_prefetch_off_starts_nothing(monkeypatch):
    from readanydoc.pipeline.tables import prefetch
    fake, doc, page = _prefetch_setup(monkeypatch)
    assert prefetch.start(doc, page, tables.parse_spec("reread=all"), "fake") == 0
    assert prefetch.start(doc, page, tables.parse_spec("prefetch=1;clean=1"), "fake") == 0  # nothing to re-read
    assert prefetch.start(doc, page, tables.parse_spec("reread=missed;prefetch=1"), "fake") == 0  # way-out prompt: another call
    assert fake.calls == []


def test_prefetched_reads_are_used_once_and_unused_ones_billed(monkeypatch):
    from readanydoc.pipeline.tables import prefetch
    fake, doc, page = _prefetch_setup(monkeypatch)
    spec = tables.parse_spec("reread=all;prefetch=1")
    assert prefetch.start(doc, page, spec, "fake") == 2 and page.properties["table_prefetch"] == 2
    prompt = reread.PROMPTS["clean"]
    out = reread._read_jobs(doc, page, [((0.1, 0.1, 0.9, 0.3), prompt, "tread:table:d0")], "fake", 0.012)
    assert out[0] is not None and out[0].text == T3
    assert [u.get("prefetched") for u in doc.usage] == [True]
    assert prefetch.settle(doc, page) == 1  # d1 was started and not used
    assert len(fake.calls) == 2  # the two prefetched reads; the job made no third call
    assert doc.usage[-1]["reason"] == "table_prefetch_unused" and page.properties["table_prefetch_unused"] == 1
    assert prefetch.settle(doc, page) == 0
    out = reread._read_jobs(doc, page, [((0.1, 0.5, 0.9, 0.7), prompt, "tread:table:d1")], "fake", 0.012)
    assert out[0] is not None and len(fake.calls) == 3  # nothing pending any more: read now


def test_zoom_gate_keeps_whole_page_strips_where_layout_shows_a_table_or_form():
    table = Page(index=0, properties={"layout_detections": [{"label": "table", "score": 0.9, "box": [0.1, 0.2, 0.9, 0.5]}]})
    form = Page(index=0, properties={"layout_detections": [{"label": "form", "score": 0.9, "box": [0.1, 0.2, 0.9, 0.5]}]})
    text = Page(index=0, properties={"layout_detections": [{"label": "text", "score": 0.9, "box": [0.1, 0.2, 0.9, 0.5]}]})
    assert zoom_regions.boxes(table, 3, "gate") == zoom.strip_boxes(3) and zoom_regions.boxes(form, 4, "gate") == zoom.strip_boxes(4)
    assert zoom_regions.boxes(text, 3, "gate") == []
    assert zoom_regions.boxes(Page(index=0), 3, "gate") == zoom.strip_boxes(3)  # no layout model ran: unchanged


def test_key_value_block_that_kv_page_makes_text_is_text_for_the_missed_table_rule():
    """Round 2, combine step: one page read wrote a menu as Key Value lines, another as Text; both must be candidates."""
    from readanydoc.pipeline.regions import fields
    det = {"id": "d0", "label": "document_index", "score": 0.84, "box": [0.1, 0.1, 0.9, 0.5], "inside": None}
    menu = Region(type=RegionType.KEY_VALUE, content="**Fish & Chips** M: $16.00 NM: $17.50\n**Chicken Parma** M: $16.00 NM: $17.50",
                  bbox=box(0.1, 0.1, 0.9, 0.5))
    page = Page(index=0, regions=[menu], properties={"layout_detections": [det]})
    assert reread.find(page, {"missed"}) == []                                  # a Key Value block: left alone
    assert fields.mark_text_like(page, [det], {"hfsplit": "on"}) == 0           # kv=page is not on: nothing is marked
    assert fields.mark_text_like(page, [det], {"kv": "page"}) == 1 and menu.type == RegionType.KEY_VALUE
    assert [c.kind for c in reread.find(page, {"missed"})] == ["missed"]
    # a page with a form region, or a block with a blank field, is not touched by kv=page and stays out
    form = {"id": "d1", "label": "form", "score": 0.4, "box": [0.0, 0.0, 1.0, 1.0], "inside": None}
    field = Region(type=RegionType.KEY_VALUE, content="Name: ________ Date: ________ Amount: ________ Signed: ____",
                   bbox=box(0.1, 0.1, 0.9, 0.5))
    for regions, dets in (([Region(type=RegionType.KEY_VALUE, content=menu.content, bbox=box(0.1, 0.1, 0.9, 0.5))], [det, form]),
                          ([field], [det])):
        p = Page(index=0, regions=regions, properties={"layout_detections": dets})
        assert fields.mark_text_like(p, dets, {"kv": "page"}) == 0 and reread.find(p, {"missed"}) == []
