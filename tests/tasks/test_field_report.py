"""Field report: every field says how it was found, for any document type.

One test group per case a bare value used to hide: two values in one document, a value the document never
states, the kinds of empty, a condition on a value, conversions to check (day/month order, parentheses,
amounts in thousands, percentages, currency), crossed-out text and VOID stamps, lists shorter than their
table, and the report with citations off.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from readanydoc.tasks import FakeLLM, extract
from readanydoc.tasks import field_report as FR
from readanydoc.tasks.common import DocView, render_pages
from tests.tasks.conftest import make_doc

ORDER_FORM = [
    [("Title", "Order Form OF-118"),                                                                   # r1
     ("Table", "<table><tr><th>Product</th><th>Price</th></tr>"
               "<tr><td>Platform fee</td><td>$4,500 / month</td></tr></table>"),                        # r2
     "Customer: Acme Robotics Ltd"],                                                                    # r3
    [("Section Header", "Special terms"),                                                              # r4
     "For the first year, the platform fee will be invoiced at $4,200 / month."],                       # r5
]
SCHEMA = {"type": "object", "properties": {"customer": {"type": "string"}, "platform_fee": {"type": "number"},
                                           "status": {"type": "string", "enum": ["paid", "unpaid"]},
                                           "po_number": {"type": "string"}, "fax": {"type": "string"},
                                           "tax_id": {"type": "string"}}}


def cfg(schema=SCHEMA, **settings):
    return {"instructions": {"schema": schema}, "settings": settings}


def run(reply, pages=ORDER_FORM, schema=SCHEMA, **settings):
    llm = FakeLLM(reply if callable(reply) else [reply])
    out = extract.run(make_doc(pages), cfg(schema, **settings), llm=llm)
    return out, llm


def fields(out):
    return out["field_report"]["fields"]


BASE = {"customer": "Acme Robotics Ltd", "platform_fee": 4500, "status": None, "po_number": None, "fax": None,
        "tax_id": None}


def reply(data=None, sources=None):
    return {"data": {**BASE, **(data or {})},
            "sources": [{"path": "customer", "block": "r3"}, {"path": "platform_fee", "block": "r2"}] + (sources or [])}


# ---------------------------------------------------------------- 1. two values in one document

def test_a_second_value_in_the_document_makes_the_field_a_conflict_with_both_candidates():
    out, llm = run(reply(sources=[{"path": "platform_fee", "block": "r2", "printed": "$4,500 / month",
                                   "alternatives": [{"value": 4200, "block": "r5", "note": "first year only"}]}]))
    f = fields(out)["platform_fee"]
    assert f["status"] == "conflict" and f["value"] == 4500  # the value is unchanged; the report says more
    assert [(c["value"], c["source"]["page"]) for c in f["candidates"]] == [(4500, 1), (4200, 2)]
    assert f["candidates"][1]["note"] == "first year only" and f["candidates"][1]["source"]["block"] == "r5"
    assert any("platform_fee: the document gives different values" in w for w in out["warnings"])
    assert '"alternatives"' in llm.calls[0]["user"]  # the model is asked for other values


def test_an_alternative_that_is_not_on_the_page_is_dropped_not_reported():
    out, _ = run(reply(sources=[{"path": "platform_fee", "block": "r2",
                                 "alternatives": [{"value": 3900, "block": "r5"}]}]))
    assert fields(out)["platform_fee"]["status"] == "found" and "candidates" not in fields(out)["platform_fee"]


def test_the_same_number_written_differently_is_not_a_conflict():
    out, _ = run(reply(sources=[{"path": "platform_fee", "block": "r2",
                                 "alternatives": [{"value": "$4,500", "block": "r2"}]}]))
    assert fields(out)["platform_fee"]["status"] == "found"


# ---------------------------------------------------------------- 2. a value the document never states

def test_a_choice_the_document_never_states_is_inferred():
    # seen live: the document has only "Total Due", the schema asks paid/unpaid, the model said "unpaid"
    out, _ = run(reply({"status": "unpaid"}))
    assert fields(out)["status"]["status"] == "inferred"
    assert any("status: 'unpaid' is not stated in the document" in w for w in out["warnings"])


def test_a_number_that_is_not_on_any_page_is_inferred_even_if_the_model_cites_a_block():
    out, _ = run(reply({"platform_fee": 54000}, [{"path": "platform_fee", "block": "r2"}]))  # 12 x 4,500
    assert fields(out)["platform_fee"]["status"] == "inferred"


def test_the_models_own_inferred_flag_is_kept_for_a_yes_no():
    schema = {"type": "object", "properties": {"auto_renew": {"type": "boolean"}}}
    out, _ = run({"data": {"auto_renew": True}, "sources": [{"path": "auto_renew", "block": "r5", "inferred": True}]},
                 schema=schema)
    assert fields(out)["auto_renew"]["status"] == "inferred"
    out, _ = run({"data": {"auto_renew": True}, "sources": [{"path": "auto_renew", "block": "r5"}]}, schema=schema)
    assert fields(out)["auto_renew"]["status"] == "found"


def test_values_on_the_page_are_found():
    out, _ = run(reply())
    assert fields(out)["customer"] == {"value": "Acme Robotics Ltd", "status": "found"}
    assert fields(out)["platform_fee"]["status"] == "found"
    assert fields(out)["platform_fee"]["printed_as"] == "4,500"  # the text the number was read from


# ---------------------------------------------------------------- 3. the kinds of empty

def test_blank_not_applicable_and_not_in_document_are_told_apart():
    out, _ = run(reply(sources=[{"path": "po_number", "empty": "blank"}, {"path": "tax_id", "empty": "not_applicable"}]))
    f = fields(out)
    assert f["po_number"]["status"] == "blank" and f["tax_id"]["status"] == "not_applicable"
    assert f["fax"]["status"] == "not_in_document"


def test_an_empty_string_or_a_line_of_underscores_is_blank_not_inferred():
    out, _ = run(reply({"po_number": "", "fax": "____"}))
    assert fields(out)["po_number"]["status"] == "blank" and fields(out)["fax"]["status"] == "blank"
    assert not any("po_number" in w for w in out["warnings"])


def test_a_part_whose_reply_cannot_be_read_is_unreadable_not_missing():
    def handler(system, user, i):
        props = json.loads(user.split("JSON Schema:\n", 1)[1].split("\n", 1)[0])["properties"]
        if "fax" in props and "customer" not in props:
            return '{"data": {"fax": "x",, "sources": []}'
        if len(props) == len(SCHEMA["properties"]):
            return '{"data": {"customer": "Acme Robotics Ltd",, "sources": []}'
        return {"data": {k: BASE.get(k) for k in props}, "sources": [{"path": "customer", "block": "r3"}]}
    out, _ = run(handler)
    f = fields(out)
    assert f["customer"]["status"] == "found"
    assert f["fax"]["status"] == "unreadable" and f["tax_id"]["status"] == "unreadable"


# ---------------------------------------------------------------- 4. a condition on a value

def test_a_condition_on_a_value_is_kept_as_a_note():
    out, _ = run(reply({"platform_fee": 4200}, [{"path": "platform_fee", "block": "r5", "note": "first year only"}]))
    assert fields(out)["platform_fee"]["notes"] == ["first year only"]


# ---------------------------------------------------------------- 5. conversions to check

@pytest.mark.parametrize("value,printed,context,flag", [
    ("2025-03-04", "03/04/2025", "", "ambiguous_date"),
    (1200, "(1,200)", "", "parentheses"),
    (450, "450", "Revenue (amounts in thousands)", "scale"),
    (0.1, "10%", "", "percent_scaled"),
    (4500, "$4,500", "", "currency"),
])
def test_conversions_a_reader_should_check_are_flagged(value, printed, context, flag):
    assert flag in [f for f, _ in FR.format_flags(value, printed, context)]


@pytest.mark.parametrize("value,printed", [("2025-03-13", "13/03/2025"), (-1200, "(1,200)"), (10, "10%"), (450, "450")])
def test_unambiguous_conversions_are_not_flagged(value, printed):
    flags = [f for f, _ in FR.format_flags(value, printed, "")]
    assert not set(flags) & {"ambiguous_date", "parentheses", "percent_scaled", "scale"}


def test_flags_reach_the_report_and_the_warnings():
    pages = [["Statement date: 03/04/2025", "Net loss: (1,200)", "All amounts in thousands"]]
    schema = {"type": "object", "properties": {"date": {"type": "string"}, "net": {"type": "number"}}}
    out, _ = run({"data": {"date": "2025-03-04", "net": 1200},
                  "sources": [{"path": "date", "block": "r1", "printed": "03/04/2025"},
                              {"path": "net", "block": "r2", "printed": "(1,200)"}]}, pages, schema)
    f = fields(out)
    assert f["date"]["printed_as"] == "03/04/2025" and "ambiguous_date" in f["date"]["flags"]
    assert {"parentheses", "scale"} <= set(f["net"]["flags"])
    assert any("day and month" in w for w in out["warnings"]) and any("parentheses" in w for w in out["warnings"])


def test_a_printed_text_the_page_does_not_have_is_ignored():
    out, _ = run(reply(sources=[{"path": "platform_fee", "block": "r2", "printed": "$9,999"}]))
    assert fields(out)["platform_fee"]["printed_as"] == "4,500"


# ---------------------------------------------------------------- 6. crossed-out text and VOID stamps

def word_mask(struck=False, underline=False, h=20, w=60):
    m = np.zeros((h, w), dtype=bool)
    for x in range(2, w - 2, 7):  # letter strokes with gaps between letters
        m[3:h - 3, x:x + 3] = True
    if struck:
        m[h // 2 - 1:h // 2 + 1, :] = True
    if underline:
        m[h - 2:h, :] = True
    return m


def test_a_line_through_a_word_is_detected_an_underline_or_plain_word_is_not():
    assert FR.is_struck(word_mask(struck=True))
    assert not FR.is_struck(word_mask())
    assert not FR.is_struck(word_mask(underline=True))
    assert not FR.is_struck(np.ones((20, 60), dtype=bool))  # a solid block (a filled box) is not a strike


def struck_doc(tmp_path):
    doc = make_doc([["Price: 4,500 4,200"]])
    page = doc.pages[0]
    region = page.regions[0]
    words = list(region.iter_words())
    W, H = 1000, 1000
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    for wd in words:
        x0, y0 = int(wd.bbox.left * W), int(wd.bbox.top * H)
        x1, y1 = int((wd.bbox.left + wd.bbox.width) * W), int((wd.bbox.top + wd.bbox.height) * H)
        for x in range(x0 + 2, x1 - 2, 7):
            d.rectangle([x, y0 + 3, x + 2, y1 - 3], fill=0)
        if wd.char_start < 12 and wd.text != "Price":  # "4,500" is crossed out (its words "4" and "500")
            d.rectangle([x0, (y0 + y1) // 2 - 1, x1, (y0 + y1) // 2], fill=0)
    path = tmp_path / "page.png"
    img.save(path)
    page.renders = {"100": str(path)}
    return doc


def test_crossed_out_text_is_shown_to_the_model_and_a_struck_value_is_a_conflict(tmp_path):
    doc = struck_doc(tmp_path)
    view = DocView(doc)
    struck = FR.struck_spans(view.pages)
    assert render_pages(view.pages, marks=struck).endswith("Price: ~~4,500~~ 4,200")
    schema = {"type": "object", "properties": {"price": {"type": "number"}}}
    llm = FakeLLM([{"data": {"price": 4500}, "sources": [{"path": "price", "block": "r1"}]}])
    out = extract.run(doc, cfg(schema), llm=llm)
    assert "~~4,500~~" in llm.calls[0]["user"] and "crossed out" in llm.calls[0]["user"]
    f = out["field_report"]["fields"]["price"]
    assert f["status"] == "conflict" and "struck_through" in f["flags"]


def test_a_page_stamped_void_is_reported():
    pages = [["Invoice 7", "Total: 90"], [("Text", "VOID"), "Invoice 7", "Total: 95"]]
    schema = {"type": "object", "properties": {"total": {"type": "number"}}}
    out, _ = run({"data": {"total": 95}, "sources": [{"path": "total", "block": "r5"}]}, pages, schema)
    assert out["field_report"]["pages"] == {"stamped": {"2": "VOID"}}
    assert "void_page" in fields(out)["total"]["flags"]
    assert "page 2 is stamped VOID" in out["warnings"]


# ---------------------------------------------------------------- 7. lists shorter than their table

ITEMS = [[("Table", "<table><tr><th>Item</th><th>Amount</th></tr>"
                    "<tr><td>Servo</td><td>400</td></tr><tr><td>Board</td><td>1100</td></tr>"
                    "<tr><td>Shipping</td><td>75</td></tr><tr><td>Total</td><td>1575</td></tr></table>")]]
LIST_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object", "properties": {"item": {"type": "string"}, "amount": {"type": "number"}}}}}}


def items_reply(names):
    rows = {"Servo": 400, "Board": 1100, "Shipping": 75}
    return {"data": {"items": [{"item": n, "amount": rows[n]} for n in names]},
            "sources": [{"path": f"items[{i}]", "block": "r1"} for i in range(len(names))]}


def test_a_list_shorter_than_its_table_is_asked_for_again_and_fixed():
    out, llm = run(lambda s, u, i: items_reply(["Servo", "Board"] if i == 0 else ["Servo", "Board", "Shipping"]),
                   ITEMS, LIST_SCHEMA)
    assert len(out["result"][0]["items"]) == 3 and len(llm.calls) == 2
    assert "has 3 rows" in llm.calls[1]["user"]  # the Total row is not counted as an item
    assert out["field_report"]["lists"] == [] and not any("rows" in w for w in out["warnings"])


def test_a_list_that_stays_short_is_reported():
    out, llm = run(items_reply(["Servo", "Board"]), ITEMS, LIST_SCHEMA)
    assert len(llm.calls) == 2
    assert out["field_report"]["lists"] == [{"path": "items", "items": 2, "table_rows": 3, "block": "r1"}]
    assert any("2 items, but the table they come from has 3 rows" in w for w in out["warnings"])


def test_a_complete_list_costs_no_extra_call():
    out, llm = run(items_reply(["Servo", "Board", "Shipping"]), ITEMS, LIST_SCHEMA)
    assert len(llm.calls) == 1 and out["field_report"]["lists"] == []


@pytest.mark.parametrize("content,rows", [
    ("<table><tr><th>A</th><th>B</th></tr><tr><td>x</td><td>1</td></tr><tr><td>Subtotal</td><td>1</td></tr>"
     "<tr><td>VAT</td><td>0.2</td></tr><tr><td></td><td>1.2</td></tr></table>", 1),
    ("| A | B |\n|---|---|\n| x | 1 |\n| y | 2 |\n| Grand total | 3 |", 2),
])
def test_item_rows_skip_headers_totals_and_near_empty_rows(content, rows):
    assert FR.item_rows(content) == rows


# ---------------------------------------------------------------- 8. with citations off, and turned off

def test_the_report_comes_with_citations_off_and_can_be_turned_off():
    out, llm = run(reply())
    assert out["result"] == [BASE] and "field_report" in out  # values in Reducto's shape, report beside them
    out, llm = run(BASE, field_report=False)
    assert "field_report" not in out and '"sources"' not in llm.calls[0]["user"]
