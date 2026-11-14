# Differences and limits

What is different from Reducto, so nothing surprises you after switching.

## Behaviour

| Area | Reducto | ReadAnyDoc |
|---|---|---|
| Speed | about 5 s a page | 20 to 50 s a page with the engine, 8 to 28 s model only; pages of one document are read in parallel |
| Page layout | ahead by about 6 points on OmniDocBench block types | 78.3 against 84.6 |
| Formulas | ahead | behind (formula edit distance 0.156 against 0.133) |
| Headers and footers left out | 90.1 | 81.2 |
| Text, multi-column pages | | ahead (olmOCR-bench 84.2 against 80.9) |
| Pages the provider will not return | not applicable | 1.5% to 3% of pages in large public sets come back empty because the model provider declines them (content filtering). The response says so in `warnings` |
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

- A small number of table-heavy documents can take much longer than average; `READANYDOC_DOCUMENT_TIMEOUT_SECONDS`
  (30 minutes by default) bounds it.
- On very long list extractions, one run in our tests lost most rows; `array_extract=True` and checking
  `warnings` are recommended for long tables.
