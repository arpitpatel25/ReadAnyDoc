# Benchmarks

!!! tip "Every score, document by document"
    Per-document scores for both systems are in [`results/`](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/results), and the scripts that ran them in [`research/benchmarks/`](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/research/benchmarks) and [`readanydoc/bench/`](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/readanydoc/bench).

ReadAnyDoc against Reducto on the same documents with the same requests (November 2026): olmOCR-bench with 0.4.7,
the other benchmarks with 0.4.5 (0.4.7 changes only how running headers, footers and page numbers are labelled):

- **Reducto**: Reducto's hosted API, default settings (r-1 for parsing, Extract for extraction).
- **ReadAnyDoc**: the default `engine` mode with Claude Haiku 5.5 (Sonnet 5.5 on handwritten pages). A test in
  `tests/test_pipeline.py` checks that the shipped profile is exactly the configuration measured here.

Every score comes from the benchmark's own grader or from code; no model judges the output. Parsing intervals are
bootstrap 95% intervals over pages. Per-document results are in [`results/`](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/results), and every extraction run can
be repeated with `readanydoc bench run <set>`.

## Parsing

| Benchmark | Measure | Reducto | ReadAnyDoc |
|---|---|---|---|
| [olmOCR-bench](https://github.com/allenai/olmocr), 1,403 pages | official score, % | 80.9 | **85.4** |
| | multi-column | 65.6 | **80.9** |
| | long tiny text | 84.2 | **94.1** |
| | headers and footers | 90.1 | **90.3** |
| | old scans | 49.0 | **52.9** |
| | tables | **92.7** | 89.8 |
| [OmniDocBench](https://github.com/opendatalab/OmniDocBench), 1,651 pages | text edit distance (lower is better) | 0.095 | **0.073** |
| | table TEDS, % | **87.9** | 87.0 |
| | reading-order edit distance (lower) | **0.161** | 0.162 |
| | formula edit distance (lower) | **0.133** | 0.155 |
| | layout: block type right, % | **84.6** | 79.0 |
| | layout: block found at IoU >= 0.5, % | **67.2** | 61.5 |
| [RD-TableBench](https://huggingface.co/datasets/reducto/rd-tablebench), 1,000 tables | Reducto's own table score | 88.85 | 88.84 |
| [FUNSD](https://guillaumejaume.github.io/FUNSD/), 199 forms | question/answer links found, % | **78.6** | 77.4 |

ReadAnyDoc is ahead on general page reading (olmOCR-bench +4.5, mostly multi-column pages and small print) and on
OmniDocBench text; level on tables (RD-TableBench, OmniDocBench TEDS), reading order and forms (differences inside
the intervals); behind on layout (block types -5.6), formulas and olmOCR-bench tables (89.8 against 92.7). Headers and
footers are level since 0.4.7 (90.3 against 90.1; 81.1 in 0.4.5), where the page read labels them itself.

## Extraction

`readanydoc bench`, scored by code against each set's answer key (numbers within 0.5%, dates the same day, text
after case and punctuation are ignored, list items paired before comparing). "Found" is the share of the answer key
that is right in the output; "right" is the share of the output that is in the answer key.

| Test set | Documents | ReadAnyDoc found / right | Reducto found / right | Cost per document, ReadAnyDoc / Reducto | Median seconds, ReadAnyDoc / Reducto |
|---|---|---|---|---|---|
| Invoices (photos, scans, handwriting, credit notes, 6 currencies) | 20 | 99.8% / 99.6% | 99.8% / 99.6% | $0.0045 / $0.027 | 18 / 5 |
| Long reports (LongExtract), table rows | 6 | **56.4%** / 99.3% | 9.1% / 66.7% | $0.44 / $1.02 | 1,036 / 34 |
| 10-Q and 10-K filings (ExtractBench), 43-82 pages | 5 | **79.1%** / 60.8% | 48.9% / **86.2%** | $0.34 / $1.24 | 354 / 78 |
| Research papers (ExtractBench) | 3 | **68.4%** / **66.6%** | 49.9% / 49.8% | $0.074 / $0.33 | 346 / 99 |
| Credit agreements (ExtractBench), 97-218 pages | 3 | 82.2% / 85.1% | 83.3% / 90.4% | $0.20 / $2.83 | 267 / 16 |
| Sports result tables (ExtractBench) | 5 | 99.6% / 98.6% | 97.4% / 98.8% | $0.015 / $0.060 | 37 / 13 |
| Resumes (ExtractBench) | 7 | 66.5% / 68.9% | 70.9% / 66.8% | $0.011 / $0.060 | 44 / 10 |
| Commercial contracts (CUAD): title, parties, dates, governing law, 12 clause types | 10 | 86.5% / 81.9% | not run | $0.026 / - | 70 / - |
| Receipts (CORD, photographed) | 20 | 93.6% / 81.2% | not run | $0.0027 / - | 13 / - |

Long reports are scored with LongExtract's own grader (row recall / row precision; Reducto returned no rows on 2
of the 6). Reducto costs are at its list
price ($0.02 a page for Extract); ReadAnyDoc costs are the Anthropic charges measured on these runs.

- **Long and dense documents are where the gap is.** On long reports ReadAnyDoc returns most table rows where
  Reducto returns a few (on two of the six it returns every row, Reducto 0% and 18%); on quarterly filings it finds
  far more of the answer key.
- **10-Q precision is lower:** ReadAnyDoc writes more values than the answer key holds (2,224 against 1,407 on one
  filing). Some are real values the key leaves out, some are repeats; the score counts all of them as wrong.
- **Level** on invoices, credit agreements and sports tables; slightly behind on resumes.
- **Cost:** 4 to 14 times cheaper per document. **Speed:** 3 to 30 times slower.

### Where the answers are on the page

| Test set | Values with a citation box, ReadAnyDoc / Reducto | Boxes that show the value, ReadAnyDoc / Reducto |
|---|---|---|
| Invoices | 89.5% / 100% | **94.9%** / 87.9% |
| Credit agreements | 95.4% / 100% | **85.5%** / 78.3% |
| Research papers | 89.6% / 100% | **66.0%** / 40.2% |
| 10-Q and 10-K | 70.2% / 100% | **36.6%** / 14.9% |
| Resumes | 99.9% / 100% | **63.1%** / 55.0% |
| Sports results | 100% / 100% | 89.8% / **96.4%** |
| Contracts (CUAD) | 92.6% / - | 90.5% / - |

A box "shows the value" when the PDF's own words inside it contain the value (checked on digital pages only).

### Modes

The same sets in ReadAnyDoc's three modes (values found, cost per document):

| Test set | Engine, two stages (default) | One stage (`parse=False`) | Model only |
|---|---|---|---|
| Invoices | 99.8%, $0.0045 | 99.8%, $0.0013 | 99.8%, $0.0026 |
| Receipts | 93.6%, $0.0027 | 94.3%, $0.0006 | 91.7%, $0.0012 |
| Contracts (CUAD) | 86.5%, $0.026 | 84.8%, $0.0085 | 86.5%, $0.024 |
| Sports results | 99.6%, $0.015 | 99.4%, $0.0030 | 99.8%, $0.0094 |
| Resumes | 66.5%, $0.011 | 63.5%, $0.0059 | 66.5%, $0.0105 |
| Research papers | 68.4%, $0.074 | 48.0%, $0.032 | 68.8%, $0.073 |

One stage costs a third as much and is as accurate on short documents; on long, dense documents (research papers)
reading the pages first matters.

### Schema validity

Checked with the standard `jsonschema` validator, independent of ReadAnyDoc's own code: every ReadAnyDoc output on
every set passes its JSON Schema (types, required fields, enums, patterns), counting `null` for a value the
document does not give as valid. Strictly, several of these schemas declare optional fields as plain `string`; there
`null` is a schema error for every system tested, Reducto included (resumes 14% strictly valid for both).

## Split, classify and edit

16 cases (tax, mortgage, insurance, healthcare and legal packets; W-2, I-9, court opinions, scanned handwritten
notes; fillable and flat forms, scans and DOCX), scored by code against the expected result:

| Task | Cases | ReadAnyDoc | Reducto |
|---|---|---|---|
| Split (pages per section) | 5 | 5 correct | 5 correct |
| Classify | 6 | 6 correct | 6 correct |
| Edit (expected values present in the filled file) | 5 | 41 of 41 | 41 of 41 |

## Cost and speed (parsing)

| | Reducto | ReadAnyDoc | ReadAnyDoc `model-only` |
|---|---|---|---|
| Cost per page | $0.010 (list price) | $0.003-0.005 | $0.001-0.002 |
| Seconds per page (median, pages with tables) | 4 | 20-50 | 8 |

Scanned pages with tables and handwritten pages cost more (up to about $0.03 a page on handwriting).

## Method and caveats

- Same input files and same request to every system. Reducto's responses were collected through its public API on
  12-19 November 2026 and are scored here with the same code as ReadAnyDoc's.
- Parsing: olmOCR-bench with its official tests; RD-TableBench with Reducto's published grading code; OmniDocBench
  with its official evaluation code; FUNSD with a script that applies the benchmark's definitions. Parsing answers
  were produced on 14-20 November; every page whose code path changed since was parsed again with 0.4.5.
  olmOCR-bench was parsed again with 0.4.7 on 22 November (all 1,403 pages, through the library, 16 pages at a time).
- Extraction samples are small on the long sets (3 to 6 documents) and are fixed by each set's sampler, so anyone
  running `readanydoc bench run <set> --limit <n>` gets the same documents.
- The ExtractBench sets are scored here with our code grader, not ExtractBench's own scorer (which asks a model to
  judge text fields). Code is stricter: a right answer worded differently from the answer key counts as wrong.
- CUAD answers are the lawyers' normalised values (`master_clauses.csv`); fields whose answer is not a plain value are
  not scored. The grader is strict ("China" is not "People's Republic of China").
- A page Claude declines to read (content filtering) gets the page's OCR text, marked low confidence. 39 OmniDocBench
  pages fail outright because the image is too large for the API (over 10 MB, 8,000 pixels a side or 50
  megapixels); they are scored as empty.
- Only Claude Haiku 5.5 (with Sonnet 5.5 for handwriting) was measured. Other models work with ReadAnyDoc; their
  accuracy is unknown until measured. Other parsers (Mistral OCR, LlamaParse and so on) were not run.
- Benchmark data is not redistributed; each benchmark's own licence applies. The scripts are in `research/` (see
  [research/README.md](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/research)) and `readanydoc/bench/`.
