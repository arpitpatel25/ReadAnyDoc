"""kv_lines, checkbox_crops and region_reread with a fake reader: no model call."""
from readanydoc.model import BBox, Document, Page, Region, RegionType, Word
from readanydoc.pipeline.forms import checkbox, lines, locate, region
from readanydoc.readers.base import ReadResult, Usage

from .test_grid import AMOUNTS, CMS


class FakeReader:
    name = "fake"
    needs_image = True

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def read(self, image, hint=None, context=None):
        self.calls.append(context.prompt)
        return ReadResult(text=self.answers[min(len(self.calls) - 1, len(self.answers) - 1)], usage=Usage(input_tokens=10, output_tokens=5))


class FakeStage:
    ctx_reader_spec = "fake"

    def __init__(self, reader, **opts):
        self.reader, self.opts = reader, opts

    def opt(self, k, d=None):
        return self.opts.get(k, d)

    def _reader(self, spec):
        return self.reader


def _words(text, top=0.1):
    out = []
    for i, w in enumerate(text.split()):
        out.append(Word(text=w, bbox=BBox(left=0.05 + 0.03 * (i % 25), top=top + 0.02 * (i // 25), width=0.025, height=0.012), line=i // 25))
    return out


def test_caps_label_lines():
    assert lines.caps_label_line("STUDY NUMBER I-7016.401")
    assert lines.caps_label_line("DATE 2/15/90")
    assert lines.caps_label_line("REASON FOR THE AMENDMENT Request from sponsor")
    for no in ("TABLE 3 Results of the test", "USA Today reported strong sales", "DEPARTMENT NAME SCIENCE & TECHNOLOGY",
               "The committee met on Friday", "NOTE This is a remark.", "ANNUAL REPORT",
               "PRINT NAME AND ADDRESS PLAINLY. (See Instruction C)",
               "APPROVED BY NATIONAL UNIFORM CLAIM COMMITTEE (NUCC) 02/12"):
        assert not lines.caps_label_line(no), no


def test_open_label_takes_the_next_short_block_and_caps_lines_are_typed():
    page = Page(index=0, regions=[
        Region(type=RegionType.KEY_VALUE, content="INDEX BINDER RE-LABELED BY:"),
        Region(type=RegionType.TEXT, content="*Doris Mallory*"),
        Region(type=RegionType.TEXT, content="DATE 2/15/90"),
        Region(type=RegionType.KEY_VALUE, content="Approved: [x]"),
        Region(type=RegionType.TEXT, content="This paragraph explains the schedule. It has two sentences.")])
    info = lines.apply(page)
    assert [r.type for r in page.regions] == [RegionType.KEY_VALUE, RegionType.KEY_VALUE, RegionType.KEY_VALUE, RegionType.TEXT]
    assert page.regions[0].content == "INDEX BINDER RE-LABELED BY:\n*Doris Mallory*"
    assert info == {"value_blocks_joined": 1, "typed_key_value": 1}


def test_caps_lines_need_a_form_page():
    page = Page(index=0, regions=[Region(type=RegionType.TEXT, content="DATE 2/15/90"),
                                  Region(type=RegionType.TEXT, content="Plain paragraph of a letter.")])
    assert lines.apply(page) == {} and page.regions[0].type == RegionType.TEXT


def test_checkbox_marks_and_alignment():
    read = "Self [ ] Spouse [x] Child [ ]\n[ ] YES [ ] NO"
    ms = checkbox.marks_in(read)
    assert [m["mark"] for m in ms] == ["[ ]", "[x]", "[ ]", "[ ]", "[ ]"]
    crop = "Self [x] Spouse [ ] Child [ ]\n[x] YES [ ] NO"
    got = checkbox.crop_states(read, crop)
    assert [got[m["start"]] for m in ms] == [True, False, False, True, False]
    # a crop that shows other words pairs nothing: a mark needs a neighbouring word in common
    assert checkbox.crop_states(read, "[x] alpha [ ] beta") == {}


def _cb_page():
    text = "6. PATIENT RELATIONSHIP TO INSURED Self [ ] Spouse [x] Child [ ] Other [ ]"
    page = Page(index=0, regions=[Region(type=RegionType.KEY_VALUE, content=text, source="reader")],
                words={"tesseract": _words(text.replace("[ ]", "").replace("[x]", ""))})
    doc = Document(source="x.png", file_type="image", pages=[page])
    return doc, page


def test_checkbox_crops_change_a_mark_only_when_both_crops_agree(monkeypatch):
    monkeypatch.setattr(locate, "crop_png", lambda doc, page, box, zoom=None: b"png")
    doc, page = _cb_page()
    both = FakeReader(["6. PATIENT RELATIONSHIP TO INSURED Self [x] Spouse [ ] Child [ ] Other [ ]"] * 2)
    info = checkbox.apply(doc, page, FakeStage(both))
    assert info["crop_reads"] == 2 and info["changed"] == 2
    assert page.regions[0].content.endswith("Self [x] Spouse [ ] Child [ ] Other [ ]")
    assert len(doc.usage) == 2 and doc.usage[0]["escalation"] == "checkbox_crop"
    doc, page = _cb_page()
    split = FakeReader(["Self [x] Spouse [ ] Child [ ] Other [ ]", "Self [ ] Spouse [x] Child [ ] Other [ ]"])
    assert checkbox.apply(doc, page, FakeStage(split))["changed"] == 0
    assert "Spouse [x]" in page.regions[0].content  # the page read stays


def test_region_reread_replaces_only_the_form_run_and_only_with_the_same_words(monkeypatch):
    monkeypatch.setattr(locate, "crop_png", lambda doc, page, box, zoom=None: b"png")
    from readanydoc.pipeline.forms.grid import to_key_value

    def page_():
        intro, tail = "Intro paragraph that stays as it is.", "Closing paragraph that stays as well."
        plain = intro + " " + to_key_value(CMS).replace("[ ]", "") + " " + tail
        return Page(index=0, words={"tesseract": _words(plain)}, regions=[
            Region(type=RegionType.TEXT, content=intro), Region(type=RegionType.TABLE, content=CMS),
            Region(type=RegionType.TABLE, content=AMOUNTS), Region(type=RegionType.TEXT, content=tail)])
    good = to_key_value(CMS).replace("1a. INSURED'S I.D. NUMBER (For Program in Item 1)", "1a. INSURED'S I.D. NUMBER (For Program in Item 1):")
    page = page_(); doc = Document(source="x.png", file_type="image", pages=[page])
    info = region.apply(doc, page, FakeStage(FakeReader([good])))
    assert info["replaced"] == 1 and page.regions[0].content.startswith("Intro") and page.regions[-1].content.startswith("Closing")
    assert page.regions[-2].content == AMOUNTS and not any("<table" in r.content for r in page.regions[1:-2])
    assert any(c.content == CMS and c.meta.get("replaced") for c in page.regions[1].candidates)
    page = page_(); doc = Document(source="x.png", file_type="image", pages=[page])
    info = region.apply(doc, page, FakeStage(FakeReader(["1. MEDICARE [ ]\nsomething else entirely"])))
    assert info["replaced"] == 0 and page.regions[1].content == CMS and "words differ" in info["kept"][0]


def test_accept_rule():
    assert region.accept("a b c d e f g h i j k l m", "a b c d e f g h i j k l m: x")[0]
    assert not region.accept("a b c d e f g h i j", "a b c d e")[0]
    assert not region.accept("a b c d", "a b c d e f g h")[0]


def test_layout_regions_from_workstream_1():
    page = Page(index=0, properties={"layout_detections": [
        {"id": "d1", "label": "form", "score": 0.9, "box": [0.1, 0.1, 0.9, 0.5], "inside": None},
        {"id": "d2", "label": "key_value_region", "score": 0.8, "box": [0.2, 0.2, 0.5, 0.3], "inside": "d1"},
        {"id": "d3", "label": "key_value_region", "score": 0.2, "box": [0.1, 0.6, 0.9, 0.7], "inside": None},
        {"id": "d4", "label": "table", "score": 0.9, "box": [0.1, 0.7, 0.9, 0.9], "inside": None}]})
    assert locate.regions_from_layout(page) == [(0.1, 0.1, 0.9, 0.5)]
    assert locate.regions_from_layout(Page(index=0)) == []
    assert locate.inside((0.2, 0.2, 0.4, 0.3), (0.1, 0.1, 0.9, 0.5)) and not locate.inside((0.2, 0.6, 0.4, 0.7), (0.1, 0.1, 0.9, 0.5))


def test_kv_lines_halves_run_alone():
    def page():
        return Page(index=0, regions=[
            Region(type=RegionType.KEY_VALUE, content="INDEX BINDER RE-LABELED BY:"),
            Region(type=RegionType.TEXT, content="*Doris Mallory*"),
            Region(type=RegionType.TEXT, content="DATE 2/15/90"),
            Region(type=RegionType.KEY_VALUE, content="Approved: [x]")])
    p = page()
    assert lines.apply(p, caps=False) == {"value_blocks_joined": 1, "typed_key_value": 0}
    assert [r.type for r in p.regions] == [RegionType.KEY_VALUE, RegionType.TEXT, RegionType.KEY_VALUE]
    p = page()
    assert lines.apply(p, join=False) == {"value_blocks_joined": 0, "typed_key_value": 1}
    assert len(p.regions) == 4 and p.regions[2].type == RegionType.KEY_VALUE
