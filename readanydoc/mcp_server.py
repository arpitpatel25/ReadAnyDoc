"""ReadAnyDoc as an MCP server: ``readanydoc mcp`` (stdio).

Gives Claude Desktop, Claude Code, Cursor and other MCP clients two tools on local files:

    parse_document   the document as Markdown (tables as HTML), with page count, warnings and cost
    extract_fields   values for a JSON Schema, each with citations back to the page

Install with ``pip install "readanydoc[engine,mcp]"``. The server reads ``ANTHROPIC_API_KEY`` (or
``OPENAI_API_KEY``) from its environment, as the library does. Files are read from the local disk only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

INSTRUCTIONS = (
    "ReadAnyDoc reads PDFs, scans, photos and Office files. Use parse_document to get a document's text, tables "
    "and layout as Markdown. Use extract_fields with a JSON Schema to get typed values; every value comes with "
    "citations (the text and page box it was read from). Paths must be files on this computer. Confidence is "
    "'high' or 'low'; check warnings before trusting a result."
)


def _path(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"no such file: {p}")
    return str(p.resolve())


def parse_document(path: str, mode: str = "engine", pages: Optional[list[int]] = None) -> dict[str, Any]:
    """Parse a local document (PDF, image, DOCX, XLSX, PPTX ...) into Markdown.

    path: file on this computer. mode: "engine" (default, most accurate) or "model-only" (one model read per
    page, faster). pages: 1-based page numbers to read; all pages when omitted.
    """
    import readanydoc
    res = readanydoc.parse(_path(path), mode=mode, pages=pages)
    return {"markdown": res.markdown, "pages": res.pages, "warnings": res.warnings,
            "cost_usd": res.usage.cost_usd, "seconds": res.usage.seconds}


def extract_fields(path: str, schema: dict[str, Any], instructions: Optional[str] = None,
                   array_extract: bool = False, mode: str = "engine", review: bool = True,
                   alternatives: bool = False, part_tokens: Optional[int] = None,
                   parse_first: bool = True) -> dict[str, Any]:
    """Extract structured values from a local document into a JSON Schema, with citations.

    schema: a JSON Schema object (type "object" with properties). instructions: optional guidance for the
    extraction. array_extract: true for long lists or tables that span many pages. review: also list values to
    check (found by code, no extra cost). alternatives: also return other values the document gives for a field
    (a special term or an amendment that changes a price). part_tokens: largest document part per call, in tokens
    (default 30,000). parse_first: false to skip parsing (the model reads the file itself: about half the cost and
    faster, less accurate, fewer citations).
    """
    import readanydoc
    if isinstance(schema, str):
        schema = json.loads(schema)
    res = readanydoc.extract(_path(path), schema, instructions=instructions, array_extract=array_extract, mode=mode,
                             review=review, alternatives=alternatives, part_tokens=part_tokens, parse=parse_first)
    data = res.data.model_dump() if hasattr(res.data, "model_dump") else res.data
    cites = {k: [c.model_dump(exclude_none=True) for c in v] for k, v in res.citations.items()}
    return {"data": data, "citations": cites, "warnings": res.warnings,
            **({"to_check": res.review} if review else {}),  # values not printed as returned, ambiguous dates, ...
            **({"alternatives": res.alternatives} if alternatives else {}),
            "cost_usd": res.usage.cost_usd, "seconds": res.usage.seconds}


def build_server():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as e:
        raise SystemExit('The MCP server needs the mcp extra: pip install "readanydoc[engine,mcp]"') from e
    import readanydoc
    server = MCPServer(name="readanydoc", title="ReadAnyDoc", instructions=INSTRUCTIONS,
                       website_url="https://arpitpatel25.github.io/ReadAnyDoc/", version=readanydoc.__version__)
    server.tool()(parse_document)
    server.tool()(extract_fields)
    return server


def main() -> None:
    build_server().run("stdio")
