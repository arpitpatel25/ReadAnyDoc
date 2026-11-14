"""Parse Judge port: prompt fidelity, blinding, robust parsing, cache, summary. No live calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from readanydoc_research.bench.judge import core, prompt as P, run as jrun, summary as S
from readanydoc_research.bench.judge.core import Judge, JudgeError, Verdict
from readanydoc_research.bench.judge.sync_prompt import DEFAULT_SOURCE, template_from_ts

PARSEJUDGE = DEFAULT_SOURCE.parents[3]


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("READANYDOC_JUDGE_CACHE", str(tmp_path / "judge_cache"))


@pytest.fixture
def page(tmp_path):
    f = tmp_path / "page1.png"
    Image.new("RGB", (400, 500), "white").save(f)
    return f


class FakeJudge:
    """Scores each parse by a number written in it ("score=7"); remembers every request."""

    def __init__(self, wrap=None, errors=None):
        self.requests, self.wrap, self.errors = [], wrap, errors or {}

    def __call__(self, req):
        self.requests.append(req)
        evs = []
        for label in req.labels:
            body = req.prompt.split(f"## {label}\n\n````text\n", 1)[1].split("\n````", 1)[0]
            score = int(body.split("score=")[1][:2].strip()) if "score=" in body else 5
            evs.append({"label": label, "score": score, "reasoning": f"{label} looks like {score}",
                        "errors": self.errors.get(body, [])})
        out = {"page_features": ["table"], "evaluations": evs}
        return {"output": self.wrap(out) if self.wrap else out, "meta": {"cost_usd": 0.01}}


HIGH = {"severity": "high", "category": "table_content", "visual_region_type": "table",
        "location_hint": "row 2", "description": "parse_a wrote '1,204' but the page shows '1,284'."}
LOW = {"severity": "low", "category": "text_corruption", "visual_region_type": "paragraph",
       "location_hint": "line 1", "description": "missing comma"}


# ---------------------------------------------------------------------------------- prompt fidelity

def test_template_hash_is_pinned():
    assert P.template_sha256() == P.TEMPLATE_SHA256
    assert P.JUDGE_VERSION == "compare-v4"


@pytest.mark.skipif(not DEFAULT_SOURCE.exists(), reason="benchmarks/parsejudge not present")
def test_prompt_matches_parsejudge_source():
    ts = DEFAULT_SOURCE.read_text(encoding="utf-8")
    assert template_from_ts(ts) == P.template()
    judge_ts = (DEFAULT_SOURCE.parent / "judge.ts").read_text(encoding="utf-8")
    assert f'JUDGE_VERSION = "{P.JUDGE_VERSION}"' in judge_ts
    domain_ts = (DEFAULT_SOURCE.parent / "domain.ts").read_text(encoding="utf-8")
    block = domain_ts.split("export const JUDGED_CATEGORIES = [", 1)[1].split("]", 1)[0]
    assert tuple(x.strip().strip('",') for x in block.strip().splitlines()) == P.CATEGORIES
    sev = judge_ts.split("const JUDGED_SEVERITIES = [", 1)[1].split("]", 1)[0]
    assert tuple(x.strip().strip('"') for x in sev.split(",")) == P.SEVERITIES
    head = PARSEJUDGE / ".git" / "HEAD"
    if head.exists():
        ref = head.read_text().strip()
        commit = (PARSEJUDGE / ".git" / ref[5:]).read_text().strip() if ref.startswith("ref: ") and \
            (PARSEJUDGE / ".git" / ref[5:]).exists() else ref
        if len(commit) == 40:
            assert commit == P.SOURCE_COMMIT


def test_prompt_assembly_is_exact():
    t = P.template()
    sections = P.parse_sections(["parse_a", "parse_b"], {"parse_a": "A `x` ${labels}", "parse_b": "B"})
    assert sections == "## parse_a\n\n````text\nA `x` ${labels}\n````\n\n## parse_b\n\n````text\nB\n````"
    out = P.compare_prompt(sections, "parse_a, parse_b")
    head, rest = t.split("${parseSections}")
    mid, tail = rest.split("${labels}")
    assert out == head + sections + mid + "parse_a, parse_b" + tail
    assert out.startswith("You are evaluating multiple text extractions of one PDF page.")
    assert "- `checkbox_state_error`: A genuine checkbox" in out and "\\`" not in out
    assert out.endswith("include the label, the list of errors as described above, concise reasoning, and the 1-10 score.\n")


def test_schema_matches_response_format():
    s = P.response_schema(["parse_a", "parse_b"])
    ev = s["properties"]["evaluations"]["items"]
    assert ev["properties"]["label"]["enum"] == ["parse_a", "parse_b"]
    assert ev["properties"]["score"] == {"type": "integer", "minimum": 1, "maximum": 10}
    err = ev["properties"]["errors"]["items"]
    assert err["required"] == ["severity", "category", "visual_region_type", "location_hint", "description"]
    assert err["properties"]["severity"]["enum"] == ["high", "low", "figure_error"]
    assert len(err["properties"]["category"]["enum"]) == 13


# ---------------------------------------------------------------------------------------- blinding

def test_blind_labels_follow_sha256_order():
    systems = ["ours", "reducto_r1", "reducto_r1mini"]
    m = core.blind_labels("page-7", systems)
    expect = sorted(systems, key=lambda s: hashlib.sha256(f"page-7:{s}".encode()).hexdigest())
    assert list(m) == ["parse_a", "parse_b", "parse_c"] and list(m.values()) == expect
    assert core.blind_labels("page-7", list(reversed(systems))) == m          # input order is irrelevant
    orders = {tuple(core.blind_labels(f"p{i}", systems).values()) for i in range(40)}
    assert len(orders) > 1                                                     # shuffled across pages


def test_no_system_name_reaches_the_judge(page):
    fake = FakeJudge()
    Judge(fake).judge_page("p1", page, {"ours": "score=9", "reducto_r1": "score=4"})
    text = fake.requests[0].prompt
    assert "ours" not in text.split("## Parses to score")[1].split("## Instructions")[0].replace("score=", "")
    assert "reducto" not in text


def test_unblind_in_reasoning_and_errors(page):
    md = {"ours": "score=9", "reducto_r1": "score=4"}
    labels = core.blind_labels("p1", list(md))
    a_system = labels["parse_a"]
    fake = FakeJudge(errors={md[a_system]: [HIGH]})
    j = Judge(fake).judge_page("p1", page, md)
    assert j.verdicts["ours"].score == 9 and j.verdicts["reducto_r1"].score == 4
    assert j.verdicts[a_system].errors[0]["description"].startswith(f"{a_system} wrote")
    assert j.verdicts[a_system].reasoning.startswith(a_system)
    assert j.verdicts[a_system].has_high


# ----------------------------------------------------------------------------------- robust parsing

GOOD = {"page_features": [], "evaluations": [{"label": "parse_a", "score": 8, "reasoning": "r", "errors": [LOW]}]}


@pytest.mark.parametrize("wrap", [
    lambda d: json.dumps(d),
    lambda d: "```json\n" + json.dumps(d, indent=2) + "\n```",
    lambda d: "Here is my evaluation {as requested}:\n\n" + json.dumps(d) + "\n\nLet me know if you need more.",
    lambda d: 'Note {"x": 1} first.\n```\n' + json.dumps(d) + "\n```",
])
def test_extract_json_variants(wrap):
    assert core.extract_json(wrap(GOOD)) == GOOD


def test_extract_json_handles_braces_inside_strings():
    d = json.loads(json.dumps(GOOD))
    d["evaluations"][0]["reasoning"] = 'the cell shows "}{" and a \\ backslash'
    assert core.extract_json("answer: " + json.dumps(d)) == d


@pytest.mark.parametrize("text", ["", "I cannot read the image.", '{"evaluations": [', '{"other": 1}'])
def test_extract_json_rejects_garbage(text):
    with pytest.raises(JudgeError):
        core.extract_json(text)


def _ev(**kw):
    return {"evaluations": [{"label": "parse_a", "score": 8, "reasoning": "r", "errors": [], **kw}],
            "page_features": []}


@pytest.mark.parametrize("raw", [
    _ev(score=11), _ev(score=0), _ev(score=7.5), _ev(score=None), _ev(score=True),
    _ev(errors=[{**LOW, "severity": "medium"}]), _ev(errors=[{"severity": "low", "category": "other"}]),
    _ev(label="parse_z"), {"evaluations": "none"},
])
def test_validate_hard_failures(raw):
    with pytest.raises(JudgeError):
        core.validate(raw, {"parse_a": "ours"})


def test_validate_repairs_are_reported():
    raw = _ev(score="9", errors=[{**LOW, "category": "typo", "severity": "LOW"}])
    del raw["page_features"]
    verdicts, features, repairs = core.validate(raw, {"parse_a": "ours"})
    assert verdicts["ours"].score == 9 and verdicts["ours"].errors[0]["category"] == "other"
    assert verdicts["ours"].errors[0]["severity"] == "low" and features == []
    assert len(repairs) == 3


def test_missing_parse_fails_then_retry_succeeds(page):
    calls = []

    def flaky(req):
        calls.append(1)
        if len(calls) == 1:
            return {"output": {"page_features": [], "evaluations": [
                {"label": "parse_a", "score": 5, "reasoning": "", "errors": []}]}}
        return FakeJudge()(req)
    judge = Judge(flaky)
    j = judge.judge_page("p1", page, {"a": "score=3", "b": "score=6"})
    assert judge.calls == 2 and {v.score for v in j.verdicts.values()} == {3, 6}
    with pytest.raises(JudgeError, match="did not score every parse"):
        Judge(lambda req: {"output": {"page_features": [], "evaluations": []}}, retries=1).judge_page(
            "p2", page, {"a": "x"})


def test_fenced_answer_is_flagged_not_well_formed(page):
    j = Judge(FakeJudge(wrap=lambda d: "```json\n" + json.dumps(d) + "\n```")).judge_page("p1", page, {"a": "score=7"})
    assert j.verdicts["a"].score == 7 and not j.well_formed


# ------------------------------------------------------------------------------------------- cache

def test_cache_pays_once_and_keys_on_everything(page, tmp_path):
    fake = FakeJudge()
    md = {"ours": "score=9", "r1": "score=4"}
    j1 = Judge(fake).judge_page("p1", page, md)
    j2 = Judge(fake).judge_page("p1", page, dict(reversed(list(md.items()))))   # new object, other order
    assert len(fake.requests) == 1 and j2.cached and not j1.cached
    assert {s: v.score for s, v in j2.verdicts.items()} == {"ours": 9, "r1": 4}
    Judge(fake).judge_page("p1", page, {**md, "r1": "score=5"})                 # a parse changed
    Judge(fake, model="claude-opus-5").judge_page("p1", page, md)               # judge model changed
    Judge(fake).judge_page("p2", page, md)                                      # page id changed
    other = tmp_path / "other.png"
    Image.new("RGB", (400, 500), "black").save(other)
    Judge(fake).judge_page("p1", other, md)                                     # page image changed
    assert len(fake.requests) == 5
    Judge(fake).judge_page("p1", page, md)
    assert len(fake.requests) == 5


def test_cache_key_includes_judge_version(page, monkeypatch):
    fake = FakeJudge()
    Judge(fake).judge_page("p1", page, {"a": "score=7"})
    monkeypatch.setattr(P, "JUDGE_VERSION", "compare-v5")
    Judge(fake).judge_page("p1", page, {"a": "score=7"})
    assert len(fake.requests) == 2


def test_budget_stops_live_calls(page):
    fake = FakeJudge()
    judge = Judge(fake, max_calls=1)
    judge.judge_page("p1", page, {"a": "score=7"})
    judge.judge_page("p1", page, {"a": "score=7"})          # cached: free
    with pytest.raises(core.BudgetExceeded):
        judge.judge_page("p2", page, {"a": "score=7"})
    assert len(fake.requests) == 1


def test_cli_judge_command_shape(monkeypatch, page):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        body = {"page_features": [], "evaluations": [{"label": "parse_a", "score": 6, "reasoning": "", "errors": []}]}

        class R:
            returncode, stderr = 0, ""
            stdout = json.dumps({"result": "", "structured_output": body, "total_cost_usd": 0.02, "usage": {}})
        return R()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-stripped")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    j = Judge().judge_page("p1", page, {"a": "hello"})
    assert j.verdicts["a"].score == 6 and j.model == "claude-sonnet-5-5"
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read" and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == '{"mcpServers":{}}' and cmd[cmd.index("--output-format") + 1] == "json"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == P.response_schema(["parse_a"])
    assert seen["cwd"] == "/tmp" and "ANTHROPIC_API_KEY" not in seen["env"] and "CLAUDECODE" not in seen["env"]
    prompt = P.compare_prompt(P.parse_sections(["parse_a"], {"parse_a": "hello"}), "parse_a")
    assert prompt in seen["input"] and str(page.resolve()) in seen["input"]


# ----------------------------------------------------------------------------------------- summary

def _results():
    v = lambda s, score, errs=(): Verdict(s, score, "", list(errs))
    R = S.PageResult
    return [
        R("p1", "ours", verdict=v("ours", 9)),            R("p1", "r1", verdict=v("r1", 7, [HIGH])),
        R("p2", "ours", verdict=v("ours", 6, [HIGH, LOW])), R("p2", "r1", verdict=v("r1", 6, [LOW])),
        R("p3", "ours", verdict=v("ours", 10)),           R("p3", "r1", status="failed"),
        R("p4", "ours", verdict=v("ours", 4, [HIGH])),    R("p4", "r1", verdict=v("r1", 8)),
    ]


def test_summary_matches_parsejudge_semantics():
    s = S.summarize(_results(), baseline="r1")
    ours, r1 = s["systems"]
    assert ours["mean_score"] == pytest.approx(7.25) and ours["median_score"] == pytest.approx(7.5)
    assert ours["high_severity_rate"] == pytest.approx(0.5) and ours["no_high_severity_share"] == pytest.approx(0.5)
    assert ours["error_counts"] == {"table_content": 2, "text_corruption": 1}
    # the failed page is not in r1's mean or error rate, but loses head-to-head with score 1
    assert r1["judged_pages"] == 3 and r1["failed_pages"] == 1 and r1["mean_score"] == pytest.approx(7.0)
    assert r1["high_severity_rate"] == pytest.approx(1 / 3)
    h = {(x["system"], x["opponent"]): (x["wins"], x["ties"], x["losses"]) for x in s["head_to_head"]}
    assert h == {("ours", "r1"): (2, 1, 1), ("r1", "ours"): (1, 1, 2)}
    (vb,) = s["vs_baseline"]
    assert vb["system"] == "ours" and vb["win_rate"] == pytest.approx(0.5)
    assert vb["win_rate_ties_half"] == pytest.approx(0.625) and vb["win_rate_decided"] == pytest.approx(2 / 3)
    assert "ours" in S.format_summary(s)


def test_wilson_and_quantile_match_reference_values():
    lo, hi = S.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-4) and hi == pytest.approx(0.9433, abs=1e-4)
    assert S.wilson(0, 0) is None and S.wilson(0, 5)[0] == 0.0
    assert S.quantile([1, 2, 3, 10], 0.5) == 2.5 and S.quantile([], 0.5) is None


# ---------------------------------------------------------------------------------------- end to end

def test_bench_judge_cli_end_to_end(tmp_path, monkeypatch, capsys):
    pages, parses = tmp_path / "set" / "img", tmp_path / "out"
    pages.mkdir(parents=True)
    rows = []
    for i, cat in enumerate(["tax", "tax", "legal", "legal"]):
        Image.new("RGB", (3000, 3600), "white").save(pages / f"d{i}.png")       # too big: gets pre-sized
        rows.append(json.dumps({"id": f"d{i}", "category": cat}))
        for name, score in (("ours", 9 - i), ("r1", 6)):
            (parses / name).mkdir(parents=True, exist_ok=True)
            if not (name == "r1" and i == 3):                                   # r1 has no parse for d3
                (parses / name / f"d{i}.md").write_text(f"score={score}")
    (tmp_path / "set" / "manifest.jsonl").write_text("\n".join(rows))
    fake = FakeJudge()
    monkeypatch.setattr(core, "claude_cli_judge", fake)
    import readanydoc_research.__main__ as cli
    out_file = tmp_path / "rep.json"
    argv = ["bench", "--judge", "--pages", str(pages), "--systems", "ours", "r1", "--parses", str(parses),
            "--baseline", "r1", "--out", str(out_file)]
    assert cli.main(argv) == 0
    text = capsys.readouterr().out
    assert "judged now 4" in text and "versus baseline r1" in text and "mean score by category" in text
    rep = json.loads(out_file.read_text())
    assert rep["judge_version"] == "compare-v4" and rep["summary"]["vs_baseline"][0]["wins"] == 4
    assert rep["summary"]["systems"][1]["failed_pages"] == 1
    from readanydoc.readers import sizing
    with Image.open(fake.requests[0].image_path) as im:
        assert sizing.fits(*im.size) and max(im.size) > 2000
    assert cli.main(argv) == 0                                                   # second run: all cached
    assert "cached 4, judged now 0" in capsys.readouterr().out and len(fake.requests) == 4
    assert cli.main(argv[:-2] + ["--sample", "2", "--dry-run"]) == 0
    assert "2 pages" in capsys.readouterr().out


def test_sample_is_seeded_and_spread_over_categories():
    ps = jrun.PageSet(ids=[f"p{i}" for i in range(20)], category={f"p{i}": "abcde"[i % 5] for i in range(20)})
    a, b = jrun.sample(ps, 10), jrun.sample(ps, 10)
    assert a == b and len(set(a)) == 10
    assert sorted(ps.category[x] for x in a) == sorted("abcde" * 2)
    assert jrun.sample(ps, 10, seed=1) != a
