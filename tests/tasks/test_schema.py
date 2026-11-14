"""Schema enforcement: Reducto's documented guarantees, plus property-style tests on random schemas."""

import random

import pytest
from jsonschema import Draft202012Validator

from readanydoc.tasks import TaskError
from readanydoc.tasks import schema as S


def strict(schema):
    S.check_schema(schema)
    return S.enforced(S.resolve_refs(schema))


def enforce(value, schema):
    st = strict(schema)
    out, unresolved = S.null_out(S.conform(value, st), st)
    return out, unresolved, st


# ---------------------------------------------------------------- invalid schemas -> 400

@pytest.mark.parametrize("bad", [
    {"type": "strng"},
    {"type": "object", "properties": 5},
    {"type": "object", "properties": {"a": {"type": "string", "minLength": -1}}},
    {"type": "array", "items": {"enum": "not-a-list"}},
    {"type": "string", "pattern": "(unclosed"},
    "not an object",
    {"type": "object", "properties": {"a": {"$ref": "https://example.com/other.json"}}},
])
def test_invalid_schema_is_a_400(bad):
    with pytest.raises(TaskError) as e:
        S.check_schema(bad)
        S.resolve_refs(bad)
    assert e.value.status_code == 400
    assert "schema" in e.value.detail.lower()
    assert e.value.to_response() == {"detail": e.value.detail}


def test_unresolvable_local_ref_is_a_400():
    with pytest.raises(TaskError) as e:
        S.resolve_refs({"type": "object", "properties": {"a": {"$ref": "#/$defs/missing"}}})
    assert e.value.status_code == 400


# ---------------------------------------------------------------- guarantees

def test_every_declared_field_present_and_null_when_missing():
    schema = {"type": "object", "properties": {
        "name": {"type": "string"}, "total": {"type": "number"},
        "address": {"type": "object", "properties": {"city": {"type": "string"}, "zip": {"type": "string"}}},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "sku": {"type": "string"}, "qty": {"type": "integer"}}}}}}
    out, unresolved, st = enforce({"name": "Acme", "address": {"city": "Leeds"}, "items": [{"sku": "A1"}, {}]}, schema)
    assert out == {"name": "Acme", "total": None, "address": {"city": "Leeds", "zip": None},
                   "items": [{"sku": "A1", "qty": None}, {"sku": None, "qty": None}]}
    assert not unresolved
    Draft202012Validator(st).validate(out)


def test_objects_are_closed():
    schema = {"type": "object", "properties": {"a": {"type": "string"},
                                               "b": {"type": "object", "properties": {"c": {"type": "integer"}}}}}
    out, _, st = enforce({"a": "x", "extra": 1, "b": {"c": 2, "also_extra": True}}, schema)
    assert out == {"a": "x", "b": {"c": 2}}
    assert st["additionalProperties"] is False
    assert S.find_errors({"a": "x", "b": None, "zzz": 1}, st) == [(("zzz",), "field is not declared in the schema")]


def test_enum_pattern_min_max_are_enforced_by_nulling():
    schema = {"type": "object", "properties": {
        "status": {"type": "string", "enum": ["paid", "unpaid"]},
        "code": {"type": "string", "pattern": "^[A-Z]{3}-\\d{4}$"},
        "qty": {"type": "integer", "minimum": 1, "maximum": 10},
        "ratio": {"type": "number", "exclusiveMinimum": 0},
        "note": {"type": "string", "maxLength": 5}}}
    out, unresolved, st = enforce({"status": "PAID!", "code": "inv2041", "qty": 99, "ratio": 0, "note": "too long"}, schema)
    assert out == {"status": None, "code": None, "qty": None, "ratio": None, "note": None}
    assert not unresolved
    good = {"status": "paid", "code": "INV-2041", "qty": 3, "ratio": 0.5, "note": "ok"}
    assert enforce(good, schema)[0] == good


def test_items_enforced_bad_items_dropped_and_maxitems_cut():
    schema = {"type": "object", "properties": {
        "tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b", "c"]}, "maxItems": 3},
        "rows": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}}}}}}
    out, unresolved, st = enforce({"tags": ["a", "zzz", "b", "c", "a"], "rows": [{"n": 1}, {"n": -5}, "junk"]}, schema)
    assert out["tags"] == ["a", "b", "c"]
    assert out["rows"] == [{"n": 1}, {"n": None}]
    assert not unresolved
    Draft202012Validator(st).validate(out)


def test_minitems_cannot_be_repaired_and_is_reported():
    schema = {"type": "object", "properties": {"xs": {"type": "array", "items": {"type": "integer"}, "minItems": 2}}}
    out, unresolved, _ = enforce({"xs": [1]}, schema)
    assert out == {"xs": [1]}
    assert unresolved and "minItems" in unresolved[0][1]


def test_local_refs_are_resolved_including_recursion():
    schema = {"type": "object",
              "properties": {"buyer": {"$ref": "#/$defs/party"}, "seller": {"$ref": "#/$defs/party"},
                             "tree": {"$ref": "#/$defs/node"}},
              "$defs": {"party": {"type": "object", "properties": {"name": {"type": "string"},
                                                                   "country": {"enum": ["IN", "DE"]}}},
                        "node": {"type": "object", "properties": {"label": {"type": "string"},
                                                                  "child": {"$ref": "#/$defs/node"}}}}}
    out, _, st = enforce({"buyer": {"name": "A", "country": "FR"}, "tree": {"label": "x", "child": {"label": "y"}}}, schema)
    assert out["buyer"] == {"name": "A", "country": None}
    assert out["seller"] is None
    assert out["tree"]["child"]["label"] == "y"
    assert "$ref" not in str(st)


def test_anyof_oneof():
    schema = {"type": "object", "properties": {
        "id": {"anyOf": [{"type": "integer"}, {"type": "string", "pattern": "^X\\d+$"}]},
        "amount": {"oneOf": [{"type": "number"}, {"type": "null"}]}}}
    assert enforce({"id": 7, "amount": 1.5}, schema)[0] == {"id": 7, "amount": 1.5}
    assert enforce({"id": "X12", "amount": None}, schema)[0] == {"id": "X12", "amount": None}
    assert enforce({"id": "nope", "amount": "abc"}, schema)[0] == {"id": None, "amount": None}


def test_unambiguous_scalar_spellings_are_converted_not_guessed():
    schema = {"type": "object", "properties": {"total": {"type": "number"}, "qty": {"type": "integer"},
                                               "paid": {"type": "boolean"}, "ref": {"type": "string"},
                                               "neg": {"type": "number"}}}
    out, _, _ = enforce({"total": "$1,575.00", "qty": "4", "paid": "yes", "ref": 2041, "neg": "(12.50)"}, schema)
    assert out == {"total": 1575.0, "qty": 4, "paid": True, "ref": "2041", "neg": -12.5}
    out, _, _ = enforce({"total": "about a thousand", "qty": "4.5", "paid": "maybe"}, schema)
    assert out["total"] is None and out["qty"] is None and out["paid"] is None


def test_best_effort_keywords_are_guidance_only():
    schema = {"type": "object", "properties": {
        "email": {"type": "string", "format": "email"}, "kind": {"const": "invoice"},
        "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}}}
    value = {"email": "not-an-email", "kind": "receipt", "tags": ["a", "a"]}
    out, unresolved, _ = enforce(value, schema)
    assert out == value and not unresolved


def test_count_leaf_fields():
    schema = {"type": "object", "properties": {
        "a": {"type": "string"}, "b": {"type": "object", "properties": {"c": {"type": "number"}, "d": {"type": "string"}}},
        "e": {"type": "array", "items": {"type": "object", "properties": {"f": {"type": "string"}, "g": {"type": "integer"}}}},
        "h": {"type": "array", "items": {"type": "string"}}}}
    assert S.count_leaf_fields(S.resolve_refs(schema)) == 6


def test_unwrap_collects_sources_and_tolerates_plain_leaves():
    schema = strict({"type": "object", "properties": {
        "a": {"type": "string"}, "xs": {"type": "array", "items": {"type": "number"}},
        "o": {"type": "object", "properties": {"b": {"type": "integer"}}}}})
    value, sources = S.unwrap({"a": {"v": "x", "r": ["r1"]}, "xs": [{"v": 1, "r": ["r2"]}, 2],
                               "o": {"b": {"v": 3, "r": "r4"}}}, schema)
    assert value == {"a": "x", "xs": [1, 2], "o": {"b": 3}}
    assert sources == {("a",): ["r1"], ("xs", 0): ["r2"], ("o", "b"): ["r4"]}


# ---------------------------------------------------------------- property-style

def random_schema(rng: random.Random, depth: int = 0) -> dict:
    kinds = ["string", "enum", "pattern", "integer", "number", "boolean"]
    if depth < 3:
        kinds += ["object", "object", "array", "anyOf"]
    k = rng.choice(kinds)
    if k == "string":
        s = {"type": "string"}
        if rng.random() < 0.3:
            s["maxLength"] = rng.randint(1, 6)
        return s
    if k == "enum":
        return {"type": "string", "enum": rng.sample(["red", "green", "blue", "n/a", "PAID"], rng.randint(1, 4))}
    if k == "pattern":
        return {"type": "string", "pattern": rng.choice(["^\\d{4}-\\d{2}-\\d{2}$", "^[A-Z]{2,3}$", "^INV-"])}
    if k in ("integer", "number"):
        s = {"type": k}
        if rng.random() < 0.5:
            s["minimum"] = rng.randint(-5, 5)
        if rng.random() < 0.5:
            s["maximum"] = rng.randint(5, 50)
        return s
    if k == "boolean":
        return {"type": "boolean"}
    if k == "array":
        s = {"type": "array", "items": random_schema(rng, depth + 1)}
        if rng.random() < 0.3:
            s["maxItems"] = rng.randint(1, 3)
        return s
    if k == "anyOf":
        return {"anyOf": [random_schema(rng, depth + 2), random_schema(rng, depth + 2)]}
    props = {f"f{i}": random_schema(rng, depth + 1) for i in range(rng.randint(1, 4))}
    s = {"type": "object", "properties": props}
    if rng.random() < 0.5:
        s["required"] = rng.sample(list(props), rng.randint(0, len(props)))
    return s


def random_value(rng: random.Random, schema: dict, depth: int = 0):
    """A plausible but unreliable model answer: mostly on-shape, with every kind of mistake mixed in."""
    r = rng.random()
    if r < 0.12:
        return rng.choice([None, "", "garbage", 123456, -7.25, True, [], {}, ["x", 1], {"zz": 1}])
    if "anyOf" in schema:
        return random_value(rng, rng.choice(schema["anyOf"]), depth)
    t = schema.get("type")
    if t == "object":
        out = {}
        for name, sub in schema["properties"].items():
            if rng.random() < 0.8:
                out[name] = random_value(rng, sub, depth + 1)
        if rng.random() < 0.3:
            out["undeclared_" + str(rng.randint(0, 9))] = "x"
        return out
    if t == "array":
        return [random_value(rng, schema["items"], depth + 1) for _ in range(rng.randint(0, 5))]
    if t == "string":
        if "enum" in schema:
            return rng.choice(schema["enum"] + ["other", "RED"])
        return rng.choice(["2024-03-01", "AB", "INV-77", "hello world", "x", "12", ""])
    if t in ("integer", "number"):
        return rng.choice([0, 3, -9, 49, 1000, 2.5, "7", "1,200.50", "n/a"])
    return rng.choice([True, False, "yes", "no", "unknown"])


@pytest.mark.parametrize("seed", range(150))
def test_property_result_always_satisfies_the_strict_schema(seed):
    rng = random.Random(seed)
    schema = {"type": "object", "properties": {f"p{i}": random_schema(rng) for i in range(rng.randint(1, 5))}}
    st = strict(schema)
    for _ in range(4):
        raw = random_value(rng, schema)
        out, unresolved = S.null_out(S.conform(raw, st), st)
        # only two things cannot be repaired: too few items, and an answer that is not an object at all
        assert all("minItems" in m or path == () for path, m in unresolved)
        # the top level is always the declared object with every field present
        assert isinstance(out, dict) and set(out) == set(schema["properties"])
        Draft202012Validator(st).validate(out)
        # idempotent: enforcing an already valid result changes nothing
        assert S.null_out(S.conform(out, st), st)[0] == out


@pytest.mark.parametrize("seed", range(40))
def test_property_valid_values_are_never_altered(seed):
    """Never make it worse: a value that already satisfies the strict schema comes back unchanged."""
    rng = random.Random(1000 + seed)
    schema = {"type": "object", "properties": {f"p{i}": random_schema(rng) for i in range(3)}}
    st = strict(schema)
    kept = 0
    for _ in range(30):
        raw = random_value(rng, schema)
        if isinstance(raw, dict) and not S.find_errors(raw, st):
            assert S.null_out(S.conform(raw, st), st)[0] == raw
            kept += 1
    assert kept >= 0
