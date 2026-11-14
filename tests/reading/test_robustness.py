"""Workstream A: broken-read detection and the retry policy, with fake readers (no model calls)."""

from readanydoc.pipeline.verify import (RetryPolicy, detect_problems, find_repetition, read_with_retry,
                                      trim_repetition)
from readanydoc.readers.base import Reader, ReaderError, ReadResult

GOOD = "A normal paragraph of text.\n\nAnother paragraph with different words in it."
LOOP = "Intro line.\n" + "\n".join(["The same row again and again"] * 30)


class FakeReader(Reader):
    name = "fake"
    needs_image = False

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def read(self, image=None, hint=None, context=None):
        self.calls += 1
        out = self.outputs[min(self.calls - 1, len(self.outputs) - 1)]
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, ReadResult) else ReadResult(text=out, stop_reason="end_turn")


def kinds(problems):
    return [p.kind for p in problems]


def test_detects_empty_refusal_truncation():
    assert kinds(detect_problems("")) == ["empty"]
    assert kinds(detect_problems("   \n")) == ["empty"]
    assert kinds(detect_problems("x", refused=True)) == ["refusal"]
    assert kinds(detect_problems("I'm sorry, I can't read this image.")) == ["refusal"]
    assert kinds(detect_problems(GOOD, stop_reason="max_tokens")) == ["truncated"]
    assert kinds(detect_problems(GOOD, stop_reason="error")) == ["error"]
    unclosed = detect_problems("<table><tr><td>a</td></tr>")
    assert kinds(unclosed) == ["truncated"] and not unclosed[0].fatal
    assert detect_problems(GOOD) == []


def test_detects_repetition_loops_but_not_layout():
    rep = find_repetition(LOOP)
    assert rep and rep[1] == "The same row again and again" and rep[2] == 30
    assert find_repetition("word " * 200)  # in-line loop
    assert find_repetition(GOOD) is None
    assert find_repetition("&nbsp;" * 80) is None  # indentation
    assert find_repetition("-" * 500) is None and find_repetition(". " * 300) is None  # rules, leaders
    assert find_repetition("\n".join(f"Row {i} | {i * 3}" for i in range(60))) is None  # a real table
    assert kinds(detect_problems(LOOP)) == ["repetition"]


def test_trim_keeps_the_first_copy_only():
    out = trim_repetition(LOOP)
    assert out.count("The same row again and again") == 1 and out.startswith("Intro line.")
    assert trim_repetition(GOOD) == GOOD
    assert len(trim_repetition("start " + "word " * 200)) < 30


def test_incomplete_read_is_flagged_not_fatal():
    probs = detect_problems(GOOD, evidence_recall=0.3, n_evidence=500)
    assert kinds(probs) == ["incomplete"] and not probs[0].fatal
    assert detect_problems(GOOD, evidence_recall=0.3, n_evidence=10) == []  # too little evidence to judge
    assert detect_problems(GOOD, evidence_recall=0.9, n_evidence=500) == []


def test_good_read_is_accepted_at_once():
    r = FakeReader([GOOD])
    res, log = read_with_retry(r, None)
    assert res.text == GOOD and r.calls == 1 and [a.action for a in log] == ["accept"]


def test_empty_then_good_retries():
    r = FakeReader(["", GOOD])
    res, log = read_with_retry(r, None)
    assert res.text == GOOD and r.calls == 2 and [a.action for a in log] == ["retry", "accept"]


def test_reader_error_then_good_retries():
    r = FakeReader([ReaderError("timeout"), GOOD])
    res, log = read_with_retry(r, None)
    assert res.text == GOOD and log[0].problems[0].kind == "error"


def test_loop_that_repeats_identically_is_trimmed_without_more_calls():
    r = FakeReader([LOOP])  # a deterministic (cached) reader returns the same loop again
    res, log = read_with_retry(r, None)
    assert r.calls == 2 and [a.action for a in log] == ["retry", "trim"]
    assert res.truncated and res.text.count("The same row again and again") == 1


def test_loop_then_good_uses_the_good_read():
    r = FakeReader([LOOP, GOOD])
    res, _ = read_with_retry(r, None)
    assert res.text == GOOD


def test_attempts_are_bounded_and_fall_back():
    r = FakeReader(["", " ", "\n", "\t", GOOD])
    res, log = read_with_retry(r, None, policy=RetryPolicy(max_attempts=3),
                               fallback=lambda: ReadResult(text="text layer words"))
    assert r.calls == 3 and res.text == "text layer words" and log[-1].reader == "fallback"


def test_no_fallback_gives_none():
    r = FakeReader([ReadResult(text="", stop_reason="error")] * 5)
    res, log = read_with_retry(r, None, policy=RetryPolicy(max_attempts=2))
    assert res is None and r.calls == 2 and log[-1].action == "fallback"


def test_refusal_goes_to_fallback_after_one_retry():
    r = FakeReader([ReadResult(text="", refused=True, stop_reason="refusal")] * 5)
    res, _ = read_with_retry(r, None, fallback=lambda: ReadResult(text="next tier"))
    assert r.calls == 2 and res.text == "next tier"
    assert RetryPolicy(use_fallback=False).next_action(2, detect_problems("", refused=True)).kind == "give_up"


def test_truncated_output_is_kept_after_retries():
    r = FakeReader([ReadResult(text=GOOD, stop_reason="max_tokens", truncated=True),
                    ReadResult(text=GOOD + " more", stop_reason="max_tokens", truncated=True),
                    ReadResult(text=GOOD + " most", stop_reason="max_tokens", truncated=True)])
    res, log = read_with_retry(r, None)
    assert r.calls == 3 and res.truncated and res.text == GOOD + " most" and log[-1].action == "trim"


def test_custom_check_overrides_detection():
    from readanydoc.pipeline.verify import Problem
    r = FakeReader(["short", GOOD])
    res, _ = read_with_retry(r, None, check=lambda x: [Problem("empty", "too short")] if len(x.text) < 10 else [])
    assert res.text == GOOD


def test_empty_table_cells_are_not_a_loop():
    form = "<table>" + "<tr>" + "<td></td>" * 40 + "</tr>" * 1 + "</table>"
    assert find_repetition(form) is None
    assert find_repetition("<table>" + "<tr><td></td><td>.</td></tr>" * 30 + "</table>") is None
    assert find_repetition("<table>" + "<tr><td>same</td></tr>" * 30 + "</table>") is not None


def test_repeated_cells_in_a_complete_table_are_never_cut():
    ledger = "<table>" + "<tr><td>[illegible]</td><td>do</td></tr>" * 30 + "</table>"
    probs = detect_problems(ledger)
    assert kinds(probs) == ["repetition"] and not probs[0].fatal
    r = FakeReader([ledger])
    res, log = read_with_retry(r, None)
    assert res.text == ledger and r.calls == 1 and log[0].action == "accept"
    cut = "<table>" + "<tr><td>same row</td></tr>" * 40  # never closed: the model ran out of tokens in a loop
    assert any(p.kind == "repetition" and p.fatal for p in detect_problems(cut))
