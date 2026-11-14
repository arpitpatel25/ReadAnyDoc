import base64
import json
import subprocess

import httpx
import pytest

from readanydoc.layout import make_layout
from readanydoc.model import RegionType
from readanydoc.readers import ReadContext, ReaderError, make_reader, make_word_provider
from readanydoc.readers.anthropic_api import AnthropicAPIReader
from readanydoc.readers.claude_cli import ClaudeCLIReader
from readanydoc.readers.openai_compatible import OpenAICompatibleReader

from .conftest import needs_spike

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
STEM = "0097571b436308f563a5808d45d0232255a2_page_6_pg1"


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_anthropic_request_and_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "claude-haiku-5-5", "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "```\n# Hello\n```"}],
            "usage": {"input_tokens": 1000, "output_tokens": 200}})

    r = AnthropicAPIReader("haiku", client=mock_client(handler)).read(PNG)
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "test-key-not-real" and seen["headers"]["anthropic-version"]
    body = seen["body"]
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 16000
    assert "temperature" not in body and "thinking" not in body
    image, text = body["messages"][0]["content"]
    assert image["source"] == {"type": "base64", "media_type": "image/png",
                               "data": base64.standard_b64encode(PNG).decode()}
    assert text["type"] == "text" and "Markdown" in text["text"]
    assert r.text == "# Hello" and r.stop_reason == "end_turn" and not r.truncated
    assert r.usage.input_tokens == 1000 and r.usage.cost_usd == pytest.approx(0.0002)


def test_anthropic_truncation_refusal_and_errors(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr("time.sleep", lambda s: None)
    reply = lambda stop: httpx.Response(200, json={"stop_reason": stop, "usage": {},
                                                   "content": [{"type": "text", "text": "partial"}]})
    assert AnthropicAPIReader(client=mock_client(lambda r: reply("max_tokens"))).read(PNG).truncated
    refused = AnthropicAPIReader(client=mock_client(lambda r: reply("refusal"))).read(PNG)
    assert refused.refused and refused.text == ""
    with pytest.raises(ReaderError, match="400"):
        AnthropicAPIReader(client=mock_client(lambda r: httpx.Response(400, text="bad"))).read(PNG)
    calls = []

    def flaky(request):
        calls.append(1)
        return httpx.Response(529, text="overloaded") if len(calls) < 3 else reply("end_turn")

    assert AnthropicAPIReader(client=mock_client(flaky)).read(PNG).text == "partial" and len(calls) == 3


def test_anthropic_without_key_fails_cleanly(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ReaderError, match="ANTHROPIC_API_KEY is not set"):
        AnthropicAPIReader(client=mock_client(lambda r: httpx.Response(500))).read(PNG)


def test_openai_compatible_request_and_response(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "glm-ocr", "choices": [
            {"message": {"content": "| a |"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 7}})

    reader = make_reader("openai_compatible:glm-ocr@http://gpu:8118/v1")
    reader._client = mock_client(handler)
    r = reader.read(PNG, RegionType.TABLE, ReadContext(prompt="Table Recognition:", max_tokens=99))
    assert seen["url"] == "http://gpu:8118/v1/chat/completions" and seen["auth"] is None
    assert seen["body"]["model"] == "glm-ocr" and seen["body"]["max_tokens"] == 99
    parts = seen["body"]["messages"][0]["content"]
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[1]["text"] == "Table Recognition:"
    assert r.text == "| a |" and r.truncated and r.stop_reason == "max_tokens" and r.usage.output_tokens == 7


def test_openai_compatible_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    reader = OpenAICompatibleReader("m", client=mock_client(lambda r: httpx.Response(503, text="down")))
    with pytest.raises(ReaderError, match="503"):
        reader.read(PNG)


def test_claude_cli_strips_keys_and_parses_json(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("CLAUDECODE", "1")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"], seen["cwd"] = cmd, kw["env"], kw["cwd"]
        out = {"result": "# Page", "total_cost_usd": 0.007, "usage": {"input_tokens": 5, "output_tokens": 9}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    img = tmp_path / "p.png"
    img.write_bytes(PNG)
    r = ClaudeCLIReader("haiku").read(str(img))
    assert r.text == "# Page" and r.usage.cost_usd == 0.007
    assert "ANTHROPIC_API_KEY" not in seen["env"] and "CLAUDECODE" not in seen["env"]
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and str(img.resolve()) in cmd[2]
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-5-5"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read" and "--strict-mcp-config" in cmd


def test_unknown_adapters_raise():
    with pytest.raises(ReaderError):
        make_reader("nope:x")
    with pytest.raises(Exception):
        make_layout("nope")


@needs_spike
def test_cached_reader_words_and_layouts():
    ctx = ReadContext(stem=STEM)
    r = make_reader("cached:haiku").read(None, None, ctx)
    assert "DISCUSSION" in r.text and r.usage.cached and r.usage.cost_usd
    words = make_word_provider("cached:ppocr").words(None, ctx)
    assert len(words) > 500 and all(0 <= w.bbox.left <= 1 and 0 <= w.bbox.bottom <= 1.0001 for w in words)
    pvl = make_layout("cached:pvl").detect(None, ctx)
    assert {b.type for b in pvl} >= {RegionType.PAGE_NUMBER, RegionType.TEXT, RegionType.SECTION_HEADER}
    glm = make_layout("cached:glm").detect(None, ctx)
    assert glm and all(0 <= b.bbox.left <= 1 and b.bbox.width > 0 for b in glm)
    # the two detectors ran on the same image: their text boxes must overlap (checks the 0-1000 scale)
    assert max(g.bbox.iou(p.bbox) for g in glm for p in pvl) > 0.8
    with pytest.raises(ReaderError):
        make_reader("cached:haiku").read(None, None, ReadContext(stem="missing"))
