"""Workstream A unit tests: sizing, evidence, selection, fusion, tables, escalation policy. No spike data."""

import pytest

from readanydoc.model import BBox, Page, Word
from readanydoc.pipeline.reading.escalate import CostModel, EscalationPolicy
from readanydoc.pipeline.reading.evidence import PageEvidence, page_evidence, read_tokens
from readanydoc.pipeline.reading.fusion import apply_edits, propose_edits
from readanydoc.pipeline.reading.select import select_page
from readanydoc.pipeline.reading.tables import (check_table, choose_table, content_grids, has_table,
                                              parse_grid)
from readanydoc.pipeline.triage import Triage
from readanydoc.readers.sizing import CLAUDE_HIRES, area_fraction, fits, native_scale, native_size, pad_box

TEXT = ("The committee met on Tuesday to review the annual budget and the proposed changes to the "
        "pension scheme for retired members of staff who served before the merger")


def ev(text=TEXT, trusted=False, source="ppocr"):
    return PageEvidence.from_tokens(source, text.split(), trusted=trusted)


# ---- sizing ----
@pytest.mark.parametrize("w,h", [(2196, 3605), (1241, 1754), (3000, 500), (612, 792), (5000, 5000)])
def test_native_size_fits_and_is_maximal(w, h):
    nw, nh = native_size(w, h)
    assert fits(nw, nh)
    assert not fits(int(nw * 1.03) + 28, int(nh * 1.03) + 28)
    assert abs(nw / nh - w / h) < 0.01


def test_native_size_matches_the_measured_spike_size():
    # spike/hires_test.py rendered 2196x3605 pages at about 1499x2461 (the 95% tiny-text run)
    nw, nh = native_size(2196, 3605)
    assert abs(nw - 1499) <= 6 and abs(nh - 2461) <= 8
    assert max(nw, nh) <= CLAUDE_HIRES.max_edge


def test_native_scale_caps_upsampling():
    assert native_scale(300, 200, max_scale=2.0) == pytest.approx(2.0, abs=0.01)
    assert native_size(300, 200, max_scale=2.0) == (600, 400)
    with pytest.raises(ValueError):
        native_scale(0, 10)


def test_crop_box_helpers():
    assert area_fraction((0.1, 0.2, 0.6, 0.4)) == pytest.approx(0.1)
    assert pad_box((0.0, 0.5, 1.0, 0.995), 0.01) == (0.0, 0.49, 1.0, 1.0)


# ---- evidence + selection ----
def test_select_keeps_default_without_clear_advantage():
    a = TEXT
    b = TEXT.replace("Tuesday", "Tuesdav")
    idx, scores, _ = select_page([("a", a), ("b", b)], ev(), min_evidence=10)
    assert idx == 0 and len(scores) == 2


def test_select_replaces_a_read_that_dropped_text():
    truncated = " ".join(TEXT.split()[:10])
    idx, scores, why = select_page([("hires", truncated), ("lowres", TEXT)], ev(), min_evidence=10)
    assert idx == 1 and "lowres" in why and scores[0].recall < scores[1].recall


def test_select_needs_evidence():
    assert select_page([("a", "x"), ("b", TEXT)], None)[0] == 0
    assert select_page([("a", "x"), ("b", TEXT)], ev("too few words"))[0] == 0
    assert select_page([("a", TEXT)], ev())[0] == 0


def test_read_tokens_skip_latex_and_tags():
    assert read_tokens("a <b>bold</b> $\\frac{x}{y}$ word") == ["a", "bold", "word"]


def test_page_evidence_trusts_only_an_exact_text_layer():
    w = lambda t, src: Word(text=t, bbox=BBox(left=0, top=0, width=0.1, height=0.01), source=src)
    page = Page(index=0, words={"pdf": [w("alpha", "pdf")] * 3, "ppocr": [w("alpha", "ppocr")] * 3})
    page.text_layer_usable = True
    assert page_evidence(page).source == "pdf" and not page_evidence(page).trusted  # usable, not exact
    page.properties["text_layer_exact"] = True
    assert page_evidence(page).trusted
    page.text_layer_usable = False
    assert page_evidence(page).source == "ppocr" and not page_evidence(page).trusted


# ---- fusion ----
def test_fusion_takes_exact_characters_from_a_trusted_layer():
    model = TEXT.replace("pension", "pensian")
    edits = propose_edits(model, [ev(trusted=True, source="pdf")])
    assert [(e.old, e.new) for e in edits] == [("pensian", "pension")]
    assert apply_edits(model, edits) == TEXT


def test_ocr_alone_never_overwrites_the_model():
    model = TEXT.replace("pension", "pensian")
    assert propose_edits(model, [ev(trusted=False)]) == []


def test_second_read_plus_ocr_outvotes_the_model():
    model = TEXT.replace("pension", "pensian")
    edits = propose_edits(model, [ev()], alt_texts=[TEXT])
    assert [(e.old, e.new, e.reason) for e in edits] == [("pensian", "pension", "second read + word evidence")]
    # ... but not when the OCR does not have the second read's token either
    assert propose_edits(model, [ev(TEXT.replace("pension", "pens1on"))], alt_texts=[TEXT]) == []
    # ... and not when a required third read disagrees
    third = PageEvidence.from_tokens("layout", model.split())
    assert propose_edits(model, [ev()], alt_texts=[TEXT], confirm=[third]) == []


def test_fusion_leaves_supported_tokens_digits_and_hyphenation_alone():
    assert propose_edits(TEXT, [ev(trusted=True)]) == []  # nothing to fix
    numbers = "paid 1234 dollars on the first day of the month"
    layer = ev("paid 1284 dollars on the first day of the month", trusted=True)
    assert propose_edits(numbers, [layer]) == []  # digits are never changed by default
    assert [(e.old, e.new) for e in propose_edits(numbers, [layer], numbers=True)] == [("1234", "1284")]
    hyph = ev("we test the hypothes is is plausible here", trusted=True)  # "hypothes-" at a line end
    assert propose_edits("we test the hypothesis is plausible here", [hyph]) == []
    big = ev(TEXT.replace("pension", "taxation"), trusted=True)
    assert propose_edits(TEXT, [big]) == []  # too far apart to be a near-miss


def test_fusion_keeps_markup_around_the_token():
    model = "<td>annual <b>budgat</b> and</td>"
    edits = propose_edits(model, [ev("annual budget and", trusted=True)])
    assert apply_edits(model, edits) == "<td>annual <b>budget</b> and</td>"


# ---- tables ----
GOOD = ("<table><tr><th>Name</th><th>Qty</th><th>Price</th></tr>"
        "<tr><td>Bolt</td><td>10</td><td>0.50</td></tr><tr><td>Nut</td><td>20</td><td>0.25</td></tr></table>")
WORDS = "name qty price bolt 10 0 50 nut 20 0 25".split()


def test_parse_grid_spans_and_unclosed_cells():
    g = parse_grid('<table><tr><th rowspan="2">A</th><th colspan="2">B</th></tr><tr><td>c<td>d</tr></table>')
    assert g.rows == [["A", "B"], ["c", "d"]] and g.widths == [3, 3] and g.n_cols == 3 and not g.ragged
    assert parse_grid("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>").ragged
    assert parse_grid("<table><tr><td>PLP &lt;20</td></tr></table>").rows == [["PLP <20"]]


def test_check_table_passes_a_supported_table():
    chk = check_table(GOOD, WORDS, other=GOOD)
    assert chk.ok and (chk.n_rows, chk.n_cols) == (3, 3) and chk.support == 1.0 and chk.agreement == 1.0


def test_check_table_reasons():
    assert check_table("Name Qty Price\nBolt 10 0.50", WORDS).reasons == ["no table found"]
    assert not has_table("plain text") and has_table("| a | b |\n|---|---|")
    invented = GOOD.replace("Bolt", "Screw").replace("Nut", "Washer")
    assert any("not backed" in r for r in check_table(invented, WORDS).reasons)
    short = "<table><tr><th>Name</th><th>Qty</th></tr><tr><td>Bolt</td><td>10</td></tr></table>"
    assert any("missing from the cells" in r for r in check_table(short, WORDS).reasons)
    other_shape = GOOD.replace("<tr><td>Nut</td><td>20</td><td>0.25</td></tr>", "")
    assert not check_table(GOOD, WORDS, other=other_shape).ok
    assert any("degenerate" in r for r in check_table("<table><tr><td>a</td><td>b</td></tr></table>", []).reasons)


def test_choose_table_by_evidence_then_by_agreement():
    invented = GOOD.replace("Bolt", "Screw").replace("Nut", "Washer")
    assert choose_table([("ours", invented), ("other", GOOD)], WORDS)[0] == 1
    assert choose_table([("ours", GOOD), ("other", invented)], WORDS)[0] == 0
    # same cells, different structure: evidence cannot tell, two reads that agree win
    merged = ("<table><tr><th>Name</th><th>Qty</th><th>Price</th></tr>"
              "<tr><td>Bolt<br>Nut</td><td>10<br>20</td><td>0.50<br>0.25</td></tr></table>")
    assert choose_table([("ours", merged), ("b", GOOD), ("crop", GOOD)], WORDS)[0] == 1
    assert choose_table([("ours", merged), ("b", GOOD)], WORDS)[0] == 0  # one against one: ours stays
    assert len(content_grids(GOOD + merged)) == 2


# ---- escalation policy + cost ----
def test_page_escalation_needs_enough_disputed_text():
    pol = EscalationPolicy(disputed_tokens=150)
    a = " ".join(f"w{i}" for i in range(1000))
    b = " ".join(f"w{i}" for i in range(500)) + " " + " ".join(f"x{i}" for i in range(500))
    assert "dispute" in pol.page_reason(a, b)
    assert pol.page_reason(a, a) is None
    small_a, small_b = "one two three four", "five six seven eight"  # total disagreement, 4 tokens
    assert pol.page_reason(small_a, small_b) is None
    assert pol.page_reason(a, None) is None
    assert EscalationPolicy(pages=False).page_reason(a, b) is None


def test_broken_fast_read_escalates():
    from readanydoc.pipeline.reading.robust import Problem
    assert "broken" in EscalationPolicy().page_reason("x", "x", [Problem("repetition", "loop")])
    assert EscalationPolicy().page_reason("x", "x", [Problem("incomplete", "", fatal=False)]) is None


def test_table_escalation_follows_the_check():
    pol = EscalationPolicy()
    assert pol.table_reason(check_table(GOOD, WORDS)) is None
    assert pol.table_reason(check_table("no table here", WORDS)) == "no table found"
    assert EscalationPolicy(tables=False).table_reason(check_table("x", WORDS)) is None


def test_cost_model():
    c = CostModel()
    assert c.crop(0.25) == pytest.approx(0.002) and c.crop(0.0001) == pytest.approx(0.008 * 0.02)
    assert c.crop(3.0) == c.strong_page


# ---- triage classification ----
def _page(pdf_text, ocr_text, kind_image=False, conf=0.99):
    def words(text, src):
        return [Word(text=t, source=src, bbox=BBox(left=0.1, top=0.1 + 0.001 * i, width=0.05, height=0.01),
                     confidence={"score": conf, "label": "high"}) for i, t in enumerate(text.split())]
    page = Page(index=0, width=612, height=792)
    if pdf_text:
        page.words["pdf"] = words(pdf_text, "pdf")
    if ocr_text:
        page.words["ppocr"] = words(ocr_text, "ppocr")
    Triage()._classify(page, {"big_image": kind_image}, 0.8)
    return page


def test_triage_text_layer_classes():
    assert _page(TEXT, TEXT).properties["text_layer_class"] == "trustworthy"
    assert _page(TEXT, TEXT).properties["text_layer_exact"] is True
    garbage = " ".join(f"g{i}" for i in range(30))
    assert _page(garbage, TEXT).properties["text_layer_class"] == "garbage"
    scan = _page(TEXT, TEXT, kind_image=True)
    assert scan.properties["text_layer_class"] == "ocr_layer" and scan.properties["text_layer_exact"] is False
    assert scan.text_layer_usable is True  # still usable as word evidence and for boxes
    assert _page(garbage, TEXT, kind_image=True).properties["text_layer_class"] == "stale_ocr"
    assert _page("", TEXT).properties["text_layer_class"] == "none"
    assert _page(TEXT, "").properties["text_layer_class"] == "unchecked"


def test_triage_handwriting_and_rotation():
    assert _page("", TEXT, kind_image=True, conf=0.7).properties["handwriting_likely"] is True
    assert _page("", TEXT, kind_image=True, conf=0.97).properties["handwriting_likely"] is False
    assert _page(TEXT, TEXT, conf=0.7).properties["handwriting_likely"] is False  # digital page
    page = Page(index=0, width=612, height=792)
    page.words["ppocr"] = [Word(text="sideways", source="ppocr",
                                bbox=BBox(left=0.1 + 0.02 * i, top=0.1, width=0.012, height=0.08))
                           for i in range(20)]
    Triage()._classify(page, {}, 0.8)
    assert page.properties["rotation_suspect"] == 90
    assert _page("", TEXT).properties["rotation_suspect"] == 0


# ---- faithfulness guards (Parse Judge error types: wrong numbers, autocorrection) ----
def test_select_never_trades_for_a_read_with_worse_numbers():
    nums = " ".join(str(1000 + i) for i in range(12))
    page = TEXT + " " + nums
    short = " ".join(TEXT.split()[:12]) + " " + nums  # default: dropped text, numbers right
    wrong = TEXT + " " + " ".join(str(5000 + i) for i in range(12))  # complete text, numbers not on the page
    evd = ev(page)
    idx, scores, _ = select_page([("a", short), ("b", wrong)], evd, min_evidence=10)
    assert scores[1].recall > scores[0].recall and scores[1].numeric_precision == 0.0
    assert idx == 0
    assert select_page([("a", short), ("b", page)], evd, min_evidence=10)[0] == 1


def test_fusion_never_touches_tokens_with_digits():
    layer = ev("invoice A123 dated today for the client", trusted=True)
    assert propose_edits("invoice A128 dated today for the client", [layer]) == []


def test_weak_evidence_flags_and_exact_layer_substitutes():
    from readanydoc.model import Document, Region
    from readanydoc.pipeline.base import PipelineConfig, PipelineContext
    from readanydoc.pipeline.verify import Verify

    def run(exact, **opts):
        w = [Word(text=t, source="s", bbox=BBox(left=0.1, top=0.1, width=0.1, height=0.01)) for t in TEXT.split()]
        page = Page(index=0, words={"pdf" if exact else "ppocr": w}, text_layer_usable=exact)
        page.properties.update({"text_layer_exact": exact,
                                "reads": [{"source": "b", "tier": "fast", "selected": False, "content": TEXT}]})
        page.regions = [Region(content=TEXT.replace("pension", "pensian"), source="a", meta={"tier": "fast"})]
        Verify(fusion=True, **opts).run(Document(pages=[page]), PipelineContext(PipelineConfig()))
        return page.regions[0]
    flagged = run(exact=False)
    assert "pensian" in flagged.content and flagged.meta["disputed_tokens"][0]["other"] == "pension"
    assert flagged.confidence.label == "low"
    assert "pension" in run(exact=False, fusion_second_read=True).content
    fixed = run(exact=True)
    assert "pension" in fixed.content and fixed.meta["fusion_edits"] and "disputed_tokens" not in fixed.meta
    assert any(c.meta.get("before_fusion") and "pensian" in c.content for c in fixed.candidates)


# ---- numeric table choice, cell votes, Claude layout parsing ----
def _tbl(rows):
    return "<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>"


ROWS = [["Item", "Q1", "Q2"], ["North", "1,200", "3,016"], ["South", "800", "5,399"], ["East", "450", "886"],
        ["Total", "2,450", "9,301"]]
EVN = ["1200", "3016", "800", "5399", "450", "886", "2450", "9301"]


def test_number_helpers():
    from readanydoc.pipeline.reading.tables import column_sums, number_tokens, parse_grid, parse_number
    assert number_tokens("3,016 and 1.50, (2,000) 12/31/2024.") == ["3016", "1.50", "2000", "12", "31", "2024"]
    assert parse_number("$1,575.00") == 1575.0 and parse_number("(200)") == -200.0 and parse_number("12 kg") is None
    assert column_sums(parse_grid(_tbl(ROWS))) == 2
    bad = [r[:] for r in ROWS]
    bad[2][2] = "5,390"
    assert column_sums(parse_grid(_tbl(bad))) == 1


def test_choose_table_numeric_is_conservative():
    from readanydoc.pipeline.reading.tables import choose_table_numeric
    bad = [r[:] for r in ROWS]
    bad[2][2], bad[3][2], bad[1][1] = "5,390", "856", "1,260"
    assert not choose_table_numeric(_tbl(ROWS), _tbl(ROWS), EVN).disputed  # same numbers: nothing to do
    win = choose_table_numeric(_tbl(bad), _tbl(ROWS), EVN)
    assert win.replace and win.disputed
    keep = choose_table_numeric(_tbl(ROWS), _tbl(bad), EVN)
    assert not keep.replace and keep.disputed  # ours is the better one: stays, flagged
    assert choose_table_numeric(_tbl(bad), _tbl(ROWS), []).replace  # no evidence, but the crop's totals add up
    nosum = [r for r in ROWS[:4]]
    nosum_bad = [r[:] for r in nosum]
    nosum_bad[2][2] = "5,390"
    unsure = choose_table_numeric(_tbl(nosum_bad), _tbl(nosum), [])
    assert not unsure.replace and unsure.disputed  # nothing decides: keep ours, low confidence
    assert not choose_table_numeric(_tbl(bad), "no table", EVN).replace
    assert not choose_table_numeric(_tbl(bad), _tbl(ROWS[:2]), EVN).replace  # crop lost rows


def test_vote_number_cells():
    from readanydoc.pipeline.reading.tables import vote_number_cells
    ours = [r[:] for r in ROWS]
    ours[2][2] = "5,390"
    fixed, edits, open_ = vote_number_cells(_tbl(ours), _tbl(ROWS), None, EVN)  # evidence backs the crop
    assert fixed == _tbl(ROWS) and edits[0]["old"] == "5,390" and not open_
    fixed, edits, open_ = vote_number_cells(_tbl(ours), _tbl(ROWS), None, [])  # nothing backs either
    assert fixed == _tbl(ours) and not edits and len(open_) == 1
    fixed, edits, _ = vote_number_cells(_tbl(ours), _tbl(ROWS), _tbl(ROWS), [])  # two reads against one
    assert fixed == _tbl(ROWS) and edits[0]["why"].startswith("second page read")
    fixed, edits, _ = vote_number_cells(_tbl(ours), _tbl(ROWS), _tbl(ROWS), ["5390"])  # evidence backs ours
    assert fixed == _tbl(ours) and not edits
    assert vote_number_cells(_tbl(ours), _tbl(ROWS[:3]), None, EVN)[1] == []  # different grid: hands off
    shifted = [[r[0], r[2], r[1]] for r in ROWS]
    assert vote_number_cells(_tbl(shifted), _tbl(ROWS), _tbl(ROWS), EVN)[1] == []  # too many differ


def test_claude_layout_parses_and_validates_boxes():
    from readanydoc.layout.base import LayoutError
    from readanydoc.layout.claude import ClaudeLayout, parse_regions
    answer = ('Here: {"regions": [{"type": "table", "box": [100, 500, 900, 1200]}, '
              '{"type": "table", "box": [102, 503, 899, 1199]}, {"type": "table", "box": [900, 100, 100, 50]}, '
              '{"type": "table", "box": [5000, 5000, 9000, 9000]}, {"type": "table", "box": [10, 10, 20, 20]}, '
              '{"type": "chart", "box": [0, 0, 500, 500]}, {"type": "table", "box": [50, 50, 950, 300]}]}')
    blocks = parse_regions(answer, 1000, 2000)
    assert len(blocks) == 2 and [b.order for b in blocks] == [0, 1]
    assert blocks[0].bbox.top < blocks[1].bbox.top  # reading order
    b = blocks[1].bbox
    assert abs(b.left - (0.1 - 0.012)) < 1e-6 and abs(b.bottom - (0.6 + 0.012)) < 1e-6  # padded, normalised
    assert parse_regions('{"regions": []}', 1000, 2000) == []
    with pytest.raises(LayoutError):
        parse_regions("I could not find any.", 1000, 2000)

    class FakeReader:
        def read(self, image, hint=None, context=None):
            assert "1000 pixels wide and 2000 pixels high" in context.prompt
            from readanydoc.readers.base import ReadResult
            return ReadResult(text='{"regions": [{"type": "table", "box": [100, 500, 900, 1200]}]}')
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1000, 2000), "white").save(buf, format="PNG")
    assert len(ClaudeLayout(reader=FakeReader()).detect(buf.getvalue())) == 1


def test_crop_that_covers_more_than_our_table_is_not_swapped_in():
    from readanydoc.pipeline.reading.tables import choose_table_numeric
    big = ROWS + [[f"Extra {i}", str(100 + i), str(200 + i)] for i in range(12)]
    ev = EVN + [str(100 + i) for i in range(12)] + [str(200 + i) for i in range(12)]
    bad = [r[:] for r in ROWS]
    bad[2][2] = "5,390"
    choice = choose_table_numeric(_tbl(bad), _tbl(big), ev)
    assert not choice.replace and "more than our table" in choice.reason


# ---- expert arbiter: majorities only ----
def test_majority_edits_need_two_other_reads():
    from readanydoc.pipeline.reading.fusion import majority_edits
    cur = "Paid to M. E. Seymoor the sum of $50 on March 3"
    a = "Paid to M. E. Seymour the sum of $500 on March 3"
    b = "Paid to M. E. Seymour the sum of $50 on March 8"
    edits = majority_edits(cur, [a, b])
    assert [(e.old, e.new) for e in edits] == [("Seymoor", "Seymour")]  # $500 and "8" have one vote each
    assert majority_edits(cur, [a]) == []  # one other read alone never changes anything
    assert apply_edits(cur, edits) == cur.replace("Seymoor", "Seymour")


def test_vote_cells_majority_needs_a_crop_read_and_aligned_grids():
    from readanydoc.pipeline.reading.tables import vote_cells_majority
    ours = [r[:] for r in ROWS]
    ours[2][2], ours[3][0] = "5,390", "Eest"
    good = _tbl(ROWS)
    fixed, edits, open_ = vote_cells_majority(_tbl(ours), [("r2", _tbl(ours), False), ("crop", good, True),
                                                           ("expert", good, True)])
    assert fixed == good and len(edits) == 2 and open_ == 0
    fixed, edits, open_ = vote_cells_majority(_tbl(ours), [("r2", good, False), ("r3", good, False)])
    assert fixed == _tbl(ours) and not edits and open_ == 2  # no crop read in the majority
    fixed, edits, open_ = vote_cells_majority(_tbl(ours), [("crop", _tbl(ours), True), ("expert", good, True)])
    assert fixed == _tbl(ours) and not edits and open_ == 2  # expert alone
    assert vote_cells_majority(_tbl(ours), [("crop", _tbl(ROWS[:3]), True), ("expert", good, True)])[1] == []


def test_majority_ignores_unrelated_words_at_the_aligned_position():
    from readanydoc.pipeline.reading.fusion import majority_edits
    cur = "Box 24 Line total due 3100 dollars"
    others = ["Box 24 Charges total due 2100 dollars"] * 2
    assert [(e.old, e.new) for e in majority_edits(cur, others)] == [("3100", "2100")]
