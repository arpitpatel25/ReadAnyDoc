# Benchmark scripts

The scripts that ran and scored the comparison in [BENCHMARKS.md](../../BENCHMARKS.md), kept as they were run.
They expect the benchmark data downloaded next to the repository (see `hf_fetch.py`) and a running ReadAnyDoc
server; paths at the top of each script may need adjusting for your machine.

| Script | What it does |
|---|---|
| `run.py`, `common.py` | send every case to each system (Reducto's API, ReadAnyDoc `model-only`, ReadAnyDoc `engine`), with budget caps and resumable progress |
| `run_rd_tablebench.py` | the same for RD-TableBench |
| `score_olmocr.py` | olmOCR-bench with the official test classes (math tests rendered as upstream) |
| `score_omnidocbench.py` | OmniDocBench end-to-end evaluation without CDM, plus layout matching |
| `score_rd_tablebench.py` | RD-TableBench with Reducto's published grading code |
| `score_funsd.py` | FUNSD question-answer links found as key-value pairs |
| `score_longextract.py` | LongExtract row recall and value accuracy |
| `ci_parse.py` | one shard of olmOCR-bench, OmniDocBench, RD-TableBench or FUNSD through a ReadAnyDoc server, for the cloud workflow `.github/workflows/benchmarks.yml` (start it from the Actions tab: Run workflow, pick a version) |
| `charts.py` | ChartParse-Bench (58 charts): runs the chart reader and scores the data points; self-contained, downloads its data |
| `stats.py` | bootstrap intervals and paired differences |

No model is called while scoring.
