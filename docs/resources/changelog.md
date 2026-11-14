# Changelog

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
  ([BENCHMARKS.md](benchmarks.md)).
