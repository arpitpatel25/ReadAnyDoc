"""LLM adapters: JSON parsing, FakeLLM, Anthropic API (mocked HTTP only), Claude CLI (mocked subprocess)."""

import json
import subprocess

import httpx
import pytest

from readanydoc.tasks import llm as L


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": [1, 2]}\n```', {"a": [1, 2]}),
    ('Here is the result:\n{"a": {"b": "x}y"}}\nHope that helps.', {"a": {"b": "x}y"}}),
    ('[1, 2, 3]', [1, 2, 3]),
    ('noise {not json} then {"ok": true}', {"ok": True}),
])
def test_parse_json(text, expected):
    assert L.parse_json(text) == expected


@pytest.mark.parametrize("text", ["", "no json here", "{broken"])
def test_parse_json_raises(text):
    with pytest.raises(ValueError):
        L.parse_json(text)


def test_fake_llm_scripted_and_handler():
    fake = L.FakeLLM([{"a": 1}, "plain text"])
    r1 = fake.complete("sys", "u1", json_schema={"type": "object"}, max_tokens=5)
    r2 = fake.complete("sys", "u2")
    r3 = fake.complete("sys", "u3")  # the last reply repeats
    assert r1.data == {"a": 1} and r2.data is None and r2.text == "plain text" and r3.text == "plain text"
    assert fake.calls[0] == {"system": "sys", "user": "u1", "images": [], "json_schema": {"type": "object"}, "max_tokens": 5}
    echo = L.FakeLLM(lambda system, user, i: {"i": i, "len": len(user)})
    assert echo.complete("s", "abc").data == {"i": 0, "len": 3}
    assert echo.complete("s", "abcd").data == {"i": 1, "len": 4}
    with pytest.raises(L.LLMError):
        L.FakeLLM([L.LLMError("down")]).complete("s", "u")


def test_ask_json_retries_once_on_non_json():
    fake = L.FakeLLM(["not json", {"ok": 1}])
    data, results = L.ask_json(fake, "s", "u")
    assert data == {"ok": 1} and len(results) == 2
    assert "not valid JSON" in fake.calls[1]["user"]
    data, results = L.ask_json(L.FakeLLM(["nope"]), "s", "u", retries=1)
    assert data is None and len(results) == 2


def test_get_llm_specs():
    assert isinstance(L.get_llm("fake"), L.FakeLLM)
    cli = L.get_llm("claude_cli")
    assert isinstance(cli, L.ClaudeCLILLM) and cli.model == "claude-haiku-5-5"
    assert L.get_llm("claude_cli:sonnet").model == "claude-sonnet-5-5"
    assert L.get_llm("anthropic_api:haiku").model == "claude-haiku-5-5"
    with pytest.raises(ValueError):
        L.get_llm("gpt")


# ---------------------------------------------------------------- Anthropic API (mocked HTTP only)

def api(handler, **kw):
    return L.AnthropicAPILLM(client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_anthropic_api_request_and_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "claude-haiku-5-5", "stop_reason": "end_turn",
                                         "content": [{"type": "thinking", "thinking": ""},
                                                     {"type": "text", "text": '{"total": 5}'}],
                                         "usage": {"input_tokens": 120, "output_tokens": 9}})

    res = api(handler).complete("You extract.", "Document: ...", images=[b"\x89PNG\r\n\x1a\nfake"], max_tokens=777)
    assert res.data == {"total": 5} and res.stop_reason == "end_turn"
    assert res.usage["input_tokens"] == 120 and res.usage["output_tokens"] == 9
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "test-key-not-real" and seen["headers"]["anthropic-version"] == "2023-06-01"
    body = seen["body"]
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 777 + L.THINKING_HEADROOM and body["system"] == "You extract."
    assert [c["type"] for c in body["messages"][0]["content"]] == ["image", "text"]
    assert body["messages"][0]["content"][0]["source"]["media_type"] == "image/png"
    # nothing the current models reject: no sampling parameters, no thinking budget, no prefill
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(body)
    assert body["output_config"] == {"effort": "high"}  # Haiku 5.5 defaults to medium; tasks run at high
    assert len(body["messages"]) == 1


def test_anthropic_api_structured_output_is_on_when_the_schema_fits(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": "{}"}], "usage": {}})

    schema = {"type": "object", "properties": {"a": {"type": "string", "maxLength": 9}}}
    api(handler).complete("s", "u", json_schema=schema)
    api(handler, structured_outputs=False, effort="low").complete("s", "u", json_schema=schema)
    api(handler).complete("s", "u")  # no schema: no format
    closed = {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False, "required": []}
    assert bodies[0]["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": closed}}
    assert bodies[1]["output_config"] == {"effort": "low"}
    assert bodies[2]["output_config"] == {"effort": "high"}


def test_anthropic_api_falls_back_to_no_format_when_the_api_rejects_the_schema(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "format" in body["output_config"]:
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": "Schema is too complex for compilation."}})
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"a": "x"}'}],
                                         "usage": {}})

    llm = api(handler)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert llm.complete("s", "u", json_schema=schema).data == {"a": "x"}
    assert llm.complete("s", "u", json_schema=schema).data == {"a": "x"}  # remembered: no second rejected call
    assert ["format" in b["output_config"] for b in bodies] == [True, False, False]


def test_anthropic_api_400_without_a_format_is_still_an_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    llm = api(lambda r: httpx.Response(400, json={"error": {"message": "bad"}}))
    with pytest.raises(L.LLMError):
        llm.complete("s", "u")


def test_anthropic_api_refusal_truncation_retry_and_errors(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    res = api(lambda r: httpx.Response(200, json={"stop_reason": "refusal", "content": [{"type": "text", "text": "x"}]})).complete("s", "u")
    assert res.refused and res.text == "" and res.data is None
    res = api(lambda r: httpx.Response(200, json={"stop_reason": "max_tokens", "content": [{"type": "text", "text": '{"a": 1'}]})).complete("s", "u")
    assert res.truncated and res.data is None
    n = {"calls": 0}

    def flaky(request):
        n["calls"] += 1
        if n["calls"] < 3:
            return httpx.Response(529, json={"error": "overloaded"})
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": "[1]"}]})

    assert api(flaky).complete("s", "u").data == [1] and n["calls"] == 3
    with pytest.raises(L.LLMError, match="400"):
        api(lambda r: httpx.Response(400, json={"error": "bad"})).complete("s", "u")


def test_anthropic_api_without_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def handler(request):  # pragma: no cover
        raise AssertionError("no request may be sent without a key")

    with pytest.raises(L.LLMError, match="ANTHROPIC_API_KEY is not set"):
        api(handler).complete("s", "u")


# ---------------------------------------------------------------- Claude CLI (mocked subprocess)

def test_claude_cli_command_env_and_parsing(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("CLAUDECODE", "1")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
            "result": '```json\n{"category": "invoice"}\n```', "is_error": False, "total_cost_usd": 0.0012,
            "usage": {"input_tokens": 900, "output_tokens": 12}}), stderr="")

    monkeypatch.setattr(L.subprocess, "run", fake_run)
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG")
    res = L.ClaudeCLILLM().complete("You classify.", "Pages: ...", images=[str(img)])
    assert res.data == {"category": "invoice"} and res.usage["cost_usd"] == 0.0012 and res.model == "claude-haiku-5-5"
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--model") + 1] == "claude-haiku-5-5"
    assert cmd[cmd.index("--system-prompt") + 1] == "You classify."
    assert cmd[cmd.index("--allowedTools") + 1] == "Read" and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    kw = seen["kw"]
    assert "ANTHROPIC_API_KEY" not in kw["env"] and "CLAUDECODE" not in kw["env"]
    assert kw["input"].endswith("Pages: ...") and str(img.resolve()) in kw["input"]  # prompt on stdin, not argv
    assert "Pages: ..." not in " ".join(cmd)


def test_claude_cli_errors(monkeypatch):
    monkeypatch.setattr(L.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="oops", stderr="bad"))
    with pytest.raises(L.LLMError, match="non-JSON"):
        L.ClaudeCLILLM().complete("s", "u")
    monkeypatch.setattr(L.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, stdout=json.dumps({"is_error": True, "result": "rate limited"}), stderr=""))
    with pytest.raises(L.LLMError, match="rate limited"):
        L.ClaudeCLILLM().complete("s", "u")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(L.subprocess, "run", boom)
    with pytest.raises(L.LLMError, match="claude CLI failed"):
        L.ClaudeCLILLM().complete("s", "u")


def test_api_cost_and_effort_in_spec(monkeypatch, tmp_path):
    assert L.api_cost("claude-haiku-5-5", {"input_tokens": 50_000, "output_tokens": 50_000}) == 0.03
    assert L.api_cost("claude-haiku-5-5", {"input_tokens": 200_000, "output_tokens": 0}) == 0.1  # long-prompt rate
    assert L.api_cost("unknown", {"input_tokens": 1}) is None
    assert L.get_llm("anthropic_api:haiku#medium").effort == "medium"
    assert L.get_llm("anthropic_api:haiku").effort == "high"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv(L.USAGE_LOG_ENV, str(log))
    res = api(lambda r: httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": "{}"}],
                                                  "usage": {"input_tokens": 1000, "output_tokens": 200}})).complete("s", "u")
    assert res.usage["cost_usd"] == pytest.approx(0.0002)
    assert json.loads(log.read_text())["cost_usd"] == pytest.approx(0.0002)
