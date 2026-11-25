# Test it yourself

Compare ReadAnyDoc with Reducto, with a plain model call, or with its own other modes, on public test sets or on
your own documents. Each system uses your own key. Every score comes from code comparing the output with an answer
key; no model judges anything.

```bash
pip install "readanydoc[engine]"
export ANTHROPIC_API_KEY=...        # for ReadAnyDoc and for a plain model call
export REDUCTO_API_KEY=...          # only to include Reducto

readanydoc bench list
readanydoc bench run invoices --systems readanydoc,plain:claude-haiku-5-5,reducto --limit 10
```

Before anything is sent, the run shows how many documents and pages it will process and a rough cost per system,
and asks you to confirm. `--budget 2` stops it from starting new documents once $2 has been spent. Results are
saved as each document finishes, so a stopped run picks up where it left off.

## Testing a code change for free

`--replay DIR` records every model call in `DIR` and answers a repeated call from disk. Run a set once with it, change
the code, and run again: only the calls whose request changed are sent (and paid for), so the difference in the
scores is the change, not the model's run-to-run variation. `--replay-only` refuses any call that is not recorded, so
the run cannot spend anything.

```bash
readanydoc bench run sec-filings --systems readanydoc --replay ./calls              # first run: pays, records
# ...change the code...
readanydoc bench run sec-filings --systems readanydoc --replay ./calls --replay-only --out ./after --yes
```

Costs printed during a replayed run include the recorded calls; the real spend is in `DIR/live.jsonl`. A change that
alters what is sent to the model (prompts, page images) needs new calls; a change after the model's answer (checks,
citations, confidence) replays completely.

## Test sets

| Set | Documents | What it tests | Licence |
|---|---|---|---|
| `invoices` | 181 | Digital PDFs in US, UK, EU and ZA formats, 6 currencies, VAT, credit notes, multi-page, freight, scans, phone photos, handwritten invoice books, several invoices in one file | CC-BY-4.0 (synthetic, made by an invoice-software vendor) |
| `receipts` | 100 | Photographed shop and restaurant receipts (CORD): items, quantities, prices, totals | CC-BY-4.0 |
| `financial-reports` | 7 | 10-Q and 10-K filings: statement values with periods, units, scales and segments | MIT (ExtractBench) |
| `credit-agreements` | 10 | Parties, facilities, amounts and terms from long credit agreements | MIT (ExtractBench) |
| `research-papers` | 6 | Title, authors, affiliations, abstract, identifiers | MIT (ExtractBench) |
| `resumes` | 7 | Experience, education, skills, certifications | MIT (ExtractBench) |
| `sports-results` | 5 | Rankings, times and records from long result tables | MIT (ExtractBench) |
| `long-documents` | 50 | Every row of long repeated tables in government and business reports (LongExtract) | CC-BY-4.0 |
| `contracts` | 510 | Title, parties, agreement, effective and expiry dates, governing law, and whether 12 kinds of clause are present, against lawyers' answers (CUAD) | CC-BY-4.0 (The Atticus Project) |
| `sec-filings` | 16 | Income statement, balance sheet and cash-flow values of 16 companies' 10-Qs (40-100 pages), checked against the numbers each company tagged itself in its SEC filing (XBRL): no hand-made answer key | PDFs: FinanceBench (CC-BY-NC-4.0, research use); answer key: SEC data, public domain |

Each set is downloaded on first use into the ReadAnyDoc cache, pinned to a fixed revision. A run takes a sample by
default (`--limit` to change it). Check a set's licence before using it.

## Systems

| Name | What it is |
|---|---|
| `readanydoc` | ReadAnyDoc as shipped: the document is parsed first, then the schema is filled, every call enforced, with citations |
| `readanydoc-one-stage` | ReadAnyDoc without parsing: the model reads the file itself. About half the cost, faster, fewer citations |
| `readanydoc-model-only` | ReadAnyDoc's plain page reading (no layout model or checks), then extraction |
| `plain:<model>` | The file and the schema sent to one model in one call, as you would without any pipeline (for example `plain:claude-haiku-5-5`). No enforced shape, no citations |
| `reducto` | Reducto's Extract API with citations. Cost is estimated at its list price ($0.02 per page) |

To compare another model in ReadAnyDoc, add its spec: `readanydoc:claude-sonnet-5-5`.

## Your own documents

```bash
readanydoc bench mine ./contracts --schema contract_schema.json --answers ./answer_keys \
    --systems readanydoc,reducto --instructions @rules.md
```

Every file in the folder is extracted with the schema. Answer keys are optional: one JSON file per document
(`<file name without extension>.json`) in the shape of your schema. With them you get accuracy; without them you
still get cost, time, failures and each system's output side by side.

## What you get

`readanydoc-bench/<set>-<time>/report.html`:

- **Per system:** answer-key values found, output values that are right, documents fully right, how often values
  labelled `high` and `low` confidence were right (a useful label has high right more often), outputs that pass the
  JSON Schema (by the standard `jsonschema` validator, with `null` for a missing value accepted; strictly too), values
  with a citation, citations whose box shows the value, cost per document and median time.
- **Per document:** each system's score, cost and time, with any error.
- **A page viewer for every document:** each system's citation boxes on the page, next to the answer key and each
  system's values. Click a value to see its box, or click a box to see its value.

The raw results are JSON next to the report, one file per system and document. `readanydoc bench rescore <folder>`
scores a finished run again without calling any model.

## How scoring works

- **Numbers** match within 0.5% whatever their printed form ("$1,575.00", "1.575,00", "(200)").
- **Dates** match when they are the same day in any format.
- **Text** matches after case, spacing and punctuation are ignored (fuzzy for long text).
- **List items** are paired with the answer key's items before comparing, so a different order does not count
  against a system.
- **A failed document** counts as an empty answer.
- **Citations** are checked on PDFs by reading the words inside each box from the PDF itself.

Our grader is not always each set's official scorer: ExtractBench's own grader uses a model for some text fields,
and the invoice set has its own script. We use the sets' rules where they differ, for example credit-note amounts
compared by size. See `readanydoc/bench/grade.py`.

## From Claude, Cursor or another agent

With the MCP server installed (`readanydoc mcp`), ask in plain words, for example "Compare ReadAnyDoc and Reducto on 10
invoices", or "Test these contracts in ~/contracts with this schema". The agent calls `list_test_sets` and
`run_test`. `run_test` first returns the plan and its rough cost without spending anything, and runs only when
called again with `confirm=true`, after you agree.

## Python

```python
from readanydoc import bench

plan = bench.prepare("receipts", ["readanydoc", "plain:claude-haiku-5-5"], limit=20)
print(plan["estimate"])                 # documents, pages, rough cost per system
summary = bench.run_prepared(plan, budget_usd=1)
print(summary["systems"], summary["report"])
```

## The full benchmarks behind our published numbers

The parsing benchmarks (olmOCR-bench, OmniDocBench, RD-TableBench, FUNSD) were run with the scripts in
[`research/benchmarks/`](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/research/benchmarks), with results in
[`results/`](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/results). They are not part of `readanydoc bench`
yet. Charts: `python research/benchmarks/charts.py run OUT` reads the 58 charts of ChartParse-Bench (CC-BY-4.0) with
ReadAnyDoc's chart reader and scores the data points (about $0.003 a chart; `--replay` works there too).
