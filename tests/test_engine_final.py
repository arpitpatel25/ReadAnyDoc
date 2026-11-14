"""Combine phase of the final round: the ``engine-final`` profile, the guarded rotation method and step seconds."""

import pytest

from readanydoc.intake import prepare
from readanydoc.profiles import get_profile
from readanydoc.readers import usage


def test_engine_final_profile_has_every_piece_as_one_option():
    cfg = get_profile("engine-final")
    assert cfg.words == "tesseract>rapidocr" and cfg.layout == "onnx:heron"
    assert cfg.stage("triage").options == {"prepare": "rotate", "prepare_method": "safe"}
    read = cfg.stage("read").options
    assert read["zoom_vote"] and read["zoom_pre_gate"] == 20 and read["zoom_prefetch"] == 1
    assert read["zoom_gate"] == "table_legible" and read["handwriting_route"] and read["page_workers"] == 16
    assert "fillable" in read["forms"]
    assert cfg.stage("verify").options["checks"] == "grid=norm+draft;numbers=1;flags=1"
    ground = cfg.stage("ground")
    assert ground.impl == "ground_regions" and "tables=" not in ground.options["regions"]  # one table repair only
    assert cfg.stage("layout").options == {"detect_only": True}


def test_engine_final_pieces_switch_off():
    from readanydoc.pipeline import checks
    from readanydoc.pipeline.regions import Switches
    cfg = get_profile("engine-final")
    off = cfg.with_stage("triage", prepare="").with_stage("verify", checks="").with_stage("ground", regions="") \
             .with_stage("read", zoom_vote=False, handwriting_route=False, forms="")
    assert not off.stage("triage").options["prepare"]
    assert checks.spec_from(off.stage("verify").options["checks"]) == {}
    assert not Switches.parse(off.stage("ground").options["regions"]).any()
    assert Switches.parse(cfg.stage("ground").options["regions"]).any()


OCR_UPRIGHT_UNSURE = (0, {"method": "ocr", "lines": 30, "sideways_share": 0.0, "score_upright": 0.43,
                          "score_flipped": 0.23, "reason": "direction unclear"})
OCR_FLIPPED_UNSURE = (0, {"method": "ocr", "lines": 30, "sideways_share": 0.0, "score_upright": 0.30,
                          "score_flipped": 0.45, "reason": "direction unclear"})
OCR_SIDEWAYS_SURE = (270, {"method": "ocr", "lines": 24, "sideways_share": 1.0, "score_upright": 0.1, "score_flipped": 0.9})
OCR_SIDEWAYS_UNSURE = (0, {"method": "ocr", "lines": 24, "sideways_share": 1.0, "score_upright": 0.5,
                           "score_flipped": 0.45, "reason": "sideways text, direction unclear"})
OCR_NO_TEXT = (0, {"method": "ocr", "lines": 1, "reason": "too little text"})
OSD_180 = (180, {"method": "osd", "confidence": 7.6})
OSD_0 = (0, {"method": "osd", "confidence": 16.5})
OSD_90 = (90, {"method": "osd", "confidence": 5.0})
OSD_UNSURE = (0, {"method": "osd", "confidence": 0.4, "reason": "low confidence"})


@pytest.mark.parametrize("ocr_says, osd_says, want", [
    (OCR_UPRIGHT_UNSURE, OSD_180, 0),      # handwritten letter: OCR leans upright, Tesseract says upside down -> stays
    (OCR_FLIPPED_UNSURE, OSD_180, 180),    # both lean upside down -> turned
    (OCR_FLIPPED_UNSURE, OSD_UNSURE, 0),
    (OCR_SIDEWAYS_SURE, OSD_0, 0),         # vertical writing: tall line boxes, but the page reads upright -> stays
    (OCR_SIDEWAYS_SURE, OSD_UNSURE, 270),
    (OCR_SIDEWAYS_SURE, OSD_90, 270),      # OCR is sure of the direction; Tesseract only has a veto for "not sideways"
    (OCR_SIDEWAYS_UNSURE, OSD_90, 90),
    (OCR_SIDEWAYS_UNSURE, OSD_180, 0),
    (OCR_NO_TEXT, OSD_180, 180),
    (OCR_NO_TEXT, OSD_UNSURE, 0),
])
def test_safe_rotation_lets_tesseract_decide_only_what_ocr_leaves_open(monkeypatch, ocr_says, osd_says, want):
    monkeypatch.setattr(prepare, "image_turn_ocr", lambda im, **k: (ocr_says[0], dict(ocr_says[1])))
    monkeypatch.setattr(prepare, "image_turn_osd", lambda path, **k: (osd_says[0], dict(osd_says[1])))
    turn, info = prepare.image_turn(None, "safe", "page.png")
    assert turn == want and info["method"] == "safe"


def test_step_seconds_groups_calls_by_step():
    s = usage.summarize([
        {"model": "claude-haiku-5-5", "input_tokens": 10, "output_tokens": 20, "seconds": 12.0},
        {"model": "claude-haiku-5-5", "input_tokens": 1, "output_tokens": 2, "seconds": 3.0, "escalation": "zoom_strip", "reason": "za"},
        {"model": "claude-haiku-5-5", "input_tokens": 1, "output_tokens": 2, "seconds": 5.0, "escalation": "zoom_strip", "reason": "za"},
        {"model": "claude-sonnet-5-5", "input_tokens": 1, "output_tokens": 2, "seconds": 9.0, "escalation": "page", "reason": "handwriting"},
        {"model": "claude-haiku-5-5", "input_tokens": 1, "output_tokens": 2, "escalation": "verify:grid"},     # not timed
    ])
    assert s["calls"] == 5
    assert s["steps"]["page"] == {"calls": 1, "seconds": 12.0, "seconds_max": 12.0}
    assert s["steps"]["zoom_strip:za"] == {"calls": 2, "seconds": 8.0, "seconds_max": 5.0}
    assert s["steps"]["page:handwriting"]["seconds"] == 9.0 and "verify:grid" not in s["steps"]
    assert "steps" not in usage.summarize([{"model": "claude-haiku-5-5", "input_tokens": 1, "output_tokens": 1}])


def _scan(ocr_words, conf=0.9):
    from readanydoc.model import Confidence, Page, Word
    p = Page(index=0, kind="scanned")
    p.properties["text_layer_exact"] = False
    p.words["tesseract"] = [Word(text=t, confidence=Confidence.of(conf), source="tesseract") for t in ocr_words]
    return p


def test_illegible_mark_on_a_mostly_printed_page_keeps_the_first_read_when_guarded():
    from readanydoc.pipeline.reading import handwriting
    printed = "Date of report March 4 1994 brand name Winston lights region east account manager name".split()
    read = " ".join(printed) + " signed [illegible]"
    assert handwriting.detect(_scan(printed), read).route                                  # as before: one mark routes
    s = handwriting.detect(_scan(printed), read, illegible_max_agreement=0.5)
    assert not s.route and "mostly print" in s.reason and s.ocr_agreement >= 0.5
    garbage = ["lk", "wv~", "ot", "Jn", "mm", "oo", "e", "tt"]
    letter = "Springfield June 4th 1861 one day after date I promise to pay William [illegible] twenty dollars"
    assert handwriting.detect(_scan(garbage, conf=0.3), letter, illegible_max_agreement=0.5).route   # handwriting still routes
    assert handwriting.detect(_scan([]), letter, illegible_max_agreement=0.5).route                  # no OCR evidence: routes


def test_form_crop_never_replaces_a_region_that_holds_filled_form_fields(monkeypatch):
    from readanydoc.model import BBox, Document, Page, Region, RegionType
    from readanydoc.pipeline.regions import crops, forms

    def page():
        p = Page(index=0, width=612, height=792)
        box = BBox(left=0.1, top=0.1, width=0.8, height=0.3)
        p.regions = [Region(type=RegionType.KEY_VALUE, content="Name Ann Lee\n\nDate 3 May 1999\n\nAmount 12.50",
                            text="Name Ann Lee Date 3 May 1999 Amount 12.50", bbox=box)]
        p.properties["layout_detections"] = [{"id": "d0", "label": "form", "type": "Key Value", "score": 0.9,
                                              "box": [0.1, 0.1, 0.9, 0.4], "order": 0, "inside": None}]
        return p

    class Res:
        text = "Name: Ann Lee\nDate: 3 May 1999\nAmount: 12.50"
    calls = []
    monkeypatch.setattr(crops, "read_many", lambda doc, pg, jobs, spec, **k: calls.append(len(jobs)) or [Res() for _ in jobs])
    doc = Document(source="x.pdf")
    blank = page()
    blank.properties["form_fields"] = [{"type": "text", "value": "", "bbox": [0.2, 0.15, 0.5, 0.18]}]
    assert forms.run(doc, blank, "reader") is True and calls == [1]            # a blank fillable form may be re-read
    filled = page()
    filled.properties["form_fields"] = [{"type": "text", "value": "Ann Lee", "bbox": [0.2, 0.15, 0.5, 0.18]}]
    assert forms.run(doc, filled, "reader") is False and calls == [1]          # values from the file stay
    assert filled.properties["region_forms_skipped"][0]["detection"] == "d0"
    ticked = page()
    ticked.properties["form_fields"] = [{"type": "checkbox", "checked": True, "value": "", "bbox": [0.2, 0.15, 0.22, 0.17]}]
    assert forms.run(doc, ticked, "reader") is False
    elsewhere = page()
    elsewhere.properties["form_fields"] = [{"type": "text", "value": "Ann Lee", "bbox": [0.2, 0.7, 0.5, 0.75]}]
    assert forms.run(doc, elsewhere, "reader") is True                          # the filled field is outside the region


# ---- round 2, combine step: profile engine-final-2 ----

def test_engine_final_2_is_engine_final_plus_both_round_2_sets():
    from readanydoc.pipeline import tables
    from readanydoc.pipeline.regions import Switches
    base, a, b, c = (get_profile(n) for n in ("engine-final", "engine-final-r2a", "engine-final-r2b", "engine-final-2"))
    assert c.profile == "engine-final-2"
    assert c.stage("ground").options["tables"] == a.stage("ground").options["tables"]
    assert c.stage("ground").options["regions"].replace(",kvsplit", "") == b.stage("ground").options["regions"]
    assert c.stage("read").options == {**a.stage("read").options, **b.stage("read").options}
    for name in ("triage", "layout", "verify", "shape"):
        assert c.stage(name).options == base.stage(name).options
    spec = tables.parse_spec(c.stage("ground").options["tables"])
    assert spec["reread"] == ["missed", "broken", "all"] and spec["accept"] == "content" and spec["prefetch"]
    more = Switches.parse(c.stage("ground").options["regions"]).more
    assert more == {"hfsplit": "on", "kvsplit": "on", "kv": "page", "formread": "on", "boxfill": "on"}  # no figures
    assert not c.stage("shape").options.get("layout_figures")                           # the figure switch stays off
    assert base.stage("ground").options.get("tables") is None                           # round 1 profile untouched


def test_one_form_crop_read_serves_both_form_steps_of_a_page(monkeypatch):
    """Round 1's guarded form crops and round 2's form evidence ask for the same crop with the same prompt."""
    from types import SimpleNamespace
    from readanydoc.pipeline.regions import crops
    calls = []

    def fake(doc, page, box, prompt, reader_spec, tag, pad=0.012, zoom=None):
        calls.append((tuple(box), prompt, tag))
        return SimpleNamespace(text=f"read {len(calls)}")

    monkeypatch.setattr(crops, "read_crop", fake)
    page = SimpleNamespace(properties={}, index=0)
    jobs = [((0.1, 0.1, 0.5, 0.5), crops.FORM_PROMPT, "form:1"), ((0.5, 0.5, 0.9, 0.9), crops.FORM_PROMPT, "form:2")]
    with crops.shared_reads(page):
        first = crops.read_many(None, page, jobs, "r")
        second = crops.read_many(None, page, jobs[:1] + [((0.1, 0.1, 0.5, 0.5), crops.TABLE_PROMPT, "t")], "r")
    assert len(calls) == 3 and second[0] is first[0] and second[1].text == "read 3"   # other prompt: its own read
    assert page.properties["region_reads_shared"] == 1
    crops.read_many(None, page, jobs[:1], "r")                                          # outside: nothing is kept
    assert len(calls) == 4 and id(page) not in crops._SHARED


def test_engine_final_2_is_selected_by_name_and_has_the_cheaper_tier(monkeypatch):
    from readanydoc.api import service
    monkeypatch.delenv("READANYDOC_PROFILE", raising=False)
    assert service.profile_for("r-1") != "engine-final-2"          # not the default: the API tests rely on the default
    monkeypatch.setenv("READANYDOC_PROFILE", "engine-final-2")
    assert service.profile_for("r-1") == service.profile_for(None) == "engine-final-2"
    assert service.profile_for("r-1-mini") == service.profile_for("legacy") == "vanilla-api"


def test_region_reads_are_timed_by_kind():
    steps = usage.step_seconds([
        {"escalation": None, "seconds": 9.0, "input_tokens": 1, "output_tokens": 1},
        {"escalation": "region", "reason": "tread:pre:d3", "seconds": 7.0, "input_tokens": 1, "output_tokens": 1},
        {"escalation": "region", "reason": "table_prefetch_unused", "seconds": 4.0, "input_tokens": 1, "output_tokens": 1},
        {"escalation": "region", "reason": "form:d5", "seconds": 3.0, "input_tokens": 1, "output_tokens": 1},
        {"escalation": "region", "reason": "table:d1", "seconds": 2.0, "input_tokens": 1, "output_tokens": 1}])
    assert steps["region:table"] == {"calls": 2, "seconds": 11.0, "seconds_max": 7.0}
    assert steps["region:form"]["seconds_max"] == 3.0 and steps["region"]["calls"] == 1 and steps["page"]["calls"] == 1
