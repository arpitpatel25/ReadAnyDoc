"""Extract: schema guarantees end to end, citations, long documents, deep_extract. All FakeLLM-driven."""

import json
import re

import pytest
from jsonschema import Draft202012Validator

from readanydoc.tasks import FakeLLM, TaskError, extract
from readanydoc.tasks import schema as S
from readanydoc.tasks.common import DocView
from tests.tasks.conftest import make_doc

INVOICE_SCHEMA = {"type": "object", "properties": {
    "invoice_number": {"type": "string", "pattern": "^INV-\\d+$"},
    "customer": {"type": "string"},
    "status": {"type": "string", "enum": ["paid", "unpaid"]},
    "total": {"type": "number", "minimum": 0},
    "po_number": {"type": "string"},
    "line_items": {"type": "array", "items": {"type": "object", "properties": {
        "item": {"type": "string"}, "qty": {"type": "integer"}, "amount": {"type": "number"}}}},
}}

GOOD = {"invoice_number": "INV-2041", "customer": "Acme Robotics Ltd", "status": "paid", "total": 1575.0,
        "po_number": None,
        "line_items": [{"item": "Servo motor", "qty": 4, "amount": 400.0},
                       {"item": "Controller board", "qty": 2, "amount": 1100.0},
                       {"item": "Shipping", "qty": 1, "amount": 75.0}]}


def cfg(schema=INVOICE_SCHEMA, system_prompt=None, **settings):
    instructions = {"schema": schema}
    if system_prompt is not None:
        instructions["system_prompt"] = system_prompt
    return {"instructions": instructions, "settings": settings}


def wrap(value, label):
    """Wrap every leaf of ``value`` as {"v", "r"} with one label (dict leaf -> label mapping allowed)."""
    if isinstance(value, dict):
        return {k: wrap(v, label) for k, v in value.items()}
    if isinstance(value, list):
        return [wrap(v, label) for v in value]
    return {"v": value, "r": [label] if label else []}


# ---------------------------------------------------------------- basics and guarantees

def test_plain_extract_shape_and_usage(invoice_doc):
    llm = FakeLLM([GOOD])
    out = extract.run(invoice_doc, cfg(), llm=llm)
    assert out["response_type"] == "v3_extract"
    assert out["result"] == [GOOD]
    # num_fields counts the returned leaf values (array items included), as Reducto's real responses do
    assert out["usage"] == {"num_pages": 1, "num_fields": 14, "credits": None, "extract_mode": "extract"}
    assert "job_id" not in out and "duration" not in out
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert "Total Due: $1,575.00" in call["user"] and json.dumps(INVOICE_SCHEMA) in call["user"]
    assert call["system"].startswith("Be precise and thorough.")
    assert call["images"] == []  # never from raw images unless include_images


def test_missing_fields_null_extra_fields_dropped_no_repair_call_needed(invoice_doc):
    llm = FakeLLM([{"invoice_number": "INV-2041", "total": "1,575.00", "made_up": "x",
                    "line_items": [{"item": "Servo motor", "sku": "zzz"}]}])
    out = extract.run(invoice_doc, cfg(), llm=llm)
    assert out["result"] == [{"invoice_number": "INV-2041", "customer": None, "status": None, "total": 1575.0,
                              "po_number": None, "line_items": [{"item": "Servo motor", "qty": None, "amount": None}]}]
    assert len(llm.calls) == 1


def test_schema_violation_triggers_a_repair_reask_with_the_errors(invoice_doc):
    bad = dict(GOOD, status="PAID", invoice_number="2041")
    llm = FakeLLM([bad, GOOD])
    out = extract.run(invoice_doc, cfg(), llm=llm)
    assert out["result"] == [GOOD]
    assert len(llm.calls) == 2
    fix = llm.calls[1]["user"]
    assert "- status:" in fix and "- invoice_number:" in fix and "'PAID' is not one of" in fix


def test_repairs_are_bounded_and_a_still_invalid_value_becomes_null(invoice_doc):
    bad = dict(GOOD, status="PAID", total=-5)
    llm = FakeLLM([bad])  # repeats the same bad answer forever
    out = extract.run(invoice_doc, cfg(), llm=llm, options={"max_repairs": 2})
    assert len(llm.calls) == 3  # 1 + 2 repairs, then stop
    res = out["result"][0]
    assert res["status"] is None and res["total"] is None
    assert res["invoice_number"] == "INV-2041" and len(res["line_items"]) == 3
    Draft202012Validator(S.enforced(INVOICE_SCHEMA)).validate(res)


def test_a_repair_that_makes_things_worse_is_not_accepted(invoice_doc):
    first = dict(GOOD, status="PAID")
    worse = dict(GOOD, status="PAID", total=-1, invoice_number="x")
    llm = FakeLLM([first, worse, worse])
    res = extract.run(invoice_doc, cfg(), llm=llm)["result"][0]
    assert res["total"] == 1575.0 and res["invoice_number"] == "INV-2041" and res["status"] is None


def test_unparseable_answer_is_an_error_not_a_silent_all_null_object(invoice_doc):
    llm = FakeLLM(["I could not find anything, sorry."])
    with pytest.raises(TaskError) as e:   # all nulls would read as "the document has none of this"
        extract.run(invoice_doc, cfg(), llm=llm)
    assert e.value.status_code == 502 and "no usable answer" in e.value.detail
    assert len(llm.calls) == 2  # one "reply with JSON only" retry


def test_invalid_schema_is_a_400_before_any_model_call(invoice_doc):
    llm = FakeLLM([GOOD])
    with pytest.raises(TaskError) as e:
        extract.run(invoice_doc, cfg({"type": "object", "properties": {"a": {"type": "strin"}}}), llm=llm)
    assert e.value.status_code == 400 and llm.calls == []


def test_local_refs_and_fenced_json(invoice_doc):
    schema = {"type": "object", "properties": {"buyer": {"$ref": "#/$defs/party"}},
              "$defs": {"party": {"type": "object", "properties": {"name": {"type": "string"}, "city": {"type": "string"}}}}}
    llm = FakeLLM(["Here you go:\n```json\n{\"buyer\": {\"name\": \"Acme Robotics Ltd\"}}\n```"])
    out = extract.run(invoice_doc, cfg(schema), llm=llm)
    assert out["result"] == [{"buyer": {"name": "Acme Robotics Ltd", "city": None}}]
    assert "$ref" not in llm.calls[0]["user"]


def test_schemaless_prompt_only(invoice_doc):
    llm = FakeLLM([{"customer_name": "Acme Robotics Ltd", "total": 1575.0}])
    out = extract.run(invoice_doc, {"instructions": {"system_prompt": "Get the customer and total."}}, llm=llm)
    assert out["result"] == [{"customer_name": "Acme Robotics Ltd", "total": 1575.0}]
    assert out["usage"]["num_fields"] == 2
    assert llm.calls[0]["system"].startswith("Get the customer and total.")


def test_page_range_limits_what_the_model_sees():
    doc = make_doc([["alpha page one"], ["beta page two"], ["gamma page three"]])
    llm = FakeLLM([{"word": "beta"}])
    out = extract.run(doc, cfg({"type": "object", "properties": {"word": {"type": "string"}}},
                               page_range={"start": 2, "end": 2}), llm=llm)
    assert out["usage"]["num_pages"] == 1
    user = llm.calls[0]["user"]
    assert "beta page two" in user and "alpha" not in user and "gamma" not in user
    with pytest.raises(TaskError) as e:
        extract.run(doc, cfg({"type": "object", "properties": {"word": {"type": "string"}}}, page_range=[9]), llm=llm)
    assert e.value.status_code == 400


def test_include_images_passes_existing_renders_only_when_asked(invoice_doc, tmp_path):
    png = tmp_path / "page1.png"
    png.write_bytes(b"png")
    invoice_doc.pages[0].renders["150"] = str(png)
    llm = FakeLLM([GOOD, GOOD, GOOD])
    extract.run(invoice_doc, cfg(), llm=llm)
    assert llm.calls[0]["images"] == []
    extract.run(invoice_doc, cfg(include_images=True), llm=llm)
    assert llm.calls[1]["images"] == [str(png)]
    png.unlink()  # the render was cleaned up with the parse job that made it: text only, no crash
    extract.run(invoice_doc, cfg(include_images=True), llm=llm)
    assert llm.calls[2]["images"] == []


def test_multiple_documents_are_numbered_continuously():
    a, b = make_doc([["first doc: code AAA"]]), make_doc([["second doc: code BBB"]])
    llm = FakeLLM([{"codes": ["AAA", "BBB"]}])
    out = extract.run([a, b], cfg({"type": "object", "properties": {"codes": {"type": "array", "items": {"type": "string"}}}}), llm=llm)
    assert out["usage"]["num_pages"] == 2
    assert "=== Page 2 ===" in llm.calls[0]["user"]


# ---------------------------------------------------------------- citations

def labels_of(doc):
    return {ref.region.content: ref.label for p in DocView(doc).pages for ref in p.regions}


def test_citations_wrap_every_leaf_and_result_is_an_object(invoice_doc):
    lab = labels_of(invoice_doc)
    table = next(v for k, v in lab.items() if k.startswith("<table"))
    answer = {
        "invoice_number": {"v": "INV-2041", "r": [lab["# Invoice INV-2041"]]},
        "customer": {"v": "Acme Robotics Ltd", "r": [lab["Billed to: Acme Robotics Ltd, 12 Harbour Road, Leeds"]]},
        "status": {"v": "paid", "r": [lab["Status: PAID"]]},
        "total": {"v": 1575.0, "r": [lab["Total Due: $1,575.00"]]},
        "po_number": {"v": None, "r": []},
        "line_items": [{"item": {"v": "Controller board", "r": [table]}, "qty": {"v": 2, "r": [table]},
                        "amount": {"v": 1100.0, "r": [table]}}],
    }
    llm = FakeLLM([answer])
    out = extract.run(invoice_doc, cfg(citations={"enabled": True}), llm=llm)
    res = out["result"]
    assert isinstance(res, dict)  # an object, not a list, when citations are on
    assert "[r5|Text] Total Due: $1,575.00" in llm.calls[0]["user"]

    total = res["total"]
    assert total["value"] == 1575.0
    (c,) = total["citations"]
    assert set(c) == {"type", "content", "bbox", "confidence", "granular_confidence", "parentBlock",
                      "image_url", "chart_data", "extra"}  # the keys of a citation in Reducto's real responses
    assert c["type"] == "Text" and c["content"] == "1,575.00" and c["confidence"] == "high"
    assert c["granular_confidence"] == {"extract_confidence": 0.95, "parse_confidence": 0.97}
    assert c["parentBlock"]["content"] == "Total Due: $1,575.00" and c["parentBlock"]["type"] == "Text"
    assert c["bbox"]["page"] == 1 and c["bbox"]["original_page"] == 1

    # the box is the union of the matched WORD boxes ("1", "575", "00"), narrower than the block
    region = invoice_doc.pages[0].regions[4]
    words = [w for w in region.iter_words() if w.text in ("1", "575", "00")]
    assert c["bbox"]["left"] == pytest.approx(min(w.bbox.left for w in words), abs=1e-3)
    assert c["bbox"]["width"] < c["parentBlock"]["bbox"]["width"]

    # table values cite the cell text and the Table block
    amount = res["line_items"][0]["amount"]["citations"][0]
    assert amount["type"] == "Table" and amount["content"] == "1,100.00" and amount["confidence"] == "high"
    assert res["line_items"][0]["item"]["citations"][0]["content"] == "Controller board"
    # "paid" is in "Status: PAID" after normalisation
    assert res["status"]["citations"][0]["confidence"] == "high"
    # a null value has no citations
    assert res["po_number"] == {"value": None, "citations": []}


def test_value_not_in_the_cited_block_is_marked_low_never_high(invoice_doc):
    lab = labels_of(invoice_doc)
    answer = wrap(dict(GOOD, line_items=[]), None)
    answer["total"] = {"v": 9999.0, "r": [lab["Total Due: $1,575.00"]]}       # wrong value, real block
    answer["customer"] = {"v": "Acme Robotics Ltd", "r": [lab["Status: PAID"]]}  # right value, wrong block
    res = extract.run(invoice_doc, cfg(citations={"enabled": True}), llm=FakeLLM([answer]))["result"]
    for key in ("total", "customer"):
        (c,) = res[key]["citations"]
        assert c["confidence"] == "low" and c["granular_confidence"]["extract_confidence"] == 0.3
        assert c["bbox"] == c["parentBlock"]["bbox"]  # block-level box only: no word box is claimed


def test_no_citation_is_ever_fabricated(invoice_doc):
    answer = wrap(dict(GOOD, line_items=[]), None)
    answer["customer"] = {"v": "Globex Corporation", "r": ["r999"]}   # unknown label, value not in the document
    answer["invoice_number"] = {"v": "INV-2041", "r": []}             # no label, but occurs exactly once
    answer["po_number"] = {"v": "Invoice", "r": ["nonsense"]}         # occurs in two blocks: ambiguous
    res = extract.run(invoice_doc, cfg(citations={"enabled": True}), llm=FakeLLM([answer]))["result"]
    assert res["customer"] == {"value": "Globex Corporation", "citations": []}
    assert res["po_number"] == {"value": "Invoice", "citations": []}
    (c,) = res["invoice_number"]["citations"]
    assert c["content"] == "INV-2041"
    assert c["confidence"] == "high" and c["granular_confidence"]["extract_confidence"] == 0.7


def test_citation_options_bbox_only_and_no_numeric_confidence(invoice_doc):
    lab = labels_of(invoice_doc)
    answer = wrap(dict(GOOD, line_items=[]), None)
    answer["total"] = {"v": 1575.0, "r": [lab["Total Due: $1,575.00"]]}
    res = extract.run(invoice_doc, cfg(citations={"enabled": True, "parent_block": "bbox_only",
                                                  "numerical_confidence": False}), llm=FakeLLM([answer]))["result"]
    (c,) = res["total"]["citations"]
    assert c["parentBlock"]["content"] == "" and c["parentBlock"]["bbox"]["width"] > 0
    assert c["granular_confidence"] == {"extract_confidence": None, "parse_confidence": None}


def test_ungrounded_document_falls_back_to_block_boxes():
    doc = make_doc([["Total Due: $1,575.00"]], grounded=False)
    res = extract.run(doc, cfg({"type": "object", "properties": {"total": {"type": "number"}}},
                               citations={"enabled": True}), llm=FakeLLM([{"total": {"v": 1575, "r": ["r1"]}}]))["result"]
    (c,) = res["total"]["citations"]
    assert c["confidence"] == "high"
    assert c["bbox"] == {"left": 0.0, "top": 0.0, "width": 1.0, "height": 1.0, "page": 1, "original_page": 1}


def test_citations_stay_aligned_when_an_invalid_array_item_is_dropped():
    doc = make_doc([["codes: AAA", "codes: BBB", "codes: CCC"]])
    schema = {"type": "object", "properties": {"codes": {"type": "array", "items": {"type": "string", "pattern": "^[A-C]{3}$"}}}}
    answer = {"codes": [{"v": "AAA", "r": ["r1"]}, {"v": "zzz", "r": ["r2"]}, {"v": "CCC", "r": ["r3"]}]}
    res = extract.run(doc, cfg(schema, citations={"enabled": True}), llm=FakeLLM([answer]), options={"max_repairs": 0})["result"]
    assert [c["value"] for c in res["codes"]] == ["AAA", "CCC"]
    assert res["codes"][1]["citations"][0]["content"] == "CCC"


def test_citations_on_a_cached_bench_table_page(table_doc):
    """Real parsed page (cached Haiku output + PDF word boxes): the cited box is the word's own box."""
    view = DocView(table_doc)
    table_ref = next(ref for p in view.pages for ref in p.regions if ref.region.type.value == "Table")
    schema = {"type": "object", "properties": {"booth": {"type": "string"}, "company": {"type": "string"}}}
    llm = FakeLLM([{"booth": {"v": "100", "r": [table_ref.label]}, "company": {"v": "Angle Park, Inc.", "r": [table_ref.label]}}])
    res = extract.run(table_doc, cfg(schema, citations={"enabled": True}), llm=llm)["result"]
    (c,) = res["company"]["citations"]
    assert c["type"] == "Table" and c["confidence"] == "high" and c["content"] == "Angle Park, Inc."
    words = [w for w in table_ref.region.iter_words() if w.text in ("Angle", "Park", "Inc")][:3]
    assert c["bbox"]["left"] == pytest.approx(min(w.bbox.left for w in words), abs=2e-3)
    assert c["bbox"]["top"] == pytest.approx(min(w.bbox.top for w in words), abs=2e-3)
    assert c["bbox"]["width"] < 0.5 and c["parentBlock"]["content"].startswith("<table>")
    assert res["booth"]["citations"][0]["content"] == "100"


# ---------------------------------------------------------------- long documents: segments, merge, de-dup

TXN_SCHEMA = {"type": "object", "properties": {
    "account": {"type": "string"},
    "transactions": {"type": "array", "items": {"type": "object", "properties": {
        "date": {"type": "string"}, "what": {"type": "string"}, "amount": {"type": "number"}}}},
    "closing_balance": {"type": "number"}}}

LINE = re.compile(r"\[(r\d+)\|Text\] Txn (\S+) (\w+) ([\d.]+)")


def statement(n_pages=5, per_page=3):
    pages, k = [], 0
    for p in range(n_pages):
        blocks = ["Account: 0012-3456"] if p == 0 else []
        for _ in range(per_page):
            k += 1
            blocks.append(f"Txn 2025-01-{k:02d} item{k} {k}.50")
        if p == n_pages - 1:
            blocks.append("Closing balance: 120.00")
        pages.append(blocks)
    return make_doc(pages)


def reader(skip=()):
    """A fake model that reads exactly the labelled lines it is shown (optionally missing some)."""
    def handler(system, user, i):
        doc_part = user.split("JSON Schema:")[0]
        items = [{"date": {"v": d, "r": [lab]}, "what": {"v": w, "r": [lab]}, "amount": {"v": float(a), "r": [lab]}}
                 for lab, d, w, a in LINE.findall(doc_part) if w not in skip]
        acct = re.search(r"\[(r\d+)\|Text\] Account: (\S+)", doc_part)
        bal = re.search(r"\[(r\d+)\|Text\] Closing balance: ([\d.]+)", doc_part)
        return {"account": {"v": acct.group(2), "r": [acct.group(1)]} if acct else {"v": None, "r": []},
                "transactions": items,
                "closing_balance": {"v": float(bal.group(2)), "r": [bal.group(1)]} if bal else {"v": None, "r": []}}
    return handler


def test_array_extract_segments_with_overlap_merges_and_deduplicates():
    doc = statement(5, 3)
    llm = FakeLLM(reader())
    out = extract.run(doc, cfg(TXN_SCHEMA, array_extract=True), llm=llm,
                      options={"segment_pages": 2, "segment_overlap": 1})
    assert len(llm.calls) == 4  # pages 1-2, 2-3, 3-4, 4-5
    assert "part 1 of 4" in llm.calls[0]["user"] and "=== Page 3 ===" not in llm.calls[0]["user"]
    res = out["result"][0]
    assert [t["what"] for t in res["transactions"]] == [f"item{k}" for k in range(1, 16)]  # no dupes, in order
    assert res["account"] == "0012-3456" and res["closing_balance"] == 120.0
    assert out["usage"]["num_pages"] == 5


def test_segments_run_in_parallel_on_a_parallel_safe_model_with_the_same_result():
    class ParallelFake(FakeLLM):
        parallel_safe = True

    doc = statement(9, 3)
    seq = extract.run(statement(9, 3), cfg(TXN_SCHEMA, array_extract=True), llm=FakeLLM(reader()),
                      options={"segment_pages": 2, "segment_overlap": 1})
    llm = ParallelFake(reader())
    par = extract.run(doc, cfg(TXN_SCHEMA, array_extract=True), llm=llm,
                      options={"segment_pages": 2, "segment_overlap": 1, "segment_workers": 4})
    assert len(llm.calls) == 8 and par["result"] == seq["result"]   # merged in document order whatever finished first
    assert [t["what"] for t in par["result"][0]["transactions"]] == [f"item{k}" for k in range(1, 28)]
    assert par["usage"]["num_fields"] == 27 * 3 + 2 and par["usage"]["extract_mode"] == "extract"


def test_identical_rows_on_different_pages_are_real_and_survive_dedup():
    doc = make_doc([["Txn 2025-01-01 coffee 4.50"], ["Txn 2025-01-01 coffee 4.50"], ["Txn 2025-01-02 tea 3.00"]])
    out = extract.run(doc, cfg(TXN_SCHEMA, array_extract=True), llm=FakeLLM(reader()),
                      options={"segment_pages": 2, "segment_overlap": 1})
    assert [t["what"] for t in out["result"][0]["transactions"]] == ["coffee", "coffee", "tea"]


def test_short_documents_are_not_segmented_and_long_text_is():
    assert len(extract._segments(DocView(statement(3)).pages, extract.DEFAULTS, force=True)) == 1
    pages = DocView(statement(20)).pages
    assert len(extract._segments(pages, extract.DEFAULTS, force=False)) == 1  # small text: one call
    assert len(extract._segments(pages, {**extract.DEFAULTS, "auto_segment_chars": 100}, force=False)) == 3
    # dense pages: the character budget makes segments shorter than segment_pages, still overlapping by one page
    dense = extract._segments(pages, {**extract.DEFAULTS, "segment_chars": 400}, force=True)
    assert all(len(seg) < 8 for seg in dense) and dense[0][-1].number == dense[1][0].number
    assert [p.number for seg in dense for p in seg][-1] == 20 and {p.number for seg in dense for p in seg} == set(range(1, 21))
    one_page_each = extract._segments(pages, {**extract.DEFAULTS, "segment_chars": 1}, force=True)
    assert [len(seg) for seg in one_page_each] == [1] * 20          # a page over the budget is still its own segment
    segs = extract._segments(pages, extract.DEFAULTS, force=True)
    assert [(s[0].number, s[-1].number) for s in segs] == [(1, 8), (8, 15), (15, 20)]


def test_unparseable_segment_is_halved_and_retried():
    doc = statement(4, 2)
    base = reader()

    def handler(system, user, i):
        if user.count("=== Page") > 2:
            return "sorry, the output was cut off {"
        return base(system, user, i)

    llm = FakeLLM(handler)
    out = extract.run(doc, cfg(TXN_SCHEMA, citations={"enabled": True}), llm=llm)
    assert [t["what"]["value"] for t in out["result"]["transactions"]] == [f"item{k}" for k in range(1, 9)]
    assert len(llm.calls) == 4  # 4 pages (2 attempts), then 2 + 2 pages


# ---------------------------------------------------------------- deep_extract

SUM_PROMPT = "Extract the statement. total must equal the sum of line_items.amount."


def test_sum_rules_are_parsed_from_the_system_prompt():
    st = S.enforced(INVOICE_SCHEMA)
    assert [(r.target, r.terms) for r in extract.parse_sum_rules(SUM_PROMPT, st)] == [
        (("total",), [(1, ("line_items", "*", "amount"))])]
    assert extract.parse_sum_rules("The sum of all line_items[].amount should equal `total`.", st)[0].target == ("total",)
    schema = S.enforced({"type": "object", "properties": {k: {"type": "number"} for k in ("total", "subtotal", "tax", "discount")}})
    (r,) = extract.parse_sum_rules("Check that total = subtotal + tax - discount.", schema)
    assert r.terms == [(1, ("subtotal",)), (1, ("tax",)), (-1, ("discount",))]
    assert extract.check_sum_rules({"total": 10, "subtotal": 9, "tax": 2, "discount": 1}, [r]) == []
    assert extract.check_sum_rules({"total": 11, "subtotal": 9, "tax": 2, "discount": 1}, [r])[0]["fields"] == [
        "discount", "subtotal", "tax", "total"]
    assert extract.check_sum_rules({"total": None, "subtotal": 9, "tax": 2, "discount": 1}, [r]) == []  # unverifiable
    assert extract.parse_sum_rules("grand_total equals the sum of widgets", st) == []  # names not in the schema
    assert extract.parse_sum_rules("Be precise and thorough.", st) == []


def test_deep_extract_reasks_only_the_failing_fields_and_fixes_them(invoice_doc):
    lab = labels_of(invoice_doc)
    table = next(v for k, v in lab.items() if k.startswith("<table"))
    items = [{"item": "Servo motor", "qty": 4, "amount": 400.0}, {"item": "Controller board", "qty": 2, "amount": 1100.0}]
    first = wrap(dict(GOOD, line_items=[]), None)
    first["total"] = {"v": 1575.0, "r": [lab["Total Due: $1,575.00"]]}
    first["line_items"] = wrap(items, table)                      # the Shipping row was missed
    second = {"total": {"v": 1575.0, "r": [lab["Total Due: $1,575.00"]]},
              "line_items": wrap(GOOD["line_items"], table)}
    llm = FakeLLM([first, second])
    out = extract.run(invoice_doc, cfg(system_prompt=SUM_PROMPT, deep_extract=True), llm=llm)
    assert len(llm.calls) == 2
    reask = llm.calls[1]["user"]
    sent_schema = json.loads(reask.split("JSON Schema:\n")[1].split("\n")[0])
    assert set(sent_schema["properties"]) == {"line_items", "total"}   # only the failing fields
    assert "terms add up to 1500.0" in reask
    assert out["result"] == [GOOD]            # deep mode tracks sources internally but returns plain values
    assert out["confidence"] == "high" and "passed" in out["confidence_reason"]


def test_deep_extract_is_bounded_and_reports_low_confidence_when_it_cannot_fix(invoice_doc):
    lab = labels_of(invoice_doc)
    table = next(v for k, v in lab.items() if k.startswith("<table"))
    bad = wrap(dict(GOOD, line_items=[]), None)
    bad["total"] = {"v": 1575.0, "r": [lab["Total Due: $1,575.00"]]}
    bad["line_items"] = wrap(GOOD["line_items"][:2], table)
    llm = FakeLLM([bad])  # the model never improves
    out = extract.run(invoice_doc, cfg(system_prompt=SUM_PROMPT, deep_extract=True), llm=llm,
                      options={"max_deep_iterations": 2})
    # one deep re-ask (it did not help, so the loop stops instead of burning calls), and one ask for the list,
    # which has 2 items while its table has 3 rows
    assert len(llm.calls) == 3
    assert out["confidence"] == "low" and "does not hold" in out["confidence_reason"]
    assert len(out["result"][0]["line_items"]) == 2  # the original answer is kept, not replaced by a guess
    assert any("2 items, but the table they come from has 3 rows" in w for w in out["warnings"])


def test_deep_extract_flags_values_that_are_not_in_their_cited_block(invoice_doc):
    lab = labels_of(invoice_doc)
    first = wrap(dict(GOOD, line_items=[]), None)
    first["customer"] = {"v": "Acme Robotix Limited", "r": [lab["Status: PAID"]]}   # hallucinated spelling
    fixed = {"customer": {"v": "Acme Robotics Ltd", "r": [lab["Billed to: Acme Robotics Ltd, 12 Harbour Road, Leeds"]]}}
    llm = FakeLLM([first, fixed])
    out = extract.run(invoice_doc, cfg(deep_extract=True), llm=llm)
    assert "customer: the value" in llm.calls[1]["user"]
    assert out["result"][0]["customer"] == "Acme Robotics Ltd" and out["confidence"] == "high"


def test_deep_extract_checks_item_counts_on_pages_shared_by_two_segments():
    doc = statement(3, 3)          # pages 1-2 and 2-3 share page 2 (items 4, 5, 6)
    full = reader()
    lazy = reader(skip=("item5",))
    calls = []

    def handler(system, user, i):
        calls.append(user)
        if "part 1 of 2" in user:
            return lazy(system, user, i)       # first segment misses item5 on the shared page
        return full(system, user, i)

    out = extract.run(doc, cfg(TXN_SCHEMA, deep_extract=True), llm=FakeLLM(handler),
                      options={"segment_pages": 2, "segment_overlap": 1})
    res = out["result"][0]
    assert [t["what"] for t in res["transactions"]] == [f"item{k}" for k in range(1, 10)]
    recount = [u for u in calls if "disagreed on how many items" in u]
    assert len(recount) == 1 and "=== Page 2 ===" in recount[0] and "=== Page 1 ===" not in recount[0]
    assert out["confidence"] == "high"


def test_non_deep_response_has_no_confidence(invoice_doc):
    out = extract.run(invoice_doc, cfg(), llm=FakeLLM([GOOD]))
    assert out["confidence"] is None and out["confidence_reason"] is None


def test_model_failure_is_a_502(invoice_doc):
    from readanydoc.tasks import LLMError
    with pytest.raises(TaskError) as e:
        extract.run(invoice_doc, cfg(), llm=FakeLLM([LLMError("boom")]))
    assert e.value.status_code == 502


def test_response_validates_against_the_openapi_schema(invoice_doc):
    from readanydoc import paths
    spec_path = paths.reference_dir() / "openapi.json"
    if not spec_path.exists():
        pytest.skip("reference/openapi.json not available")
    spec = json.loads(spec_path.read_text())
    lab = labels_of(invoice_doc)
    answer = wrap(dict(GOOD, line_items=[]), None)
    answer["total"] = {"v": 1575.0, "r": [lab["Total Due: $1,575.00"]]}
    for settings, reply in (({}, GOOD), ({"citations": {"enabled": True}}, answer), ({"deep_extract": True}, answer)):
        out = extract.run(invoice_doc, cfg(**settings), llm=FakeLLM([reply]))
        body = {**out, "job_id": "j", "duration": 0.1}
        Draft202012Validator({"$ref": "#/components/schemas/V3ExtractResponse", "components": spec["components"]}).validate(body)


def test_repeated_values_in_a_table_are_cited_in_their_own_row():
    table = ("<table><tr><td>Ann</td><td>Sales</td><td>100</td></tr>"
             "<tr><td>Bob</td><td>Sales</td><td>100</td></tr>"
             "<tr><td>Cy</td><td>Ops</td><td>250</td></tr></table>")
    doc = make_doc([[("Table", table)]])
    schema = {"type": "object", "properties": {"staff": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "dept": {"type": "string"}, "bonus": {"type": "number"}}}}}}
    rows = [{"name": "Ann", "dept": "Sales", "bonus": 100}, {"name": "Bob", "dept": "Sales", "bonus": 100}]
    res = extract.run(doc, cfg(schema, citations={"enabled": True}), llm=FakeLLM([{"staff": wrap(rows, "r1")}]))["result"]
    ann, bob = res["staff"]
    # "Sales" and "100" occur twice; each row's citation is the occurrence next to that row's unique name
    assert ann["dept"]["citations"][0]["content"] == "Sales" and bob["bonus"]["citations"][0]["content"] == "100"
    region = doc.pages[0].regions[0]
    starts = lambda c: next(w.char_start for w in region.iter_words()  # noqa: E731
                            if abs(w.bbox.left - c["bbox"]["left"]) < 1e-3)
    assert starts(ann["dept"]["citations"][0]) < table.index("Bob") < starts(bob["dept"]["citations"][0])
    assert starts(ann["bonus"]["citations"][0]) < table.index("Bob") < starts(bob["bonus"]["citations"][0])
    assert bob["dept"]["citations"][0]["granular_confidence"]["extract_confidence"] == 0.95


def test_page_range_refers_to_source_pages_even_on_a_pre_filtered_document():
    """The API parses only the requested pages and still passes page_range on: that must not double-filter."""
    doc = make_doc([["gamma page three"], ["delta page four"]])
    for page, original in zip(doc.pages, (3, 4)):
        page.original_page = original
    doc.meta["total_pages"] = 9
    schema = {"type": "object", "properties": {"word": {"type": "string"}}}
    llm = FakeLLM([{"word": {"v": "delta", "r": ["r2"]}}])
    out = extract.run(doc, cfg(schema, page_range={"start": 3, "end": 4}, citations={"enabled": True}), llm=llm)
    assert out["usage"]["num_pages"] == 2 and "delta page four" in llm.calls[0]["user"]
    box = out["result"]["word"]["citations"][0]["bbox"]
    assert box["page"] == 2 and box["original_page"] == 4
    out = extract.run(doc, cfg(schema, page_range=[4]), llm=FakeLLM([{"word": "delta"}]))
    assert out["usage"]["num_pages"] == 1
    with pytest.raises(TaskError):
        extract.run(doc, cfg(schema, page_range=[7]), llm=llm)   # a real page, but not in this parse


def test_numbers_printed_in_other_conventions_are_still_located():
    f = extract._find_number
    assert f("Insgesamt 19 670 22 585 2 915 14,8", 19670) == [(10, 16)]
    assert f("Insgesamt 19 670 22 585 2 915 14,8", 14.8) == [(30, 34)]
    assert f("Betrag 1.234,56 EUR", 1234.56) == [(7, 15)]
    assert f("Composition 104 pages 1 53 159 12", 159.12) == [(27, 33)]     # ledger: dollars and cents apart
    assert f("Press Work 2 50 35 00", 35) == [(16, 18)]                    # "35" itself is there
    assert f("Total 1,575.00 and 1 575", 1575) == [(6, 14)]                # the plain form wins when it is there
    assert f("pages 12 and 34", 12.34) == []                               # "12 and 34" is not 12.34


def test_sum_rule_ignores_rows_without_an_amount():
    schema = {"type": "object", "properties": {"total": {"type": "number"}, "line_items": {"type": "array", "items": {
        "type": "object", "properties": {"amount": {"type": ["number", "null"]}}}}}}
    rules = extract.parse_sum_rules("The total must equal the sum of line_items.amount.", schema)
    rows = [{"amount": 28.5}, {"amount": None}, {"amount": 159.12}]
    assert extract.check_sum_rules({"total": 187.62, "line_items": rows}, rules) == []
    (failure,) = extract.check_sum_rules({"total": 187.6, "line_items": rows}, rules)
    assert failure["kind"] == "arithmetic" and "187.62" in failure["message"]


def test_truncated_answer_is_never_salvaged_the_segment_is_halved_instead():
    from readanydoc.tasks.llm import LLMResult
    doc = statement(4, 3)
    good = reader()

    def handler(system, user, i):
        if "=== Page 1 ===" in user and "=== Page 4 ===" in user:   # all four pages at once: too long, cut off
            return LLMResult(text='{"account": {"v": "0012-3456", "r": ["r1"]}, "transactions": [{"date": {"v": "2025-01-01", "r": ["r2"]}',
                             data={"v": "2025-01-01", "r": ["r2"]}, stop_reason="max_tokens", model="fake")
        return good(system, user, i)

    llm = FakeLLM(handler)
    out = extract.run(doc, cfg(TXN_SCHEMA, citations={"enabled": True}), llm=llm)
    assert len(llm.calls) == 3                                      # the whole document, then its two halves
    res = out["result"]
    assert [t["what"]["value"] for t in res["transactions"]] == [f"item{k}" for k in range(1, 13)]
    assert res["account"]["value"] == "0012-3456" and res["closing_balance"]["value"] == 120.0


GROUP_SCHEMA = {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "object", "properties": {
    "state": {"type": ["string", "null"]}, "area": {"type": "string"}, "count": {"type": "integer"}}}}}}


def test_group_heading_from_an_earlier_segment_is_carried_into_the_next():
    # the state heading is printed once, on page 1; pages 3-4 belong to the next segment
    doc = make_doc([["State: Bavaria", "Area A 10"], ["Area B 20"], ["Area C 30"], ["State: Hesse", "Area D 40"]])
    row = re.compile(r"\[(r\d+)\|Text\] Area (\w) (\d+)")

    def handler(system, user, i):
        text = user.split("JSON Schema:")[0]
        carried = re.search(r'"state": "(\w+)"', user.split("Context from the preceding pages")[1]) \
            if "Context from the preceding pages" in user else None
        state = carried.group(1) if carried else None
        rows = []
        for line in text.splitlines():
            m = re.search(r"State: (\w+)", line)
            if m:
                state = m.group(1)
            m = row.search(line)
            if m:
                rows.append({"state": {"v": state, "r": [m.group(1)]}, "area": {"v": m.group(2), "r": [m.group(1)]},
                             "count": {"v": int(m.group(3)), "r": [m.group(1)]}})
        return {"rows": rows}

    llm = FakeLLM(handler)
    out = extract.run(doc, cfg(GROUP_SCHEMA, array_extract=True), llm=llm, options={"segment_pages": 2, "segment_overlap": 0})
    assert [(r["state"], r["area"]) for r in out["result"][0]["rows"]] == [
        ("Bavaria", "A"), ("Bavaria", "B"), ("Bavaria", "C"), ("Hesse", "D")]
    assert len(llm.calls) == 3 and "do not repeat these items" in llm.calls[2]["user"]   # only segment 2 was asked again
    assert extract.needs_carry({"rows": [{"state": "X", "area": "A"}]}, {"rows": [{"state": "Y", "area": "B"}]}) is False


def test_settle_boxes_keeps_one_box_per_printed_word():
    """Pieces of one printed word ("01/01/2025") share the box of the rarest piece, so a repeated piece
    matched to the neighbouring date does not stretch the citation over both dates."""
    from types import SimpleNamespace as NS
    from readanydoc.tasks.extract import _settle_boxes
    left, right = NS(left=0.55, top=0.41, width=0.056, height=0.006), NS(left=0.63, top=0.41, width=0.056, height=0.006)
    content = "01/01/2025 01/01/2026"
    mk = lambda t, a, b, box: NS(text=t, char_start=a, char_end=b, bbox=box)
    first = [mk("01", 0, 2, right), mk("01", 3, 5, right), mk("2025", 6, 10, left)]
    second = [mk("01", 11, 13, right), mk("01", 14, 16, right), mk("2026", 17, 21, right)]
    region = NS(iter_words=lambda: first + second)
    assert _settle_boxes(first, content, region) == [left]
    assert _settle_boxes(first + second, content, region) == [left, right]  # whitespace separates printed words
