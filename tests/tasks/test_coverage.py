"""Coverage flags: printed values the output did not take (code only, generic rules)."""
from readanydoc.tasks import coverage as C

SCHEMA = {"type": "object", "properties": {
    "customer": {"type": "object", "properties": {
        "name": {"type": "string"}, "email": {"type": ["string", "null"]},
        "contacts": {"type": "array", "items": {"type": "object", "properties": {
            "first_name": {"type": "string"}, "last_name": {"type": "string"}}}}}},
    "invoice_date": {"type": ["string", "null"]},
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "description": {"type": "string"}, "amount": {"type": "number"}}}}}}


def page(*regions):
    return [{"number": 1, "regions": [{"label": f"r{i}", "type": t, "content": c} for i, (t, c) in enumerate(regions)]}]


TABLE = ("<table><tr><th>Description</th><th>Qty</th><th>Amount</th></tr>"
         "<tr><td>Widget large</td><td>2</td><td>40.00</td></tr>"
         "<tr><td>Gadget small</td><td>1</td><td>15.50</td></tr>"
         "<tr><td>Spanner set</td><td>3</td><td>27.25</td></tr>"
         "<tr><td colspan='2'>Total</td><td>82.75</td></tr></table>")
PAGES = page(("Key Value", "Customer: Acme Ltd"), ("Key Value", "Billing Contact: Dana Ruiz, Controller"),
             ("Key Value", "Email: ap@acme.test"), ("Key Value", "Date: 03/02/2026"), ("Table", TABLE),
             ("Footer", "Contact: footer@acme.test"))
FULL = {"customer": {"name": "Acme Ltd", "email": "ap@acme.test",
                     "contacts": [{"first_name": "Dana", "last_name": "Ruiz"}]},
        "invoice_date": "2026-02-03",
        "items": [{"description": "Widget large", "amount": 40.0}, {"description": "Gadget small", "amount": 15.5},
                  {"description": "Spanner set", "amount": 27.25}]}


def test_complete_output_has_no_flags():
    assert C.check(FULL, SCHEMA, PAGES) == []


def test_dropped_contact_is_flagged_on_its_field():
    v = {**FULL, "customer": {**FULL["customer"], "contacts": []}}
    f = C.check(v, SCHEMA, PAGES)
    assert [x["field"] for x in f] == ["customer.contacts"] and "Dana Ruiz" in f[0]["detail"]


def test_shared_email_does_not_hide_a_dropped_contact():
    pages = page(("Key Value", "Billing Contact: Dana Ruiz"), ("Key Value", "Billing Email: ap@acme.test"))
    v = {"customer": {"name": "Acme", "email": "ap@acme.test", "contacts": []}}
    assert [x["field"] for x in C.check(v, SCHEMA, pages)] == ["customer.contacts"]


def test_value_shape_matches_an_empty_field():
    v = {**FULL, "customer": {**FULL["customer"], "email": None}}
    assert [x["field"] for x in C.check(v, SCHEMA, PAGES)] == ["customer.email"]
    v = {**FULL, "invoice_date": None}
    assert [x["field"] for x in C.check(v, SCHEMA, PAGES)] == ["invoice_date"]


def test_missing_rows_and_emptied_list():
    v = {**FULL, "items": FULL["items"][:1] + FULL["items"][2:]}
    f = C.check(v, SCHEMA, PAGES)
    assert [x["flag"] for x in f] == ["row_not_extracted"] and "Gadget small" in f[0]["detail"]
    f = C.check({**FULL, "items": []}, SCHEMA, PAGES)
    assert [(x["flag"], x["field"]) for x in f] == [("printed_but_empty", "items")]


def test_form_drawn_as_a_table_is_read_as_pairs():
    t = ("<table><tr><th>Label</th><th>Value</th></tr><tr><td>Customer</td><td>Acme Ltd</td></tr>"
         "<tr><td>Billing Contact</td><td>Dana Ruiz</td></tr></table>")
    assert [p.kind for p in C.pieces(page(("Table", t)))] == ["pair", "pair"]
    v = {"customer": {"name": "Acme Ltd", "contacts": []}}
    assert [x["field"] for x in C.check(v, SCHEMA, page(("Table", t)))] == ["customer.contacts"]


def test_unrelated_printed_values_are_not_flagged():
    pages = page(("Key Value", "PO Number: 4471"), ("Key Value", "Signed by: Lee Park"))
    assert C.check({"customer": {"name": "Acme", "contacts": [{"first_name": "Dana"}]}}, SCHEMA, pages) == []


def test_stem_and_dates():
    assert [C._stem(w) for w in ("lines", "parties", "taxes", "contacts", "address")] == \
        ["line", "party", "tax", "contact", "address"]
    from datetime import date
    assert date(2026, 2, 3) in C._dates("03/02/2026") and date(2026, 3, 13) in C._dates("2026/03/13")


def test_statement_rows_taken_by_different_fields_are_not_flagged():
    """A financial statement read metric by metric: each field takes one row; the rows nobody asked for are fine."""
    from readanydoc.tasks import coverage
    schema = {"type": "object", "properties": {
        m: {"type": "array", "items": {"type": "object", "properties": {"value": {"type": "string"}, "period": {"type": "string"}}}}
        for m in ("cash", "goodwill", "inventories")}}
    html = ("<table><tr><th>Item</th><th>2023</th><th>2022</th></tr><tr><td>Cash</td><td>1,111</td><td>1,010</td></tr>"
            "<tr><td>Goodwill</td><td>2,222</td><td>2,020</td></tr><tr><td>Inventories</td><td>3,333</td><td>3,030</td></tr>"
            "<tr><td>Prepaid expenses</td><td>4,444</td><td>4,040</td></tr></table>")
    value = {"cash": [{"value": "1,111", "period": "2023"}], "goodwill": [{"value": "2,222", "period": "2023"}],
             "inventories": [{"value": "3,333", "period": "2023"}]}
    pages = [{"number": 1, "regions": [{"type": "Table", "content": html, "label": "r1"}]}]
    assert not [f for f in coverage.check(value, schema, pages) if f["flag"] == "row_not_extracted"]
