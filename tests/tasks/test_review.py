"""What an extraction returns besides the user's fields, and what it costs.

By default: only the schema's fields (and citations when asked). Nothing else is written by the model. Opt-in:
``review`` (code-only checks, no model call), ``alternatives`` (other values the document gives for a field) and
``derived_values: false`` (printed values only). Also: a reply that fails is reported only if its fields stay
empty, and a cut-off reply is split by fields with the pages kept together.
"""

from __future__ import annotations

import json

import numpy as np  # noqa: F401  (kept for parity with the image helpers)
import pytest
from PIL import Image, ImageDraw

from readanydoc.model import BBox, Word
from readanydoc.tasks import FakeLLM, extract
from readanydoc.tasks import field_report as FR
from readanydoc.tasks.common import DocView, render_pages
from readanydoc.tasks.llm import LLMResult
from tests.tasks.conftest import make_doc

ORDER_FORM = [
    [("Title", "Order Form OF-118"),
     ("Table", "<table><tr><th>Product</th><th>Price</th></tr>"
               "<tr><td>Platform fee</td><td>$4,500 / month</td></tr></table>"),
     "Customer: Acme Robotics Ltd", "Start date: 03/04/2025"],
    [("Section Header", "Special terms"),
     "For the first year, the platform fee will be invoiced at $4,200 / month."],
]
SCHEMA = {"type": "object", "properties": {"customer": {"type": "string"}, "platform_fee": {"type": "number"},
                                           "start": {"type": ["string", "null"]}, "end": {"type": ["string", "null"]},
                                           "po_number": {"type": ["string", "null"]}}}
ANSWER = {"customer": "Acme Robotics Ltd", "platform_fee": "4200", "start": "2025-03-04", "end": "2026-03-04",
          "po_number": ""}


def run(reply, pages=ORDER_FORM, schema=SCHEMA, **settings):
    llm = FakeLLM(reply if callable(reply) else [reply])
    out = extract.run(make_doc(pages), {"instructions": {"schema": schema}, "settings": settings}, llm=llm)
    return out, llm


# ---------------------------------------------------------------- the default: only the user's fields

def test_by_default_the_output_is_the_fields_and_nothing_else():
    out, llm = run(ANSWER)
    assert out["result"] == [{"customer": "Acme Robotics Ltd", "platform_fee": 4200, "start": "2025-03-04",
                              "end": "2026-03-04", "po_number": None}]
    assert "field_report" not in out and "review" not in out and "alternatives" not in out
    sent = llm.calls[0]["json_schema"]
    assert set(sent["properties"]) == set(SCHEMA["properties"])  # the model writes the user's fields only
    assert len(llm.calls) == 1
    assert not [w for w in out["warnings"] if "worked out" in w or "assumed" in w]  # no "inferred" noise


def test_derived_values_can_be_turned_off_in_the_request():
    _, llm = run(ANSWER, derived_values=False)
    assert "Do not work out or assume a value" in llm.calls[0]["user"]
    _, llm = run(ANSWER)
    assert "Do not work out or assume a value" not in llm.calls[0]["user"]


# ---------------------------------------------------------------- alternatives (opt-in)

def test_alternatives_are_returned_apart_from_the_fields_with_their_place():
    answer = dict(ANSWER, _alternatives=[{"field": "platform_fee", "value": "$4,500 / month",
                                          "note": "fee table; special term 1 gives 4,200"}])
    out, llm = run(answer, alternatives=True, citations={"enabled": True})
    assert "_alternatives" in llm.calls[0]["json_schema"]["properties"]
    res = out["result"]
    assert "_alternatives" not in res and res["platform_fee"]["value"] == 4200
    (alt,) = out["alternatives"]["platform_fee"]
    assert alt["value"] == "$4,500 / month" and "special term" in alt["note"]
    assert alt["citations"] and alt["citations"][0]["bbox"]["page"] == 1


def test_alternatives_can_be_limited_to_some_fields():
    _, llm = run(ANSWER, alternatives=["platform_fee"])
    assert "Only for these fields: platform_fee" in json.dumps(llm.calls[0]["json_schema"])


# ---------------------------------------------------------------- review (opt-in, code only)

def flags(out):
    return {(r["field"], r["flag"]) for r in out["review"]}


def test_review_flags_values_not_printed_as_returned_and_ambiguous_dates():
    out, llm = run(ANSWER, review=True)
    assert len(llm.calls) == 1  # no extra model call
    f = flags(out)
    assert ("end", "not_found_on_page") in f       # worked out from a term
    assert ("start", "ambiguous_date") in f        # printed 03/04/2025: March 4 or April 3
    assert ("customer", "not_found_on_page") not in f and ("platform_fee", "not_found_on_page") not in f


def test_a_page_stamped_void_is_flagged():
    pages = [["Invoice 7", "Total: 90"], [("Text", "VOID"), "Invoice 7", "Total: 95"]]
    schema = {"type": "object", "properties": {"total": {"type": "number"}}}
    out, _ = run({"total": "95"}, pages, schema, review=True)
    assert ("total", "void_page") in flags(out)


ITEMS = [[("Table", "<table><tr><th>Item</th><th>Amount</th></tr>"
                    "<tr><td>Servo</td><td>$400.00</td></tr><tr><td>(includes mounting kit)</td><td></td></tr>"
                    "<tr><td>Board</td><td>$1,100.00</td></tr><tr><td>Shipping</td><td>$75.00</td></tr>"
                    "<tr><td>Total</td><td>$1,575.00</td></tr></table>")]]
LIST_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object", "properties": {"item": {"type": "string"}, "amount": {"type": "number"}}}}}}


def test_a_list_shorter_than_its_rows_with_an_amount_is_flagged_without_an_extra_call():
    short = {"items": [{"item": "Servo", "amount": "400"}, {"item": "Board", "amount": "1100"}]}
    out, llm = run(short, ITEMS, LIST_SCHEMA, review=True)
    assert len(llm.calls) == 1
    (r,) = [r for r in out["review"] if r["flag"] == "row_not_extracted"]
    assert r["field"] == "items" and "Shipping" in r["detail"]


def test_a_complete_list_is_not_flagged_wrapped_lines_and_totals_do_not_count():
    full = {"items": [{"item": "Servo", "amount": "400"}, {"item": "Board", "amount": "1100"},
                      {"item": "Shipping", "amount": "75"}]}
    out, _ = run(full, ITEMS, LIST_SCHEMA, review=True)
    assert not [r for r in out["review"] if r["flag"] in ("row_not_extracted", "printed_but_empty")]


@pytest.mark.parametrize("content,rows", [
    ("<table><tr><th>A</th><th>B</th></tr><tr><td>x</td><td>$1.00</td></tr><tr><td>wrapped</td><td></td></tr>"
     "<tr><td>Subtotal</td><td>$1.00</td></tr></table>", 1),
    ("| Item | Amount |\n|---|---|\n| x | 1,200.00 |\n| y | 2.50 |\n| note | n/a |\n| Grand total | 1,202.50 |", 2),
])
def test_amount_rows_skip_headers_totals_and_lines_without_an_amount(content, rows):
    assert FR.amount_rows(content) == rows


def struck_doc(tmp_path):
    doc = make_doc([["Price: 4,500 4,200"]])
    page = doc.pages[0]
    words = list(page.regions[0].iter_words())
    W, H = 1000, 1000
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    for wd in words:
        x0, y0 = int(wd.bbox.left * W), int(wd.bbox.top * H)
        x1, y1 = int((wd.bbox.left + wd.bbox.width) * W), int((wd.bbox.top + wd.bbox.height) * H)
        for x in range(x0 + 2, x1 - 2, 7):
            d.rectangle([x, y0 + 3, x + 2, y1 - 3], fill=0)
        if wd.char_start < 12 and wd.text != "Price":  # "4,500" is crossed out
            d.rectangle([x0, (y0 + y1) // 2 - 1, x1, (y0 + y1) // 2], fill=0)
    path = tmp_path / "page.png"
    img.save(path)
    page.renders = {"100": str(path)}
    return doc


def test_crossed_out_text_is_shown_to_the_model_and_a_struck_value_is_flagged(tmp_path):
    doc = struck_doc(tmp_path)
    view = DocView(doc)
    assert render_pages(view.pages, marks=FR.struck_spans(view.pages)).endswith("Price: ~~4,500~~ 4,200")
    schema = {"type": "object", "properties": {"price": {"type": "number"}}}
    llm = FakeLLM([{"price": "4500"}])
    out = extract.run(doc, {"instructions": {"schema": schema}, "settings": {"review": True}}, llm=llm)
    assert "~~4,500~~" in llm.calls[0]["user"] and "crossed out" in llm.calls[0]["user"]
    assert {"field": "price", "flag": "struck_through", "detail": "every place this value is printed is crossed out"} \
        in out["review"]


@pytest.mark.parametrize("value,printed,context,flag", [
    ("2025-03-04", "03/04/2025", "", "ambiguous_date"),
    (1200, "(1,200)", "", "parentheses"),
    (450, "450", "Revenue (amounts in thousands)", "scale"),
    (0.1, "10%", "", "percent_scaled"),
])
def test_conversions_a_reader_should_check_are_flagged(value, printed, context, flag):
    assert flag in [f for f, _ in FR.format_flags(value, printed, context)]


@pytest.mark.parametrize("value,printed", [("2025-03-13", "13/03/2025"), (-1200, "(1,200)"), (10, "10%"), (450, "450")])
def test_unambiguous_conversions_are_not_flagged(value, printed):
    assert not {f for f, _ in FR.format_flags(value, printed, "")} & {"ambiguous_date", "parentheses",
                                                                       "percent_scaled", "scale"}


# ---------------------------------------------------------------- failures: honest, and never stale

BIG = {"type": "object", "properties": {
    "customer": {"type": "object", "properties": {"name": {"type": "string"}}},
    "contract": {"type": "object", "properties": {"name": {"type": "string"}, "price": {"type": "number"},
                                                  "package_size": {"type": ["number", "null"]}}}}}
TWO_PAGES = [["Order Form No. LCS-OF-7", "Customer: Acme"], ["Special terms: the price is 4,200 per month."]]


def cut(text="{"):
    return LLMResult(text=text, stop_reason="max_tokens", model="fake")


def test_a_cut_off_reply_is_split_by_fields_with_the_pages_kept_together():
    def handler(system, user, i):
        fields = list(json.loads(user.split("JSON Schema:\n", 1)[1].split("\n", 1)[0])["properties"])
        if len(fields) > 1:
            return cut()  # the whole schema is too long an answer
        assert "=== Page 1 ===" in user and "=== Page 2 ===" in user  # the special term on page 2 stays in view
        return {"customer": {"name": "Acme"}} if fields == ["customer"] else \
            {"contract": {"name": "Order Form No. LCS-OF-7", "price": "4200", "package_size": ""}}
    out, llm = run(handler, TWO_PAGES, BIG)
    assert out["result"][0] == {"customer": {"name": "Acme"},
                                "contract": {"name": "Order Form No. LCS-OF-7", "price": 4200, "package_size": None}}
    assert not [w for w in out["warnings"] if "could not be read" in w]
    assert len(llm.calls) == 3


def test_a_part_that_stays_unreadable_is_reported_but_a_field_that_does_not_apply_is_just_empty():
    def handler(system, user, i):
        fields = list(json.loads(user.split("JSON Schema:\n", 1)[1].split("\n", 1)[0])["properties"])
        if "contract" in fields:
            return cut()
        return {"customer": {"name": "Acme"}}
    out, _ = run(handler, TWO_PAGES, BIG, review=True)
    (w,) = [w for w in out["warnings"] if "could not be read" in w]
    assert "contract" in w and "customer" not in w
    assert ("contract", "unreadable") in {(r["field"], r["flag"]) for r in out["review"]}
    assert not [r for r in out["review"] if r["field"].startswith("customer")]


def test_a_failed_attempt_that_a_later_call_fills_leaves_no_warning():
    calls = {"n": 0}

    def handler(system, user, i):
        calls["n"] += 1
        fields = list(json.loads(user.split("JSON Schema:\n", 1)[1].split("\n", 1)[0])["properties"])
        if calls["n"] == 1:
            return LLMResult(text='{"customer": {"name": "Acme"}, "contract": {"na', stop_reason="end_turn",
                             model="fake")  # broken
        return {f: {"customer": {"name": "Acme"}, "contract": {"name": "X", "price": "1", "package_size": ""}}[f]
                for f in fields}
    out, _ = run(handler, TWO_PAGES, BIG)
    assert not [w for w in out["warnings"] if "could not be read" in w]
    assert out["result"][0]["contract"]["name"] == "X"


# ---------------------------------------------------------------- citations: code finds the words

def test_a_date_is_cited_on_the_words_it_is_printed_as():
    out, _ = run(ANSWER, citations={"enabled": True})
    (c,) = out["result"]["start"]["citations"]
    assert c["content"] == "03/04/2025" and c["confidence"] == "high"


def test_a_value_whose_words_have_no_box_is_placed_from_the_page_words():
    doc = make_doc([["1 Wages, tips, other compensation: $28,000"]])
    page = doc.pages[0]
    region = page.regions[0]
    for w in region.iter_words():
        if "28" in w.text or "000" in w.text:
            w.bbox = None  # the label and the amount are printed apart; the amount was not placed
    page.words = {"pdf": [Word(text="$28,000", bbox=BBox(left=0.6, top=0.12, width=0.05, height=0.01, page=0),
                               source="pdf"),
                          Word(text="$28,000", bbox=BBox(left=0.6, top=0.62, width=0.05, height=0.01, page=0),
                               source="pdf")]}
    schema = {"type": "object", "properties": {"wages": {"type": "number"}}}
    out = extract.run(doc, {"instructions": {"schema": schema}, "settings": {"citations": {"enabled": True}}},
                      llm=FakeLLM([{"wages": "28000"}]))
    (c,) = out["result"]["wages"]["citations"]
    assert abs(c["bbox"]["top"] - 0.12) < 1e-6 and abs(c["bbox"]["left"] - 0.6) < 1e-6  # the nearer one


@pytest.mark.parametrize("text,value,hit", [
    ("Due 03/31/2025.", "2025-03-31", "03/31/2025"), ("Due 31.03.25", "2025-03-31", "31.03.25"),
    ("Date 03 31 2025", "2025-03-31", "03 31 2025"), ("on March 31, 2025", "2025-03-31", "March 31, 2025"),
    ("on 31st Mar 2025", "2025-03-31", "31st Mar 2025"), ("on 2025/03/31", "2025-03-31", "2025/03/31"),
    ("on 03/30/2025", "2025-03-31", None), ("no date here 12", "2025-03-31", None),
])
def test_printed_dates_are_matched_in_any_common_form(text, value, hit):
    found = extract._find_date(text, value)
    assert [text[a:b] for a, b in found] == ([hit] if hit else [])
