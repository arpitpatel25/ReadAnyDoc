"""readanydoc bench: grading, running, resuming, budget and report, with fake systems (no network, no key)."""

from __future__ import annotations

import json

import pytest

from readanydoc.bench import catalog, grade, report, runner
from readanydoc.bench.systems import System, _unwrap, make


@pytest.mark.parametrize("gold,pred,ok", [
    (1575.0, "$1,575.00", True), (60000, "60.000", True), (-200, "(200)", True), (-1234.5, "R -1,234.50", True),
    ("AC-87051", "AC-87051", True), ("AC-87051", "AC-87052", False), (10406.52, 10500, False),
    ("2026-03-09", "09/03/2026", True), ("2026-03-09", "March 9, 2026", True), ("2026-03-09", "2026-03-10", False),
    ("Calanque Hardware SARL", "CALANQUE HARDWARE S.A.R.L.", True), (True, "yes", True), (None, "", True),
])
def test_values_are_compared_by_meaning_not_by_printed_form(gold, pred, ok):
    assert grade.same(gold, pred) is ok


def test_list_items_are_paired_before_scoring_and_extras_lower_precision():
    gold = {"total": 10, "lines": [{"d": "bolts", "amount": 1}, {"d": "washers", "amount": 2}]}
    pred = {"total": "10.00", "lines": [{"d": "Washers", "amount": 2}, {"d": "bolts", "amount": 1},
                                        {"d": "glue", "amount": 9}]}
    s = grade.score(gold, pred)
    assert s["recall"] == 1.0 and s["exact"] and s["written"] == 7 and s["precision"] == pytest.approx(5 / 7)
    assert grade.score(gold, None)["recall"] == 0.0  # a failed document is an empty answer


def test_reducto_style_values_are_unwrapped_with_their_citations():
    box = {"page": 1, "left": 0.1, "top": 0.2, "width": 0.1, "height": 0.02}
    cites: dict = {}
    data = _unwrap({"total": {"value": 5, "citations": [{"content": "5", "bbox": box}]},
                    "lines": [{"amount": {"value": 2, "citations": []}}]}, "", cites)
    assert data == {"total": 5, "lines": [{"amount": 2}]} and cites["total"][0]["bbox"]["left"] == 0.1


class Fake(System):
    est_per_page = 0.01

    def __init__(self, name, answer, cost=0.01):
        self.name, self.answer, self.cost, self.calls = name, answer, cost, 0

    def run(self, case):
        self.calls += 1
        return {"data": self.answer(case), "citations": {}, "cost_usd": self.cost, "cost_note": "measured",
                "seconds": 1.0, "error": None}


def _cases(tmp_path, n=3):
    out = []
    for i in range(n):
        f = tmp_path / f"d{i}.png"
        f.write_bytes(b"x")
        out.append({"id": f"d{i}", "file": str(f), "schema": {"type": "object"}, "gold": {"total": i + 1}})
    return out


def test_a_run_scores_resumes_and_writes_a_report(tmp_path):
    cases = _cases(tmp_path)
    good, bad = Fake("good", lambda c: c["gold"]), Fake("bad", lambda c: {"total": 99})
    out = str(tmp_path / "run")
    s = runner.run(cases, [good, bad], out, workers=2)
    assert s["systems"]["good"]["recall"] == 1.0 and s["systems"]["bad"]["recall"] == 0.0
    assert s["systems"]["good"]["cost_per_document_usd"] == pytest.approx(0.01)
    runner.run(cases, [good, bad], out)  # resumes: nothing is run twice
    assert good.calls == 3 and bad.calls == 3
    page = report.write(out, cases, ["good", "bad"], "t", s)
    assert "good" in open(page).read() and (tmp_path / "run" / "docs" / "d0.html").exists()


def test_a_budget_stops_new_documents(tmp_path):
    cases = _cases(tmp_path, 6)
    spender = Fake("spender", lambda c: c["gold"], cost=1.0)
    runner.run(cases, [spender], str(tmp_path / "b"), budget_usd=2.0, workers=1)
    assert spender.calls == 2


def test_own_documents_take_an_optional_answer_key(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "keys").mkdir()
    for n in ("a", "b"):
        (tmp_path / "docs" / f"{n}.pdf").write_bytes(b"%PDF")
    (tmp_path / "keys" / "a.json").write_text(json.dumps({"x": 1}))
    (tmp_path / "s.json").write_text(json.dumps({"type": "object", "properties": {"x": {"type": "number"}}}))
    cases = catalog.own_documents(str(tmp_path / "docs"), str(tmp_path / "s.json"), str(tmp_path / "keys"))
    assert [c["gold"] for c in cases] == [{"x": 1}, None]


def test_every_listed_system_can_be_built_and_names_its_key():
    for spec in ("readanydoc", "readanydoc-one-stage", "readanydoc-model-only", "plain:claude-haiku-5-5", "reducto"):
        s = make(spec)
        assert s.key_env in ("ANTHROPIC_API_KEY", "REDUCTO_API_KEY")
    with pytest.raises(KeyError):
        make("nope")


def test_the_catalogue_lists_licence_and_source_for_every_set():
    for s in catalog.SETS.values():
        assert s.licence and s.source.startswith("https://") and s.documents > 0 and s.default_limit > 0


def test_small_invoice_sample_covers_hard_input_types():
    from readanydoc.bench.catalog import _spread
    meta = [{"category": c, "difficulty": d, "doc_id": f"{c}-{d}-{i}"}
            for c in ["clean", "credit", "currency", "photo", "scan", "handwritten"]
            for d in ["easy", "medium", "hard"] for i in range(2)]
    three = _spread(meta, 3)
    assert [m["category"] for m in three] == ["photo", "scan", "handwritten"]
    assert len({m["difficulty"] for m in three}) == 3
    every = _spread(meta, len(meta))
    assert sorted(m["doc_id"] for m in every) == sorted(m["doc_id"] for m in meta)
    assert {m["category"] for m in _spread(meta, 6)} == {"clean", "credit", "currency", "photo", "scan", "handwritten"}


def test_schema_validity_strict_and_with_null_for_missing():
    from readanydoc.bench import grade as G
    schema = {"type": "object", "required": ["a"], "properties": {
        "a": {"type": "string"}, "b": {"type": "array", "items": {"type": "object", "properties": {
            "c": {"type": "string", "enum": ["x"]}}}}}}
    out = {"a": None, "b": [{"c": None}]}
    assert G.schema_errors(out, schema)  # strict: null is not a string
    assert G.schema_errors(out, schema, nulls_ok=True) == []
    assert G.schema_errors({"a": 1}, schema, nulls_ok=True)  # a wrong type is still an error
    assert G.schema_errors({}, schema, nulls_ok=True)  # a missing required field too
    assert G.schema_errors(None, schema) == ["no output"]


def test_cuad_answer_key_from_the_lawyers_sheet():
    from readanydoc.bench import catalog as C
    row = {"Document Name-Answer": "DISTRIBUTOR AGREEMENT",
           "Parties-Answer": 'Acme Corp ("Supplier"); Beta Ltd. ("Distributor"); the Company',
           "Agreement Date-Answer": "2/10/14", "Effective Date-Answer": "Perpetual",
           "Expiration Date-Answer": "12/31/1999", "Governing Law-Answer": "Nevada",
           "Non-Compete-Answer": "Yes", "Exclusivity-Answer": "No"}
    g = C._cuad_gold(row)
    assert g["document_name"] == "DISTRIBUTOR AGREEMENT"
    assert g["parties"] == ["Acme Corp", "Beta Ltd."]
    assert g["agreement_date"] == "2014-02-10" and g["expiration_date"] == "1999-12-31"
    assert "effective_date" not in g  # not a plain value: not scored
    assert g["clauses"] == {"non_compete": True, "exclusivity": False}
    assert C.SETS["contracts"].documents == 510
