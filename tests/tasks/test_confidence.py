"""Calibrated confidence (tasks/confidence.py): conflicting values are marked low, others keep a measured score."""
from readanydoc.tasks import confidence


def _cite(raw):
    return [{"confidence": "high", "granular_confidence": {"extract_confidence": raw, "parse_confidence": 0.9}}]


def test_conflict_found_only_for_same_thing():
    v = {"revenue": [{"value": "4816", "period": "Q2"}, {"value": "4517", "period": "Q2"}, {"value": "4400", "period": "Q1"}]}
    assert confidence.conflicts(v) == {("revenue", 0, "value"), ("revenue", 1, "value")}


def test_calibrate_marks_conflicts_low_and_keeps_others_high():
    v = {"revenue": [{"value": "4816", "period": "Q2"}, {"value": "4517", "period": "Q2"}, {"value": "4400", "period": "Q1"}]}
    cites = {("revenue", 0, "value"): _cite(0.95), ("revenue", 1, "value"): _cite(0.9), ("revenue", 2, "value"): _cite(0.9)}
    confidence.calibrate(v, cites)
    assert [cites[("revenue", i, "value")][0]["confidence"] for i in range(3)] == ["low", "low", "high"]
    assert cites[("revenue", 0, "value")][0]["granular_confidence"]["extract_confidence"] == 0.65


def test_unmeasured_scores_untouched_and_labels_never_raised():
    low = [{"confidence": "low", "granular_confidence": {"extract_confidence": 0.9}}]  # box uncertain: stays low
    cites = {("a",): _cite(0.85), ("b",): [{"confidence": "high", "granular_confidence": {"extract_confidence": None}}],
             ("c",): low}
    confidence.calibrate({"a": "x", "b": "y", "c": "z"}, cites)
    assert cites[("a",)][0]["granular_confidence"]["extract_confidence"] == 0.85
    assert cites[("b",)][0]["confidence"] == "high"
    assert cites[("c",)][0]["confidence"] == "low"


def test_conflict_with_unmeasured_score_is_capped():
    v = {"x": [{"n": "1", "k": "a"}, {"n": "2", "k": "a"}]}
    cites = {("x", 0, "n"): _cite(0.85), ("x", 1, "n"): _cite(0.6)}
    confidence.calibrate(v, cites)
    assert cites[("x", 0, "n")][0]["granular_confidence"]["extract_confidence"] == confidence.CONFLICT
    assert cites[("x", 1, "n")][0]["granular_confidence"]["extract_confidence"] == 0.6
