"""Fillable PDFs: field values and checkbox states from the file correct and complete the page read."""
from pathlib import Path

import pytest

from readanydoc.model import Document, Page, Region, RegionType
from readanydoc.pipeline.forms import acroform, fillable


def _fields():
    return [
        {"page": 1, "type": "text", "name": "f1", "label": "Last Name (Family Name)", "tooltip": "", "bbox": [0.1, 0.10, 0.4, 0.12], "value": "Morgan", "checked": None, "on_state": None, "group": None},
        {"page": 1, "type": "text", "name": "f2", "label": "First Name", "tooltip": "", "bbox": [0.5, 0.10, 0.8, 0.12], "value": "", "checked": None, "on_state": None, "group": None},
        {"page": 1, "type": "checkbox", "name": "c1", "label": "A citizen of the United States", "tooltip": "", "bbox": [0.1, 0.2, 0.12, 0.22], "value": None, "checked": True, "on_state": "1", "group": None},
        {"page": 1, "type": "checkbox", "name": "c2", "label": "A noncitizen national", "tooltip": "", "bbox": [0.1, 0.24, 0.12, 0.26], "value": None, "checked": False, "on_state": "2", "group": None},
        {"page": 1, "type": "text", "name": "f3", "label": "", "tooltip": "", "bbox": [0.1, 0.5, 0.4, 0.52], "value": "42 Elm Street", "checked": None, "on_state": None, "group": None},
        {"page": 1, "type": "text", "name": "f4", "label": "CODE", "tooltip": "", "bbox": [0.1, 0.6, 0.2, 0.62], "value": "A1", "checked": None, "on_state": None, "group": None},
        {"page": 1, "type": "text", "name": "f5", "label": "CODE", "tooltip": "", "bbox": [0.6, 0.6, 0.7, 0.62], "value": "B2", "checked": None, "on_state": None, "group": None},
    ]


def _regions():
    return [Region(type=RegionType.TABLE, content="<table><tr><td>Last Name (<i>Family Name</i>)</td><td>First Name</td></tr></table>"),
            Region(type=RegionType.TEXT, content="[ ] 1. A citizen of the United States\n[x] 2. A noncitizen national"),
            Region(type=RegionType.TEXT, content="6. ISSUED BY CODE ____ and 7. ADMINISTERED BY CODE")]


def test_marks_values_and_empty_fields():
    regions, fields = _regions(), _fields()
    found = fillable.locate(regions, fields)
    assert set(found) == {0, 1, 2, 3, 5, 6}  # the unlabelled field is not located
    edits, stats, left = fillable.plan_edits(regions, fields, found)
    fillable._apply_edits(regions, edits)
    assert regions[0].content == "<table><tr><td>Last Name (<i>Family Name</i>) Morgan</td><td>First Name <empty/></td></tr></table>"
    assert regions[1].content == "[x] 1. A citizen of the United States\n[ ] 2. A noncitizen national"
    assert regions[2].content == "6. ISSUED BY CODE A1 and 7. ADMINISTERED BY CODE B2"  # same label twice: paired in order
    assert stats["marks_corrected"] == 2 and stats["values_inserted"] == 3 and stats["empty_marked"] == 1
    assert [f["name"] for f in left] == ["f3"]
    assert any(c.meta.get("replaced") for c in regions[1].candidates)  # the read is kept as a candidate


def test_ambiguous_labels_are_left_alone():
    regions = [Region(type=RegionType.TEXT, content="CODE one CODE two CODE three")]
    fields = [f for f in _fields() if f["label"] == "CODE"]
    assert fillable.locate(regions, fields) == {}  # three occurrences, two fields
    edits, stats, left = fillable.plan_edits(regions, fields, {})
    assert not edits and len(left) == 2


def test_value_already_in_the_read_is_not_written_twice():
    regions = [Region(type=RegionType.TEXT, content="Last Name (Family Name): MORGAN")]
    edits, stats, left = fillable.plan_edits(regions, _fields()[:1], fillable.locate(regions, _fields()[:1]))
    assert not edits and stats["values_present"] == 1


def test_apply_on_a_real_fillable_pdf(tmp_path):
    src = Path(__file__).resolve().parents[3] / "bizdocs" / "pages" / "legal_sf30_p001.pdf"
    if not src.exists():
        pytest.skip("public test page not present")
    fields = acroform.page_fields(src, 1)
    assert len(fields) == 40 and {f["type"] for f in fields} == {"text", "checkbox"}
    assert all(0 <= f["bbox"][0] < f["bbox"][2] <= 1 and 0 <= f["bbox"][1] < f["bbox"][3] <= 1 for f in fields)
    page = Page(index=0, original_page=1, regions=[
        Region(type=RegionType.TEXT, content="[x] is extended. [ ] is not extended."),
        Region(type=RegionType.TEXT, content="2. AMENDMENT/MODIFICATION NUMBER")])
    doc = Document(source=str(src), file_type="pdf", meta={"path": str(src)}, pages=[page])
    info = fillable.apply(doc, page)
    assert info["fields"] == 40 and info["located"] >= 2
    assert page.regions[0].content.startswith("[ ] is extended")  # the file says it is not checked
    assert page.regions[1].type == RegionType.KEY_VALUE and page.regions[1].content.endswith("<empty/>")


def test_file_without_fields_changes_nothing(tmp_path):
    p = tmp_path / "x.pdf"
    import pikepdf
    pdf = pikepdf.new(); pdf.add_blank_page(); pdf.save(str(p))
    page = Page(index=0, original_page=1, regions=[Region(type=RegionType.TEXT, content="Name: [ ]")])
    doc = Document(source=str(p), file_type="pdf", meta={"path": str(p)}, pages=[page])
    assert fillable.apply(doc, page) == {} and page.regions[0].content == "Name: [ ]"


def test_value_goes_after_the_bracket_the_label_stopped_in():
    regions = [Region(type=RegionType.LIST_ITEM, content="9. Place where injury occurred (e.g. 2nd floor, Main Post Office Bldg., 12th & Pine)")]
    fields = [{"page": 1, "type": "text", "name": "p", "label": "9. Place where injury occurred (e.g. 2nd floor, Main Post", "tooltip": "",
               "bbox": [0.1, 0.1, 0.9, 0.12], "value": "Dock 4", "checked": None, "on_state": None, "group": None}]
    edits, _, _ = fillable.plan_edits(regions, fields, fillable.locate(regions, fields))
    fillable._apply_edits(regions, edits)
    assert regions[0].content.endswith("12th & Pine) Dock 4")


def test_a_box_and_a_text_field_may_share_one_label_and_tooltips_are_a_fallback():
    regions = [Region(type=RegionType.TEXT, content="[ ] 3. A lawful permanent resident\n[ ] ARE [ ] ARE NOT ATTACHED")]
    base = {"page": 1, "tooltip": "", "value": None, "checked": None, "on_state": None, "group": None}
    fields = [{**base, "type": "checkbox", "name": "c", "label": "3. A lawful permanent resident", "bbox": [0.1, 0.1, 0.12, 0.12], "checked": True},
              {**base, "type": "text", "name": "t", "label": "3. A lawful permanent resident", "bbox": [0.5, 0.1, 0.8, 0.12], "value": "A123"},
              {**base, "type": "checkbox", "name": "n", "label": "NOT", "tooltip": "ARE NOT ATTACHED", "bbox": [0.3, 0.2, 0.32, 0.22], "checked": True},
              {**base, "type": "checkbox", "name": "u", "label": "", "tooltip": "", "bbox": [0.3, 0.3, 0.32, 0.32], "checked": True}]
    found = fillable.locate(regions, fields)
    assert set(found) == {0, 1, 2}
    edits, stats, left = fillable.plan_edits(regions, fields, found)
    fillable._apply_edits(regions, edits)
    assert regions[0].content == "[x] 3. A lawful permanent resident A123\n[ ] ARE [x] ARE NOT ATTACHED"
    assert [f["name"] for f in left] == ["u"] and stats["marks_unplaced"] == 1
