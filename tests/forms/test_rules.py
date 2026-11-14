from readanydoc.model import Page, Region, RegionType
from readanydoc.pipeline.forms import rules, switches

from .test_grid import AMOUNTS, CMS, W2


def _page(*contents):
    return Page(index=0, regions=[Region(type=RegionType.TABLE, content=c, source="reader") for c in contents])


def test_form_grid_becomes_key_value_and_data_table_stays():
    page = _page(CMS, AMOUNTS)
    info = rules.apply(page)
    assert [r.type for r in page.regions] == [RegionType.KEY_VALUE, RegionType.TABLE]
    assert info["converted"] == 1
    kv = page.regions[0]
    assert "<table" not in kv.content and "1a. INSURED'S I.D. NUMBER (For Program in Item 1)" in kv.content.split("\n")
    assert any(c.content == CMS and c.meta.get("replaced") for c in kv.candidates)  # the read is kept
    assert page.regions[1].content == AMOUNTS


def test_retype_only_keeps_the_table_markup():
    page = _page(W2)
    rules.apply(page, retype_only=True)
    assert page.regions[0].type == RegionType.KEY_VALUE and page.regions[0].content == W2


def test_text_around_a_table_is_kept():
    page = _page("Section A\n" + CMS + "\ntrailing note")
    rules.apply(page)
    lines = page.regions[0].content.split("\n")
    assert lines[0] == "Section A" and lines[-1] == "trailing note"


def test_switches_are_off_by_default(monkeypatch):
    monkeypatch.delenv("READANYDOC_FORMS", raising=False)
    assert switches() == set()
    monkeypatch.setenv("READANYDOC_FORMS", "kv_rules, nonsense,fillable")
    assert switches() == {"kv_rules", "fillable"}


def test_rows_variant_writes_one_key_value_block_per_row_and_keeps_every_line():
    whole = _page(CMS)
    rules.apply(whole)
    page = _page(CMS, AMOUNTS)
    rules.apply(page, by_row=True)
    kv = [r for r in page.regions if r.type == RegionType.KEY_VALUE]
    assert len(kv) > 1 and page.regions[-1].type == RegionType.TABLE and page.regions[-1].content == AMOUNTS
    assert "\n".join(r.content for r in kv) == whole.regions[0].content  # same lines, only cut into blocks
    assert any(c.content == CMS and c.meta.get("replaced") for c in kv[0].candidates)


def test_rows_tied_by_a_rowspan_stay_in_one_block():
    from readanydoc.pipeline.forms import grid
    t = ("<table><tr><td rowspan=\"2\">1 Name<br>Ann</td><td>2 Date<br>1/2</td></tr><tr><td>3 City<br>Rome</td></tr>"
         "<tr><td>4 Zip<br>1</td><td>5 Tel<br>2</td></tr></table>")
    assert grid.to_key_value_groups(t) == ["1 Name\nAnn\n2 Date\n1/2\n3 City\nRome", "4 Zip\n1\n5 Tel\n2"]


def test_sub_switches_are_parsed(monkeypatch):
    monkeypatch.setenv("READANYDOC_FORMS", "kv_rules:rows,kv_lines:join")
    assert switches() == {"kv_rules:rows", "kv_lines:join"}


# ---------------------------------------------------------------------------------------------- round 4: kv_guard
# a data table whose cells start with a count: every cell looks like a numbered form box to grid.classify
COUNTS = ("<table><tr><td>Trust</td><td>Foundation</td><td>Finance Corporation</td></tr>"
          "<tr><td>5 Council Trustees</td><td>5 Council Directors</td><td>5 Council Directors</td></tr>"
          "<tr><td>3 Community Trustees</td><td>3 Community Directors</td><td>3 Community Advisors</td></tr>"
          "<tr><td>1 City Trustee</td><td>1 City Director</td><td>1 City Advisor</td></tr></table>")


def _located_page(content, dets, box=(0.1, 0.1, 0.9, 0.9)):
    """A page with one table of the read, the layout detections ``dets`` and OCR words of the table inside ``box``."""
    import re

    from readanydoc.model import BBox, Confidence, Word
    page = _page(content)
    toks = re.findall(r"\w+", re.sub(r"<[^>]+>", " ", content))
    x0, y0, x1, y1 = box
    page.words["tesseract"] = [
        Word(text=t, bbox=BBox.from_xyxy(x0 + (x1 - x0) * (k % 10) / 11, y0 + (y1 - y0) * (k // 10) / 12,
                                         x0 + (x1 - x0) * (k % 10 + 0.8) / 11, y0 + (y1 - y0) * (k // 10 + 0.8) / 12, 0),
             source="tesseract", line=k // 10, confidence=Confidence.of(0.95)) for k, t in enumerate(toks)]
    page.properties["layout_detections"] = dets
    return page


def _det(label, score, box):
    return {"id": f"d{label}", "label": label, "type": label, "score": score, "box": list(box), "order": 0, "inside": None}


def test_counts_table_is_a_form_grid_for_the_text_rule_alone():
    from readanydoc.pipeline.forms import grid
    assert grid.classify(COUNTS).kind == "form"          # the failure of the full RD-TableBench run (table 7564)
    page = _located_page(COUNTS, [_det("table", 0.98, (0.05, 0.05, 0.95, 0.95))])
    rules.apply(page, by_row=True)                       # without the guard: as before
    assert all(r.type == RegionType.KEY_VALUE for r in page.regions)


def test_guard_keeps_a_table_the_layout_model_sees():
    page = _located_page(COUNTS, [_det("table", 0.98, (0.05, 0.05, 0.95, 0.95))])
    info = rules.apply(page, by_row=True, guard=True)
    assert [r.type for r in page.regions] == [RegionType.TABLE] and page.regions[0].content == COUNTS
    assert info["converted"] == 0 and info["kept_by_layout"][0]["score"] == 0.98
    assert page.regions[0].meta["forms"]["rule"] == "kv_guard"


def test_guard_needs_a_sure_table_detection_around_the_tables_words():
    for dets in ([],                                                    # no layout evidence
                 [_det("form", 0.96, (0.05, 0.05, 0.95, 0.95))],        # the layout model sees a form
                 [_det("table", 0.4, (0.05, 0.05, 0.95, 0.95))],        # an unsure table
                 [_det("table", 0.9, (0.05, 0.92, 0.95, 0.99))]):       # a table somewhere else on the page
        page = _located_page(COUNTS, dets)
        rules.apply(page, by_row=True, guard=True)
        assert all(r.type == RegionType.KEY_VALUE for r in page.regions), dets


def test_guard_leaves_real_form_grids_and_data_tables_as_they_were():
    page = _located_page(CMS, [_det("form", 0.9, (0.0, 0.0, 1.0, 1.0))])
    rules.apply(page, guard=True)
    assert page.regions[0].type == RegionType.KEY_VALUE
    page = _page(AMOUNTS)
    assert rules.apply(page, guard=True) == {} and page.regions[0].type == RegionType.TABLE


def test_guard_without_page_words_counts_only_for_a_single_table():
    one = _page(COUNTS)
    one.properties["layout_detections"] = [_det("table", 0.9, (0.1, 0.1, 0.9, 0.9))]
    rules.apply(one, guard=True)
    assert one.regions[0].type == RegionType.TABLE
    two = _page(COUNTS, COUNTS)
    two.properties["layout_detections"] = [_det("table", 0.9, (0.1, 0.1, 0.9, 0.9))]
    rules.apply(two, guard=True)
    assert all(r.type == RegionType.KEY_VALUE for r in two.regions)


def test_kv_guard_is_a_forms_switch(monkeypatch):
    monkeypatch.setenv("READANYDOC_FORMS", "kv_rules:rows,kv_guard")
    assert switches() == {"kv_rules:rows", "kv_guard"}


# a checkbox list with an amount column: the amounts carry a currency word, so no cell is a bare number
EXEMPTIONS = ("<table><tr><td rowspan=\"4\">1. Organizacja korzystała z następujących zwolnień</td>"
              "<td>[ ] z podatku dochodowego od osób prawnych</td><td>0.00 zł</td></tr>"
              "<tr><td>[ ] z podatku od nieruchomości</td><td>0.00 zł</td></tr>"
              "<tr><td>[ ] z opłaty skarbowej</td><td>12 500,00 zł</td></tr>"
              "<tr><td>[x] nie korzystała</td><td></td></tr></table>")


def test_amounts_with_a_unit_are_amounts_and_box_ids_are_not():
    from readanydoc.pipeline.forms import grid
    for s in ("0.00 zł", "550 kg", "USD 12.50", "Rs. 500", "1,20 m", "12 345,00 EUR", "(1 234.50) kr", "45", "3.5 %"):
        assert grid.is_amount(s), s
    for s in ("1 a", "2 b", "12a", "5 Council", "10 Finanzvermögen", "[ ] 550 kg", "Total 12", "", "kg"):
        assert not grid.is_amount(s), s


def test_a_column_of_amounts_is_data_table_evidence_only_with_the_guard():
    from readanydoc.pipeline.forms import grid
    assert grid.classify(EXEMPTIONS).kind == "form"                      # table 21693 of the full run: as before
    v = grid.classify(EXEMPTIONS, amounts=True)
    assert v.kind == "data" and "amounts" in v.reason
    page = _page(EXEMPTIONS)
    assert rules.apply(page, by_row=True, guard=True) == {} and page.regions[0].type == RegionType.TABLE
    assert grid.classify(CMS, amounts=True).kind == "form" and grid.classify(W2, amounts=True).kind == grid.classify(W2).kind
