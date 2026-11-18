"""The reply schema: structured outputs for a schema of any size, without making the model guess.

The user's schema is rewritten in code (every field required, "" for a value the document does not have, numbers
and yes/no as text) so the API can enforce it, and the answer is turned back into the user's types. A schema
still too large is split into groups of fields before any call.
"""

from __future__ import annotations

import json
import pytest
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


def test_an_optional_object_the_document_does_not_have_comes_back_as_null():
    addr = {"type": "object", "properties": {"line1": {"type": "string"}, "city": {"type": "string"}}}
    schema = {"type": "object", "properties": {"ship_to": {"anyOf": [addr, {"type": "null"}]}, "bill_to": addr}}
    got = RS.from_reply({"ship_to": {"line1": "", "city": ""}, "bill_to": {"line1": "", "city": ""}}, schema)
    assert got["ship_to"] is None  # nullable: the user's schema says null when absent
    assert got["bill_to"] == {"line1": None, "city": None}  # required object: kept, with empty fields


def test_null_written_as_text_is_null_where_the_schema_allows_it():
    schema = {"type": "object", "properties": {"payee": {"type": ["string", "null"]}, "code": {"type": "string"}}}
    assert RS.from_reply({"payee": "null", "code": "null"}, schema) == {"payee": None, "code": "null"}


def _coded_fake(monkeypatch, answer_for, refuse_user_shape=True, truncate_first=False):
    """A fake Anthropic API: refuses the user's own shape (too complex), accepts the coded format and answers with
    ``answer_for(field path) -> [(item, value)]`` for the fields listed in each call."""
    import httpx

    from readanydoc.tasks.llm import AnthropicAPILLM

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    sent = []
    state = {"cut": truncate_first}

    def handler(request):
        body = json.loads(request.content)
        fmt = (body.get("output_config") or {}).get("format")
        if fmt and refuse_user_shape and "values" not in fmt["schema"]["properties"]:
            return httpx.Response(400, json={"type": "error", "error": {"message": "Schema is too complex for compilation."}})
        if body["max_tokens"] == 1:
            return httpx.Response(200, json={"content": [], "stop_reason": "max_tokens", "usage": {}})
        sent.append(body)
        text = body["messages"][0]["content"][-1]["text"]
        rows = [l.split(" | ") for l in text.splitlines() if l.startswith("f") and l.count(" | ") >= 2]
        if state["cut"] and len(rows) > 1:
            state["cut"] = False
            return httpx.Response(200, json={"content": [{"type": "text", "text": '{"values": [{"code": "f1"'}],
                                             "stop_reason": "max_tokens", "usage": {"input_tokens": 1, "output_tokens": 1}})
        values = [{"code": code, "item": item, "value": v} for code, path, _ in rows for item, v in answer_for(path)]
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps({"values": values})}],
                                         "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}})

    return AnthropicAPILLM("haiku", client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0), sent


BIG = {"type": "object", "properties": {
    "seller": {"type": "object", "properties": {"name": {"type": "string"}, "country": {"type": ["string", "null"]}}},
    "total": {"type": ["number", "null"]},
    "paid": {"type": "boolean"},
    "lines": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "amount": {"type": "number"},
        "tiers": {"type": "array", "items": {"type": "object", "properties": {"up_to": {"type": ["number", "null"]}}}}}}}}}

ANSWERS = {"seller.name": [("", "Acme Ltd")], "seller.country": [("", "US")], "total": [("", "4,200.50")],
           "paid": [("", "yes")], "lines[].name": [("0", "Seats"), ("1", "Support")],
           "lines[].amount": [("0", "4000"), ("1", "200.50")], "lines[].tiers[].up_to": [("0.0", "10"), ("0.1", "")]}


def test_a_refused_schema_comes_back_in_the_users_exact_shape_from_coded_answers(monkeypatch):
    from readanydoc.tasks import extract
    from tests.tasks.conftest import make_doc

    llm, sent = _coded_fake(monkeypatch, lambda p: ANSWERS.get(p, []))
    out = extract.run(make_doc([["hello"]]), {"instructions": {"schema": BIG}, "settings": {}}, llm=llm)
    assert out["result"][0] == {"seller": {"name": "Acme Ltd", "country": "US"}, "total": 4200.5, "paid": True,
                                "lines": [{"name": "Seats", "amount": 4000, "tiers": [{"up_to": 10}]},
                                          {"name": "Support", "amount": 200.5, "tiers": []}]}
    assert len(sent) == 1 and all("values" in b["output_config"]["format"]["schema"]["properties"] for b in sent)


def test_coded_groups_share_one_cached_document_and_one_format(monkeypatch):
    """Every coded call has the same system prompt, the same cached document block and the same format, so the
    groups read the document from the cache; the first call goes alone and writes it."""
    from readanydoc.tasks import extract
    from tests.tasks.conftest import make_doc

    llm, sent = _coded_fake(monkeypatch, lambda p: ANSWERS.get(p, []))
    out = extract.run(make_doc([["hello world"]]), {"instructions": {"schema": BIG}, "settings": {}}, llm=llm,
                      options={"coded_group_leaves": 2})
    assert len(sent) > 2
    first = sent[0]["messages"][0]["content"][0]
    assert first["cache_control"] == {"type": "ephemeral"} and "hello world" in first["text"]
    assert all(b["messages"][0]["content"][0] == first for b in sent)
    assert all(b["system"] == sent[0]["system"] for b in sent)
    assert all(b["output_config"] == sent[0]["output_config"] for b in sent)
    assert "<<readanydoc:cache>>" not in json.dumps(sent)
    assert out["result"][0]["lines"][1]["amount"] == 200.5  # a list's fields stay in one group


def test_a_coded_reply_cut_off_is_asked_again_in_halves(monkeypatch):
    from readanydoc.tasks import extract
    from tests.tasks.conftest import make_doc

    llm, sent = _coded_fake(monkeypatch, lambda p: ANSWERS.get(p, []), truncate_first=True)
    out = extract.run(make_doc([["hello"]]), {"instructions": {"schema": BIG}, "settings": {}}, llm=llm)
    assert len(sent) == 3  # cut off, then two halves
    assert out["result"][0]["seller"]["name"] == "Acme Ltd" and len(out["result"][0]["lines"]) == 2


def test_an_either_or_object_is_asked_field_by_field_and_matched_back(monkeypatch):
    """"fixed {strategy, value} or metered {strategy}" is listed as its fields; the answer lands in the right form."""
    from readanydoc.tasks import extract
    from tests.tasks.conftest import make_doc

    schema = {"type": "object", "properties": {"source": {"oneOf": [
        {"type": "object", "properties": {"strategy": {"const": "fixed"}, "value": {"type": "number"}},
         "required": ["strategy", "value"]},
        {"type": "object", "properties": {"strategy": {"const": "metered"}}, "required": ["strategy"]}]},
        "total": {"type": ["number", "null"]}}}
    answers = {"source.strategy": [("", "fixed")], "source.value": [("", "40")], "total": [("", "12")]}
    llm, sent = _coded_fake(monkeypatch, lambda p: answers.get(p, []))
    out = extract.run(make_doc([["hello"]]), {"instructions": {"schema": schema}, "settings": {}}, llm=llm)
    assert out["result"][0]["source"] == {"strategy": "fixed", "value": 40}


def test_a_less_complete_copy_of_a_list_item_is_dropped_and_distinct_items_are_kept():
    from readanydoc.tasks.extract import drop_partial_copies
    rows = [{"period": "Q2", "value": 2353.6, "scale": None},
            {"period": "Q2", "value": 2353.6, "scale": 1000000},
            {"period": "Q1", "value": 2100.0, "scale": None},          # no fuller copy: kept as written
            {"period": "Q2", "value": 99.0, "scale": None}]            # a different value: kept
    out, n = drop_partial_copies({"goodwill": rows})
    assert n == 1 and out["goodwill"] == rows[1:]
    assert drop_partial_copies({"x": [{"a": 1, "b": None}, {"a": 1, "b": 2}]})[1] == 0  # one field: never merged


def test_page_parts_are_sized_by_the_models_limits_and_can_be_set(monkeypatch):
    from readanydoc import model_limits as ML
    from readanydoc.tasks import extract
    from readanydoc.tasks.common import DocView
    from readanydoc.tasks.llm import AnthropicAPILLM, FakeLLM
    from tests.tasks.conftest import make_doc

    budget, source, limits = extract.part_budget_for(AnthropicAPILLM("haiku"), {})
    assert limits["price_jump"] == 100_000 and budget == extract.DEFAULT_PART_TOKENS and "allows up to" in source
    small = FakeLLM()
    small.context_limits = {"max_input": 20_000, "max_output": 4_000}
    assert extract.part_budget_for(small, {})[0] < 20_000                # a small window gets its own largest
    assert extract.part_budget_for(AnthropicAPILLM("haiku"), {"part_tokens": 5000})[0] == 5000
    pages = DocView(make_doc([["x" * 30_000]] * 10)).pages          # ~10K tokens a page (3 characters a token)
    assert len(extract._token_parts(pages, 200_000)) == 1             # fits: one part, every field asked once
    parts = extract._token_parts(pages, 35_000)
    assert len(parts) > 1 and parts[0][-1].number == parts[1][0].number  # neighbouring parts share a page
    assert {p.number for part in parts for p in part} == set(range(1, 11))

    class Unknown(FakeLLM):
        context_limits = None
        model = "my-private-model"
    with pytest.raises(extract.TaskError) as e:
        extract.part_budget_for(Unknown(), {})
    assert "part_tokens" in str(e.value) and ML.ENV in str(e.value)  # names the setting, never guesses
    assert extract.part_budget_for(Unknown(), {"part_tokens": 9000})[0] == 9000


def test_the_part_size_can_be_set_in_the_request_settings(monkeypatch):
    """settings.part_tokens (and extract(part_tokens=...)) sets the page-part size; it is never sent to the API."""
    from readanydoc.tasks import extract
    from tests.tasks.conftest import make_doc

    seen = []
    real = extract._token_parts
    monkeypatch.setattr(extract, "_token_parts", lambda pages, budget, overlap=1, per_page=0: seen.append(budget) or real(pages, budget, overlap, per_page))
    llm, sent = _coded_fake(monkeypatch, lambda p: ANSWERS.get(p, []))
    extract.run(make_doc([["hello"]]), {"instructions": {"schema": BIG}, "settings": {"part_tokens": 30000}}, llm=llm)
    assert seen == [30000] and all("part_tokens" not in json.dumps(b) for b in sent)
    with pytest.raises(extract.TaskError):
        extract.run(make_doc([["hello"]]), {"instructions": {"schema": BIG}, "settings": {"part_tokens": "big"}}, llm=llm)


CITE_SCHEMA = {"type": "object", "properties": {"revenue": {"type": "array", "items": {"type": "object", "properties": {
    "value": {"type": "number"}, "period": {"type": "string"},
    "segment_type": {"type": "string", "enum": ["company", "segment"]}, "repurchased": {"type": "number"}}}}}}


def _cite_run(reply_for):
    import re as _re
    from readanydoc.tasks import FakeLLM, extract
    from tests.tasks.conftest import make_doc

    doc = make_doc([[
        "Indicate whether the registrant is a smaller reporting company.",
        "Revenue grew to 5,048.4 this quarter.",
        ("Table", "<table><tr><th>(In millions)</th><th>Three Months Ended December 31, 2024</th></tr>"
                  "<tr><td>Revenue</td><td>5,048.4</td></tr></table>"),
        "Shares 2 — 1 were cancelled.",
    ]])

    def handler(system, user, i):
        table = _re.search(r"\[([^|\]]+)\|Table\]", user).group(1)
        return json.dumps(reply_for(table))
    out = extract.run(doc, {"instructions": {"schema": CITE_SCHEMA}, "settings": {"citations": {"enabled": True}}},
                      llm=FakeLLM(handler))
    return out["result"]["revenue"][0]


def test_citations_follow_the_models_block_and_named_words_and_skip_chosen_categories():
    row = _cite_run(lambda t: {"revenue": [{"value": "5048.4", "period": "FY2025 Q2", "segment_type": "company",
                                            "repurchased": "2.1", "_block": t,
                                            "_from": "period: Three Months Ended December 31, 2024"}]})
    v = row["value"]["citations"][0]
    assert v["content"] == "5,048.4" and v["parentBlock"]["type"] == "Table"          # the row's table, not the text
    assert "Three Months Ended December 31, 2024" in row["period"]["citations"][0]["content"]  # a worked-out value
    assert row["segment_type"]["citations"] == []                  # "company" on the cover page is not its source
    rep = row["repurchased"]["citations"]                          # 2.1 is not the digits of "2 — 1": not found,
    assert all("2 — 1" not in c["content"] and c["confidence"] == "low" for c in rep)  # only its row's block, low


def test_marks_follow_their_objects_when_parts_are_merged_and_items_move():
    from readanydoc.tasks import extract
    part1 = ({"rows": [{"name": "Seats", "amount": 4000.0}]}, {("rows", 0): {"block": "r3", "from": {}}})
    part2 = ({"rows": [{"name": "Support", "amount": 200.5}]},
             {("rows", 0): {"block": "r9", "from": {"amount": "two hundred"}}})
    final = {"rows": [{"name": "Support", "amount": 200.5}, {"name": "Seats", "amount": 4000.0, "qty": 2}]}
    marks = extract.remap_marks(final, [part1, part2])
    assert marks[("rows", 0)] == {"block": "r9", "from": {"amount": "two hundred"}}
    assert marks[("rows", 1)]["block"] == "r3"            # a fuller merged copy keeps the marks of its part



def test_one_stage_sends_the_pdf_as_a_cached_document_block():
    from readanydoc.tasks.llm import CACHE_MARK, AnthropicAPILLM, pdf_mark
    user = "Document:\n" + pdf_mark(b"%PDF-1.4 fake") + CACHE_MARK + "JSON Schema: {}"
    body = AnthropicAPILLM("haiku").build_request("sys", user, None, None, 1000)
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "document" and content[0]["source"]["media_type"] == "application/pdf"
    assert content[1]["cache_control"] == {"type": "ephemeral"} and "readanydoc:pdf" not in json.dumps(content)


def test_one_stage_builds_placed_words_for_digital_pdfs_scans_and_images(tmp_path):
    """No model call: a digital PDF gives its own words, a scan or an image gives local OCR words, and the model is
    sent the PDF or the page images."""
    pytest.importorskip("pypdfium2")
    from readanydoc.pipeline.grounding import ocr
    from readanydoc.textlayer import one_stage_document
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    pdf = tmp_path / "f.pdf"
    c = canvas.Canvas(str(pdf))
    c.setFont("Helvetica", 18)
    for i, line in enumerate(["Order Form", "Platform Standard USD 1,200.00", "Customer Brightpath Logistics LLC"]):
        c.drawString(72, 720 - 40 * i, line)
    c.save()
    doc, direct = one_stage_document(str(pdf))
    text = " ".join(r.content for p in doc.pages for r in p.regions)
    assert direct == {"pdf": str(pdf)} and "1,200.00" in text and doc.pages[0].kind == "digital"
    assert all(w.bbox is not None for p in doc.pages for r in p.regions for ln in r.lines for w in ln.words)
    if not ocr.available():
        return
    import pypdfium2 as pdfium
    png = tmp_path / "f.png"
    pdfium.PdfDocument(str(pdf))[0].render(scale=3).to_pil().convert("RGB").save(png)
    doc, direct = one_stage_document(str(png))
    assert list(direct) == ["images"] and "1,200.00" in " ".join(r.content for p in doc.pages for r in p.regions)
