# Performance and scaling

## What takes time

Almost all time is spent waiting for the model provider. With the engine, a page takes 20 to 50 seconds
(more for scans with tables and for handwriting); model only takes 8 to 28. Pages of one document are read in
parallel, and so are the zoomed strips of a page.

## Settings

| Variable | Default | Effect |
|---|---|---|
| `READANYDOC_WORKERS` | 4 | Async jobs processed at once |
| `READANYDOC_API_CONCURRENCY` | 32 | Model requests in flight at once across the server. A rate-limit answer pauses all of them, then they resume |
| `READANYDOC_DOCUMENT_TIMEOUT_SECONDS` | 1800 | Longest time one document may take (504 `timeout`) |

## Sizing (starting points, not measured limits)

- **Throughput is bounded by your provider's rate limits.** Raise `READANYDOC_API_CONCURRENCY` only as far as your
  Anthropic or OpenAI tier allows.
- **CPU** is used by the layout model and OCR: 2 to 4 cores per instance is enough for the default settings.
- **Memory**: 2 to 4 GB per instance with the engine.
- **Scale out** by running more instances behind a load balancer with a shared volume for `/data`, or one instance
  per queue.

## Cost control

- Use `mode="model-only"` (or `READANYDOC_PROFILE=model-only`) where layout and citations are not needed.
- For extract on long documents, `settings.relevance_filter: true` reads only the pages your schema needs.
- Every response reports `usage` with tokens and cost, so you can track spend per document.
