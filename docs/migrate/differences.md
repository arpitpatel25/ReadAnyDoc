# Differences and limits

What is different from Reducto, so nothing surprises you after switching.

## Behaviour

| Area | Reducto | ReadAnyDoc |
|---|---|---|
| Speed | about 5 s a page | 20 to 50 s a page with the engine, 8 to 28 s model only; pages of one document are read in parallel |
| Page layout | ahead by about 1 point on OmniDocBench block types | 83.4 against 84.6 |
| Formulas | ahead | behind (formula edit distance 0.155 against 0.133) |
| Headers and footers left out | 90.1 | 90.3 |
| Text, multi-column pages | | ahead (olmOCR-bench 85.4 against 80.9) |
| Long documents (extraction) | returns a few rows of long tables | ahead (LongExtract rows 56.4% against 9.1%; 10-Q values 79.1% against 48.9%) |
| Very large images | accepted | a page image over 10 MB, 8,000 pixels a side or 50 megapixels fails (not shrunk first) |
| Pages the provider will not return | not applicable | about 1% of pages in large public sets are declined by the model provider (content filtering). They fall back to plain OCR text at `low` confidence, or to a second model with `READANYDOC_REFUSAL_READER`. The response says so in `warnings` |
| Confidence | | tiers from how a value was found, not calibrated probabilities |

## Response fields

- `usage.credits` is always `null` (no billing); `studio_link` is `null`.
- `pdf_url` is set for PDF inputs only.
- `usage.model_usage` (tokens per model) is an addition.
- Extract responses always include `warnings`.

## Not available

- `POST /pipeline` and `/pipeline_async` (deprecated by Reducto) return 501 with advice to chain `/parse` and `/extract`.
- `/configure_webhook` has no Svix portal; it returns and sets the default webhook URL.
- `settings.timeout`, `tenant_throttling`, `force_file_extension`, `hybrid_vpc` are accepted and ignored.
- No hosted Studio. Use the library, the CLI or your own UI.

## Known issues

- Table-heavy documents take longer than plain ones. Table re-reads are capped per page (16 re-reads, 180 seconds,
  `READANYDOC_TABLE_SECONDS`) and `READANYDOC_DOCUMENT_TIMEOUT_SECONDS` (30 minutes by default) bounds a whole document.
- Long list extractions are checked against the parsed tables' row counts and a short segment is extracted again; if
  a gap remains it is listed in `warnings`. Use `array_extract=True` for long tables and check `warnings`.
- A page the model provider declines to read falls back to plain OCR text with `low` confidence unless
  `READANYDOC_REFUSAL_READER` names a second model.
