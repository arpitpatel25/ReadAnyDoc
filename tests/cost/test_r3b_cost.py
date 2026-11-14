"""Round 3, workstream B: spending cap, strip effort and schedule, form prefetch, re-read triggers, grid mode ``pre``.
No model call: readers are fakes."""
import pytest

from readanydoc.model import BBox, Document, Page, Region, RegionType
from readanydoc.pipeline import checks, tables
from readanydoc.pipeline.checks import gridrepair
from readanydoc.pipeline.checks.common import PageChecks
from readanydoc.pipeline.reading import budget, form_prefetch, strips, zoom, zoom_schedule
from readanydoc.pipeline.regions import crops
from readanydoc.pipeline.tables import prefetch, reread
from readanydoc.readers.base import ReadResult, Usage

T3 = "<table><tr><th>Item</th><th>M</th></tr><tr><td>Fish</td><td>16.00</td></tr><tr><td>Parma</td><td>17.50</td></tr></table>"
BROKEN = "<table><tr><td>Item</td><td>M</td></tr><tr><td>Fish</td></tr><tr><td>Parma</td><td>17.50</td></tr></table>"
FIXED = "<table><tr><td>Item</td><td>M</td></tr><tr><td>Fish</td><td></td></tr><tr><td>Parma</td><td>17.50</td></tr></table>"


class FakeReader:
    name = "anthropic_api:claude-haiku-5-5"

    def __init__(self, text=T3, tokens=(1000, 2000)):
        self.calls, self.text, self.tokens = [], text, tokens

    def read(self, image, hint=None, context=None):
        self.calls.append((image, context.prompt))
        return ReadResult(text=self.text, stop_reason="end_turn",
                          usage=Usage(model="claude-haiku-5-5", input_tokens=self.tokens[0], output_tokens=self.tokens[1]))


def setup(monkeypatch, dets=None, **reader):
    fake = FakeReader(**reader)
    monkeypatch.setattr(crops, "reader_for", lambda spec: fake)
    monkeypatch.setattr(crops, "crop_png", lambda doc, page, box, pad=0.012, zoom=None, max_dpi=400.0: repr(tuple(box)).encode())
    page = Page(index=0, properties={"layout_detections": dets if dets is not None else [
        {"id": "f0", "label": "form", "score": 0.9, "box": [0.1, 0.1, 0.9, 0.3]},
        {"id": "f1", "label": "key_value_region", "score": 0.8, "box": [0.1, 0.5, 0.9, 0.7]},
        {"id": "t0", "label": "table", "score": 0.9, "box": [0.1, 0.75, 0.9, 0.95]}]})
    doc = Document(source="x.png", pages=[page])
    return fake, doc, page


def page_read(doc, usd_tokens=(1000, 2000)):
    doc.usage.append({"page": 0, "reader": "anthropic_api:claude-haiku-5-5", "model": "claude-haiku-5-5",
                      "input_tokens": usd_tokens[0], "output_tokens": usd_tokens[1]})   # 0.0011 USD


# ---------------------------------------------------------------- spending cap
def test_no_cap_allows_everything_and_records_nothing():
    doc = Document(source="x.png", pages=[Page(index=0)])
    assert budget.configure(doc, None, None) is None and not budget.active(doc)
    assert budget.allow(doc, doc.pages[0], "zoom_strip", calls=7)
    assert "spend_cap" not in doc.pages[0].properties and budget.close(doc) is None and budget.summary([doc]) is None


def test_cap_skips_steps_that_do_not_fit_and_records_them():
    doc = Document(source="x.png", pages=[Page(index=0), Page(index=1)])
    budget.configure(doc, usd=0.003)
    page_read(doc)                                            # page 0 has spent 0.0011
    assert budget.spent(doc, 0)[0] == pytest.approx(0.0011) and budget.spent(doc, 1) == (0.0, 0)
    assert budget.estimate(doc, 0, "form")[0] == pytest.approx(0.0011 * budget.RATIO["form"])
    assert budget.allow(doc, doc.pages[0], "form", tag="form:f0")              # 0.0011 + 0.00121 fits
    doc.usage.append({"page": 0, "reader": "x", "model": "claude-haiku-5-5", "escalation": "region", "reason": "form:f0",
                      "input_tokens": 1000, "output_tokens": 2000})
    assert not budget.allow(doc, doc.pages[0], "zoom_strip", calls=3, tag="za")  # 0.0022 + 3 strips does not fit
    assert not budget.allow(doc, doc.pages[0], "handwriting", reader="anthropic_api:sonnet")
    out = budget.close(doc)
    assert out["cap_usd"] == 0.003 and out["pages_with_skips"] == 1 and out["pages_over"] == 0
    assert [s["step"] for s in out["skipped"]] == ["zoom_strip:za", "handwriting"] and out["skipped"][0]["page"] == 1
    rec = doc.pages[0].properties["spend_cap"]
    assert rec["spent_usd"] == pytest.approx(0.0022) and "over_usd" not in rec
    assert budget.close(doc)["skipped"] == out["skipped"]     # safe to call twice


def test_token_cap_and_a_page_read_dearer_than_the_cap():
    doc = Document(source="x.png", pages=[Page(index=0)])
    budget.configure(doc, tokens=2500)
    page_read(doc)                                            # 3000 tokens: the page read alone is over
    assert not budget.allow(doc, doc.pages[0], "table")
    out = budget.close(doc)
    assert out["pages_over"] == 1 and doc.pages[0].properties["spend_cap"]["over_tokens"] == 500


def test_handwriting_estimate_uses_the_price_of_its_model():
    doc = Document(source="x.png", pages=[Page(index=0)])
    page_read(doc)
    haiku, sonnet = budget.estimate(doc, 0, "handwriting")[0], budget.estimate(doc, 0, "handwriting", reader="anthropic_api:sonnet")[0]
    assert sonnet == pytest.approx(20 * haiku)


def test_capped_crop_read_is_not_made(monkeypatch):
    fake, doc, page = setup(monkeypatch)
    budget.configure(doc, usd=0.0015)
    page_read(doc)
    assert crops.read_crop(doc, page, (0.1, 0.1, 0.9, 0.3), crops.FORM_PROMPT, "fake", "form:f0") is None
    assert fake.calls == [] and page.properties["spend_cap"]["skipped"][0]["step"] == "form:form:f0"
    doc.meta.pop(budget.KEY)
    assert crops.read_crop(doc, page, (0.1, 0.1, 0.9, 0.3), crops.FORM_PROMPT, "fake", "form:f0") is not None


def test_capped_verify_reread_is_not_made(monkeypatch):
    page = Page(index=0, width=612, height=792)
    page.regions = [Region(type=RegionType.TABLE, content=BROKEN, text=BROKEN)]
    doc = Document(source="x.pdf", stem="x", file_type="pdf", pages=[page])
    budget.configure(doc, usd=0.0012)
    page_read(doc)
    pc = PageChecks(doc=doc, page=page, reader_spec="none")
    monkeypatch.setattr(checks.reread, "crop_png", lambda *a, **k: pytest.fail("no crop when the cap is reached"))
    assert pc.ask((0, 0, 1, 1), "p", "grid_draft") is None and pc.calls == 0
    assert page.properties["spend_cap"]["skipped"][0]["step"] == "verify:grid_draft"


def test_request_settings_carry_the_cap_and_nothing_else_unknown():
    from readanydoc.api import schemas
    m = schemas.SyncParseConfig.model_validate({"input": "reducto://a.pdf", "settings": {
        "page_cap_usd": 0.01, "page_cap_tokens": 9000.0, "unknown": True}})
    st = m.dump()["settings"]
    assert st["page_cap_usd"] == 0.01 and st["page_cap_tokens"] == 9000 and "unknown" not in st
    assert "page_cap_usd" not in schemas.SyncParseConfig.model_validate({"input": "reducto://a.pdf"}).dump()["settings"]
    with pytest.raises(Exception):
        schemas.SyncParseConfig.model_validate({"input": "reducto://a.pdf", "settings": {"page_cap_usd": -1}})


def test_request_cap_reaches_the_read_stage_and_only_lowers_a_profile_cap(monkeypatch):
    from readanydoc.api import service
    monkeypatch.setenv("READANYDOC_PROFILE", "engine-final-2")
    monkeypatch.delenv("READANYDOC_STAGE_OPTIONS", raising=False)
    assert "page_cap_usd" not in service.pipeline_config({}).stage("read").options
    assert service.pipeline_config({"settings": {"page_cap_usd": 0.01}}).stage("read").options["page_cap_usd"] == 0.01
    monkeypatch.setenv("READANYDOC_STAGE_OPTIONS", '{"read": {"page_cap_usd": 0.005}}')
    assert service.pipeline_config({"settings": {"page_cap_usd": 0.01}}).stage("read").options["page_cap_usd"] == 0.005
    assert service.pipeline_config({"settings": {"page_cap_usd": 0.002}}).stage("read").options["page_cap_usd"] == 0.002


def test_cheapest_profile_has_no_optional_model_step():
    from readanydoc.profiles import get_profile
    cfg = get_profile("engine-cheapest")
    read, ground = cfg.stage("read").options, cfg.stage("ground").options
    assert not read.get("zoom_vote") and not read.get("handwriting_route") and not read.get("form_reread")
    assert checks.parse_spec(cfg.stage("verify").options["checks"]) == {"grid": ["norm"], "flags": True}
    assert tables.parse_spec(ground["tables"]) == {"clean": True}
    assert "forms" not in ground["regions"] and "formread" not in ground["regions"]
    assert get_profile("engine-final-2").stage("read").options.get("page_cap_usd") is None   # untouched


# ---------------------------------------------------------------- strips: effort and second pass
class _Stage:
    def __init__(self, spec, **opts):
        self.ctx_reader_spec, self._o = spec, opts

    def opt(self, k, d=None):
        return self._o.get(k, d)


def test_strip_reader_spec():
    assert strips.reader_spec(_Stage("anthropic_api:haiku#high")) == "anthropic_api:haiku#high"
    assert strips.reader_spec(_Stage("anthropic_api:haiku#high", zoom_effort="low")) == "anthropic_api:haiku#low"
    # the disk cache of test servers does not key on effort: the sample name carries it
    assert strips.reader_spec(_Stage("anthropic_api:haiku#high@dev1", zoom_effort="low")) == "anthropic_api:haiku#low@dev1_low"
    assert strips.reader_spec(_Stage("anthropic_api:haiku#high@dev1", zoom_effort="high")) == "anthropic_api:haiku#high@dev1"
    assert strips.reader_spec(_Stage("claude_cli:haiku", zoom_effort="low")) == "claude_cli:haiku"
    assert strips.reader_spec(_Stage("anthropic_api:haiku#high", zoom_effort="low", zoom_reader="x:y")) == "x:y"


PAGE = "Alpha 1,234 beta\n\nGamma 5,678 delta\n\nOmega 9,012 kappa"
CUT3 = zoom.strip_boxes(3)


def test_second_pass_only_over_disputed_strips():
    parts = ["Alpha 1,234 beta", "Gamma 5,673 delta", "Omega 9,012 kappa"]          # a digit dispute in strip 2 only
    per = zoom_schedule.disputes_by_strip(PAGE, parts)
    assert [d["all"] for d in per] == [0, 1, 0] and [d["digits"] for d in per] == [0, 1, 0]
    run, keep, rec = zoom_schedule.plan("disputed", PAGE, parts, CUT3)
    kept = [b for b in zoom.strip_boxes(4) if keep(b)]
    assert run and rec["disputed_strips"] == 1 and 0 < len(kept) < 4
    assert all(min(b[3], CUT3[1][3]) > max(b[1], CUT3[1][1]) for b in kept)


def test_second_pass_digits_only():
    words = ["Alpha 1,234 beta", "Gamna 5,678 delta", "Omega 9,012 kappa"]          # a word dispute, no digit dispute
    assert zoom.disputes(PAGE, "\n\n".join(words)) == 1
    assert zoom_schedule.plan("digits", PAGE, words, CUT3)[0] is False
    assert zoom_schedule.plan("digits+disputed", PAGE, words, CUT3)[0] is False
    run, keep, _ = zoom_schedule.plan("disputed", PAGE, words, CUT3)
    assert run and keep is not None
    digits = ["Alpha 1,284 beta", "Gamma 5,678 delta", "Omega 9,012 kappa"]
    run, keep, _ = zoom_schedule.plan("digits", PAGE, digits, CUT3)
    assert run and keep is None                                                        # all four strips
    assert zoom_schedule.plan("", PAGE, digits, CUT3)[:2] == (True, None)             # switch off: as before
    assert zoom_schedule.plan("digits", PAGE, digits[:2], CUT3)[:2] == (True, None)   # strips do not line up: as before


def test_partial_second_pass_gives_the_same_vote_for_the_disputed_part():
    a = "Alpha 1,234 beta\n\nGamma 5,673 delta\n\nOmega 9,012 kappa"
    b_full = "Alpha 1,234 beta Gamma 5,673\n\ndelta Omega 9,012 kappa"
    b_part = "beta Gamma 5,673 delta"
    assert zoom.vote(PAGE, a, b_full) == zoom.vote(PAGE, a, b_part)
    assert zoom.vote(PAGE, a, b_part)[1] == [("5,678", "5,673")]


# ---------------------------------------------------------------- form reads started with the page read
def test_form_prefetch_needs_a_form_step_and_skips_filled_regions(monkeypatch):
    monkeypatch.delenv("READANYDOC_REGIONS", raising=False)
    fake, doc, page = setup(monkeypatch)
    assert form_prefetch.start(doc, page, {"regions": "types=guarded,boxes"}, "fake") == 0
    assert form_prefetch.start(doc, page, {}, "fake") == 0 and fake.calls == []
    monkeypatch.setattr(form_prefetch, "filled_fields", lambda d, p: [{"bbox": [0.2, 0.15, 0.3, 0.2], "value": "x"}])
    assert [d["id"] for d in form_prefetch.regions_to_read(doc, page)] == ["f1"]
    page.properties["layout_detections"].append({"id": "f2", "label": "form", "score": 0.9, "box": [0.1, 0.96, 0.4, 0.99]})
    assert [d["id"] for d in form_prefetch.regions_to_read(doc, page)] == ["f1"]          # f2: under 5% of the page
    assert [d["id"] for d in form_prefetch.regions_to_read(doc, page, min_area=0)] == ["f1", "f2"]


def test_prefetched_form_read_is_used_once_billed_once_and_unused_ones_are_billed(monkeypatch):
    monkeypatch.delenv("READANYDOC_REGIONS", raising=False)
    fake, doc, page = setup(monkeypatch)
    assert form_prefetch.start(doc, page, {"regions": "forms=notables,formread"}, "fake") == 2
    assert page.properties["form_prefetch"] == 2
    jobs = [((0.1, 0.1, 0.9, 0.3), crops.FORM_PROMPT, "form:f0")]
    with crops.shared_reads(page):
        first = crops.read_many(doc, page, jobs, "fake")     # the round-1 form crop step
        second = crops.read_many(doc, page, jobs, "fake")    # the form evidence step: shared, no second call
    assert first[0].text == T3 and second[0] is first[0]
    assert len(fake.calls) == 2 and len(doc.usage) == 1 and doc.usage[0]["prefetched"] is True
    assert doc.usage[0]["reason"] == "form:f0" and doc.usage[0]["escalation"] == "region"
    assert crops.settle_prefetched(doc) == 1                 # f1 was started and never asked for
    assert doc.usage[-1]["reason"].startswith("form_prefetch_unused") and page.properties["form_prefetch_unused"] == 1
    assert crops.settle_prefetched(doc) == 0 and len(fake.calls) == 2


def test_no_form_prefetch_on_a_turned_page(monkeypatch):
    monkeypatch.delenv("READANYDOC_REGIONS", raising=False)
    fake, doc, page = setup(monkeypatch)
    page.properties["rotation_applied"] = 90
    assert form_prefetch.start(doc, page, {"regions": "formread"}, "fake") == 0 and fake.calls == []
    page.properties["rotation_applied"] = 0
    assert form_prefetch.start(doc, page, {"regions": "formread"}, "fake") == 2
    crops.settle_prefetched(doc)


def test_same_answer_with_and_without_form_prefetch(monkeypatch):
    monkeypatch.delenv("READANYDOC_REGIONS", raising=False)
    jobs = [((0.1, 0.1, 0.9, 0.3), crops.FORM_PROMPT, "form:f0"), ((0.1, 0.5, 0.9, 0.7), crops.FORM_PROMPT, "form:f1")]
    fake, doc, page = setup(monkeypatch)
    plain = [r.text for r in crops.read_many(doc, page, jobs, "fake")]
    calls_plain = sorted(fake.calls)
    fake2, doc2, page2 = setup(monkeypatch)
    form_prefetch.start(doc2, page2, {"regions": "formread"}, "fake")
    pre = [r.text for r in crops.read_many(doc2, page2, jobs, "fake")]
    assert pre == plain and sorted(fake2.calls) == calls_plain        # same crops, same prompt, same number of calls
    strip = lambda u: {k: v for k, v in u.items() if k != "prefetched"}
    assert all(u["prefetched"] is False for u in doc.usage) and all(u["prefetched"] is True for u in doc2.usage)
    assert sorted(map(str, map(strip, doc2.usage))) == sorted(map(str, map(strip, doc.usage)))


def test_step_seconds_tell_prefetched_form_reads_apart():
    from readanydoc.readers.usage import step_seconds
    e = {"escalation": "region", "reason": "form:f0", "input_tokens": 1, "output_tokens": 1, "seconds": 2.0}
    assert set(step_seconds([e])) == {"region:form"}
    assert set(step_seconds([{**e, "prefetched": True}])) == {"region:form_pre"}
    t = {**e, "reason": "tread:table:d0"}
    assert set(step_seconds([{**t, "prefetched": False}])) == {"region:table_late"}
    assert set(step_seconds([t])) == set(step_seconds([{**t, "prefetched": True}])) == {"region:table"}
    assert set(step_seconds([{**e, "reason": "table_prefetch_unused"}])) == {"region:table"}


# ---------------------------------------------------------------- table re-read triggers
SPAN = T3.replace("<th>Item</th>", '<th colspan="2">Item</th>')
HEAD2 = "<table><tr><th>A</th><th>B</th></tr><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"
EMPTY = "<table><tr><td>A</td><td></td></tr><tr><td></td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table>"


def test_all_if_spec_and_triggers():
    assert tables.parse_spec("reread=all;all_if=spans+header2")["all_if"] == ["spans", "header2"]
    assert "all_if" not in tables.parse_spec("reread=all")
    with pytest.raises(ValueError):
        tables.parse_spec("all_if=always")
    rules = list(reread.ALL_IF)
    assert reread.all_trigger(rules, T3, None) == []
    assert reread.all_trigger(rules, SPAN, None) == ["spans"]
    assert reread.all_trigger(rules, T3.replace("<th>M</th>", '<th colspan="1">M</th>'), None) == []
    assert reread.all_trigger(rules, HEAD2, None) == ["header2"]
    assert reread.all_trigger(rules, EMPTY, None) == ["empty"]
    assert reread.all_trigger(["spans"], HEAD2, None) == []


def test_all_if_starts_no_table_read_before_the_page_read(monkeypatch):
    fake, doc, page = setup(monkeypatch)
    assert prefetch.start(doc, page, tables.parse_spec("reread=all;prefetch=1;all_if=spans"), "fake") == 0
    assert prefetch.start(doc, page, tables.parse_spec("reread=all;prefetch=1"), "fake") == 1
    prefetch.settle(doc, page)
    assert prefetch.start(doc, page, tables.parse_spec("reread=all;prefetch=1;prefetch_score=0.95"), "fake") == 0
    assert prefetch.start(doc, page, tables.parse_spec("reread=all;prefetch=1;prefetch_area=0.5"), "fake") == 0
    assert prefetch.start(doc, page, tables.parse_spec("reread=all;prefetch=1;prefetch_score=0.6;prefetch_area=0.02"), "fake") == 1
    prefetch.settle(doc, page)


# ---------------------------------------------------------------- verify: the prefetched table read as repair
class _PC(PageChecks):
    def box_of(self, region):
        return (0.1, 0.75, 0.9, 0.95)

    def ask(self, box, prompt, tag, zoom=None):
        self.calls += 1
        return None


def _grid_setup(monkeypatch, text):
    monkeypatch.delenv("READANYDOC_TABLES", raising=False)
    fake, doc, page = setup(monkeypatch, text=text)
    page.regions = [Region(type=RegionType.TABLE, content=BROKEN, text=BROKEN)]
    spec = "reread=all;prefetch=1"
    prefetch.start(doc, page, tables.parse_spec(spec), "fake")
    pc = _PC(doc=doc, page=page, reader_spec="fake", ground_options={"tables": spec},
             options=checks.parse_spec("grid=pre+draft"))
    return fake, doc, page, pc


def test_grid_pre_uses_the_started_table_read_and_makes_no_call(monkeypatch):
    fake, doc, page, pc = _grid_setup(monkeypatch, FIXED)
    gridrepair.run(pc, ["pre", "draft"])
    assert page.regions[0].content == FIXED and pc.calls == 0
    assert page.properties["checks"][0]["how"] == "pre" and doc.usage == []            # not billed here
    out = reread._read_jobs(doc, page, [((0.1, 0.75, 0.9, 0.95), reread.PROMPTS["clean"], "tread:table:t0")], "fake", 0.012)
    assert out[0].text == FIXED and len(doc.usage) == 1 and len(fake.calls) == 1        # the table step bills it, once


def test_grid_pre_falls_back_to_the_repair_call(monkeypatch):
    fake, doc, page, pc = _grid_setup(monkeypatch, T3.replace("Fish", "Fowl"))             # other words: not accepted
    gridrepair.run(pc, ["pre", "draft"])
    assert page.regions[0].content == BROKEN and pc.calls == 1
    prefetch.settle(doc, page)
    assert "pre" in checks.GRID_MODES and checks.parse_spec("grid=norm+pre+draft")["grid"] == ["norm", "pre", "draft"]


def test_final_cost_profile_is_engine_final_2_plus_two_switches():
    from readanydoc.profiles import get_profile
    a, b = get_profile("engine-final-2"), get_profile("engine-final-3-cost")
    ra, rb = a.stage("read").options, b.stage("read").options
    assert {k: v for k, v in rb.items() if k not in ("zoom_second", "form_prefetch")} == ra
    assert rb["zoom_second"] == "disputed" and rb["form_prefetch"] is True
    for st in ("triage", "layout", "verify", "shape"):
        assert a.stage(st).options == b.stage(st).options
    ga, gb = a.stage("ground").options, b.stage("ground").options
    assert gb["regions"] == ga["regions"] and gb["tables"] == ga["tables"] + ";prefetch_score=0.6;prefetch_area=0.02"
    ta, tb = tables.parse_spec(ga["tables"]), tables.parse_spec(gb["tables"])
    assert {k: v for k, v in tb.items() if not k.startswith("prefetch_")} == ta
    assert "zoom_second" not in ra and "form_prefetch" not in ra
