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
    "'high' or 'low'; check warnings before trusting a result. To test ReadAnyDoc against other systems (Reducto, "
    "a plain model call) on public test sets or the user's own documents: list_test_sets, then run_test with "
    "confirm=false to see the plan and its rough cost, and confirm=true only after the user agrees to the cost."
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
                   parse_first: bool = True, fill_required: Any = "choices") -> dict[str, Any]:
    """Extract structured values from a local document into a JSON Schema, with citations.

    schema: a JSON Schema object (type "object" with properties). instructions: optional guidance for the
    extraction. array_extract: true for long lists or tables that span many pages. review: also list values to
    check (found by code, no extra cost). alternatives: also return other values the document gives for a field
    (a special term or an amendment that changes a price). part_tokens: largest document part per call, in tokens
    (default 30,000). parse_first: false to skip parsing (the model reads the file itself: about half the cost and
    faster, less accurate, fewer citations). fill_required: which required fields always get a value: "choices"
    (default, yes/no and enum fields), true (numbers and text too: the model may guess, each guess is flagged in
    to_check as filled_because_required), a list of field paths, or false (nothing forced).
    """
    import readanydoc
    if isinstance(schema, str):
        schema = json.loads(schema)
    res = readanydoc.extract(_path(path), schema, instructions=instructions, array_extract=array_extract, mode=mode,
                             review=review, alternatives=alternatives, part_tokens=part_tokens, parse=parse_first,
                             fill_required=fill_required)
    data = res.data.model_dump() if hasattr(res.data, "model_dump") else res.data
    cites = {k: [c.model_dump(exclude_none=True) for c in v] for k, v in res.citations.items()}
    return {"data": data, "citations": cites, "warnings": res.warnings,
            **({"to_check": res.review} if review else {}),  # values not printed as returned, ambiguous dates, ...
            **({"alternatives": res.alternatives} if alternatives else {}),
            "cost_usd": res.usage.cost_usd, "seconds": res.usage.seconds}


def list_test_sets() -> dict[str, Any]:
    """The public test sets (invoices, receipts, financial reports, contracts, research papers, resumes, result
    tables, long reports), with what each tests, its size and its licence, and the systems that can be compared."""
    from . import bench
    return {"sets": bench.sets(), "systems": bench.systems.NAMES,
            "keys": "each system uses the user's own key: ANTHROPIC_API_KEY, REDUCTO_API_KEY"}


def run_test(set_id: Optional[str] = None, systems: Optional[list[str]] = None, limit: Optional[int] = None,
             folder: Optional[str] = None, schema_path: Optional[str] = None, answers_folder: Optional[str] = None,
             budget_usd: Optional[float] = None, confirm: bool = False, out: Optional[str] = None) -> dict[str, Any]:
    """Compare systems on a public test set (set_id) or on the user's own documents (folder + schema_path, and
    answers_folder with <file stem>.json answer keys if they have them). Scored by code against the answer key.

    With confirm=false (the default) nothing is spent: it returns the plan (documents, pages, rough cost per
    system, systems that cannot run and why). Ask the user, then call again with confirm=true to run it. Returns
    the scores per system and the path of an HTML report with a citation viewer for every document."""
    from . import bench
    plan = bench.prepare(set_id, systems, limit, out, folder=folder, schema=schema_path, answers=answers_folder)
    head = {"title": plan["title"], "estimate": plan["estimate"], "cannot_run": plan["not_ready"],
            "licence": (plan["info"] or {}).get("licence"), "results_folder": plan["out"]}
    if not confirm:
        return {**head, "status": "not run: show the user the rough cost and call again with confirm=true"}
    summary = bench.run_prepared(plan, budget_usd=budget_usd)
    return {**head, "status": "done", "summary": summary["systems"], "report": summary["report"],
            "stopped_by_budget": summary.get("stopped_by_budget")}


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
    server.tool()(list_test_sets)
    server.tool()(run_test)
    return server


def main() -> None:
    build_server().run("stdio")
