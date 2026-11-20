# Benchmark results, per document

The numbers behind [BENCHMARKS.md](../BENCHMARKS.md), one row per page, table, form or document: `reducto_r1`
(Reducto's API) and `engine` (ReadAnyDoc 0.4.5 with Claude Haiku 5.5), plus `model_only` (ReadAnyDoc's one-read mode)
where it was run. Only scores
are here: no benchmark content and no system output, so each benchmark's own license is untouched.

| File | Benchmark | One row per | Columns per system |
|---|---|---|---|
| `olmocr_bench.csv` | olmOCR-bench, 1,403 pages | page | tests passed, tests |
| `omnidocbench.csv` | OmniDocBench, 1,651 pages | page | text edit distance, reading-order edit distance, layout type recall, mean box IoU, share of boxes at IoU >= 0.5 |
| `rd_tablebench.csv` | RD-TableBench, 1,000 tables | table | table score (0-1) |
| `funsd.csv` | FUNSD, 199 forms | form | labelled question-answer links, links found, labelled words, words found |
| `extraction.csv` | `readanydoc bench`: invoices, receipts, resumes, sports results, research papers, 10-Q/10-K, long reports, credit agreements, CUAD contracts | document and system | answer-key values, values right, values written, schema valid (and strict), citation boxes, USD, seconds |
| `longextract.csv` | LongExtract, 32 documents (10 on all three systems) | document | row recall, row precision, value accuracy, USD, seconds |
| `*_summary.json` | each of the above | - | means with 95% bootstrap intervals, paired differences, cost and timing |

`olmocr_bench.csv` holds the per-page test counts of the full run; the summary (`olmocr_bench_summary.json`) is the
0.4.5 score. `longextract.csv` is the earlier 32-document run with LongExtract's grader; the 0.4.5 long-report
results are in `extraction.csv` (set `long-documents`).

`used_in_development` marks the items we looked at while building; the headline numbers also hold on the rest.
The scripts that produced these files are in [`research/benchmarks/`](../research/benchmarks/).
