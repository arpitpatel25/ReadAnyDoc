# Benchmark results, per document

The numbers behind [BENCHMARKS.md](../BENCHMARKS.md), one row per page, table, form or document, for the three
systems: `reducto_r1` (Reducto's API), `model_only` and `engine` (ReadAnyDoc with Claude Haiku 5.5). Only scores
are here: no benchmark content and no system output, so each benchmark's own license is untouched.

| File | Benchmark | One row per | Columns per system |
|---|---|---|---|
| `olmocr_bench.csv` | olmOCR-bench, 1,403 pages | page | tests passed, tests |
| `omnidocbench.csv` | OmniDocBench, 1,651 pages | page | text edit distance, reading-order edit distance, layout type recall, mean box IoU, share of boxes at IoU >= 0.5 |
| `rd_tablebench.csv` | RD-TableBench, 1,000 tables | table | table score (0-1) |
| `funsd.csv` | FUNSD, 199 forms | form | labelled question-answer links, links found, labelled words, words found |
| `longextract.csv` | LongExtract, 32 documents (10 on all three systems) | document | row recall, row precision, value accuracy, USD, seconds |
| `*_summary.json` | each of the above | - | means with 95% bootstrap intervals, paired differences, cost and timing |

`used_in_development` marks the items we looked at while building; the headline numbers also hold on the rest.
The scripts that produced these files are in [`research/benchmarks/`](../research/benchmarks/).
