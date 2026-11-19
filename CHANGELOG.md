# Changelog

## 0.4.1 (2026-11-19)

- `readanydoc bench`: a small sample now covers the set's hard documents (photos, scans, handwriting,
  multi-page) and mixes difficulties, instead of the first clean PDFs.
- BENCHMARKS.md: a 20-invoice extraction run against Reducto and a plain model.

## 0.4.0 (2026-11-19)

Test it yourself: compare ReadAnyDoc with other systems on public test sets or your own documents.

- `readanydoc bench`: compare ReadAnyDoc with Reducto, a plain model call or its own other modes, on 8 public test
  sets (invoices, receipts, quarterly reports, credit agreements, research papers, resumes, result tables, long
  reports) or on your own documents, with your own keys. Cost estimate and confirmation before spending, a budget
  cap, resumable runs, scoring by code against answer keys, and an HTML report with every system's citation boxes on
  the page. Also as MCP tools (`list_test_sets`, `run_test`) and in Python (`readanydoc.bench`).

## 0.3.0 (2026-11-19)

Every extraction call is enforced by the API, whatever the schema's size, and reads one cached copy of the
document; citations point where a person checking a value would look; and a cheaper one-stage mode.

- Enforced output at any size. A schema the API refuses as one structured-output call is asked in one fixed answer
  format (`{"values": [{"code", "item", "value"}]}`, codes from your schema's own fields), the same in every call,
  and turned back into your exact shape. No call is ever made just to fix the shape.
- The document is cached within a run: after the first group of fields, every group reads it at a tenth of the
  price. Nothing is reused between runs.
- Page parts are sized in tokens: `part_tokens` (default 30,000, capped by the model's own limit; `extract(...,
  part_tokens=...)`, `settings.part_tokens`, MCP `part_tokens`). Measured on a 43-page 10-Q: 30,000 kept the
  accuracy of small parts at lower cost; one 43-page part lost a third of the values.
- Model limits (context, output, price jump, prices) come from a snapshot of LiteLLM's public model list, about
  4,300 models, or an OpenAI-compatible server's `/models`; your own file in `READANYDOC_MODEL_LIMITS` overrides
  them. An unknown model is an error naming `part_tokens`, never a guess. The output limit is the model's own.
- One log line per run with every setting chosen (`readanydoc` logger, `extract settings: {...}`), and
  `READANYDOC_TRACE=<file.jsonl>`: one line per model call (tokens, cache reads and writes, cost, seconds, why the
  call was made, the reply) and per decision.
- A value stated once for a whole table or page (a unit, "in thousands", a period in a heading) is written on every
  item it covers; of two copies of the same list item, the less complete one is dropped; names are copied in full.
- Citations: the table the model read each row from is kept across page parts; worked-out values (a period from a
  column heading, a scale from "in millions", a code from a name) are boxed on the printed words they come from;
  a category chosen from your schema's list is never boxed on the same word elsewhere; a number is matched only as
  a whole number; a short value printed in several places is not boxed at a guess. On a 10-Q, numbers boxed on the
  right row: 94% (Reducto 81%); category boxes on the cover page: 128 to 0.
- `extract(..., parse=False)` (MCP `parse_first=false`): one stage, off by default. The model reads the file itself
  (PDF, scans, images, Office files) and citations come from the PDF's own words or local OCR, with no model. About
  half the cost and 2.5 times faster on a 43-page 10-Q, but less accurate (102-110 values against 99-129) and with
  3-4 times fewer citations.
- A value the document does not give stays null, also for a required field: never an invented value.

Full research record: `docs/research/extraction.md`.

## 0.2.0 (2026-11-18)

Extraction costs what the answer costs again, and stays reliable on schemas of any size. Breaking: the
`field_report` response key (`report` in Python, `fields_to_check` in MCP) is gone; its useful parts are opt-in.

- The model writes only your schema's fields. 0.1.8 asked for a source, a printed form and notes for every value in
  the same reply, also with citations off; on large schemas that reply ran past the length limit, and the recovery
  (pages and fields in halves) multiplied the calls. That is removed.
- Structured outputs for any schema size: your schema is rewritten in code (every field required, "" for a value
  the document does not have, numbers and yes/no as text), the API enforces it, and the answer is turned back into
  your types and checked against your schema, patterns and ranges included. Schemas over 40 fields are asked in
  groups before any call, in parallel, each seeing the whole document; a group the API will not compile is split
  further (a one-token check, remembered on disk per schema).
- Citations are found by code: each value is looked up on the parsed pages, including dates and numbers printed
  another way (`03/04/2025`, `$4,200.00`); a value printed in several places is cited next to the rest of its object
  (one short `_block` label per object, removed from the result). A value whose words were not placed by the parser
  is found in the page's own words (form amounts printed apart from their labels).
- A reply that is cut off is split by fields, lists apart from the rest, with all pages in view; only a single long
  list is split by pages. A field is reported as unreadable only if it is still empty at the end.
- Opt-in: `review=True` (`settings.review`) lists values to check, found by code with no model call
  (`not_found_on_page`, `ambiguous_date`, `struck_through`, `void_page`, `list_may_be_incomplete` counted on rows
  with an amount, ...); `alternatives=True` or a list of fields (`settings.alternatives`) returns other values the
  document gives for a field, with where they are printed; `derived_values=False` asks for printed values only.
  CLI: `--review`, `--alternatives [FIELD ...]`, `--printed-only`.
- No more "inferred" lines in `warnings`, and no automatic re-ask for lists that look short.
- Enum values that differ only in case are matched ("PAID" to "paid").
- Each group of a large schema is told which fields are asked separately, so neighbouring objects (buyer and
  seller) are not mixed up. An optional object the document does not have comes back as null, and "null" written
  as text is null where your schema allows it. A value the document gives in another form ("USA" for a country
  code) is written in the field's form; a field the document does not answer stays empty instead of guessed.
- Measured on 12 test documents against 0.1.8: 2.6x cheaper and 2.8x faster at extraction, about the cost and time
  of 0.1.6; citations on the right words 93.6% (0.1.8: 84.7%). On a 5-contract test pack: 98.5% of fields right
  (0.1.8: 98.7%), at a quarter of the cost and an eighth of the time.

## 0.1.8 (2026-11-17)

- Field report: every extracted field says how it was found. `conflict` when the document gives two values (every
  candidate with its page), `inferred` when the document never states it, and `blank`, `not_applicable`,
  `not_in_document` or `unreadable` instead of a bare null. Flags for conversions to check (day/month order,
  parentheses, "in thousands", percentages, currency), crossed-out values and pages stamped VOID. Lists with fewer
  items than their table are asked for again, then reported. In `field_report` (HTTP), `report` (Python),
  `fields_to_check` (MCP), and in `warnings`. On by default; `settings.field_report: false` turns it off.
- Python 3.13 is supported.
- `readanydoc extract --instructions @rules.md` reads long instructions from a file.
- The "figure blocks could not be located" warning only appears when figure images, descriptions or chart data were
  asked for.

## 0.1.7 (2026-11-17)

- Extract on deep schemas with citations on no longer comes back empty. A broken model reply is never read as a
  fragment: it is repaired only when the result fits the schema exactly, otherwise asked for again, then in halves.
  A field that still cannot be read says so ("could not be read") instead of "not found in the document".
- Citations are asked for as a flat source list next to the data, not by wrapping every value: less for the model to
  get wrong on deep schemas. Fields the model writes one level off are put back.
- Structured outputs are used on the Anthropic API whenever the schema fits the API's limits.

## 0.1.6 (2026-11-15)

- After the first successful `readanydoc parse` or `extract` in a terminal, one line asks for a GitHub star, once
  ever. Never in scripts, CI, the server, the MCP server or the Python library; `READANYDOC_NO_STAR=1` turns it off.

## 0.1.5 (2026-11-15)

- The MCP server is part of the base install: `pip install readanydoc` then `readanydoc mcp` (the `mcp` extra still
  works and installs nothing more).
- Claude Code plugin pinned to an exact commit, as the Claude plugin directory requires; submitted to the directory.
- Privacy page in the docs.

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
