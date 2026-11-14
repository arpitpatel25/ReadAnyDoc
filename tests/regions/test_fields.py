"""Round 2, workstream B: form fields and block structure (regions/fields.py). No model call, no network."""

from readanydoc.model import BBox, Line, Page, Region, RegionType, Word
from readanydoc.pipeline.reading import handwriting
from readanydoc.pipeline.regions import Switches, fields
from readanydoc.model import Confidence


def det(i, label, type_, box, score=0.9):
    return {"id": f"d{i}", "label": label, "type": type_, "score": score, "box": list(box), "order": i, "inside": None}


def bbox(box):
    return BBox(left=box[0], top=box[1], width=box[2] - box[0], height=box[3] - box[1])


def region(type_, content, box=None, words=()):
    """``words``: (text, left, right, top) in text order; char offsets are found in the content."""
    r = Region(type=type_, content=content, text=content, bbox=bbox(box) if box else None)
    pos, ws = 0, []
    for t, x0, x1, y in words:
        k = content.index(t, pos)
        ws.append(Word(text=t, bbox=bbox((x0, y, x1, y + 0.01)), char_start=k, char_end=k + len(t)))
        pos = k + len(t)
    if ws:
        r.lines = [Line(words=ws)]
    return r


def page_with(regions, dets=()):
    p = Page(index=0, width=612, height=792)
    p.regions = list(regions)
    p.properties["layout_detections"] = list(dets)
    return p


# ---------------------------------------------------------------------------------------------- switches
def test_new_switches_parse_and_are_off_by_default():
    assert not Switches.parse("types=guarded,boxes,split").more
    s = Switches.parse("hfsplit,kvsplit,kv=form,figures,boxfill")
    assert s.more == {"hfsplit": "on", "kvsplit": "on", "kv": "form", "figures": "on", "boxfill": "on"} and s.any()


# ---------------------------------------------------------------------------------------------- furniture lines
def test_running_head_is_cut_into_its_groups():
    r = region(RegionType.HEADER, "436 MIRANDA *v.* ARIZONA. Syllabus.", (0.1, 0.05, 0.9, 0.06),
               [("436", 0.10, 0.13, 0.05), ("MIRANDA", 0.38, 0.46, 0.05), ("v", 0.47, 0.48, 0.05),
                ("ARIZONA", 0.49, 0.58, 0.05), ("Syllabus", 0.82, 0.90, 0.05)])
    p = page_with([r])
    assert fields.split_furniture(p, []) == 1
    assert [x.content for x in p.regions] == ["436", "MIRANDA *v.* ARIZONA.", "Syllabus."]
    assert all(x.type == RegionType.HEADER and x.meta["search_bbox"] for x in p.regions)
    assert p.regions[0].candidates[-1].content == r.content            # the block before the cut is kept


def test_footer_written_on_three_lines_but_printed_on_one_is_cut_and_bold_stays_whole():
    c = "report.rpt\nPage 1 of 1\n**Print Date:** 04/16/2025"
    r = region(RegionType.TEXT, c, (0.03, 0.97, 0.92, 0.98),
               [("report", 0.03, 0.2, 0.97), ("Page", 0.46, 0.49, 0.97), ("1", 0.50, 0.51, 0.97), ("of", 0.51, 0.52, 0.97),
                ("Print", 0.76, 0.79, 0.97), ("Date", 0.80, 0.83, 0.97), ("04", 0.85, 0.92, 0.97)])
    p = page_with([r])
    assert fields.split_furniture(p, []) == 1
    assert [x.content for x in p.regions] == ["report.rpt", "Page 1 of 1", "**Print Date:** 04/16/2025"]


def test_body_text_two_lines_and_close_words_are_not_cut():
    body = region(RegionType.TEXT, "Total amount due", (0.1, 0.5, 0.9, 0.51),
                  [("Total", 0.1, 0.2, 0.5), ("amount", 0.5, 0.6, 0.5), ("due", 0.8, 0.9, 0.5)])   # not in a band
    close = region(RegionType.HEADER, "Annual Report 2023", (0.1, 0.05, 0.4, 0.06),
                   [("Annual", 0.1, 0.18, 0.05), ("Report", 0.19, 0.27, 0.05), ("2023", 0.28, 0.33, 0.05)])
    two = region(RegionType.HEADER, "Form 1065 Page 2", (0.1, 0.03, 0.9, 0.07),
                 [("Form", 0.1, 0.15, 0.03), ("1065", 0.16, 0.2, 0.03), ("Page", 0.8, 0.85, 0.055), ("2", 0.86, 0.87, 0.055)])
    heading = region(RegionType.TITLE, "# Form 1065 Page 2", (0.1, 0.03, 0.9, 0.04),
                     [("Form", 0.1, 0.15, 0.03), ("1065", 0.16, 0.2, 0.03), ("Page", 0.8, 0.85, 0.03), ("2", 0.86, 0.87, 0.03)])
    p = page_with([body, close, two, heading])
    assert fields.split_furniture(p, []) == 0 and len(p.regions) == 4


def test_cut_never_falls_inside_a_bold_span():
    r = region(RegionType.FOOTER, "**Form 1040 Page 2**x", (0.1, 0.96, 0.9, 0.97),
               [("Form", 0.1, 0.15, 0.96), ("1040", 0.16, 0.2, 0.96), ("Page", 0.8, 0.85, 0.96), ("2", 0.86, 0.87, 0.96)])
    p = page_with([r])
    assert fields.split_furniture(p, []) == 0


# ---------------------------------------------------------------------------------------------- key value split
def test_key_value_block_is_cut_between_layout_regions_only_where_a_field_starts():
    c = "**Provider:** TEXAS DEPT\n1100 WEST ST\n**Physician:** TEST PRACT\n**Patient:**\nJOHN DOE"
    r = region(RegionType.KEY_VALUE, c, (0.03, 0.2, 0.9, 0.4),
               [("Provider", 0.04, 0.1, 0.20), ("TEXAS", 0.11, 0.2, 0.20), ("1100", 0.04, 0.1, 0.22),
                ("Physician", 0.04, 0.1, 0.27), ("TEST", 0.11, 0.2, 0.27),
                ("Patient", 0.56, 0.62, 0.30), ("JOHN", 0.56, 0.62, 0.32)])
    dets = [det(0, "text", "Text", (0.03, 0.195, 0.4, 0.24)), det(1, "text", "Text", (0.03, 0.26, 0.3, 0.29)),
            det(2, "text", "Text", (0.55, 0.295, 0.8, 0.315)), det(3, "text", "Text", (0.55, 0.318, 0.8, 0.34))]
    p = page_with([r], dets)
    assert fields.split_key_values(p, dets) == 0                       # no form region around it: left alone
    dets.append(det(4, "key_value_region", "Key Value", (0.02, 0.19, 0.92, 0.41)))
    assert fields.split_key_values(p, dets) == 1
    assert [x.content for x in p.regions] == ["**Provider:** TEXAS DEPT\n1100 WEST ST", "**Physician:** TEST PRACT",
                                              "**Patient:**\nJOHN DOE"]   # the open label keeps its value
    assert all(x.type == RegionType.KEY_VALUE for x in p.regions)


def test_key_value_block_in_one_region_or_without_field_starts_stays():
    c = "Name: John\nAddress: 1 Main St"
    one = region(RegionType.KEY_VALUE, c, (0.1, 0.2, 0.5, 0.25), [("Name", 0.1, 0.15, 0.2), ("Address", 0.1, 0.17, 0.22)])
    d = [det(0, "text", "Text", (0.09, 0.19, 0.5, 0.26)), det(9, "form", "Key Value", (0.0, 0.0, 1.0, 0.9))]
    p = page_with([one], d)
    assert fields.split_key_values(p, d) == 0
    cont = region(RegionType.KEY_VALUE, "Name: John\ncontinued here", (0.1, 0.2, 0.5, 0.3),
                  [("Name", 0.1, 0.15, 0.2), ("continued", 0.1, 0.17, 0.27)])
    d2 = [det(0, "text", "Text", (0.09, 0.19, 0.5, 0.22)), det(1, "text", "Text", (0.09, 0.26, 0.5, 0.29)),
          det(9, "form", "Key Value", (0.0, 0.0, 1.0, 0.9))]
    p2 = page_with([cont], d2)
    assert fields.split_key_values(p2, d2) == 0


# ---------------------------------------------------------------------------------------------- field typing
def test_field_like():
    assert fields.field_like("DATE 2/15/90") and fields.field_like("COST CENTER NUMBER 141")
    assert fields.field_like("Employment income\nRevenus d'emploi") and fields.field_like("**Exempt**\nCPP EI")
    assert not fields.field_like("The employer must complete this section before the form is given to the employee.")
    assert not fields.field_like("This is a notice. It has two sentences and more words than a label has.")
    assert not fields.field_like("a\nb\nc\nd\ne") and not fields.field_like("---")
    assert not fields.field_like("www.irs.gov/Form1099") and not fields.field_like("*See attached statement for additional information.")
    assert fields.field_like("Check No.") and fields.field_like("Date received: 3 May.")


def test_text_inside_a_form_region_becomes_key_value_and_prose_does_not():
    form = det(0, "form", "Key Value", (0.1, 0.1, 0.9, 0.9), 0.93)
    a = region(RegionType.TEXT, "DATE 2/15/90", (0.6, 0.4, 0.8, 0.41))
    prose = region(RegionType.TEXT, "Read the instructions on the back before you complete this form in ink.", (0.1, 0.5, 0.9, 0.53))
    outside = region(RegionType.TEXT, "COPY 2", (0.92, 0.02, 0.98, 0.03))
    head = region(RegionType.SECTION_HEADER, "## Part I", (0.1, 0.2, 0.3, 0.22))
    foot = region(RegionType.TEXT, "Form 101", (0.1, 0.95, 0.2, 0.96))
    p = page_with([a, prose, outside, head, foot], [form])
    assert fields.type_fields(p, [form]) == {}                       # the page read shows no field in the region
    assert fields.type_fields(p, [form], "form+any") == {"typed_key_value": 1}
    a.type = RegionType.TEXT
    seen = region(RegionType.KEY_VALUE, "Name: ____", (0.1, 0.6, 0.5, 0.61))
    p = page_with([a, prose, outside, head, foot, seen], [form])
    assert fields.type_fields(p, [form]) == {"typed_key_value": 1}
    assert [r.type for r in p.regions[:5]] == [RegionType.KEY_VALUE, RegionType.TEXT, RegionType.TEXT,
                                               RegionType.SECTION_HEADER, RegionType.TEXT]
    assert a.meta["type_before_fields"] == "Text" and a.meta["type_rule"] == "form_field:form"


def test_no_form_region_or_an_unsure_one_types_nothing():
    a = region(RegionType.TEXT, "DATE 2/15/90", (0.6, 0.4, 0.8, 0.41))
    assert fields.type_fields(page_with([a]), []) == {}
    weak = det(0, "form", "Key Value", (0.1, 0.1, 0.9, 0.9), 0.4)
    assert fields.type_fields(page_with([a], [weak]), [weak]) == {} and a.type == RegionType.TEXT


# ---------------------------------------------------------------------------------------------- figures
def test_picture_without_a_block_becomes_a_figure_and_a_covered_one_does_not():
    logo = det(0, "picture", "Figure", (0.05, 0.03, 0.2, 0.12), 0.9)
    seen = det(1, "picture", "Figure", (0.6, 0.5, 0.9, 0.7), 0.9)
    letterhead = det(2, "picture", "Figure", (0.3, 0.03, 0.7, 0.08), 0.9)
    weak = det(3, "picture", "Figure", (0.05, 0.8, 0.2, 0.9), 0.4)
    regs = [region(RegionType.TEXT, "ACME Corp. 1 Main Street", (0.31, 0.035, 0.69, 0.075)),
            region(RegionType.TEXT, "Body text", (0.1, 0.2, 0.9, 0.4)),
            region(RegionType.FIGURE, "[Figure: chart]", (0.6, 0.5, 0.9, 0.7))]
    p = page_with(regs, [logo, seen, letterhead, weak])
    assert fields.add_figures(p, p.properties["layout_detections"]) == 1
    figs = [r for r in p.regions if r.type == RegionType.FIGURE]
    assert len(figs) == 2 and figs[0].meta["added_by"] == "figures" and abs(figs[0].bbox.left - 0.05) < 1e-9
    assert p.regions.index(figs[0]) < p.regions.index(regs[1])       # in reading order, before the body


# ---------------------------------------------------------------------------------------------- handwriting guard
def _scan(agree: int, total: int = 20):
    p = Page(index=0, kind="scanned")
    toks = [f"word{i}" for i in range(total)]
    p.words["tesseract"] = [Word(text=t, confidence=Confidence.of(0.7), source="tesseract") for t in toks[:agree]]
    return p, " ".join(toks)


def test_half_confirmed_print_with_one_mark_is_not_routed_but_many_marks_are():
    p, text = _scan(8)                                                # agreement 0.4: a faint printed form
    kw = dict(illegible_max_agreement=0.5, illegible_sure_agreement=0.33, illegible_min_density=0.03)
    assert handwriting.detect(p, text + " [illegible]", illegible_max_agreement=0.5).route      # round 1: routed
    s = handwriting.detect(p, text + " [illegible]", **{**kw, "illegible_min_density": 0.1})
    assert not s.route and "half-confirms" in s.reason
    assert handwriting.detect(p, text + " [illegible]" * 4, **{**kw, "illegible_min_density": 0.1}).route


def test_low_agreement_still_routes_with_the_guard():
    p, text = _scan(3)                                                # agreement 0.15: handwriting
    assert handwriting.detect(p, text + " [illegible]", illegible_max_agreement=0.5, illegible_sure_agreement=0.33).route


# ---------------------------------------------------------------------------------------------- box fill
def test_block_without_a_box_takes_the_free_region_with_its_words():
    p = Page(index=0, width=612, height=792)
    p.words["pdf"] = [Word(text="Year", bbox=bbox((0.43, 0.05, 0.45, 0.06))), Word(text="Année", bbox=bbox((0.455, 0.05, 0.47, 0.06))),
                      Word(text="Total", bbox=bbox((0.1, 0.5, 0.15, 0.51))), Word(text="due", bbox=bbox((0.16, 0.5, 0.2, 0.51)))]
    p.properties["text_layer_exact"] = True
    lost = region(RegionType.TEXT, "Year / Année")                       # no box
    placed = region(RegionType.TEXT, "Total due", (0.1, 0.5, 0.2, 0.51))
    dets = [det(0, "text", "Text", (0.425, 0.045, 0.475, 0.065)), det(1, "text", "Text", (0.09, 0.495, 0.21, 0.515)),
            det(2, "form", "Key Value", (0.0, 0.0, 1.0, 1.0))]
    p.regions = [lost, placed]
    assert fields.fill_boxes(p, dets) == 1
    assert lost.meta["bbox_source"] == "layout_text" and abs(lost.bbox.left - 0.425) < 1e-9
    assert abs(placed.bbox.left - 0.1) < 1e-9                             # a placed block keeps its box


def test_same_words_twice_on_the_page_are_left_alone_without_neighbours():
    p = Page(index=0, width=612, height=792)
    p.words["pdf"] = [Word(text="45", bbox=bbox((0.45, 0.11, 0.47, 0.12))), Word(text="45", bbox=bbox((0.45, 0.61, 0.47, 0.62)))]
    p.properties["text_layer_exact"] = True
    p.regions = [region(RegionType.TEXT, "45")]
    dets = [det(0, "text", "Text", (0.445, 0.105, 0.475, 0.125)), det(1, "text", "Text", (0.445, 0.605, 0.475, 0.625))]
    assert fields.fill_boxes(p, dets) == 0 and p.regions[0].bbox is None


# ---------------------------------------------------------------------------------------------- form evidence
def test_pairs_of_a_form_read():
    from readanydoc.pipeline.regions import formevidence as fe
    text = "DATE: 2/15/90\n**COST CENTER NUMBER:** 141\n- Signature:\nTime: 10:30\n<table><tr><td>a: b</td></tr></table>\n# Title: x"
    assert fe.pairs(text) == [(["date"], ["2", "15", "90"]), (["cost", "center", "number"], ["141"]), (["signature"], []),
                              (["time"], ["10", "30"])]


def test_form_read_supports_a_label_with_its_value_and_a_bare_label_but_not_prose():
    from readanydoc.pipeline.regions import formevidence as fe
    prs = fe.pairs("DATE: 2/15/90\nDEPARTMENT NAME: SCIENCE & TECHNOLOGY\nSignature:")
    tok = lambda s: fe.norm_tokens(s)
    assert fe.supports(tok("DATE 2/15/90"), prs) and fe.supports(tok("DEPARTMENT NAME SCIENCE & TECHNOLOGY"), prs)
    assert fe.supports(tok("Signature"), prs)
    assert not fe.supports(tok("DATE 3/1/91"), prs)                       # another value: no support
    assert fe.supports(tok("DATE"), prs)                                  # the label alone (value in another block)
    t4 = fe.pairs("14 Employment income – Revenus d'emploi:\n22 Income tax deducted:")
    assert fe.supports(tok("Employment income Revenus d'emploi"), t4) and fe.supports(tok("14"), t4)
    assert not fe.supports(tok("income"), t4) and not fe.supports(tok("1999"), t4)
    assert not fe.supports(tok("The date of the report was set by the department and nobody else"), prs)
    assert fe.value_of(tok("RE-LABELED BY DATE"), tok("2/15/90"), prs)
    assert not fe.value_of(tok("RE-LABELED BY DATE"), tok("some other text"), prs)
    assert not fe.value_of(tok("Signature"), tok("John"), prs)            # the form read saw no value there


def test_form_evidence_types_and_joins_only_with_the_page_reads_text(monkeypatch):
    from readanydoc.model import Document
    from readanydoc.pipeline.regions import formevidence as fe
    from readanydoc.readers.base import ReadResult
    form = det(0, "form", "Key Value", (0.1, 0.1, 0.9, 0.9), 0.93)
    a = region(RegionType.TEXT, "COST CENTER NUMBER 141", (0.12, 0.2, 0.4, 0.21))
    b = region(RegionType.TEXT, "PLACED IN FILE BY", (0.12, 0.3, 0.6, 0.31))
    c = region(RegionType.TEXT, "Wayne Baughan", (0.3, 0.33, 0.5, 0.34))
    t = region(RegionType.TABLE, "<table><tr><td>Qty</td><td>1</td></tr></table>", (0.12, 0.5, 0.8, 0.6))
    p = page_with([a, b, c, t], [form])
    monkeypatch.setattr(fe.crops, "read_many", lambda doc, page, jobs, spec: [
        ReadResult(text="COST CENTER NUMBER: 141\nPLACED IN FILE BY: Wayne Baughan")])
    info = fe.run(Document(source="x"), p, "none")
    assert info["typed"] == 0 and info["joined"] == 0 and len(p.regions) == 4     # two lines: not a form, nothing decided
    monkeypatch.setattr(fe.crops, "read_many", lambda doc, page, jobs, spec: [
        ReadResult(text="COST CENTER NUMBER: 141\nPLACED IN FILE BY: Wayne Baughan\nQty: 1\nDATE: 2/15/90")])
    info = fe.run(Document(source="x"), p, "none")
    assert info["typed"] == 2 and info["joined"] == 1 and len(p.regions) == 3     # a, and b as a label; c joined to b
    assert p.regions[1].content == "PLACED IN FILE BY\nWayne Baughan" and t.type == RegionType.TABLE
    assert fe.retype(p) == 2 and [r.type for r in p.regions] == [RegionType.KEY_VALUE, RegionType.KEY_VALUE, RegionType.TABLE]


def test_heads_mode_and_page_mode():
    form = det(0, "form", "Key Value", (0.05, 0.02, 0.95, 0.9), 0.9)
    h = region(RegionType.HEADER, "OMB No. 1545-0116", (0.8, 0.03, 0.93, 0.04))
    h.meta["type_before_layout"] = "Text"                                  # made a Header by the top band rule
    real = region(RegionType.SECTION_HEADER, "## Part I", (0.1, 0.2, 0.3, 0.22))
    p = page_with([h, real, region(RegionType.KEY_VALUE, "1 Payer [ ]", (0.1, 0.5, 0.4, 0.51))], [form])
    assert fields.type_fields(p, [form], "form") == {} and h.type == RegionType.HEADER
    assert fields.type_fields(p, [form], "form+heads") == {"typed_key_value": 1}
    assert h.type == RegionType.KEY_VALUE and real.type == RegionType.SECTION_HEADER
    # no form region on the page: a colon line is text, a blank field stays
    kv = region(RegionType.KEY_VALUE, "Keywords: layout, parsing", (0.1, 0.3, 0.6, 0.31))
    blank = region(RegionType.KEY_VALUE, "Name: ________", (0.1, 0.4, 0.6, 0.41))
    text = det(1, "text", "Text", (0.1, 0.3, 0.6, 0.31))
    q = page_with([kv, blank], [text])
    assert fields.type_fields(q, [text], "form") == {} and kv.type == RegionType.KEY_VALUE
    assert fields.type_fields(q, [text], "form+page") == {"untyped_key_value": 1}
    assert kv.type == RegionType.TEXT and blank.type == RegionType.KEY_VALUE
    kv.type = RegionType.KEY_VALUE
    assert fields.type_fields(q, [text], "page") == {"untyped_key_value": 1}        # the page rule alone
    seen = region(RegionType.KEY_VALUE, "Name: ____", (0.1, 0.6, 0.5, 0.61))
    lab = region(RegionType.TEXT, "DATE 2/15/90", (0.6, 0.4, 0.8, 0.41))
    assert fields.type_fields(page_with([lab, seen], [form]), [form], "page") == {} and lab.type == RegionType.TEXT
    weak = det(2, "form", "Key Value", (0.0, 0.0, 1.0, 1.0), 0.35)        # an unsure form region: nothing is untyped
    kv2 = region(RegionType.KEY_VALUE, "Date: 1 May", (0.1, 0.3, 0.6, 0.31))
    assert fields.type_fields(page_with([kv2], [weak]), [weak], "form+page") == {} and kv2.type == RegionType.KEY_VALUE


def test_form_read_marks_plain_lines_and_field_typing_leaves_them(monkeypatch):
    from readanydoc.model import Document
    from readanydoc.pipeline.regions import formevidence as fe
    from readanydoc.readers.base import ReadResult
    form = det(0, "form", "Key Value", (0.05, 0.05, 0.95, 0.9), 0.93)
    cap = region(RegionType.TEXT, "Copy 1—For State Tax Department", (0.7, 0.2, 0.9, 0.22))
    lab = region(RegionType.TEXT, "Payer's name", (0.1, 0.2, 0.4, 0.22))
    kv = region(RegionType.KEY_VALUE, "1 Rents: ____", (0.1, 0.3, 0.4, 0.32))
    p = page_with([cap, lab, kv], [form])
    monkeypatch.setattr(fe.crops, "read_many", lambda doc, page, jobs, spec: [ReadResult(
        text="Payer's name:\n1 Rents:\n2 Royalties:\n3 Other income:\nCopy 1—For State Tax Department")])
    assert fe.plain_lines("a: b\nCopy 1—For State\n# Title") == [["copy", "1", "for", "state"], ["title"]]
    info = fe.run(Document(source="x"), p, "none")
    assert info.get("plain") == 1 and cap.meta.get("form_plain") and not lab.meta.get("form_plain")
    fe.retype(p)
    assert lab.type == RegionType.KEY_VALUE                                # the form read has it as a label
    assert fields.type_fields(p, [form], "form") == {} and cap.type == RegionType.TEXT   # field-like, but vetoed


def test_profile_engine_final_r2b_adds_only_its_switches():
    from readanydoc.pipeline.regions import Switches as S
    from readanydoc.profiles import get_profile
    a, b = get_profile("engine-final-r2b"), get_profile("engine-final")
    assert a.profile == "engine-final-r2b" and b.profile == "engine-final"
    sa, sb = S.parse(a.stage("ground").options["regions"]), S.parse(b.stage("ground").options["regions"])
    assert sb.more == {} and sa.more == {"hfsplit": "on", "kv": "page", "formread": "on", "boxfill": "on"}
    assert (sa.types, sa.boxes, sa.split, sa.forms) == (sb.types, sb.boxes, sb.split, sb.forms)
    ra, rb = dict(a.stage("read").options), dict(b.stage("read").options)
    assert ra.pop("handwriting_illegible_sure_agreement") == 0.33 and ra.pop("handwriting_max_ocr_agreement") == 0.33
    assert ra == rb and "handwriting_illegible_sure_agreement" not in rb
    for st in ("triage", "layout", "verify", "shape"):
        assert a.stage(st).options == b.stage(st).options
