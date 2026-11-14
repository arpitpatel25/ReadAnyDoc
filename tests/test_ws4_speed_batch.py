"""Workstream 4 (final round): strip prefetch, API rate-limit handling, Message Batches reader. Mocked HTTP only."""
import json
import threading

import httpx
import pytest
from PIL import Image

from readanydoc.pipeline import Pipeline, PipelineConfig, StageConfig
from readanydoc.readers import READERS, prompts
from readanydoc.readers import anthropic_api as api
from readanydoc.readers.anthropic_api import AnthropicAPIReader
from readanydoc.readers.anthropic_batch import AnthropicBatchReader, BatchCollector
from readanydoc.readers.base import Reader, ReaderError, ReadResult, Usage

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeReader(Reader):
    name = "fake"

    def __init__(self, page_text):
        self.page_text = page_text
        self.calls = []
        self.lock = threading.Lock()

    def read(self, image, hint=None, context=None):
        strip = context is not None and context.prompt == prompts.STRIP_PROMPT_FILE
        with self.lock:
            self.calls.append(context.stem if strip else "page")
        text = "Canada 5,399 India 856" if strip else self.page_text
        return ReadResult(text=text, stop_reason="end_turn", usage=Usage(model="fake", input_tokens=10, output_tokens=5))


def run(tmp_path, monkeypatch, page_text, **read_options):
    monkeypatch.setenv("READANYDOC_CACHE_DIR", str(tmp_path / "cache"))
    img = tmp_path / "scan.png"
    Image.new("RGB", (1700, 2200), "white").save(img)
    fake = FakeReader(page_text)
    READERS["fake_ws4"] = lambda arg: fake
    from readanydoc.pipeline.read import Read
    Read._readers.pop("fake_ws4", None)
    off = {n: StageConfig(enabled=False) for n in ("layout", "verify", "ground", "assemble", "shape")}
    cfg = PipelineConfig(reader="fake_ws4", words="tesseract", stages={
        **off, "read": StageConfig(options={"zoom_vote": True, "robust": False, **read_options})})
    try:
        doc = Pipeline(cfg).run(str(img))
    finally:
        READERS.pop("fake_ws4", None)
        Read._readers.pop("fake_ws4", None)
    return doc, fake


TABLE = "<table><tr><td>Canada</td><td>5,390</td></tr><tr><td>India</td><td>856</td></tr></table>"


def test_prefetched_strips_give_the_same_result_and_calls(tmp_path, monkeypatch):
    base, f0 = run(tmp_path, monkeypatch, TABLE)
    assert base.pages[0].properties["zoom_changes"] == [["5,390", "5,399"]] and len(f0.calls) == 1 + 3 + 4
    for level in (1, 2):
        doc, f = run(tmp_path, monkeypatch, TABLE, zoom_prefetch=level)
        assert doc.pages[0].properties["zoom_changes"] == [["5,390", "5,399"]]
        assert sorted(f.calls) == sorted(f0.calls)  # same reads, only started earlier
        assert doc.pages[0].regions[0].content == base.pages[0].regions[0].content
        assert len(doc.usage) == len(base.usage) and "zoom_prefetch_unused" not in doc.pages[0].properties


def test_prefetched_strips_that_are_not_used_are_billed(tmp_path, monkeypatch):
    text = "Dear [illegible] the [illegible] of [illegible] is due"
    base, f0 = run(tmp_path, monkeypatch, text)
    assert f0.calls == ["page"]  # 3 illegible marks: the zoom vote is skipped
    doc, f = run(tmp_path, monkeypatch, text, zoom_prefetch=1)
    assert len(f.calls) == 4 and doc.pages[0].properties["zoom_prefetch_unused"] == ["za"]
    assert sum(1 for u in doc.usage if u.get("escalation") == "zoom_strip_unused") == 3
    assert doc.pages[0].regions[0].content == base.pages[0].regions[0].content


def test_zoom_gate_skips_strips_without_table_numbers(tmp_path, monkeypatch):
    doc, f = run(tmp_path, monkeypatch, "A letter with no table at all, dated 12 March.", zoom_gate="table_numbers")
    assert f.calls == ["page"] and doc.pages[0].properties["zoom_gate"]["run"] is False
    doc, f = run(tmp_path, monkeypatch, TABLE.replace("856", "856</td><td>1,200</td><td>77"), zoom_gate="table_numbers")
    assert len(f.calls) > 1 and doc.pages[0].properties["zoom_gate"]["run"] is True


def reply():
    return {"model": "claude-haiku-5-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": "# Page"}],
            "usage": {"input_tokens": 1000, "output_tokens": 200}}


def test_rate_limit_waits_as_told_and_retries(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("READANYDOC_API_CONCURRENCY", "2")
    slept = []
    monkeypatch.setattr(api, "_wait_cool", lambda: slept.append(max(api._COOL["until"] - api.time.time(), 0)) or api._COOL.update(until=0.0))
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "7"}, text="slow down") if len(calls) <= 4 else httpx.Response(200, json=reply())
    before = api.STATS["rate_limited"]
    r = AnthropicAPIReader(client=httpx.Client(transport=httpx.MockTransport(handler))).read(PNG)
    assert r.text == "# Page" and len(calls) == 5  # more attempts than max_retries=2: rate limits have their own budget
    assert api.STATS["rate_limited"] - before == 4 and max(slept) >= 7
    monkeypatch.setenv("READANYDOC_API_RATE_RETRIES", "0")
    calls.clear()
    with pytest.raises(ReaderError, match="429"):
        AnthropicAPIReader(max_retries=0, client=httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(429, text="no")))).read(PNG)


def test_batch_reader_create_poll_results(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr("readanydoc.readers.anthropic_batch.time.sleep", lambda s: None)
    seen = {"polls": 0}

    def handler(request):
        url = str(request.url)
        assert request.headers["x-api-key"] == "test-key-not-real"
        if request.method == "POST" and url.endswith("/v1/messages/batches"):
            seen["requests"] = json.loads(request.content)["requests"]
            return httpx.Response(200, json={"id": "msgbatch_1", "processing_status": "in_progress"})
        if url.endswith("/v1/messages/batches/msgbatch_1"):
            seen["polls"] += 1
            done = seen["polls"] >= 2
            return httpx.Response(200, json={
                "id": "msgbatch_1", "processing_status": "ended" if done else "in_progress",
                "request_counts": {"succeeded": 2, "errored": 1},
                "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results" if done else None})
        if url.endswith("/results"):
            ids = [r["custom_id"] for r in seen["requests"]]
            rows = [{"custom_id": ids[2], "result": {"type": "errored", "error": {"type": "invalid_request"}}},
                    {"custom_id": ids[1], "result": {"type": "succeeded", "message": {**reply(), "content": [{"type": "text", "text": "second"}]}}},
                    {"custom_id": ids[0], "result": {"type": "succeeded", "message": {**reply(), "content": [{"type": "text", "text": "first"}]}}}]
            return httpx.Response(200, text="\n".join(json.dumps(r) for r in rows))
        return httpx.Response(404)
    col = BatchCollector("https://api.anthropic.com", "2023-06-01", "ANTHROPIC_API_KEY",
                         client=httpx.Client(transport=httpx.MockTransport(handler)), window=0.3, poll=0.01)
    reader = AnthropicBatchReader("haiku", effort="high", collector_=col)
    out = {}

    def one(k, data):
        try:
            out[k] = reader.read(data)
        except ReaderError as e:
            out[k] = e
    # submit in a fixed order so that results (returned in another order) can be checked against custom ids
    futures = [col.submit(reader.build_request(PNG, "p", 100)) for _ in range(3)]
    results = []
    for f in futures:
        try:
            results.append(reader.parse_message(f.result(timeout=20)[0], 0.0).text)
        except ReaderError as e:
            results.append(str(e))
    assert results[:2] == ["first", "second"] and "errored" in results[2]
    assert len(seen["requests"]) == 3 and seen["polls"] == 2  # one batch for the three requests
    p = seen["requests"][0]["params"]
    assert p["model"] == "claude-haiku-5-5" and p["output_config"] == {"effort": "high"} and "stream" not in p
    assert col.batches[0]["id"] == "msgbatch_1" and col.batches[0]["requests"] == 3


def test_batch_usage_is_priced_at_half():
    from readanydoc.readers import usage
    s = usage.summarize([{"model": "claude-haiku-5-5", "input_tokens": 1_000_000, "output_tokens": 1_000_000, "batch": True},
                         {"model": "claude-haiku-5-5", "input_tokens": 1_000_000, "output_tokens": 0}])
    assert s["models"]["claude-haiku-5-5"]["batch_calls"] == 1
    assert usage.cost_usd(s) == pytest.approx(0.05 + 0.25 + 0.10)
    assert usage.cost_usd(usage.merge(s, s)) == pytest.approx(2 * 0.40)


def test_batch_mode_only_for_async_non_priority_jobs(monkeypatch):
    from types import SimpleNamespace

    from readanydoc.profiles import get_profile
    from readanydoc.readers import batch_mode
    job = SimpleNamespace(sync=False, priority=False)
    assert not batch_mode.wanted(job)
    monkeypatch.setenv("READANYDOC_BATCH_ASYNC", "1")
    assert batch_mode.wanted(job)
    assert not batch_mode.wanted(SimpleNamespace(sync=True, priority=False))
    assert not batch_mode.wanted(SimpleNamespace(sync=False, priority=True))
    cfg = batch_mode.apply(get_profile("engine-api"))
    assert cfg.reader == "anthropic_batch:haiku#high" and cfg.stage("read").options["page_workers"] >= 64
    assert cfg.stage("read").options["handwriting_reader"] == "anthropic_batch:sonnet"


def test_pre_read_gate_stops_strips_and_prefetch(tmp_path, monkeypatch):
    # a blank image: local OCR sees no numbers, so with a pre-read gate nothing is prefetched and nothing is zoomed
    from readanydoc.pipeline.reading import zoom_gate
    monkeypatch.setattr(zoom_gate, "pre_decide", lambda page, n=20: (False, {"run": False, "numbers": 0, "min": n}))
    doc, f = run(tmp_path, monkeypatch, TABLE, zoom_pre_gate=20, zoom_prefetch=1)
    assert f.calls == ["page"] and doc.pages[0].properties["zoom_pre_gate"]["run"] is False
    assert "zoom_changes" not in doc.pages[0].properties
    monkeypatch.setattr(zoom_gate, "pre_decide", lambda page, n=20: (True, {"run": True, "numbers": 99, "min": n}))
    doc, f = run(tmp_path, monkeypatch, TABLE, zoom_pre_gate=20, zoom_prefetch=1)
    assert len(f.calls) == 8 and doc.pages[0].properties["zoom_changes"] == [["5,390", "5,399"]]


def test_effort_route_sends_easy_pages_to_the_other_reader(tmp_path, monkeypatch):
    from readanydoc.pipeline.reading import effort_route
    cheap = FakeReader("cheap read")
    READERS["fake_ws4_cheap"] = lambda arg: cheap
    from readanydoc.pipeline.read import Read
    Read._readers.pop("fake_ws4_cheap", None)
    try:
        monkeypatch.setattr(effort_route, "decide", lambda page, a=40, b=0.12: (True, {"easy": True}))
        doc, f = run(tmp_path, monkeypatch, "A plain letter.", zoom_vote=False, effort_route="fake_ws4_cheap:x")
        assert cheap.calls == ["page"] and f.calls == [] and doc.pages[0].regions[0].content == "cheap read"
        assert doc.pages[0].properties["effort_route"]["reader"] == "fake_ws4_cheap:x"
        monkeypatch.setattr(effort_route, "decide", lambda page, a=40, b=0.12: (False, {"easy": False}))
        doc, f = run(tmp_path, monkeypatch, "A plain letter.", zoom_vote=False, effort_route="fake_ws4_cheap:x")
        assert f.calls == ["page"] and len(cheap.calls) == 1
    finally:
        READERS.pop("fake_ws4_cheap", None)
        Read._readers.pop("fake_ws4_cheap", None)
