import pytest

from readanydoc.model import BBox, Confidence, Document, Page, Region, RegionType, Word
from readanydoc.pipeline import checks
from readanydoc.pipeline.checks import flags, gridrepair, htmlgrid, numbers
from readanydoc.pipeline.checks.common import PageChecks

SHORT = ("<table><tr><td>Item</td><td>Old</td><td>New</td></tr><tr><td>Alpha</td><td>2</td></tr>"
         "<tr><td>Beta</td><td>4</td><td>5</td></tr></table>")
FIXED = SHORT.replace("<td>Alpha</td><td>2</td>", "<td>Alpha</td><td></td><td>2</td>")
TITLE = ("<table><tr><td>Item</td><td>Old</td><td>New</td></tr><tr><td>Section one</td></tr>"
         "<tr><td>Beta</td><td>4</td><td>5</td></tr></table>")
SUM_BAD = ("<table><tr><th>Item</th><th>Amount</th></tr><tr><td>Member banks</td><td>12,522,980</td></tr>"
           "<tr><td>Nonmember banks</td><td>9,186,684</td></tr><tr><td>Other</td><td>5,515,175</td></tr>"
           "<tr><td>Total</td><td>27,174,839</td></tr></table>")
SUM_OK = SUM_BAD.replace("9,186,684", "9,136,684")


class Fake(PageChecks):
    answers: dict = {}
    seen: list = []

    def box_of(self, region):
        return (0.1, 0.1, 0.9, 0.5)

    def located(self):
        return None

    def ask(self, box, prompt, tag, zoom=None):
        self.calls += 1
        self.seen.append((tag, prompt))
        return self.answers.get(tag.rstrip("0123456789"))


def make(content, answers=None, **options):
    page = Page(index=0, width=612, height=792)
    page.regions = [Region(type=RegionType.TEXT, content="Intro", text="Intro"),
                    Region(type=RegionType.TABLE, content=content, text=content)]
    doc = Document(source="x.pdf", stem="x", file_type="pdf", pages=[page])
    pc = Fake(doc=doc, page=page, reader_spec="none", options=options)
    pc.answers, pc.seen = dict(answers or {}), []
    return pc, page.regions[1]


def test_spec_parsing():
    assert checks.parse_spec(None) == {} and checks.parse_spec("") == {}
    s = checks.parse_spec("grid=norm+draft; complete=text+scan; numbers=1; flags=1; grid_numbers_at=0.97")
    assert s == {"grid": ["norm", "draft"], "complete": ["text", "scan"], "numbers": True, "flags": True,
                 "grid_numbers_at": 0.97}
    assert checks.parse_spec("numbers=0;flags=off") == {}
    assert checks.parse_spec({"grid": ["crop"], "numbers": True}) == {"grid": ["crop"], "numbers": True}
    with pytest.raises(ValueError):
        checks.parse_spec("grid=magic")


def test_env_switch(monkeypatch):
    monkeypatch.delenv(checks.ENV, raising=False)
    assert checks.spec_from(None) == {}
    monkeypatch.setenv(checks.ENV, "numbers=1")
    assert checks.spec_from(None) == {"numbers": True}
    assert checks.spec_from("grid=norm") == {"grid": ["norm"]}  # the stage option wins


def test_grid_crop_reread_accepted_only_when_consistent_and_same_text():
    pc, region = make(SHORT, {"grid_crop": FIXED})
    gridrepair.run(pc, ["crop"])
    assert region.content == FIXED and region.meta["checks"][0]["status"] == "repaired"
    assert region.candidates[0].content == SHORT and region.candidates[0].meta["before_checks"]

    pc, region = make(SHORT, {"grid_crop": FIXED.replace("<td>5</td>", "<td>6</td>")})  # a digit changed
    gridrepair.run(pc, ["crop"])
    assert region.content == SHORT and region.meta["checks"][0]["status"] == "unresolved"

    pc, region = make(SHORT, {"grid_crop": SHORT})  # still inconsistent
    gridrepair.run(pc, ["crop"])
    assert region.content == SHORT and region.meta["checks"][0]["status"] == "unresolved"


def test_grid_modes_are_tried_in_order_and_draft_prompt_carries_the_draft():
    pc, region = make(TITLE, {"grid_draft": "should not be asked"})
    gridrepair.run(pc, ["norm", "draft"])
    assert pc.calls == 0 and htmlgrid.consistent(region.content) and region.meta["checks"][0]["how"] == "norm"

    pc, region = make(SHORT, {"grid_draft": "Here it is:\n" + FIXED})
    gridrepair.run(pc, ["norm", "draft"])
    assert pc.calls == 1 and region.content == FIXED and region.meta["checks"][0]["how"] == "draft"
    assert SHORT in pc.seen[0][1]


def test_consistent_tables_are_never_touched():
    pc, region = make(FIXED, {"grid_crop": "<table><tr><td>x</td></tr><tr><td>y</td></tr></table>"})
    gridrepair.run(pc, ["norm", "crop", "draft"])
    assert pc.calls == 0 and region.content == FIXED and "checks" not in region.meta


def test_totals_reread_changes_only_the_reconciling_cell():
    pc, region = make(SUM_BAD, {"totals": SUM_OK.replace("12,522,980", "12,522,930")})
    numbers.run(pc)
    assert region.content == SUM_OK and region.meta["checks"][0]["status"] == "repaired"
    assert region.meta["totals"] == {"found": 1, "reconciled": 1}


def test_totals_unresolved_near_miss_is_recorded_and_far_miss_is_not():
    pc, region = make(SUM_BAD, {"totals": SUM_BAD.replace("9,186,684", "9,166,684")})  # re-read differs, still no sum
    numbers.run(pc)
    assert region.content == SUM_BAD and [e["status"] for e in region.meta["checks"]] == ["unresolved"]
    pc, region = make(SUM_BAD)
    numbers.run(pc, reread_cells=False)  # detection only (flags without the repair)
    assert pc.calls == 0 and region.meta["checks"][0]["status"] == "unresolved"
    far = SUM_BAD.replace("27,174,839", "1,000")
    pc, region = make(far, {"totals": far})
    numbers.run(pc)
    assert pc.calls == 0 and "checks" not in region.meta and region.meta["totals"]["not_understood"] == 1


def test_reread_that_agrees_confirms_the_page_read():
    pc, region = make(SUM_BAD, {"totals": SUM_BAD})
    numbers.run(pc)
    assert pc.calls >= 1 and "checks" not in region.meta and region.meta["totals"]["confirmed_by_reread"] == 1


def test_rounding_is_not_a_near_miss():
    t = ("<table><tr><th>Item</th><th>Amount</th></tr>" + "".join(f"<tr><td>r{i}</td><td>10</td></tr>" for i in range(8))
         + "<tr><td>Total</td><td>83</td></tr></table>")
    tt = numbers.table_totals(t)
    assert not tt[0].ok and not tt[0].near


def test_reconciling_totals_cost_nothing():
    pc, region = make(SUM_OK)
    numbers.run(pc)
    assert pc.calls == 0 and "checks" not in region.meta


def test_flags_lower_confidence_and_list_the_checks():
    pc, region = make(SHORT)
    region.confidence = Confidence.of(0.95)
    flags.detect(pc, set())
    assert flags.apply(pc) == 1
    assert region.confidence.label == "low" and region.confidence.score <= 0.5
    assert region.meta["flagged"] and region.meta["checks_listed"][0]["check"] == "table_grid"
    assert "flagged" not in pc.page.regions[0].meta


def test_repaired_blocks_are_listed_but_not_flagged():
    pc, region = make(TITLE)
    gridrepair.run(pc, ["norm"])
    flags.detect(pc, {"grid"})
    assert flags.apply(pc) == 0 and region.meta["checks_listed"][0]["status"] == "repaired" and "flagged" not in region.meta


def test_serializer_and_ground_keep_the_flag():
    from readanydoc.pipeline.base import STAGES
    from readanydoc.pipeline import ground  # noqa: F401
    from readanydoc.serialize.reducto import to_parse_response
    pc, region = make(SHORT)
    flags.detect(pc, set())
    flags.apply(pc)
    STAGES["ground"](tesseract="off")._page(pc.doc, pc.page)
    assert region.confidence.label == "low"
    block = to_parse_response(pc.doc)["result"]["chunks"][0]["blocks"][1]
    assert block["confidence"] == "low" and block["extra"]["checks"][0]["status"] == "unresolved"
    assert "checks" not in to_parse_response(pc.doc)["result"]["chunks"][0]["blocks"][0]["extra"]


def test_verify_stage_is_unchanged_without_the_switch(monkeypatch):
    from readanydoc.pipeline.base import PipelineConfig, PipelineContext, STAGES
    from readanydoc.pipeline import verify  # noqa: F401
    monkeypatch.delenv(checks.ENV, raising=False)
    pc, region = make(SHORT)
    before = pc.doc.model_dump()
    out = STAGES["verify"]().run(pc.doc, PipelineContext(PipelineConfig()))
    assert out.model_dump() == before
    out = STAGES["verify"](checks="grid=pad").run(pc.doc, PipelineContext(PipelineConfig()))
    assert htmlgrid.consistent(out.pages[0].regions[1].content) is True
