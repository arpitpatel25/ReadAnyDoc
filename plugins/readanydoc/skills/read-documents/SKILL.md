---
name: read-documents
description: Use when the user wants to read, parse, convert or extract data from a PDF, scan, photo, Word, Excel or PowerPoint file on their computer (invoices, statements, receipts, forms, contracts, tables, reports), especially when they need exact values with the place on the page they came from.
---

# Reading documents with ReadAnyDoc

The `readanydoc` MCP server gives you two tools for files on this computer:

- `parse_document(path, mode?, pages?)`: the document as Markdown (tables as HTML). Use it to read, summarise or
  answer questions about a document.
- `extract_fields(path, schema, instructions?, array_extract?, mode?)`: values for a JSON Schema, each with
  citations (text, page, box, confidence). Use it when the user wants specific fields, a table as rows, or data to
  save or compare.

## How to use them well

1. Pass an absolute path. Expand `~` yourself if needed.
2. For extraction, write a JSON Schema of type `object` with clear property names and a `description` on any field
   that could be confused with another (for example "box 1 wages" on a W-2). Use arrays for line items or
   transactions, and set `array_extract: true` for long lists across many pages.
3. Report values with their citation when it matters ("$162,000, page 1"). Confidence is `high` or `low`; say when a
   value is `low` and suggest checking it.
4. Always read `warnings` in the result and pass anything relevant on to the user. `null` means the value was not
   found or not legible; never fill it in yourself.
5. `mode: "model-only"` is faster and cheaper for simple pages; the default `engine` is more accurate on scans,
   forms and tables. A page can take 20-50 seconds in engine mode.

## If the tools fail

- "no such file": check the path with the user.
- An authentication error: the server needs `ANTHROPIC_API_KEY` in the environment Claude Code was started from.
- Setup and options: https://arpitpatel25.github.io/ReadAnyDoc/docs/get-started/mcp/
