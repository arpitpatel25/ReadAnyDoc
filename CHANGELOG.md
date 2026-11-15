# Changelog

## 0.1.4 (2026-11-15)

- The PyPI page now shows the showcase image and working links (README links are absolute).

## 0.1.3 (2026-11-15)

- LangChain loader and LlamaIndex reader: `readanydoc.integrations.langchain.ReadAnyDocLoader`,
  `readanydoc.integrations.llamaindex.ReadAnyDocReader` (extras `langchain`, `llamaindex`).
- Claude Code plugin: `/plugin marketplace add arpitpatel25/ReadAnyDoc`, then `/plugin install readanydoc@readanydoc`
  (the MCP server through `uvx`, plus a skill for reading documents).
- Listed in the official MCP Registry as `io.github.arpitpatel25/readanydoc`, published from each release.
- Showcase of two real public-domain documents with every output (`examples/showcase/`), a Colab notebook, use-case
  pages and a comparison of alternatives in the docs, and link previews for the website and docs.

## 0.1.2 (2026-11-15)

- `readanydoc mcp`: an MCP server with two tools, `parse_document` and `extract_fields` (with citations), for Claude
  Code, Claude Desktop, Cursor and other assistants. Install with `pip install "readanydoc[engine,mcp]"`.
- [AGENT_SETUP.md](AGENT_SETUP.md): step-by-step setup instructions for coding agents, and a one-line prompt to
  paste into Claude Code, Codex or Cursor (README, website and docs).
- Community: GitHub Discussions, issue forms, and links from the README, website and docs.

## 0.1.1 (2026-11-15)

- A page the model provider declines to read (content filtering) is no longer empty: a second model reads it if
  `READANYDOC_REFUSAL_READER` (or the read option `refusal_reader`) is set, otherwise the page's local OCR text is
  used. OCR-text blocks are always `low` confidence and the response keeps a warning.
- Table re-reads are bounded per page: at most 16 re-reads, and a page waits at most 180 seconds for them
  (`READANYDOC_TABLE_SECONDS`). Unfinished re-reads keep the page read's table. This stops the rare very slow
  table-heavy document.
- Long list extraction checks its rows: a segment that returns far fewer items than its parsed tables have rows is
  extracted again in halves (kept only when it finds more); a gap that remains is reported in `warnings`.
- `usage.by_model` names the model used for extract, split, classify and edit calls instead of `task:extract`.

## 0.1.0 (2026-11-14)

First public release.

- `readanydoc.parse` and `readanydoc.extract` as a Python library; `extract` takes a pydantic class or JSON Schema and
  returns typed values with citations.
- Two modes: `engine` (default) and `model-only`, both on Claude Haiku 5.5 at high effort; handwritten pages go to
  Claude Sonnet 5.5.
- Every model the pipeline uses can be chosen, with its effort and any provider request options. Anthropic, OpenAI
  and OpenAI-compatible endpoints.
- Reducto-compatible HTTP server: parse, extract, split, classify, edit, async jobs, webhooks, citations.
- Benchmarks against Reducto r-1 on olmOCR-bench, OmniDocBench, RD-TableBench, FUNSD and long-document extraction
  ([BENCHMARKS.md](BENCHMARKS.md)).
