"""Round 2 extract additions: relevance step, example outputs, required-field warnings, page requests."""

import pytest

from readanydoc.tasks import FakeLLM, TaskError, extract
from readanydoc.tasks import extract_extras as X
from readanydoc.tasks.common import DocView
from tests.tasks.conftest import make_doc

SCHEMA = {"type": "object", "required": ["supplier", "total", "governing_law"], "properties": {
    "supplier": {"type": "string", "description": "Name of the supplier"},
    "total": {"type": "number"},
    "governing_law": {"type": "string"},
    "items": {"type": "array", "items": {"type": "object", "required": ["name", "price"], "properties": {
        "name": {"type": "string"}, "price": {"type": "number"}}}}}}


def long_doc():
    return make_doc([
        [("Title", "# Supply Agreement"), "This agreement is made with Borealis Metals AB as the supplier."],
        ["Recital text about the parties, nothing to extract here."],
        [("Section Header", "## Schedule of prices"),
         ("Table", "<table><tr><th>Item</th><th>Price</th></tr><tr><td>Copper rod</td><td>120.50</td></tr>"
                   "<tr><td>Zinc plate</td><td>80.00</td></tr></table>"), "Total payable: 200.50"],
        ["Boilerplate about notices."],
        ["Boilerplate about assignment."],
        [("Section Header", "## 14 Governing law"), "This agreement is governed by the laws of Sweden."]])


def cfg(schema=SCHEMA, examples=None, **settings):
    ins = {"schema": schema}
    if examples is not None:
        ins["examples"] = examples
    return {"instructions": ins, "settings": settings}


ANSWER = {"supplier": "Borealis Metals AB", "total": 200.5, "governing_law": "Sweden",
          "items": [{"name": "Copper rod", "price": 120.5}, {"name": "Zinc plate", "price": 80.0}]}


# ---------------------------------------------------------------- warnings
def test_warnings_name_required_fields_that_were_not_found():
    got = dict(ANSWER, governing_law=None, items=[{"name": "Copper rod", "price": None}, {"name": "Zinc plate", "price": 80.0}])
    out = extract.run(long_doc(), cfg(), llm=FakeLLM([got]))
    assert out["warnings"] == ["required field 'governing_law' was not found in the document",
                               "required field 'items[].price' was not found in 1 of 2 items"]
    assert extract.run(long_doc(), cfg(), llm=FakeLLM([ANSWER]))["warnings"] == []
    assert "extract_pages" not in out["usage"] and out["usage"]["num_pages"] == 6      # nothing else changes


def test_required_warnings_on_nested_objects_and_optional_fields():
    schema = {"type": "object", "required": ["a"], "properties": {
        "a": {"type": "object", "required": ["b"], "properties": {"b": {"type": "string"}, "c": {"type": "string"}}},
        "d": {"type": "string"}}}
    assert X.required_warnings({"a": {"b": None, "c": None}, "d": None}, schema) == \
        ["required field 'a' was not found in the document"]
    assert X.required_warnings({"a": {"b": "", "c": "x"}, "d": None}, schema) == \
        ["required field 'a.b' was not found in the document"]
    assert X.required_warnings({"a": {"b": "x"}, "d": None}, schema) == []


# ---------------------------------------------------------------- relevance
def test_page_digest_shows_headings_table_header_and_first_lines():
    page = DocView(long_doc()).pages[2]
    d = X.page_digest(page)
    assert "headings: Schedule of prices" in d and "[table, 3 rows] Item | Price / Copper rod | 120.50" in d
    assert "Total payable" in d
    assert "supplier (string): Name of the supplier" in X.schema_fields(SCHEMA) and "items[].price (number)" in X.schema_fields(SCHEMA)


def test_relevance_filter_extracts_from_the_chosen_pages_only():
    def handler(system, user, i):
        if i == 0:
            assert "=== Page 6 ===" in user and "- governing_law (string)" in user and "<table" not in user
            return {"pages": [1, 3, 6], "why": "parties, prices, law"}
        assert "Boilerplate" not in user and "Borealis" in user and "laws of Sweden" in user
        return ANSWER
    llm = FakeLLM(handler)
    out = extract.run(long_doc(), cfg(relevance_filter=True), llm=llm)
    assert out["result"] == [ANSWER] and len(llm.calls) == 2 and out["warnings"] == []
    rel = out["usage"]["extract_pages"]["relevance"]
    assert rel["pages_used"] == [1, 3, 6] and rel["pages_total"] == 6 and rel["filtered"]
    assert out["usage"]["num_pages"] == 6


def test_an_optional_field_left_empty_by_the_filter_gets_the_second_look_too():
    schema = {"type": "object", "properties": {"supplier": {"type": "string"}, "governing_law": {"type": "string"},
                                               "fax": {"type": "string"}}}
    def handler(system, user, i):
        if i == 0:
            return {"pages": [1]}
        if i == 1:
            return {"supplier": "Borealis Metals AB", "governing_law": None, "fax": None}
        return {"governing_law": "Sweden", "fax": None}                  # fax is not in the document at all
    out = extract.run(long_doc(), cfg(schema, relevance_filter=True), llm=FakeLLM(handler))
    assert out["result"] == [{"supplier": "Borealis Metals AB", "governing_law": "Sweden", "fax": None}]
    assert out["usage"]["extract_pages"]["relevance"]["second_look"]["found"] == ["governing_law"]
    assert out["warnings"] == []                                         # nothing is required in this schema


def test_a_required_field_missing_after_the_filter_gets_a_second_look_at_the_other_pages():
    def handler(system, user, i):
        if i == 0:
            return {"pages": [1, 3]}                          # the relevance step missed the governing-law page
        if i == 1:
            return dict(ANSWER, governing_law=None)
        assert "laws of Sweden" in user and "Borealis" not in user and '"supplier"' not in user
        return {"governing_law": "Sweden"}
    llm = FakeLLM(handler)
    out = extract.run(long_doc(), cfg(relevance_filter=True), llm=llm)
    assert out["result"] == [ANSWER] and len(llm.calls) == 3 and out["warnings"] == []
    assert out["usage"]["extract_pages"]["relevance"]["second_look"] == {
        "fields": ["governing_law"], "found": ["governing_law"], "pages": [2, 4, 5, 6]}


@pytest.mark.parametrize("reply", ["not json", {"pages": []}, {"pages": [99]}, RuntimeError("down")])
def test_relevance_falls_back_to_every_page(reply):
    seen = {}
    def handler(system, user, i):
        if i == 0:
            if isinstance(reply, Exception):
                raise reply
            return reply
        seen["user"] = user
        return ANSWER
    out = extract.run(long_doc(), cfg(relevance_filter=True), llm=FakeLLM(handler))
    assert "Boilerplate about notices" in seen["user"] and out["result"] == [ANSWER]
    assert not out["usage"]["extract_pages"]["relevance"]["filtered"]


def test_relevance_is_skipped_on_short_documents():
    doc = make_doc([["Supplier: Borealis Metals AB"], ["Total 200.50, law of Sweden"]])
    llm = FakeLLM([dict(ANSWER, items=[])])
    out = extract.run(doc, cfg(relevance_filter=True), llm=llm)
    assert len(llm.calls) == 1 and "short" in out["usage"]["extract_pages"]["relevance"]["reason"]


# ---------------------------------------------------------------- examples
def test_examples_are_shown_and_a_copied_value_that_is_not_in_the_document_is_removed():
    example = {"supplier": "Example Corp Ltd", "total": 99999.0, "governing_law": "England and Wales", "items": []}
    llm = FakeLLM([dict(ANSWER, supplier="Example Corp Ltd")])
    out = extract.run(long_doc(), cfg(examples=[example]), llm=llm)
    assert "Example 1: " in llm.calls[0]["user"] and "never copy a value from an example" in llm.calls[0]["user"]
    assert out["result"][0]["supplier"] is None and out["result"][0]["governing_law"] == "Sweden"
    assert out["warnings"][0] == "required field 'supplier' was not found in the document"
    assert "equals an example value and is not in the document" in out["warnings"][1]
    # a value that equals the example but IS in the document stays
    same = {"supplier": "Borealis Metals AB", "total": 1.0, "governing_law": "Sweden", "items": []}
    out = extract.run(long_doc(), cfg(examples=[same]), llm=FakeLLM([ANSWER]))
    assert out["result"] == [ANSWER] and out["warnings"] == []
    assert "Example" not in FakeLLM([ANSWER]).calls and "Example 1" not in _first_prompt(cfg())


def _first_prompt(config):
    llm = FakeLLM([ANSWER])
    extract.run(long_doc(), config, llm=llm)
    return llm.calls[0]["user"]


def test_examples_are_validated():
    with pytest.raises(TaskError) as e:
        extract.run(long_doc(), cfg(examples="x"), llm=FakeLLM())
    assert e.value.status_code == 400
    with pytest.raises(TaskError):
        extract.run(long_doc(), cfg(examples=[{}] * 6), llm=FakeLLM())
    with pytest.raises(TaskError):
        extract.run(long_doc(), cfg(examples=["{not json"]), llm=FakeLLM())
    out = extract.run(long_doc(), cfg(examples=['{"supplier": 5}', {"output": ANSWER}]), llm=FakeLLM([ANSWER]))
    assert any("examples[0] does not follow the schema" in w for w in out["warnings"])
    partial = {"supplier": "Example Corp Ltd", "total": 1.0, "governing_law": "Norway"}   # optional `items` left out
    assert extract.run(long_doc(), cfg(examples=[partial]), llm=FakeLLM([ANSWER]))["warnings"] == []


# ---------------------------------------------------------------- page requests
def test_the_model_can_ask_for_a_page_and_search_before_it_answers():
    def handler(system, user, i):
        if i == 0:
            return {"pages": [1], "why": "parties"}
        if i == 1:
            assert "Full text of pages 1:" in user and "=== Page 6 (digest) ===" in user and '{"need"' in user
            assert "Copper rod</td>" not in user                       # page 3 only as a digest so far
            return {"need": [{"page": 3}, {"search": "governed by the laws"}, {"page": 77}]}
        assert "Full text of pages 1, 3, 6:" in user and "Copper rod</td>" in user and "laws of Sweden" in user
        return ANSWER
    llm = FakeLLM(handler)
    out = extract.run(long_doc(), cfg(request_pages=True), llm=llm)
    assert out["result"] == [ANSWER] and len(llm.calls) == 3
    info = out["usage"]["extract_pages"]["page_requests"]
    assert info["start_pages"] == [1] and info["pages_used"] == [1, 3, 6] and info["rounds"] == 2
    assert info["requests"] == [{"page": 3}, {"search": "governed by the laws", "pages": [6]}]
    assert all(c["json_schema"] is None for c in llm.calls)            # a request must be allowed as a reply


def test_page_requests_are_bounded_and_the_last_turn_must_answer():
    def handler(system, user, i):
        if i == 0:
            return {"pages": [1]}
        if "everything that can be given" in user:
            return ANSWER
        return {"need": [{"page": i + 1}]}
    llm = FakeLLM(handler)
    out = extract.run(long_doc(), cfg(request_pages=True), llm=llm, options={"max_page_requests": 2})
    assert out["result"] == [ANSWER] and len(llm.calls) == 4           # relevance + 2 request rounds + final
    assert out["usage"]["extract_pages"]["page_requests"]["pages_used"] == [1, 2, 3]
    # a model that keeps asking even on the last turn: no invented answer, required fields are reported
    stubborn = FakeLLM(lambda s, u, i: {"pages": [1]} if i == 0 else {"need": [{"page": 2}]})
    with pytest.raises(TaskError):
        extract.run(long_doc(), cfg(request_pages=True), llm=stubborn, options={"max_page_requests": 1})


def test_search_pages_ranks_by_phrase():
    pages = DocView(long_doc()).pages
    assert [p.number for p, _ in X.search_pages(pages, "Governing law")] == [6]
    assert [p.number for p, _ in X.search_pages(pages, "boilerplate")] == [4, 5]
    assert X.search_pages(pages, "!!") == [] and X.search_pages(pages, "nowhere to be found") == []
