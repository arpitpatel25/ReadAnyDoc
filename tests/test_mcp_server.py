"""`readanydoc mcp`: the two tools are registered and check their input before any model call."""
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
    assert {t.name for t in tools} == {"parse_document", "extract_fields"}


def test_cli_has_the_mcp_command():
    from readanydoc import cli
    with pytest.raises(SystemExit):
        cli.main(["mcp", "--help"])


def test_cli_extract_reads_instructions_from_a_file_and_prints_the_field_report(tmp_path, monkeypatch, capsys):
    import json

    import readanydoc.library as lib
    from readanydoc import cli
    from readanydoc.library import ExtractResult, Usage

    seen = {}

    def fake_extract(path, schema, **kw):
        seen.update(kw)
        return ExtractResult(data={"a": 1}, usage=Usage(seconds=0.1),
                             report={"fields": {"a": {"value": 1, "status": "found"}}, "lists": [], "pages": {}})

    monkeypatch.setattr(lib, "extract", fake_extract)
    monkeypatch.setenv("READANYDOC_NO_STAR", "1")
    (tmp_path / "rules.md").write_text("Use the billing contact, not the signer.\n" * 50)
    (tmp_path / "s.json").write_text('{"type": "object", "properties": {"a": {"type": "number"}}}')
    cli.main(["extract", str(tmp_path / "x.pdf"), "--schema", str(tmp_path / "s.json"),
              "--instructions", "@" + str(tmp_path / "rules.md")])
    assert seen["instructions"].startswith("Use the billing contact") and len(seen["instructions"]) > 1000
    out = json.loads(capsys.readouterr().out)
    assert out["field_report"]["fields"]["a"]["status"] == "found"


def test_mcp_extract_lists_only_the_fields_to_check(monkeypatch):
    import readanydoc
    from readanydoc.library import ExtractResult, Usage

    report = {"fields": {"a": {"value": 1, "status": "found"},
                         "b": {"value": 2, "status": "conflict", "candidates": []},
                         "c": {"value": None, "status": "not_in_document"},
                         "d": {"value": "2025-03-04", "status": "found", "flags": ["ambiguous_date"]}}}
    monkeypatch.setattr(readanydoc, "extract", lambda *a, **k: ExtractResult(data={}, usage=Usage(seconds=0),
                                                                              report=report))
    monkeypatch.setattr(mcp_server, "_path", lambda p: p)
    out = mcp_server.extract_fields("x.pdf", {"type": "object", "properties": {}})
    assert set(out["fields_to_check"]) == {"b", "c", "d"}
