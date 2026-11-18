"""The reply schema: structured outputs for a schema of any size, without making the model guess.

The user's schema is rewritten in code (every field required, "" for a value the document does not have, numbers
and yes/no as text) so the API can enforce it, and the answer is turned back into the user's types. A schema
still too large is split into groups of fields before any call.
"""

from __future__ import annotations

import json
import random

from hypothesis import given, settings
from hypothesis import strategies as st

from readanydoc.tasks import llm as L
from readanydoc.tasks import reply_schema as RS
from readanydoc.tasks import schema as S
from tests.tasks.test_extract_robust import BILLING_SCHEMA, TRUTH


def stringify(v):
    """A value as a model writes it in the reply schema."""
    if isinstance(v, dict):
        return {k: stringify(x) for k, x in v.items()}
    if isinstance(v, list):
        return [stringify(x) for x in v]
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def counts(node, out=None):
    out = out if out is not None else {"optional": 0, "unions": 0}
    if isinstance(node, dict):
        if "anyOf" in node or isinstance(node.get("type"), list):
            out["unions"] += 1
        if node.get("type") == "object":
            out["optional"] += len(node.get("properties", {})) - len(node.get("required", []))
        for k in ("properties",):
            for v in node.get(k, {}).values():
                counts(v, out)
        for b in node.get("anyOf", []):
            counts(b, out)
        if isinstance(node.get("items"), dict):
            counts(node["items"], out)
    return out


def nullable_schema(n):
    return {"type": "object", "properties": {f"f{i}": {"type": ["string", "null"]} for i in range(n)}}


def test_nullable_fields_need_neither_optional_fields_nor_unions():
    for schema in (S.resolve_refs(BILLING_SCHEMA), nullable_schema(40)):
        assert L.api_schema(schema) is None  # as the user wrote it: over the API's limits
        reply = RS.to_reply(schema)
        c = counts(reply)
        assert c["optional"] == 0
        assert c["unions"] <= RS.MAX_UNIONS  # only the user's own choices between shapes are left
        assert L.api_schema(reply) is not None


def test_numbers_dates_and_yes_no_are_text_and_empty_is_always_allowed():
    schema = {"type": "object", "properties": {
        "total": {"type": ["number", "null"], "minimum": 0}, "paid": {"type": "boolean"},
        "status": {"type": "string", "enum": ["Open", "Closed", None]}, "due": {"type": "string", "format": "date"},
        "code": {"type": "string", "pattern": "^[A-Z]{3}$"}, "kind": {"const": "invoice"}}}
    p = RS.to_reply(schema)["properties"]
    assert p["total"]["type"] == "string" and "minimum 0" in p["total"]["description"]
    assert p["paid"]["enum"] == ["true", "false", ""]
    assert p["status"]["enum"] == ["Open", "Closed", ""]
    assert "format: date" in p["due"]["description"] and "pattern: ^[A-Z]{3}$" in p["code"]["description"]
    assert p["kind"]["enum"] == ["invoice", ""]


def test_answers_come_back_in_the_users_types():
    user = S.resolve_refs(BILLING_SCHEMA)
    back = S.conform(RS.from_reply(stringify(TRUTH), user), S.enforced(user))
    assert back == TRUTH


def test_spellings_are_matched_back():
    schema = {"type": "object", "properties": {"status": {"enum": ["paid", "unpaid"]}, "ok": {"type": "boolean"},
                                               "n": {"type": "integer"}, "tags": {"type": ["array", "null"],
                                                                                  "items": {"type": "string"}}}}
    got = S.conform(RS.from_reply({"status": "PAID", "ok": "Yes", "n": "1,200", "tags": ""}, schema),
                    S.enforced(schema))
    assert got == {"status": "paid", "ok": True, "n": 1200, "tags": []}


@settings(max_examples=60, deadline=None)
@given(st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(-10**6, 10**6), st.text(max_size=8)),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.sampled_from(["a", "b", "c"]), inner, max_size=3),
    max_leaves=12))
def test_from_reply_never_crashes_and_never_invents(value):
    schema = S.resolve_refs(BILLING_SCHEMA)
    out = RS.from_reply(value, schema)
    # an empty answer stays empty: nothing is filled in that the reply did not have
    if value in ("", None, [], {}):
        assert out in (None, [], {}) or all(v in (None, [], {}) for v in (out or {}).values())


def test_a_schema_that_fits_is_one_group_a_large_one_is_split_before_any_call():
    small = RS.to_reply(S.resolve_refs(BILLING_SCHEMA))
    assert RS.plan(small) == [[("customer",), ("business_entity",), ("contract",)]]
    big = RS.to_reply({"type": "object", "properties": {
        "header": {"type": "object", "properties": {f"h{i}": {"type": "string"} for i in range(50)}},
        "parties": {"type": "object", "properties": {f"p{i}": {"type": "string"} for i in range(50)}},
        "lines": {"type": "array", "items": {"type": "object",
                                             "properties": {f"l{i}": {"type": "string"} for i in range(70)}}}}})
    groups = RS.plan(big)
    assert len(groups) > 1
    flat = [p for g in groups for p in g]
    assert sorted(flat) == sorted(set(flat))  # every field in exactly one group
    assert ("lines",) in flat  # a list is never split inside its items, even when larger than a group
    for g in groups:
        part = RS.project(big, g)
        assert L.api_schema(part) is not None
        assert RS._leaves(part) <= RS.MAX_LEAVES or g == [("lines",)]


def test_groups_merge_back_into_one_answer():
    reply = RS.to_reply({"type": "object", "properties": {
        "a": {"type": "object", "properties": {"x": {"type": "string"}, "y": {"type": "string"}}},
        "b": {"type": "string"}}})
    part1 = RS.project(reply, [("a", "x")])
    assert list(part1["properties"]) == ["a"] and list(part1["properties"]["a"]["properties"]) == ["x"]
    assert RS.merge(RS.merge({}, {"a": {"x": "1"}}), {"a": {"y": "2"}, "b": "3"}) == {"a": {"x": "1", "y": "2"},
                                                                                    "b": "3"}


def test_block_fields_are_added_below_the_root_and_taken_out_again():
    reply = RS.add_blocks(RS.to_reply(S.resolve_refs(BILLING_SCHEMA)))
    assert "_block" not in reply["properties"]
    assert "_block" in reply["properties"]["customer"]["properties"]
    item = reply["properties"]["contract"]["properties"]["line_items"]["items"]
    assert "_block" in item["properties"] and "_block" in item["required"]
    answer = {"customer": {"name": "Acme", "_block": "r3"}, "contract": {"line_items": [{"_block": "r8", "x": 1}]}}
    assert RS.take_blocks(answer) == {("customer",): "r3", ("contract", "line_items", 0): "r8"}
    assert json.dumps(answer).count("_block") == 0


def test_alternatives_are_one_list_at_the_root_only_when_asked():
    reply = RS.add_alternatives(RS.to_reply(S.resolve_refs(BILLING_SCHEMA)), ["contract.number"])
    alt = reply["properties"][RS.ALT_FIELD]
    assert alt["type"] == "array" and "contract.number" in alt["description"]
    assert L.api_schema(reply) is not None


def test_random_schemas_always_fit_after_the_rewrite_or_are_split():
    rng = random.Random(7)
    for _ in range(30):
        props = {}
        for i in range(rng.randint(5, 140)):
            t = rng.choice([["string", "null"], ["number", "null"], "integer", ["boolean", "null"], "string"])
            props[f"f{i}"] = {"type": t}
        reply = RS.to_reply({"type": "object", "properties": props})
        for g in RS.plan(reply):
            assert L.api_schema(RS.project(reply, g)) is not None


def test_a_group_the_api_will_not_compile_is_split_before_the_call(monkeypatch):
    """The API caps the compiled grammar (unpublished): a refused group is split until each part compiles, and the
    real calls all carry a format."""
    import httpx

    from readanydoc.tasks import extract
    from readanydoc.tasks.llm import AnthropicAPILLM
    from tests.tasks.conftest import make_doc

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    sent = []

    def handler(request):
        body = json.loads(request.content)
        fmt = (body.get("output_config") or {}).get("format")
        n = json.dumps(fmt["schema"]).count('"type": "string"') if fmt else 0
        if fmt and n > 6:  # "too large" for this fake API
            return httpx.Response(400, json={"type": "error", "error": {"message": "The compiled grammar is too large"}})
        if body["max_tokens"] == 1:  # the check
            return httpx.Response(200, json={"content": [], "stop_reason": "max_tokens", "usage": {}})
        sent.append(fmt is not None)
        props = fmt["schema"]["properties"] if fmt else {}
        answer = {k: ({kk: "x" for kk in v["properties"]} if v.get("type") == "object" else "x") for k, v in props.items()}
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(answer)}],
                                         "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}})

    llm = AnthropicAPILLM("haiku", client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0)
    schema = {"type": "object", "properties": {
        "a": {"type": "object", "properties": {f"a{i}": {"type": ["string", "null"]} for i in range(5)}},
        "b": {"type": "object", "properties": {f"b{i}": {"type": ["string", "null"]} for i in range(5)}}}}
    out = extract.run(make_doc([["hello"]]), {"instructions": {"schema": schema}, "settings": {}}, llm=llm)
    assert sent and all(sent)  # every real call had an enforced format
    assert out["result"][0] == {"a": {f"a{i}": "x" for i in range(5)}, "b": {f"b{i}": "x" for i in range(5)}}


def test_an_optional_object_the_document_does_not_have_comes_back_as_null():
    addr = {"type": "object", "properties": {"line1": {"type": "string"}, "city": {"type": "string"}}}
    schema = {"type": "object", "properties": {"ship_to": {"anyOf": [addr, {"type": "null"}]}, "bill_to": addr}}
    got = RS.from_reply({"ship_to": {"line1": "", "city": ""}, "bill_to": {"line1": "", "city": ""}}, schema)
    assert got["ship_to"] is None  # nullable: the user's schema says null when absent
    assert got["bill_to"] == {"line1": None, "city": None}  # required object: kept, with empty fields


def test_null_written_as_text_is_null_where_the_schema_allows_it():
    schema = {"type": "object", "properties": {"payee": {"type": ["string", "null"]}, "code": {"type": "string"}}}
    assert RS.from_reply({"payee": "null", "code": "null"}, schema) == {"payee": None, "code": "null"}
