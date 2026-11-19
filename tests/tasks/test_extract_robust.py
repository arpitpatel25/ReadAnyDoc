"""Extract on deep schemas with broken model replies: never a silent empty answer, never a wrong value.

Background (a user report, 16 Nov 2026): a 22 KB billing-contract schema with 20 nested types came back as
{"customer": null, "business_entity": null, "contract": null} with "not found in the document" warnings.
Haiku had read everything correctly but wrote one closing bracket too many (inside a list) in the old
{"v", "r"}-per-value citation format; the JSON reader kept the longest piece that parsed (the customer
block) and used it as the whole answer.

These tests mimic the mistakes a model makes in long JSON (an extra or missing bracket, a trailing comma,
smart quotes, a cut-off reply, a refusal, prose around the JSON, an answer of the wrong shape) and check
one rule for every outcome: each value returned is the correct one, and a value that is missing because
the reply could not be read is reported as such, or the request fails with a clear error.
"""

from __future__ import annotations

import copy
import json
import random

import httpx
import pytest

from readanydoc.tasks import FakeLLM, TaskError, extract
from readanydoc.tasks import llm as L
from readanydoc.tasks import schema as S
from readanydoc.tasks.llm import LLMResult
from tests.tasks.conftest import make_doc

# ---------------------------------------------------------------- a large billing contract schema

S_STR = {"type": "string"}
S_NUM = {"type": "number"}
S_INT = {"type": "integer"}


def obj(props: dict, required: list | None = None) -> dict:
    out = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


DEFS = {
    "address": obj({"line1": S_STR, "city": S_STR, "country": S_STR, "postal_code": S_STR}),
    "contact": obj({"name": S_STR, "email": {"type": "string", "format": "email"}, "role": S_STR}),
    "tax_id": obj({"scheme": S_STR, "value": S_STR}),
    "customer": obj({"name": S_STR, "address": {"$ref": "#/$defs/address"},
                     "billing_contact": {"$ref": "#/$defs/contact"}, "tax_id": {"$ref": "#/$defs/tax_id"}},
                    ["name"]),
    "business_entity": obj({"name": S_STR, "address": {"$ref": "#/$defs/address"}}, ["name"]),
    "payment_terms": obj({"net_days": S_INT, "method": {"type": "string", "enum": ["ach", "card", "wire"]}}),
    "renewal": obj({"auto_renew": {"type": "boolean"}, "notice_days": S_INT}),
    "terms": obj({"start_date": {"type": "string", "format": "date"}, "end_date": {"type": "string", "format": "date"},
                  "currency": S_STR, "payment": {"$ref": "#/$defs/payment_terms"},
                  "renewal": {"$ref": "#/$defs/renewal"}}),
    "tier": obj({"up_to": {"type": ["integer", "null"]}, "unit_price": S_NUM}),
    "flat_price": obj({"type": {"const": "flat"}, "amount": S_NUM}, ["type", "amount"]),
    "tiered_price": obj({"type": {"const": "tiered"}, "tiers": {"type": "array", "items": {"$ref": "#/$defs/tier"}}},
                        ["type", "tiers"]),
    "price": {"anyOf": [{"$ref": "#/$defs/flat_price"}, {"$ref": "#/$defs/tiered_price"}]},
    "price_period": obj({"start": {"type": "string", "format": "date"}, "price": {"$ref": "#/$defs/price"}}),
    "price_schedule": obj({"periods": {"type": "array", "items": {"$ref": "#/$defs/price_period"}}}),
    "billing_schedule": obj({"cadence": {"type": "string", "enum": ["recurring", "on_demand", "one_time"]},
                             "period": S_STR, "in_advance": {"type": "boolean"}}),
    "quantity_period": obj({"start": {"type": "string", "format": "date"}, "quantity": S_NUM}),
    "quantity_schedule": obj({"periods": {"type": "array", "items": {"$ref": "#/$defs/quantity_period"}}}),
    "discount": obj({"percent": S_NUM, "description": S_STR}),
    "product": obj({"sku": S_STR, "name": S_STR}),
    "line_item": obj({"product": {"$ref": "#/$defs/product"}, "description": S_STR,
                      "price_schedule": {"$ref": "#/$defs/price_schedule"},
                      "billing_schedule": {"$ref": "#/$defs/billing_schedule"},
                      "quantity_schedule": {"$ref": "#/$defs/quantity_schedule"},
                      "discount": {"$ref": "#/$defs/discount"}}, ["product"]),
    "contract": obj({"number": S_STR, "terms": {"$ref": "#/$defs/terms"},
                     "line_items": {"type": "array", "items": {"$ref": "#/$defs/line_item"}}}),
}
BILLING_SCHEMA = obj({"customer": {"$ref": "#/$defs/customer"}, "business_entity": {"$ref": "#/$defs/business_entity"},
                      "contract": {"$ref": "#/$defs/contract"}})
BILLING_SCHEMA["$defs"] = DEFS
assert len(DEFS) >= 20

BLOCKS = [
    ("Title", "Order Form OF-2026-118"),                                                    # r1
    "Provider: Ledgerline Inc, 500 Howard St, San Francisco, US, 94105",                       # r2
    "Customer: Acme Robotics Ltd, 12 Harbour Road, Leeds, GB, LS1 4AB",                     # r3
    "Billing contact: Priya Shah, ap@acme-robotics.com, Accounts Payable",                  # r4
    "VAT number: GB 123 4567 89",                                                           # r5
    "Term: 2026-01-01 to 2026-12-31, currency USD, payment net 30 by ach",                   # r6
    "Renewal: auto renews, 60 days notice",                                                 # r7
    ("Table", "<table><tr><th>SKU</th><th>Product</th><th>Price</th><th>Billing</th><th>Qty</th></tr>"
              "<tr><td>PLT-1</td><td>Platform fee</td><td>4500</td><td>recurring P1M in advance</td><td>1</td></tr>"
              "<tr><td>SEAT</td><td>Seats</td><td>20 up to 100, then 15</td><td>recurring P1M</td><td>40</td></tr>"
              "</table>"),                                                                  # r8
    "Special terms: platform fee discount 10 percent for year one",                         # r9
]

TRUTH = {
    "customer": {"name": "Acme Robotics Ltd",
                 "address": {"line1": "12 Harbour Road", "city": "Leeds", "country": "GB", "postal_code": "LS1 4AB"},
                 "billing_contact": {"name": "Priya Shah", "email": "ap@acme-robotics.com", "role": "Accounts Payable"},
                 "tax_id": {"scheme": "VAT", "value": "GB 123 4567 89"}},
    "business_entity": {"name": "Ledgerline Inc",
                        "address": {"line1": "500 Howard St", "city": "San Francisco", "country": "US",
                                    "postal_code": "94105"}},
    "contract": {
        "number": "OF-2026-118",
        "terms": {"start_date": "2026-01-01", "end_date": "2026-12-31", "currency": "USD",
                  "payment": {"net_days": 30, "method": "ach"}, "renewal": {"auto_renew": True, "notice_days": 60}},
        "line_items": [
            {"product": {"sku": "PLT-1", "name": "Platform fee"}, "description": "Platform fee",
             "price_schedule": {"periods": [{"start": "2026-01-01", "price": {"type": "flat", "amount": 4500}}]},
             "billing_schedule": {"cadence": "recurring", "period": "P1M", "in_advance": True},
             "quantity_schedule": {"periods": [{"start": "2026-01-01", "quantity": 1}]},
             "discount": {"percent": 10, "description": "discount 10 percent for year one"}},
            {"product": {"sku": "SEAT", "name": "Seats"}, "description": "Seats",
             "price_schedule": {"periods": [{"start": "2026-01-01", "price": {"type": "tiered", "tiers": [
                 {"up_to": 100, "unit_price": 20}, {"up_to": None, "unit_price": 15}]}}]},
             "billing_schedule": {"cadence": "recurring", "period": "P1M", "in_advance": None},
             "quantity_schedule": {"periods": [{"start": "2026-01-01", "quantity": 40}]},
             "discount": None},
        ],
    },
}
SOURCE_OF = {"customer.name": "r3", "customer.address": "r3", "customer.billing_contact": "r4", "customer.tax_id": "r5",
             "business_entity": "r2", "contract.number": "r1", "contract.terms.renewal": "r7", "contract.terms": "r6",
             "contract.line_items": "r8", "contract.line_items[0].discount": "r9"}


def billing_doc():
    return make_doc([BLOCKS])


def cfg(schema=BILLING_SCHEMA, **settings):
    return {"instructions": {"schema": schema}, "settings": settings}


def leaves(value, path=()):
    """(path, value) for every non-null scalar in ``value``."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from leaves(v, path + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from leaves(v, path + (i,))
    elif value is not None:
        yield path, value


def dotted(path) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else p)
    return out


def source_for(path) -> str:
    d = dotted(path)
    best = max((k for k in SOURCE_OF if d == k or d.startswith(k + ".") or d.startswith(k + "[")), key=len)
    return SOURCE_OF[best]


def envelope(value=TRUTH) -> dict:
    return {"data": value, "sources": [{"path": dotted(p), "block": source_for(p)} for p, _ in leaves(value)]}


def wrapped(value=TRUTH, path=()):
    """The old reply format: every leaf as {"v": value, "r": [label]}."""
    if isinstance(value, dict):
        return {k: wrapped(v, path + (k,)) for k, v in value.items()}
    if isinstance(value, list):
        return [wrapped(v, path + (i,)) for i, v in enumerate(value)]
    return {"v": value, "r": [source_for(path)] if value is not None else []}


def compact(x) -> str:
    return json.dumps(x, separators=(",", ":"))


def plain_result(out):
    """The extracted value whether citations were on (value/citations leaves) or off ([value])."""
    res = out["result"]
    if isinstance(res, list):
        return res[0]

    def strip(v):
        if isinstance(v, dict) and set(v) == {"value", "citations"}:
            return v["value"]
        if isinstance(v, dict):
            return {k: strip(x) for k, x in v.items()}
        if isinstance(v, list):
            return [strip(x) for x in v]
        return v
    return strip(res)


def get(value, path):
    for p in path:
        if isinstance(p, int):
            if not isinstance(value, list) or p >= len(value):
                return None
            value = value[p]
        else:
            if not isinstance(value, dict):
                return None
            value = value.get(p)
    return value


# ---------------------------------------------------------------- Haiku-like mistakes in long JSON

def _structural(text: str) -> list[int]:
    return [i for i, c in L._scan_brackets(text)]


def _after_array_item(text: str, rng: random.Random) -> int:
    """A position right after an object that is an item of a list (where the reported extra "}" was)."""
    stack, spots = [], []
    for i, c in L._scan_brackets(text):
        if c in "{[":
            stack.append(c)
        else:
            stack.pop()
            if c == "}" and stack and stack[-1] == "[":
                spots.append(i + 1)
    return rng.choice(spots) if spots else -1


def mutate(text: str, kind: str, rng: random.Random):
    """One mistake of the given kind. Returns reply text, or an LLMResult for a cut-off reply or a refusal."""
    pos = _structural(text)
    if kind == "extra_closer_in_list":
        i = _after_array_item(text, rng)
        if i < 0:  # no list in this reply (one group of fields): a "]" inside an object, the same kind of mistake
            stack, spots = [], []
            for j, c in L._scan_brackets(text):
                if c in "{[":
                    stack.append(c)
                else:
                    stack.pop()
                    if c == "}" and stack and stack[-1] == "{":
                        spots.append(j + 1)
            i = rng.choice(spots)
            return text[:i] + "]" + text[i:]
        return text[:i] + "}" + text[i:]
    if kind == "extra_closer_anywhere":
        i = rng.choice(pos) + 1
        return text[:i] + rng.choice("}]") + text[i:]
    if kind == "missing_closer":
        closers = [i for i in pos if text[i] in "}]"]
        i = rng.choice(closers)
        return text[:i] + text[i + 1:]
    if kind == "trailing_comma":
        closers = [i for i in pos if text[i] in "}]" and i > 0 and text[i - 1] not in "{["]
        i = rng.choice(closers)
        return text[:i] + "," + text[i:]
    if kind == "missing_comma":
        commas = [i for i, c in enumerate(text) if c == "," and text[i + 1:i + 2] == '"']
        i = rng.choice(commas)
        return text[:i] + text[i + 1:]
    if kind == "smart_quotes":
        quotes = [i for i, c in enumerate(text) if c == '"']
        i = rng.choice(quotes)
        return text[:i] + rng.choice("“”") + text[i + 1:]
    if kind == "raw_newline_in_string":
        i = text.index('":"') + 4  # inside the first string value
        return text[:i] + "\n" + text[i:]
    if kind == "comment":
        i = rng.choice([i for i, c in enumerate(text) if c == ","]) + 1
        return text[:i] + " // from the table\n" + text[i:]
    if kind == "prose_around":
        return "Here is the extracted JSON:\n" + text + "\n\nLet me know if you need anything else."
    if kind == "fenced":
        return "```json\n" + text + "\n```"
    if kind == "truncated":
        cut = rng.randint(len(text) // 4, len(text) - 2)
        return LLMResult(text=text[:cut], data=L._try_json(text[:cut]), stop_reason="max_tokens", model="fake")
    if kind == "refusal":
        return LLMResult(text="", data=None, stop_reason="refusal", model="fake")
    if kind == "fragment_only":  # valid JSON, but only the customer block
        return compact(TRUTH["customer"])
    if kind == "prose_only":
        return "I could not produce the JSON for this document."
    raise ValueError(kind)


MISTAKES = ["extra_closer_in_list", "extra_closer_anywhere", "missing_closer", "trailing_comma", "missing_comma",
            "smart_quotes", "raw_newline_in_string", "comment", "prose_around", "fenced", "truncated", "refusal",
            "fragment_only", "prose_only"]


def check_invariants(doc_truth: dict, run):
    """The rule every outcome must follow. ``run`` returns the extract output or raises TaskError."""
    try:
        out = run()
    except TaskError as e:
        assert e.status_code == 502 and ("no usable answer" in e.detail or "could not be read" in e.detail)
        return "error"
    value = plain_result(out)
    for path, v in leaves(value):  # never a wrong value
        assert get(doc_truth, path) == v, f"{dotted(path)}: got {v!r}, truth {get(doc_truth, path)!r}"
    unread = [w for w in out["warnings"] if "could not be read" in w]
    for field in doc_truth:
        if value.get(field) in (None, {}, []) or not list(leaves(value.get(field))):
            # an empty field must be explained as unreadable, never as "not found in the document"
            assert any(field in w for w in unread), f"{field} is empty without an 'unreadable' warning"
            assert not any(f"'{field}" in w and "not found" in w for w in out["warnings"])
    complete = all(get(value, p) == v for p, v in leaves(doc_truth))
    return "complete" if complete else "partial"


# ---------------------------------------------------------------- the reported case, exactly

def test_reported_case_old_format_extra_bracket_in_a_list_is_read_in_full():
    text = compact(wrapped())
    broken = mutate(text, "extra_closer_in_list", random.Random(0))
    with pytest.raises(json.JSONDecodeError):
        json.loads(broken)

    def handler(system, user, i):  # with citations the schema is asked in groups; each reply has the extra "}"
        fields = list(json.loads(user.split("JSON Schema:\n", 1)[1].split("\n", 1)[0])["properties"])
        part = {f: wrapped()[f] for f in fields if f in TRUTH}
        return mutate(compact(part), "extra_closer_in_list", random.Random(0)) if "contract" in part else compact(part)

    # before the fix, the reader kept the customer block and returned all nulls
    llm = FakeLLM(handler)
    out = extract.run(billing_doc(), cfg(citations={"enabled": True}), llm=llm)
    assert plain_result(out) == TRUTH
    assert len(llm.calls) == 2  # one call per group: repaired, no extra model call
    assert not [w for w in out["warnings"] if "not found" in w]


def test_reader_never_returns_a_fragment_of_broken_json():
    text = compact(TRUTH)
    i = text.index('"business_entity"') - 1  # an extra "}" that closes the root early: ambiguous, not repaired
    broken = text[:i] + "}" + text[i:]
    with pytest.raises(ValueError):
        L.parse_json(broken)
    with pytest.raises(ValueError):
        L.parse_json(text[: len(text) // 2])  # cut off
    assert L.parse_json("noise {not json} then " + text) == TRUTH  # prose with braces around complete JSON is fine


@pytest.mark.parametrize("bad", [
    '[{"a":1}},{"a":2}]',          # extra } in a list
    '{"x":[{"a":1}}],"y":2}',
    '{"x":[1,2]],"y":2}',          # extra ] in an object
])
def test_wrong_kind_closer_is_repaired_only_on_request(bad):
    good = bad.replace("}}", "}", 1) if "}}" in bad else bad.replace("]]", "]", 1)
    assert L.repair_brackets(bad) == json.loads(good)
    with pytest.raises(ValueError):  # the plain reader never repairs: a repair must be checked against a schema
        L.parse_json(bad)


def test_a_repair_that_would_reshape_the_answer_is_not_used():
    # "[[], null]]" may be an extra "]" or a missing "[": the repaired shape breaks the schema, so ask again
    schema = obj({"rows": {"type": "array", "items": {"type": "array", "items": S_STR}}, "note": S_STR})
    doc = make_doc([["rows: a b", "note: hi"]])
    llm = FakeLLM(['{"rows": [["a"], "b"]], "note": "hi"}', {"rows": [["a"], ["b"]], "note": "hi"}])
    out = extract.run(doc, cfg(schema), llm=llm)
    assert out["result"] == [{"rows": [["a"], ["b"]], "note": "hi"}] and len(llm.calls) == 2


@pytest.mark.parametrize("bad", ['{"a":{"b":1}},"c":2}', '{"a":[1,2},"b":3}', '{"a":1,}', '{"a":1',
                                 '{"a":{"b":1},"b":2,"b":3}',   # a key twice: a value landed in the wrong object
                                 '[{"x":{"d":1}},"d":2}}]'])
def test_ambiguous_mistakes_are_not_guessed(bad):
    with pytest.raises(ValueError):
        L.parse_json(bad)


# ---------------------------------------------------------------- new reply format: data + flat sources

def test_citations_ask_only_for_the_fields_plus_one_block_label_per_object():
    doc = billing_doc()
    llm = FakeLLM([envelope()])  # an older-format reply is still read
    out = extract.run(doc, cfg(citations={"enabled": True}), llm=llm)
    assert plain_result(out) == TRUTH
    sent = [c["json_schema"] for c in llm.calls]
    props = {k: v for s_ in sent for k, v in s_["properties"].items()}
    assert set(props) == {"customer", "business_entity", "contract"}  # the user's fields only (in groups)
    assert "_block" in props["customer"]["properties"]  # one label per object, not per value
    assert not any("_block" in s_["properties"] for s_ in sent)  # (the root has none)
    assert not any('"v"' in c["user"] for c in llm.calls) and '"sources"' not in json.dumps(sent)
    name = out["result"]["customer"]["name"]["citations"][0]
    assert name["confidence"] == "high" and name["content"] == "Acme Robotics Ltd"
    seats = out["result"]["contract"]["line_items"][1]["quantity_schedule"]["periods"][0]["quantity"]
    assert seats["value"] == 40 and seats["citations"][0]["type"] == "Table"


def test_a_source_given_for_an_object_applies_to_its_values():
    reply = {"data": TRUTH, "sources": [{"path": "customer", "block": "r3"}, {"path": "contract.line_items", "block": "r8"}]}
    out = extract.run(billing_doc(), cfg(citations={"enabled": True}), llm=FakeLLM([reply]))
    city = out["result"]["customer"]["address"]["city"]["citations"][0]
    assert city["confidence"] == "high" and city["content"] == "Leeds"
    sku = out["result"]["contract"]["line_items"][0]["product"]["sku"]["citations"][0]
    assert sku["type"] == "Table" and sku["content"] == "PLT-1"


def test_missing_or_bad_sources_still_find_unique_values_and_never_invent():
    reply = {"data": TRUTH, "sources": [{"path": "customer.name", "block": "r99"}, "junk", {"path": "", "block": "r3"}]}
    out = extract.run(billing_doc(), cfg(citations={"enabled": True}), llm=FakeLLM([reply]))
    assert plain_result(out) == TRUTH
    vat = out["result"]["customer"]["tax_id"]["value"]["citations"]
    assert vat and vat[0]["content"] == "GB 123 4567 89"  # found by searching the blocks
    scheme = out["result"]["customer"]["tax_id"]["scheme"]
    assert scheme["value"] == "VAT" and all(c["confidence"] != "high" or "VAT" in c["content"]
                                            for c in scheme["citations"])


def test_old_wrapped_format_and_plain_replies_are_still_read():
    for reply in (wrapped(), TRUTH, [envelope()]):
        out = extract.run(billing_doc(), cfg(citations={"enabled": True}), llm=FakeLLM([reply]))
        assert plain_result(out) == TRUTH


def test_a_user_schema_with_its_own_data_field_is_not_mistaken_for_the_envelope():
    schema = obj({"data": obj({"x": S_STR}), "note": S_STR})
    doc = make_doc([["x is 5", "note: hi"]])
    out = extract.run(doc, cfg(schema), llm=FakeLLM([{"data": {"x": "5"}, "note": "hi"}]))
    assert out["result"] == [{"data": {"x": "5"}, "note": "hi"}]
    out = extract.run(doc, cfg(schema, citations={"enabled": True}),
                      llm=FakeLLM([{"data": {"data": {"x": "5"}, "note": "hi"}, "sources": []}]))
    assert plain_result(out) == {"data": {"x": "5"}, "note": "hi"}


# ---------------------------------------------------------------- fields written one level off

def test_a_field_closed_out_of_its_object_is_put_back_with_its_sources():
    # seen live from Haiku 5.5 on this schema: "line_items" written next to "contract" instead of inside it
    data = copy.deepcopy(TRUTH)
    items = data["contract"].pop("line_items")
    data["line_items"] = items
    reply = {"data": data, "sources": [{"path": e["path"].replace("contract.line_items", "line_items"),
                                        "block": e["block"]} for e in envelope()["sources"]]}
    out = extract.run(billing_doc(), cfg(citations={"enabled": True}), llm=FakeLLM([reply]))
    assert plain_result(out) == TRUTH and out["warnings"] == []
    seats = out["result"]["contract"]["line_items"][1]["product"]["name"]["citations"][0]
    assert seats["type"] == "Table" and seats["confidence"] == "high"


def test_a_field_opened_inside_a_sibling_is_put_back():
    data = copy.deepcopy(TRUTH)
    data["customer"]["business_entity"] = data.pop("business_entity")
    out = extract.run(billing_doc(), cfg(), llm=FakeLLM([data]))
    assert plain_result(out) == TRUTH


def test_a_misplaced_field_with_two_possible_homes_is_reported_not_guessed():
    data = copy.deepcopy(TRUTH)
    data["address"] = data["customer"].pop("address")  # customer.address or business_entity.address?
    data["business_entity"]["address"] = None
    out = extract.run(billing_doc(), cfg(), llm=FakeLLM([data]))
    value = plain_result(out)
    assert value["customer"]["address"] is None and value["business_entity"]["address"] is None
    assert any("address" in w and "left out" in w for w in out["warnings"])


def test_an_extra_field_the_schema_never_mentions_is_dropped_quietly():
    data = copy.deepcopy(TRUTH)
    data["customer"]["favourite_colour"] = "blue"
    out = extract.run(billing_doc(), cfg(), llm=FakeLLM([data]))
    assert plain_result(out) == TRUTH and out["warnings"] == []


# ---------------------------------------------------------------- recovery: retry, then split the fields

def test_a_reply_of_the_wrong_shape_is_asked_for_again_never_used():
    llm = FakeLLM([compact(TRUTH["customer"]), envelope()])
    out = extract.run(billing_doc(), cfg(), llm=llm)
    assert plain_result(out) == TRUTH
    assert len(llm.calls) == 2 and "top-level keys" in llm.calls[1]["user"]


def test_a_reply_that_keeps_breaking_is_split_into_smaller_requests():
    def handler(system, user, i):
        schema = json.loads(user.split("JSON Schema:\n", 1)[1].split("\n", 1)[0])
        fields = list(schema["properties"])
        if len(fields) == 3:  # the full request: always broken, like a long reply
            return mutate(compact(envelope()), "missing_closer", random.Random(i))
        return envelope({f: TRUTH[f] for f in fields})

    llm = FakeLLM(handler)
    out = extract.run(billing_doc(), cfg(), llm=llm)
    assert plain_result(out) == TRUTH
    assert len(llm.calls) == 4  # 2 tries on the whole schema, then one call per half


def test_a_half_that_cannot_be_read_is_reported_as_unreadable_not_as_missing():
    def handler(system, user, i):
        schema = json.loads(user.split("JSON Schema:\n", 1)[1].split("\n", 1)[0])
        fields = list(schema["properties"])
        if "contract" in fields:
            return '{"data": {"contract": {"number": "OF-2026-118",, "sources": []}'  # broken every time
        return envelope({f: TRUTH[f] for f in fields})

    out = extract.run(billing_doc(), cfg(citations={"enabled": True}), llm=FakeLLM(handler))
    value = plain_result(out)
    assert value["customer"] == TRUTH["customer"] and value["contract"] is None
    assert any("could not be read" in w and "contract" in w for w in out["warnings"])


def test_required_field_in_an_unreadable_part_is_not_called_missing():
    schema = copy.deepcopy(BILLING_SCHEMA)
    schema["required"] = ["customer", "contract"]

    def handler(system, user, i):
        fields = list(json.loads(user.split("JSON Schema:\n", 1)[1].split("\n", 1)[0])["properties"])
        if "contract" in fields:
            return '{"contract": {"number": "x",}'
        return {f: TRUTH[f] for f in fields}

    out = extract.run(billing_doc(), cfg(schema), llm=FakeLLM(handler))
    assert not any("'contract' was not found" in w for w in out["warnings"])
    assert any("could not be read" in w for w in out["warnings"])


@pytest.mark.parametrize("reply", ["I cannot help with that.", LLMResult(text="", stop_reason="refusal", model="f")])
def test_no_readable_reply_at_all_is_an_error(reply):
    llm = FakeLLM([reply])
    with pytest.raises(TaskError) as e:
        extract.run(billing_doc(), cfg(), llm=llm)
    assert e.value.status_code == 502
    assert len(llm.calls) <= 3  # no long chain of calls for a reply with no JSON in it


# ---------------------------------------------------------------- property test: every Haiku-like mistake

@pytest.mark.parametrize("fmt", ["envelope", "wrapped", "plain"])
@pytest.mark.parametrize("recovers", [True, False])
def test_every_mistake_gives_correct_values_or_an_honest_report(fmt, recovers):
    """For 14 kinds of mistakes x 3 reply formats x (model recovers on the next try / keeps failing), with
    random positions: no outcome is ever a wrong value or an empty field passed off as "not found"."""
    good = {"envelope": envelope(), "wrapped": wrapped(), "plain": TRUTH}[fmt]
    settings = {} if fmt == "plain" else {"citations": {"enabled": True}}
    outcomes = {}
    for seed in range(10):
        for kind in MISTAKES:
            rng = random.Random(f"{fmt}-{recovers}-{seed}-{kind}")

            def handler(system, user, i, rng=rng, kind=kind):
                schema = json.loads(user.split("JSON Schema:\n", 1)[1].split("\n", 1)[0])
                fields = list(schema["properties"])
                want = good if len(fields) == 3 else (
                    {"data": {f: TRUTH[f] for f in fields}, "sources": []} if fmt == "envelope"
                    else {f: (wrapped()[f] if fmt == "wrapped" else TRUTH[f]) for f in fields})
                if i == 0 or not recovers:
                    return mutate(compact(want), kind, rng)
                return want

            res = check_invariants(TRUTH, lambda h=handler: extract.run(billing_doc(), cfg(**settings), llm=FakeLLM(h)))
            outcomes.setdefault(res, []).append(kind)
    if recovers:
        assert set(outcomes) == {"complete"}, outcomes.get("error", []) + outcomes.get("partial", [])
    else:  # repairs and harmless mistakes are still read in full; the rest fail loudly or report what is missing
        assert {"prose_around", "fenced", "extra_closer_in_list"} <= set(outcomes.get("complete", []))


# ---------------------------------------------------------------- structured outputs: when the API can enforce JSON

def test_large_schema_is_sent_without_a_format_small_one_with():
    big = L.api_schema(extract.envelope_schema(S.resolve_refs(BILLING_SCHEMA)))
    assert big is None  # over the API's published limits (optional fields / union types)
    small = obj({"invoice_number": S_STR, "total": {"type": "number", "minimum": 0},
                 "lines": {"type": "array", "items": obj({"item": S_STR, "amount": S_NUM})}})
    fmt = L.api_schema(extract.envelope_schema(small))
    assert fmt is not None and fmt["required"] == ["data", "sources"]
    data = fmt["properties"]["data"]
    assert data["additionalProperties"] is False and data["required"] == []  # nothing forced: no invented values
    assert "minimum" not in data["properties"]["total"]
    assert data["properties"]["lines"]["items"]["additionalProperties"] is False


@pytest.mark.parametrize("schema", [
    {"type": "object", "properties": {}},                                     # a free-form object
    {"type": "object", "properties": {"a": {"not": {"type": "string"}}}},
    {"type": "object", "properties": {"a": {"type": "array"}}},               # array without items
    obj({f"f{i}": S_STR for i in range(25)}),                                 # 25 optional fields
    obj({f"f{i}": {"type": ["string", "null"]} for i in range(17)}, [f"f{i}" for i in range(17)]),  # 17 unions
])
def test_schemas_the_api_cannot_enforce_are_sent_without_a_format(schema):
    assert L.api_schema(schema) is None


def test_a_schema_the_api_refuses_is_asked_in_the_coded_format_with_enforcement(monkeypatch):
    """The API refuses the whole reply schema: the answer comes in the fixed coded format, still enforced, and is
    rebuilt into the user's exact object."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    small = obj({"customer": obj({"name": S_STR}), "number": S_STR})
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        fmt = (body.get("output_config") or {}).get("format")
        if fmt and "values" not in fmt["schema"]["properties"]:  # this API refuses the user's own shape
            return httpx.Response(400, json={"error": {"message": "Schema is too complex for compilation."}})
        if body["max_tokens"] == 1:
            return httpx.Response(200, json={"stop_reason": "max_tokens", "content": [], "usage": {}})
        codes = {c: None for c in fmt["schema"]["properties"]["values"]["items"]["properties"]["code"]["enum"]}
        text = body["messages"][0]["content"][-1]["text"]
        line = {l.split(" | ")[1]: l.split(" | ")[0] for l in text.splitlines() if l.startswith("f") and " | " in l}
        reply = {"values": [{"code": line["customer.name"], "item": "", "value": "Acme Robotics Ltd"},
                            {"code": line["customer._block"], "item": "", "value": "r3"},
                            {"code": line["number"], "item": "", "value": "OF-2026-118"},
                            {"code": "f999", "item": "", "value": "invented"}]}
        assert set(line.values()) <= set(codes)
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(reply)}],
                                         "usage": {"input_tokens": 10, "output_tokens": 10}})

    llm = L.AnthropicAPILLM(client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = extract.run(billing_doc(), cfg(small, citations={"enabled": True}), llm=llm)
    assert out["result"]["customer"]["name"]["value"] == "Acme Robotics Ltd"
    assert out["result"]["number"]["value"] == "OF-2026-118"
    checks = [b for b in bodies if b["max_tokens"] == 1]
    calls = [b for b in bodies if b["max_tokens"] > 1]
    assert len(checks) == 2  # the whole schema (refused), then the coded format (accepted)
    assert len(calls) == 1 and "values" in calls[0]["output_config"]["format"]["schema"]["properties"]


def test_a_schema_the_api_accepts_is_asked_in_one_enforced_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    small = obj({"customer": obj({"name": S_STR}), "number": S_STR})
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if body["max_tokens"] == 1:
            return httpx.Response(200, json={"stop_reason": "max_tokens", "content": [], "usage": {}})
        reply = {"customer": {"name": "Acme Robotics Ltd", "_block": "r3"}, "number": "OF-2026-118"}
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(reply)}],
                                         "usage": {"input_tokens": 10, "output_tokens": 10}})

    llm = L.AnthropicAPILLM(client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = extract.run(billing_doc(), cfg(small, citations={"enabled": True}), llm=llm)
    assert out["result"]["number"]["value"] == "OF-2026-118"
    calls = [b for b in bodies if b["max_tokens"] > 1]
    assert len(calls) == 1 and set(calls[0]["output_config"]["format"]["schema"]["properties"]) == {"customer", "number"}
    assert calls[0]["max_tokens"] == 128000  # the model's own output allowance (public limits): never cut short


# ---------------------------------------------------------------- property test of the JSON reader (hypothesis)

from hypothesis import given, settings as h_settings, strategies as st  # noqa: E402

_scalars = st.one_of(st.none(), st.booleans(), st.integers(-10**6, 10**6), st.text(max_size=8))
_json = st.recursive(_scalars, lambda kids: st.one_of(st.lists(kids, max_size=4),
                                                      st.dictionaries(st.text(min_size=1, max_size=6), kids,
                                                                      min_size=1, max_size=4)), max_leaves=25)


@h_settings(max_examples=400, deadline=None)
@given(value=st.one_of(st.lists(_json, min_size=1, max_size=4),
                       st.dictionaries(st.text(min_size=1, max_size=6), _json, min_size=1, max_size=4)),
       data=st.data())
def test_property_the_reader_returns_the_whole_answer_or_nothing(value, data):
    """One bracket too many, one missing, or a stray comma anywhere in any JSON answer: the reader returns
    exactly the original answer (a safe repair) or raises; never a fragment and never a reshaped answer."""
    text = json.dumps(value, ensure_ascii=False)
    brackets = [i for i, _ in L._scan_brackets(text)]
    i = data.draw(st.sampled_from(brackets))
    kind = data.draw(st.sampled_from(["insert", "delete", "comma"]))
    if kind == "insert":
        bad = text[:i + 1] + data.draw(st.sampled_from("}]")) + text[i + 1:]
    elif kind == "delete":
        bad = text[:i] + text[i + 1:]
    else:
        bad = text[:i] + "," + text[i:]
    if bad == text:
        return
    try:
        got = L.parse_json(bad)
    except ValueError:
        return
    assert got == value, (bad, got)
