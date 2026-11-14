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
