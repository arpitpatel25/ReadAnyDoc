# Guide for coding agents

If you use Claude Code, Codex, Cursor or another coding agent, point it at the repository. It reads
[`AGENTS.md`](https://github.com/arpitpatel25/ReadAnyDoc/blob/main/AGENTS.md) first, which maps every kind of
question (using it, results, research, design choices) to the file that answers it, and
[`llms.txt`](https://github.com/arpitpatel25/ReadAnyDoc/blob/main/llms.txt) for a short index.

## Fastest path

1. `pip install "readanydoc[engine]"` and set `ANTHROPIC_API_KEY`.
2. Pick one interface:
    - Python in the same process: `readanydoc.parse` / `readanydoc.extract` ([Python reference](../reference/python.md)).
    - HTTP from another language or service: `readanydoc serve` ([HTTP reference](../reference/http.md)).
    - An existing Reducto integration: change the SDK's `base_url` ([Migrate from Reducto](../migrate/from-reducto.md)).
3. For structured data, define a pydantic class and call `extract`; read values from `out.data` and their sources
   from `out.citations`.

## Prompt you can give your agent

```text
Use the open-source library readanydoc (pip install "readanydoc[engine]", docs at
https://arpitpatel25.github.io/ReadAnyDoc/docs/). Define a pydantic model for the fields we need, call
readanydoc.extract(path, schema=Model), and store out.data together with out.citations so a reviewer can
see where each value came from. Log out.usage.cost_usd and out.warnings.
```

## Facts worth getting right

- Only Claude Haiku 5.5 (Sonnet 5.5 for handwriting) is benchmarked. Other models work; their accuracy is unmeasured.
- Benchmark scores come from each benchmark's own grader, not an AI judge.
- Confidence values are tiers (`high`, `low`) from how a value was found, not calibrated probabilities.
- Split, classify and edit are server features; the library covers parse and extract.
