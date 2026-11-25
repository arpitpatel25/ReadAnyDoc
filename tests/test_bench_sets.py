"""New bench pieces: the SEC XBRL scorer, per-case scorers, the confidence check, the replay flags. No network."""
import pytest

from readanydoc.bench import grade, runner, sec
from readanydoc.cli import main

GOLD = {"facts": [{"concept": "us-gaap:Revenues", "val": 4816000000}, {"concept": "us-gaap:Revenues", "val": 4386000000},
                  {"concept": "us-gaap:NetIncomeLoss", "val": 1771000000}]}


def test_sec_score_found_and_right():
    data = {"income_statement": {
        "revenue": [{"value": "4,816", "scale": 1000000, "segment_type": "company"},
                    {"value": "4,517", "scale": 1000000, "segment_type": "company"}],  # a sub-line: not tagged
        "net_income": [{"value": "(1,771)", "scale": 1000000}]}}
    s = sec.score(GOLD, data)
    assert (s["right"], s["expected"]) == (2, 3)  # 4,386 not found
    assert s["written"] == 3 and abs(s["precision"] - 2 / 3) < 1e-9
    assert [f["ok"] for f in s["fields"] if f["path"] == "revenue"] == [False, True]


def test_sec_facts_of_picks_one_filing():
    cf = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [{"accn": "A", "val": 5, "end": "2023-06-30"},
                                                             {"accn": "B", "val": 6, "end": "2023-03-31"}]}}}}}
    assert [f["val"] for f in sec.facts_of(cf, "A")] == [5]


def test_score_case_dispatch():
    assert grade.score_case({"gold": GOLD, "scorer": "sec"}, {})["expected"] == 3
    assert grade.score_case({"gold": {"a": "x"}}, {"a": "x"})["right"] == 1


def test_confidence_check():
    recs = [{"citations": {"a": [{"confidence": "high"}], "b": [{"confidence": "low"}], "c": [{"confidence": "low"}]},
             "score": {"fields": [{"path": "a", "got": 1, "ok": True}, {"path": "b", "got": 1, "ok": False},
                                  {"path": "c", "got": 1, "ok": True}]}}]
    c = runner.confidence_check(recs)
    assert c["high_right"] == 1.0 and c["low_right"] == 0.5 and c["low_values"] == 2
    assert runner.confidence_check([{"score": {"fields": []}}]) is None


def test_replay_only_needs_a_folder(capsys):
    assert main(["bench", "run", "invoices", "--replay-only", "--yes"]) == 2
    assert "--replay DIR" in capsys.readouterr().out
