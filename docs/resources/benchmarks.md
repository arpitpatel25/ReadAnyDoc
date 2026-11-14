# Benchmarks

!!! tip "Every score, document by document"
    Per-page and per-table scores for all three systems are in [`results/`](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/results), and the scripts that ran them in [`research/benchmarks/`](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/research/benchmarks).

Three systems on the same documents with the same requests (November 2026):

- **Reducto r-1**: Reducto's hosted API, default settings.
- **Model only (Haiku 5.5)**: ReadAnyDoc's `model-only` profile: one Claude Haiku 5.5 read per page, nothing else.
- **Engine (Haiku 5.5)**: ReadAnyDoc's `engine` profile, the default. A test in `tests/test_pipeline.py` checks that
  the shipped profiles are exactly the configurations measured here.

Every score comes from the benchmark's own grader or a deterministic script; no model judges the output. 95%
confidence intervals are bootstrap intervals over documents; "paired" differences resample the same documents for
both systems. Pages used while developing ReadAnyDoc are reported separately as "the sample"; the headline numbers
hold on the pages never seen during development.

## Results

| Benchmark | Measure | Reducto r-1 | Model only | Engine |
|---|---|---|---|---|
| [olmOCR-bench](https://github.com/allenai/olmocr), 1,403 pages | official score, % | 80.9 | 82.2 | **84.2** |
| | multi-column | 65.6 | 79.8 | **81.0** |
| | long tiny text | 84.2 | 93.2 | **93.9** |
| | headers and footers | **90.1** | 70.4 | 81.2 |
| | old scans | 49.0 | 47.5 | **50.4** |
| | tables | **92.7** | 91.7 | 92.2 |
| [OmniDocBench](https://github.com/opendatalab/OmniDocBench), 1,651 pages | text edit distance (lower is better) | 0.095 | **0.081** | **0.081** |
| | table TEDS, % | **87.9** | 85.4 | 87.0 |
| | reading-order edit distance (lower) | **0.161** | 0.167 | 0.170 |
| | formula edit distance (lower) | **0.133** | 0.158 | 0.156 |
| | layout: block type right, % | **84.6** | 55.6 | 78.3 |
| | layout: block found at IoU >= 0.5, % | **67.2** | 26.5 | 61.1 |
| [RD-TableBench](https://huggingface.co/datasets/reducto/rd-tablebench), 1,000 tables | Reducto's own table score | **88.85** | 88.20 | 88.17 |
| [FUNSD](https://guillaumejaume.github.io/FUNSD/), 199 forms | question/answer links found, % | **78.6** | 71.6 | 77.4 |
| LongExtract, 10 long documents | table rows extracted, % | 53.9 | 75.4 | **75.9** |

What the differences mean, using the paired intervals:

- **The engine against one model read**: clearly better on layout (+23 points block types, +35 boxes found),
  forms (+5.8), headers and footers (+11) and the olmOCR overall score (+2.0); level on tables, plain text and long
  extraction. The engine costs about 2-3x as much per page.
- **The engine against Reducto**: ahead on olmOCR (+3.3; multi-column and small print), on OmniDocBench text and on
  long extraction; level on RD-TableBench and FUNSD (differences inside the intervals); behind on layout (-6),
  formulas, header and footer removal, and speed.

## Cost and speed

| | Reducto r-1 | Model only | Engine |
|---|---|---|---|
| Cost per page | $0.010 (list price) | $0.001-0.002 | $0.003-0.005 |
| Seconds per page (median, tables) | 4 | 8 | 20-50 |

ReadAnyDoc costs are Anthropic API charges measured on these runs. Scanned pages with tables and handwritten pages
cost more (up to about $0.03 a page on handwriting, which goes to Sonnet 5.5).

## Method and caveats

- Same input files and same request to every system. Reducto was called through its public API in November 2026.
- olmOCR-bench is scored with its official `benchmark.py`; RD-TableBench with Reducto's published grading code;
  OmniDocBench and FUNSD with scripts that apply the benchmark's definitions to each system's blocks; LongExtract
  counts expected table rows found in the extraction result.
- LongExtract ran on the 10 shortest documents of the set for all three systems; Reducto alone ran on 32.
- Between 1.5% and 3% of pages in these sets came back empty from Claude because the provider declined to return
  their text (content filtering). They are scored as empty, which lowers both ReadAnyDoc columns; the response
  reports them in `warnings`.
- Only Claude Haiku 5.5 (with Sonnet 5.5 for handwriting) was measured. Other models work with ReadAnyDoc; their
  accuracy is unknown until measured.
- Other parsers (Mistral OCR, LlamaParse and so on) were not run.
- Benchmark data is not redistributed here; each benchmark's own license applies. The scripts that ran these
  numbers are in `research/` (see [research/README.md](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/research)).
