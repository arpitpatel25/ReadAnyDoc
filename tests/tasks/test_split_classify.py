"""Split and Classify, FakeLLM-driven."""

import json

import pytest
from jsonschema import Draft202012Validator

from readanydoc import paths
from readanydoc.tasks import FakeLLM, TaskError, classify, split
from tests.tasks.conftest import make_doc

BIG_TABLE = "<table>" + "".join(f"<tr><td>row {i}</td><td>Account 77{i:02d}</td></tr>" for i in range(30)) + "</table>"


def packet():
    return make_doc([
        ["Cover letter", "Dear customer, please find your statements enclosed."],
        ["Statement for Account 1111", ("Table", BIG_TABLE)],
        ["Statement for Account 1111 (continued)"],
        ["Statement for Account 2222"],
        ["Terms and conditions", "These terms apply to all accounts."],
    ])


DESC = [{"name": "Cover", "description": "cover letter"},
        {"name": "Statements", "description": "account statements", "partition_key": "account number"},
        {"name": "Terms", "description": "terms and conditions"}]

ANSWER = {"sections": [
    {"name": "Cover", "pages": [1], "conf": "high"},
    {"name": "Statements", "pages": [2, 3, 4], "conf": "high", "partitions": [
        {"name": "1111", "pages": [2, 3], "conf": "high"}, {"name": "2222", "pages": [4], "conf": "low"}]},
    {"name": "Terms", "pages": [5], "conf": "high"}]}


def validate(component, body):
    spec_path = paths.reference_dir() / "openapi.json"
    if not spec_path.exists():
        pytest.skip("reference/openapi.json not available")
    spec = json.loads(spec_path.read_text())
    Draft202012Validator({"$ref": f"#/components/schemas/{component}", "components": spec["components"]}).validate(body)


# ---------------------------------------------------------------- split

def test_split_shape_partitions_and_legacy_mapping():
    llm = FakeLLM([ANSWER])
    out = split.run(packet(), {"split_description": DESC}, llm=llm)
    assert out["response_type"] == "split" and out["usage"] == {"num_pages": 5, "credits": None}
    assert out["result"]["section_mapping"] is None  # Reducto's real responses carry null here
    assert out["result"]["splits"] == [
        {"name": "Cover", "pages": [1], "conf": "high", "partitions": None},
        {"name": "Statements", "pages": [2, 3, 4], "conf": "high", "partitions": [
            {"name": "1111", "pages": [2, 3], "conf": "high"}, {"name": "2222", "pages": [4], "conf": "low"}]},
        {"name": "Terms", "pages": [5], "conf": "high", "partitions": None}]
    assert len(llm.calls) == 1
    user = llm.calls[0]["user"]
    assert 'name: "Statements"; description: account statements; partition key: account number' in user
    assert split.DEFAULT_RULES in user and "=== Page 5 ===" in user
    validate("SplitResponse", {**out, "job_id": "j", "duration": 0.1})


def test_split_rules_are_passed_through():
    llm = FakeLLM([ANSWER])
    split.run(packet(), {"split_description": DESC, "split_rules": "Every page belongs to exactly one section."}, llm=llm)
    assert "Every page belongs to exactly one section." in llm.calls[0]["user"]
    assert split.DEFAULT_RULES not in llm.calls[0]["user"]


def test_table_cutoff_truncate_vs_preserve():
    llm = FakeLLM([ANSWER])
    split.run(packet(), {"split_description": DESC}, llm=llm)                                       # default: truncate
    split.run(packet(), {"split_description": DESC, "settings": {"table_cutoff": "preserve"}}, llm=llm)
    truncated, preserved = llm.calls[0]["user"], llm.calls[1]["user"]
    assert "row 2" in truncated and "row 29" not in truncated and "[table truncated: 26 more rows]" in truncated
    assert "row 29" in preserved and "Account 7729" in preserved and "truncated" not in preserved
    with pytest.raises(TaskError):
        split.run(packet(), {"split_description": DESC, "settings": {"table_cutoff": "half"}}, llm=llm)


def test_split_cleans_up_what_the_model_returns():
    messy = {"sections": [
        {"name": "cover", "pages": [1, 1, "2", 99, 0, True], "conf": "HIGH"},                # case, dupes, out of range
        {"name": "Appendix", "pages": [3]},                                                 # not a described section
        {"name": "Statements", "pages": [2], "partitions": [{"name": "1111", "pages": [2, 3]},  # page 3 not in section
                                                           {"name": "", "pages": [4]}]},
        {"name": "Cover", "pages": [5], "partitions": [{"name": "x", "pages": [5]}]}]}       # no partition key: dropped
    out = split.run(packet(), {"split_description": DESC}, llm=FakeLLM([messy]))["result"]
    by = {s["name"]: s for s in out["splits"]}
    assert by["Cover"]["pages"] == [1, 2, 5] and by["Cover"]["partitions"] is None
    assert by["Statements"]["pages"] == [2, 3] and by["Statements"]["conf"] == "low"   # repaired by a rule: low
    assert by["Statements"]["partitions"] == [{"name": "1111", "pages": [2, 3], "conf": "low"}]
    assert "Terms" not in by and "Appendix" not in by  # like Reducto: a section with no pages is left out
    legacy = split.run(packet(), {"split_description": DESC}, llm=FakeLLM([messy]),
                       options={"keep_empty_sections": True})["result"]
    assert legacy["splits"][-1] == {"name": "Terms", "pages": [], "conf": "low", "partitions": None}
    assert legacy["section_mapping"] == {"Cover": [1, 2, 5], "Statements": [2, 3], "Terms": []}


def test_allow_page_overlap_false_gives_each_page_one_section():
    overlapping = {"sections": [{"name": "Cover", "pages": [1, 2], "conf": "low"},
                                {"name": "Statements", "pages": [2, 3, 4], "conf": "high",
                                 "partitions": [{"name": "1111", "pages": [2, 3, 4]}, {"name": "2222", "pages": [4]}]},
                                {"name": "Terms", "pages": [4, 5], "conf": "high"}]}
    on = split.run(packet(), {"split_description": DESC}, llm=FakeLLM([overlapping]))["result"]
    assert {s["name"]: s["pages"] for s in on["splits"]} == {"Cover": [1, 2], "Statements": [2, 3, 4], "Terms": [4, 5]}
    off = split.run(packet(), {"split_description": DESC, "settings": {"allow_page_overlap": False}},
                    llm=FakeLLM([overlapping]))["result"]
    assert {s["name"]: s["pages"] for s in off["splits"]} == {"Cover": [1], "Statements": [2, 3, 4], "Terms": [5]}
    pages = [p for s in off["splits"] for p in s["pages"]]
    assert sorted(pages) == sorted(set(pages))
    st = next(s for s in off["splits"] if s["name"] == "Statements")
    assert st["conf"] == "low"  # a conflict was resolved by rule: say so
    assert st["partitions"] == [{"name": "1111", "pages": [2, 3, 4], "conf": "low"}]


def test_deep_split_verify_pass_corrects_and_flags():
    first = {"sections": [{"name": "Cover", "pages": [1], "conf": "high"},
                          {"name": "Statements", "pages": [2, 4], "conf": "high",
                           "partitions": [{"name": "1111", "pages": [2], "conf": "high"},
                                          {"name": "2222", "pages": [4], "conf": "high"}]},
                          {"name": "Terms", "pages": [5], "conf": "high"}]}
    doc = packet()
    quote = {n: " ".join(doc.pages[n - 1].regions[0].content.split()[:3]) for n in range(1, 6)}
    second = {"sections": [
        {"name": "Cover", "conf": "high", "pages": [{"page_number": 1, "evidence": quote[1]}]},
        {"name": "Statements", "conf": "high",
         "pages": [{"page_number": 2, "evidence": quote[2]}, {"page_number": 3, "evidence": "words not on the page"},
                   {"page_number": 4, "evidence": quote[4]}],
         "partitions": [{"name": "1111", "pages": [{"page_number": 2, "evidence": quote[2]}, {"page_number": 3}]},
                        {"name": "2222", "pages": [4]}]},
        {"name": "Terms", "conf": "high", "pages": [5]}]}
    llm = FakeLLM([first, second])
    resp = split.run(doc, {"split_description": DESC, "settings": {"deep_split": True}}, llm=llm)
    out = resp["result"]
    assert len(llm.calls) == 2 and llm.calls[0]["system"] == split.DEEP_SYSTEM
    verify = llm.calls[1]["user"]
    assert "pages assigned to no section: [3]" in verify and "has gaps: [2, 4]" in verify
    assert '"page_number": 4' in verify
    # Reducto's DeepSplitResult: no section_mapping, pages are {page_number, evidence, confidence}
    assert set(out) == {"splits"}
    by = {s["name"]: s for s in out["splits"]}
    assert by["Cover"] == {"name": "Cover", "partitions": None,
                           "pages": [{"page_number": 1, "evidence": quote[1], "confidence": "high"}]}
    st = by["Statements"]
    assert [p["page_number"] for p in st["pages"]] == [2, 3, 4]
    assert {p["confidence"] for p in st["pages"]} == {"low"}  # the two passes disagreed on this section
    assert st["pages"][1]["evidence"] == "words not on the page"
    assert st["partitions"][0]["name"] == "1111" and [p["page_number"] for p in st["partitions"][0]["pages"]] == [2, 3]
    assert st["partitions"][0]["pages"][0]["evidence"] == quote[2]
    terms = by["Terms"]["pages"][0]  # no quote from the model: the start of the page, marked low
    assert terms["confidence"] == "low" and terms["evidence"] and terms["evidence"] in doc.pages[4].regions[0].content
    validate("SplitResponse", {**resp, "job_id": "j", "duration": 0.1})


def test_deep_split_auto_partition_switch():
    auto = {"sections": [{"name": "Cover", "pages": [1]},
                         {"name": "Terms", "pages": [5], "partitions": [{"name": "General", "pages": [5]}]}]}
    desc = [d for d in DESC if d["name"] != "Statements"]
    on = split.run(packet(), {"split_description": desc, "settings": {"deep_split": True}}, llm=FakeLLM([auto]))["result"]
    assert [(p["name"], [x["page_number"] for x in p["pages"]]) for p in on["splits"][1]["partitions"]] == [("General", [5])]
    off = split.run(packet(), {"split_description": desc, "settings": {"deep_split": True, "auto_partition": False}},
                    llm=FakeLLM([auto]))["result"]
    assert off["splits"][1]["partitions"] is None


def test_long_documents_are_split_in_windows_with_carry_over():
    doc = make_doc([[f"page {i} of the report"] for i in range(1, 8)])

    def handler(system, user, i):
        nums = [int(x) for x in __import__("re").findall(r"=== Page (\d+) ===", user)]
        return {"sections": [{"name": "Report", "pages": nums, "conf": "high"}]}

    llm = FakeLLM(handler)
    out = split.run(doc, {"split_description": [{"name": "Report", "description": "the report"}]}, llm=llm,
                    options={"window_pages": 3})
    assert len(llm.calls) == 3
    assert "this is part 2 of 3 (pages 4-6)" in llm.calls[1]["user"] and "Assignments so far" in llm.calls[1]["user"]
    assert out["result"]["splits"] == [{"name": "Report", "pages": [1, 2, 3, 4, 5, 6, 7], "conf": "high", "partitions": None}]


def test_split_request_errors_and_unusable_answer():
    for bad in ({}, {"split_description": []}, {"split_description": [{"name": "A"}]},
                {"split_description": [{"name": "A", "description": "x"}, {"name": "a", "description": "y"}]}):
        with pytest.raises(TaskError) as e:
            split.run(packet(), bad, llm=FakeLLM())
        assert e.value.status_code == 400
    out = split.run(packet(), {"split_description": DESC}, llm=FakeLLM(["no idea"]))["result"]
    assert out["splits"] == []


# ---------------------------------------------------------------- classify

SCHEMA = [{"category": "invoice", "criteria": ["has an invoice number", "lists amounts due", "names a customer"]},
          {"category": "contract", "criteria": ["has signature blocks", "defines parties"]},
          {"category": "receipt", "criteria": ["shows a payment was made"]}]


def test_classify_response_structure_and_fraction_matched(invoice_doc):
    llm = FakeLLM([{"categories": [{"category": "invoice", "criteria": [True, True, False]},
                                   {"category": "contract", "criteria": [False, False]},
                                   {"category": "receipt", "criteria": [False]}], "best": "invoice"}])
    out = classify.run(invoice_doc, {"classification_schema": SCHEMA}, llm=llm)
    assert out == {
        "response_type": "classify",
        "result": {"category": "invoice"},
        "response_confidence": {"categories": [
            {"category": "invoice", "confidence": 0.6667, "criteria_confidence": [
                {"criterion": "has an invoice number", "confidence": "high"},
                {"criterion": "lists amounts due", "confidence": "high"},
                {"criterion": "names a customer", "confidence": "low"}]},
            {"category": "contract", "confidence": 0.0, "criteria_confidence": [
                {"criterion": "has signature blocks", "confidence": "low"},
                {"criterion": "defines parties", "confidence": "low"}]},
            {"category": "receipt", "confidence": 0.0, "criteria_confidence": [
                {"criterion": "shows a payment was made", "confidence": "low"}]}]},
        "usage": {"num_pages": 1, "num_categories": 3, "credits": None},
    }
    assert len(llm.calls) == 1  # exactly one model call
    assert "1. has an invoice number" in llm.calls[0]["user"] and "Total Due" in llm.calls[0]["user"]
    validate("ClassifyResponse", {**out, "job_id": "j", "duration": 0.1})


def test_classify_picks_highest_fraction_not_the_models_favourite(invoice_doc):
    llm = FakeLLM([{"categories": [{"category": "invoice", "criteria": [True, False, False]},
                                   {"category": "receipt", "criteria": [True]}], "best": "invoice"}])
    out = classify.run(invoice_doc, {"classification_schema": SCHEMA}, llm=llm)
    assert out["result"]["category"] == "receipt"  # 1/1 beats 1/3
    confs = {c["category"]: c["confidence"] for c in out["response_confidence"]["categories"]}
    assert confs == {"invoice": 0.3333, "contract": 0.0, "receipt": 1.0}  # a category the model skipped scores 0


def test_classify_ties_use_the_models_pick_then_schema_order(invoice_doc):
    tie = [{"category": "invoice", "criteria": [True, True, True]}, {"category": "receipt", "criteria": [True]}]
    assert classify.run(invoice_doc, {"classification_schema": SCHEMA},
                        llm=FakeLLM([{"categories": tie, "best": "receipt"}]))["result"]["category"] == "receipt"
    assert classify.run(invoice_doc, {"classification_schema": SCHEMA},
                        llm=FakeLLM([{"categories": tie, "best": "something else"}]))["result"]["category"] == "invoice"


def test_classify_category_groups(invoice_doc):
    reply = {"categories": [{"category": "invoice", "criteria": [True, True, True]}]}
    groups = {"financial": ["invoice", "receipt"], "legal": ["contract"]}
    out = classify.run(invoice_doc, {"classification_schema": SCHEMA, "category_groups": groups}, llm=FakeLLM([reply]))
    assert out["extra_metadata"] == {"grouping": "financial"}
    out = classify.run(invoice_doc, {"classification_schema": SCHEMA, "category_groups": {"legal": ["contract"]}},
                       llm=FakeLLM([reply]))
    assert out["extra_metadata"] == {"grouping": "ungrouped"}
    assert "extra_metadata" not in classify.run(invoice_doc, {"classification_schema": SCHEMA}, llm=FakeLLM([reply]))


def test_classify_page_range_default_first_5_max_10():
    doc = make_doc([[f"text of page {i}"] for i in range(1, 15)])
    reply = {"categories": [{"category": "invoice", "criteria": [True, True, True]}]}
    llm = FakeLLM([reply])
    out = classify.run(doc, {"classification_schema": SCHEMA}, llm=llm)
    assert out["usage"]["num_pages"] == 5
    assert "text of page 5" in llm.calls[0]["user"] and "text of page 6" not in llm.calls[0]["user"]
    out = classify.run(doc, {"classification_schema": SCHEMA, "page_range": {"start": 3, "end": 12}}, llm=llm)
    assert out["usage"]["num_pages"] == 10
    assert "text of page 12" in llm.calls[1]["user"] and "text of page 2\n" not in llm.calls[1]["user"]
    out = classify.run(doc, {"classification_schema": SCHEMA, "page_range": [14, 2]}, llm=llm)
    assert out["usage"]["num_pages"] == 2
    with pytest.raises(TaskError) as e:
        classify.run(doc, {"classification_schema": SCHEMA, "page_range": {"start": 1, "end": 11}}, llm=llm)
    assert e.value.status_code == 400 and "at most 10" in e.value.detail
    assert len(llm.calls) == 3  # the rejected request never reached the model


def test_classify_document_metadata_is_in_the_prompt(invoice_doc):
    llm = FakeLLM([{"categories": []}])
    classify.run(invoice_doc, {"classification_schema": SCHEMA, "document_metadata": "source: AP inbox"}, llm=llm)
    assert "source: AP inbox" in llm.calls[0]["user"]


def test_classify_accurate_requires_evidence_that_is_really_on_the_page(invoice_doc):
    reply = {"categories": [{"category": "invoice", "criteria": [
        {"met": True, "evidence": "Invoice INV-2041"},              # on the page
        {"met": True, "evidence": "amount payable within 30 days"},  # not on the page
        {"met": False, "evidence": ""}]}], "best": "invoice"}
    llm = FakeLLM([reply])
    out = classify.run(invoice_doc, {"classification_schema": SCHEMA, "model": "accurate"}, llm=llm)
    inv = out["response_confidence"]["categories"][0]
    assert [c["confidence"] for c in inv["criteria_confidence"]] == ["high", "low", "low"]
    assert inv["confidence"] == 0.3333
    assert "quote" in llm.calls[0]["system"]
    # the same reply in default mode trusts the yes/no
    out = classify.run(invoice_doc, {"classification_schema": SCHEMA}, llm=FakeLLM([reply]))
    assert out["response_confidence"]["categories"][0]["confidence"] == 0.6667


def test_classify_request_errors(invoice_doc):
    for bad in ({}, {"classification_schema": []}, {"classification_schema": [{"category": "a"}]},
                {"classification_schema": SCHEMA, "model": "turbo"},
                {"classification_schema": SCHEMA + [SCHEMA[0]]},
                {"classification_schema": SCHEMA, "category_groups": {"g": "invoice"}}):
        with pytest.raises(TaskError) as e:
            classify.run(invoice_doc, bad, llm=FakeLLM())
        assert e.value.status_code == 400
    with pytest.raises(TaskError) as e:
        classify.run(invoice_doc, {"classification_schema": SCHEMA}, llm=FakeLLM(["not json"]))
    assert e.value.status_code == 502
