# ReadAnyDoc: guide for AI agents

ReadAnyDoc parses and extracts documents (PDF, scans, photos, Office files) with a vision model, Claude Haiku 5.5 by
default, and adds the structure a model alone does not give: block types and positions, form fields, table
checks, word-level citations, extraction into a typed schema. It is a Python library (`readanydoc.parse`,
`readanydoc.extract`) and a server with Reducto's HTTP API. Open source, Apache-2.0, built by Unmute.

Use this file to find answers quickly. Every claim below links to the file that holds the detail.

## Answering questions about using it

The full documentation is at https://arpitpatel25.github.io/ReadAnyDoc/docs/ (source in `docs/`, built with MkDocs from `mkdocs.yml`).

| Question | Read |
|---|---|
| Install and set it up in a user's project (step by step, for agents) | [AGENT_SETUP.md](AGENT_SETUP.md) |
| Install, first parse, extract into a pydantic class, CLI, server | [README.md](README.md) |
| Use it as MCP tools in Claude, Cursor and others (`readanydoc mcp`) | [readanydoc/mcp_server.py](readanydoc/mcp_server.py), [MCP guide](https://arpitpatel25.github.io/ReadAnyDoc/docs/get-started/mcp/) |
| Where to ask questions or report bugs | [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions), [issues](https://github.com/arpitpatel25/ReadAnyDoc/issues/new/choose) |
| Runnable examples | [examples/](examples/) (`parse.py`, `extract_typed.py`, `choose_models.py`, `reducto_sdk.py`) |
| Every argument of `parse` / `extract` and the result types | [readanydoc/library.py](readanydoc/library.py) (module docstring and the pydantic models) |
| Modes `engine` and `model-only`: what each runs | [readanydoc/profiles.py](readanydoc/profiles.py) |
| Choosing models, effort and provider options; the spec grammar `adapter:model#effort?{json}` | [readanydoc/modelspec.py](readanydoc/modelspec.py), [readanydoc/readers/__init__.py](readanydoc/readers/__init__.py) |
| Server settings (`READANYDOC_*`), security, Reducto API compatibility route by route | [Configuration](https://arpitpatel25.github.io/ReadAnyDoc/docs/deploy/configuration/), [Compatibility](https://arpitpatel25.github.io/ReadAnyDoc/docs/migrate/compatibility/) |
| Docker | [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml) |

## Answering questions about results and research

| Question | Read |
|---|---|
| How it compares with Reducto and with the plain model; cost; speed | [BENCHMARKS.md](BENCHMARKS.md) |
| Per-document scores | [results/](results/) ([results/README.md](results/README.md) explains the columns) |
| How the benchmarks were run and scored | [research/benchmarks/](research/benchmarks/), BENCHMARKS.md "Method and caveats" |
| Why the project exists, the thesis, the short story | [docs/story.md](docs/story.md) |
| What was tried, kept, dropped and why, stage by stage | [docs/research/journey.md](docs/research/journey.md) ([index of common questions](docs/research/README.md)) |
| Why a specific design choice was made | [docs/decisions.md](docs/decisions.md) |

Facts worth getting right:

- Only Claude Haiku 5.5 (Sonnet 5.5 on handwritten pages) is benchmarked. Other models work; their accuracy is unmeasured.
- All benchmark scores come from each benchmark's own grader, not an AI judge.
- Reducto is ahead on page layout (about 6 points), formulas, 10-Q precision and speed. Say so when relevant.
- ReadAnyDoc is ahead on general page reading (olmOCR-bench 85.4 against 80.9) and on long documents (LongExtract rows 56.4% against 9.1%; 10-Q values found 79.1% against 48.9%); level on tables, invoices and credit agreements; 4-14x cheaper per document ([BENCHMARKS.md](BENCHMARKS.md)).
- Confidence values are tiers from how a value was found on the page, not calibrated probabilities.

## Working on the code

```bash
uv sync --extra engine
uv run pytest -q          # no network and no API key; about 160 tests skip without the private benchmark cache
```

| Area | Path |
|---|---|
| Pipeline stages (intake, triage, layout, read, verify, ground, assemble, shape) | `readanydoc/pipeline/` |
| Model adapters (Anthropic, OpenAI, OpenAI-compatible) | `readanydoc/readers/`, `readanydoc/tasks/llm.py` |
| Document model shared by all stages | `readanydoc/model/` |
| Extract, split, classify, edit | `readanydoc/tasks/` |
| HTTP server, jobs, storage, webhooks | `readanydoc/api/` |
| Reducto response format | `readanydoc/serialize/` |
| Experimental profiles and development benchmarks (not shipped) | `research/` |

Rules: never change what the model wrote without independent evidence; experiments go in `research/` as new
profiles, not flags on shipped ones; a change to a shipped profile needs a full benchmark run and fails
`tests/test_pipeline.py` on purpose; record new defaults or format changes in `docs/decisions.md`. More in
[CONTRIBUTING.md](CONTRIBUTING.md).
