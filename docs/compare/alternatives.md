---
title: Reducto alternatives, open source and hosted
description: How ReadAnyDoc compares with Reducto, LlamaParse, Docling, Marker, Unstructured and cloud document AI services, and when to pick which.
---

# Reducto alternatives

Teams look for an alternative to [Reducto](https://reducto.ai) for three reasons: price per page, keeping documents
on their own model account, and not wanting a closed service at the centre of a pipeline. This page compares the
common options honestly. We measured **only Reducto and the plain model** against ReadAnyDoc; for the others we
describe what they are, not how accurate they are.

## The short version

| | Kind | Licence | Reads pages with | Citations per value | Reducto-compatible API |
|---|---|---|---|---|---|
| **ReadAnyDoc** | library, server, MCP | Apache-2.0 | a vision LLM you choose (Claude Haiku 5.5 by default) plus local layout and OCR | yes, word-level | yes |
| Reducto | hosted API | closed | its own models | yes | — |
| LlamaParse | hosted API (LlamaIndex) | closed service | LLM / VLM based | see LlamaIndex docs (LlamaExtract) | no |
| Docling | library (IBM) | MIT | local layout, table and OCR models | positions per element | no |
| Marker | library | GPL-3.0 code, model weights with commercial limits | local models, optional LLM | no | no |
| Unstructured | library and hosted API | Apache-2.0 library | local models and OCR, hosted options | element metadata | no |
| Azure Document Intelligence, AWS Textract, Google Document AI | cloud APIs | closed | provider models | boxes per word | no |

Check each project's own site before deciding: features and licences change.

## Measured: ReadAnyDoc against Reducto

On public benchmarks, each scored with the benchmark's own grader (full method in [Benchmarks](../resources/benchmarks.md)):

- **Ahead**: olmOCR-bench (+3.3, multi-column and small print), OmniDocBench text, long-document extraction.
- **Level**: tables (RD-TableBench, Reducto's own table benchmark) and forms (FUNSD).
- **Behind**: page layout (about 1 point), formulas, header and footer removal, and speed (about 20-50 s a page
  against about 5 s).
- **Price**: about $0.003-0.005 a page in the default engine mode ($0.001-0.002 in model-only mode) on your own Anthropic account, against about $0.01 at Reducto's list price.

## When to pick which

- **You use Reducto today and want to cut cost or keep data on your own model account**: ReadAnyDoc. Keep your
  Reducto SDK code and [change one URL](../migrate/from-reducto.md).
- **You need typed fields with a citation for every value** (finance, insurance, legal review): ReadAnyDoc or Reducto.
- **No model provider at all; everything on your own hardware**: Docling or Marker, or ReadAnyDoc pointed at a
  self-hosted OpenAI-compatible vision model (accuracy unmeasured).
- **Speed matters more than cost** (interactive upload, sub-10-second pages): Reducto or a cloud document API.
- **Formula-heavy scientific papers**: Reducto scored higher than ReadAnyDoc on formulas in our tests.

## See it on real pages

[Two real documents, end to end](../examples/real-documents.md): a mortgage Loan Estimate with every citation drawn
on the page, and a faded 1904 invoice read into a table.
