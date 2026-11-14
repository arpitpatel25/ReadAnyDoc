# Contributing

Thanks for helping. Issues and pull requests are welcome.

## Set up

```bash
git clone <this repo> && cd readanydoc
uv sync --extra engine          # Python 3.12; installs the dev tools too
uv run pytest -q                # about 900 tests, no network, no API key needed
```

About 160 tests skip without the cached benchmark data (`READANYDOC_SPIKE_DIR`, `READANYDOC_REFERENCE_DIR`); that is
expected. Tests never call a model provider: they use recorded outputs and fake HTTP.

## Where things are

| Path | What |
|---|---|
| `readanydoc/library.py` | `readanydoc.parse` and `readanydoc.extract` |
| `readanydoc/profiles.py` | the two shipped profiles, `engine` and `model-only` |
| `readanydoc/pipeline/` | the stages: intake, triage, layout, read, verify, ground, assemble, shape |
| `readanydoc/readers/` | model adapters (Anthropic, OpenAI, OpenAI-compatible) and the model spec grammar |
| `readanydoc/tasks/` | extract, split, classify, edit |
| `readanydoc/api/` | the Reducto-compatible server, jobs, storage, webhooks |
| `research/` | benchmark scripts, experimental profiles, the research log |

## Rules for changes

- **Accuracy first.** A change to what the shipped profiles do needs a benchmark run on pages not used to develop it
  (see [BENCHMARKS.md](benchmarks.md)), and the result in the pull request. `tests/test_pipeline.py` fails when a
  shipped profile changes, on purpose: update it together with the numbers.
- **Never change what the model wrote without independent evidence** (a second read that agrees, the PDF's own text,
  a checked total). Record evidence and confidence instead of guessing.
- **Experiments go in `research/`** as a new profile, not as a flag on a shipped one.
- Add a line to [docs/decisions.md](../decisions.md) when you change the document format, a default, or a public
  interface, with the reason.
- Tests for new behaviour; no network in tests.
- Small pull requests with one purpose are reviewed fastest.

## Reporting bugs

Include the ReadAnyDoc version, the mode and model, the command or code you ran, and what you expected. If you can,
attach a page that shows the problem (only documents you are allowed to share). Security problems: see
[SECURITY.md](../deploy/security.md), not a public issue.
