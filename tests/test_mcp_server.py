"""`readanydoc mcp`: the tools are registered and check their input before any model call."""
import asyncio

import pytest

from readanydoc import mcp_server


def test_missing_file_is_refused_before_any_model_call(tmp_path):
    with pytest.raises(ValueError, match="no such file"):
        mcp_server.parse_document(str(tmp_path / "nope.pdf"))
    with pytest.raises(ValueError, match="no such file"):
        mcp_server.extract_fields(str(tmp_path / "nope.pdf"), {"type": "object", "properties": {}})


def test_server_lists_parse_and_extract():
    pytest.importorskip("mcp")
    server = mcp_server.build_server()
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {"parse_document", "extract_fields", "list_test_sets", "run_test"}


def test_cli_has_the_mcp_command():
    from readanydoc import cli
    with pytest.raises(SystemExit):
        cli.main(["mcp", "--help"])


def test_cli_extract_reads_instructions_from_a_file_and_passes_the_extract_options(tmp_path, monkeypatch, capsys):
    import json

    import readanydoc.library as lib
    from readanydoc import cli
    from readanydoc.library import ExtractResult, Usage

    seen = {}

    def fake_extract(path, schema, **kw):
        seen.update(kw)
        return ExtractResult(data={"a": 1}, usage=Usage(seconds=0.1),
                             review=[{"field": "a", "flag": "not_found_on_page", "detail": "x"}],
                             alternatives={"a": [{"value": "2", "note": "amendment"}]})

    monkeypatch.setattr(lib, "extract", fake_extract)
    monkeypatch.setenv("READANYDOC_NO_STAR", "1")
    (tmp_path / "rules.md").write_text("Use the billing contact, not the signer.\n" * 50)
    (tmp_path / "s.json").write_text('{"type": "object", "properties": {"a": {"type": "number"}}}')
    cli.main(["extract", str(tmp_path / "x.pdf"), "--schema", str(tmp_path / "s.json"),
              "--instructions", "@" + str(tmp_path / "rules.md")])
    assert seen["instructions"].startswith("Use the billing contact") and len(seen["instructions"]) > 1000
    assert seen["review"] is False and seen["alternatives"] is False and seen["derived_values"] is True
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"data", "citations", "usage"}  # only what was asked for
    cli.main(["extract", str(tmp_path / "x.pdf"), "--schema", str(tmp_path / "s.json"), "--review",
              "--alternatives", "a", "--printed-only"])
    assert seen["review"] is True and seen["alternatives"] == ["a"] and seen["derived_values"] is False
    out = json.loads(capsys.readouterr().out)
    assert out["review"][0]["flag"] == "not_found_on_page" and out["alternatives"]["a"][0]["value"] == "2"


def test_mcp_extract_lists_the_values_to_check(monkeypatch):
    import readanydoc
    from readanydoc.library import ExtractResult, Usage

    review = [{"field": "d", "flag": "ambiguous_date", "detail": "printed as 03/04/2025"}]
    seen = {}

    def fake(*a, **k):
        seen.update(k)
        return ExtractResult(data={}, usage=Usage(seconds=0), review=review)

    monkeypatch.setattr(readanydoc, "extract", fake)
    monkeypatch.setattr(mcp_server, "_path", lambda p: p)
    out = mcp_server.extract_fields("x.pdf", {"type": "object", "properties": {}})
    assert out["to_check"] == review and seen["review"] is True and "alternatives" not in out
