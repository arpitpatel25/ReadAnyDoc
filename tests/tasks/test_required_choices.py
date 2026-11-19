"""Required yes/no and enum fields come back in the user's type; review and alternatives follow the field."""
from readanydoc.tasks import reply_schema as RS
from readanydoc.tasks import extract as E
from readanydoc.tasks import field_report as FR

TERMS = {"type": "object", "required": ["mode", "days", "last_day"], "properties": {
    "mode": {"type": "string", "enum": ["relative", "absolute"]},
    "days": {"type": "integer"},
    "last_day": {"type": "boolean"}}}
SCHEMA = {"type": "object", "required": ["flag", "terms"], "properties": {
    "flag": {"type": "boolean"},
    "note": {"type": "boolean"},
    "maybe": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
    "terms": {"anyOf": [TERMS, {"type": "null"}]},
    "items": {"type": "array", "items": {"type": "object", "required": ["kind"], "properties": {
        "kind": {"type": "string", "enum": ["fixed", "percent"]}, "value": {"type": "number"}}}}}}


def test_required_choices_have_no_empty_option():
    r = RS.to_reply(SCHEMA)["properties"]
    assert r["flag"]["enum"] == ["true", "false"]
    assert "" in r["note"]["enum"] and "" in r["maybe"]["enum"]       # optional or nullable: may stay empty
    t = r["terms"]["properties"]
    assert t["last_day"]["enum"] == ["true", "false"] and t["mode"]["enum"] == ["relative", "absolute"]
    assert r["items"]["items"]["properties"]["kind"]["enum"] == ["fixed", "percent"]


def test_absent_optional_object_and_items_are_not_made_up():
    got = RS.from_reply({"flag": "false", "terms": {"mode": "relative", "days": "", "last_day": "false"},
                         "items": [{"kind": "fixed", "value": ""}, {"kind": "percent", "value": "10"}]}, SCHEMA)
    assert got["flag"] is False and got["terms"] is None
    assert [i["kind"] for i in got["items"]] == ["percent"]
    kept = RS.from_reply({"terms": {"mode": "relative", "days": "30", "last_day": "false"}}, SCHEMA)["terms"]
    assert kept["last_day"] is False


def test_alternative_values_take_the_field_type():
    leaf = E._schema_at(SCHEMA, "items[0].value")
    assert leaf == {"type": "number"} and E._as_field("4,500.00", leaf) == 4500
    assert E._schema_at(SCHEMA, "terms.last_day") == {"type": "boolean"}
    assert E._schema_at(SCHEMA, "nope.x") is None and E._as_field("x", None) == "x"


def test_html_entity_is_not_a_word():
    assert [t.norm for t in E._words("<td>Harbor &amp; Pine</td>")] == ["harbor", "pine"]


def test_total_row_without_item_name_is_not_an_item():
    t = ("<table><tr><td>Product</td><td>Amount</td></tr><tr><td>A</td><td>USD 10.00</td></tr>"
         "<tr><td>B</td><td>USD 20.00</td></tr><tr><td></td><td>USD 30.00</td></tr></table>")
    assert FR.amount_rows(t) == 2
    assert FR._category({"enum": ["a"]}) and FR._category({"anyOf": [{"enum": ["a"]}, {"type": "null"}]})
    assert not FR._category({"type": "string"})
