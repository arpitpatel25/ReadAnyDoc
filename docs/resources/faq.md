# FAQ and troubleshooting

## General

**Is it really free?**
Yes. ReadAnyDoc is Apache-2.0. You pay only your model provider for the calls it makes; every result reports what
they cost.

**Is it as good as Reducto?**
Ahead on reading text and multi-column pages, level on tables and forms, behind on page layout, formulas, removing
headers and footers, and speed. See [Benchmarks](benchmarks.md).

**Which models can I use?**
Any Claude model, OpenAI models, or a model you host behind an OpenAI-compatible server. See
[Choosing models](../concepts/models.md). Only Claude Haiku 5.5 is benchmarked.

**Do I need a server?**
No. The library runs in your process. Run the server for Reducto's API, other languages, or a shared service.

**Where do my documents go?**
To your machine and the model provider you configure, under your account. Nothing goes to the ReadAnyDoc project.

**Is the confidence a probability?**
No. It is a tier (`high`, `low`) from how a value was found. Use it to decide what to check first.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The first parse takes minutes | The layout and OCR weights (100 to 200 MB) download once. Later runs are normal speed |
| `warnings` says the layout model is not installed | `pip install "readanydoc[engine]"` |
| 401 or `authentication_error` from the provider | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` missing or wrong where the code runs |
| `warnings` says a page used OCR text (content filtering) | The provider declined that page, so its plain OCR text was used at `low` confidence. Set `READANYDOC_REFUSAL_READER` to a second model (for example `anthropic_api:sonnet` or `openai:gpt-5`) to read such pages again |
| 415 for a `.doc`, `.xls`, `.ppt` or `.heic` file | Install LibreOffice or a HEIC decoder; the error names the command |
| 442 | The PDF is password protected: send `settings.document_password` |
| 504 `timeout` | Raise `READANYDOC_DOCUMENT_TIMEOUT_SECONDS`, or split the document |
| Rate-limit errors | Lower `READANYDOC_API_CONCURRENCY` or raise your provider tier |
| Reducto's SDK says an API key is required | Pass any string; it is only checked when `READANYDOC_API_KEY` is set |

Still stuck? [Open an issue](https://github.com/arpitpatel25/ReadAnyDoc/issues) with the version, mode, model and
the command you ran.

## Where to ask

Questions that are not answered here: [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions).
Bugs: [open an issue](https://github.com/arpitpatel25/ReadAnyDoc/issues/new/choose).
