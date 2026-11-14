# Configuration

The server is configured with environment variables. The library uses the same model and cache settings; the server
settings only apply to `readanydoc serve` and Docker.

## Model provider keys

| Variable | Used for |
|---|---|
| `ANTHROPIC_API_KEY` | Claude models |
| `OPENAI_API_KEY` | OpenAI models |
| `OPENAI_BASE_URL` | a proxy or gateway in front of OpenAI's API |

## Server settings

| Variable | Default | Meaning |
|---|---|---|
| `READANYDOC_API_KEY` | unset | When set, every API route needs `Authorization: Bearer <key>`. Unset = open server, for local use. |
| `READANYDOC_DATA_DIR` | `.cache/server` | SQLite job database, uploads, results. |
| `READANYDOC_PROFILE` | `engine` | `engine` or `model-only` (see [Modes](../concepts/modes.md)). |
| `READANYDOC_READER` | Claude Haiku 5.5, high effort | Page-reading model as a spec, e.g. `anthropic_api:claude-sonnet-5-5#high` or `openai:<model>`. |
| `READANYDOC_HANDWRITING_READER` | Claude Sonnet 5.5 | Reader for handwritten pages (engine only). |
| `READANYDOC_REFUSAL_READER` | not set | Second reader for a page the first model declines (content filtering). Not set: the page's OCR text is used, at `low` confidence. |
| `READANYDOC_TASK_LLM` | Claude Haiku 5.5 | Model for extract, split, classify and edit. |
| `OPENAI_BASE_URL` | OpenAI's API | Address for `openai:` models, for a proxy or gateway (the OpenAI SDK's own setting). |
| `READANYDOC_PUBLIC_URL` | from the request | Base URL written into file links and presigned upload URLs. Set it behind a proxy. |
| `READANYDOC_UPLOAD_TTL_HOURS` | 24 | Uploads, downloaded inputs and edited documents expire after this. |
| `READANYDOC_RESULT_TTL_HOURS` | 12 | Job results and result URLs expire after this (`settings.persist_results` skips it). |
| `READANYDOC_JANITOR_SECONDS` | 300 | How often the clean-up pass runs (also once at start-up). `uv run readanydoc cleanup` runs the same pass by hand. |
| `READANYDOC_MAX_DATA_GB` | 0 (no limit) | Above this size of uploads + results, finished jobs are removed oldest first (`persist_results` jobs last), then loose uploads. Waiting and running jobs and their files are never removed. |
| `READANYDOC_CACHE_TTL_HOURS` | 6 | Page renders, decrypted copies and figure crops in the working cache (`READANYDOC_CACHE_DIR`, default `.cache`) older than this are removed. A task job's renders go when the job ends, a parse job's when its result expires or is deleted. |
| `READANYDOC_REUSED_CACHE_TTL_DAYS` | 14 | Age limit for the reused caches (PDF words, OCR words, prepared images). |
| `READANYDOC_MAX_CACHE_GB` | 5 | Size limit over all working caches; oldest entries go first. |
| `READANYDOC_CLEAN_MIN_AGE_SECONDS` | 900 | Cache files and unknown folders younger than this are never removed (a running job may be using them). |
| `READANYDOC_CLEAN_CACHE` | on | `0` = the server never touches the working cache. |
| `READANYDOC_MAX_INLINE_BYTES` | 6000000 | Larger results are returned as `{"type": "url", "url", "result_id"}`. |
| `READANYDOC_MAX_UPLOAD_MB` | 100 | Direct upload and URL download limit (413 above it). |
| `READANYDOC_WORKERS` | 4 | Async jobs processed at once (pages of one document are read in parallel within a job). |
| `READANYDOC_DOCUMENT_TIMEOUT_SECONDS` | 1800 | Longest time one document may take; longer fails with a 504 `timeout` error. `0` = no limit. |
| `READANYDOC_TABLE_SECONDS` | 180 | Longest a page waits for its table re-reads; unfinished ones keep the page read's table. `0` = no limit. |
| `READANYDOC_API_CONCURRENCY` | 32 | Model requests in flight at once across the server. A rate-limit answer pauses all of them. |
| `READANYDOC_WEBHOOK_SECRET` | unset | When set, webhooks are signed (below). |
| `READANYDOC_WEBHOOK_URL` | unset | Default URL for `svix`-mode webhooks. |
| `READANYDOC_WEBHOOK_RETRIES` | 3 | Retries after the first attempt, exponential backoff. |
| `READANYDOC_JOB_STATUS_DETAIL` | off | `GET /job` reports `InProgress` / `Completing` / `Cancelled` instead of folding them. |
| `READANYDOC_ALLOW_LOCAL_PATHS` | off | Accept server-side file paths as `input`. Leave off on a shared server. |
| `READANYDOC_BLOCK_PRIVATE_URLS` | on | Refuse URL inputs that resolve to private or loopback addresses. Set to `0` to fetch from internal hosts. |
| `READANYDOC_FIGURE_LLM` | the task model, else the reader's provider | Model for figure prompts and chart data, e.g. `anthropic_api:haiku`. Called only when a request asks for them. |
| `READANYDOC_EMBED_LLM` | as above | Model for table summaries under `retrieval.embedding_optimized`; `off` = deterministic `header: value` lines. |
| `READANYDOC_SOFFICE` | found on PATH | LibreOffice executable for DOC / XLS / PPT / RTF / ODT conversion. |
| `READANYDOC_HEIC_DECODER` | first found | `pillow_heif`, `sips`, a `heif-convert` path, or `none`. |

File links (`/files/<id>?token=...`) carry an unguessable token and need no API key, like a presigned URL.

## Cache locations

| Variable | Default | Meaning |
|---|---|---|
| `READANYDOC_CACHE_DIR` | `.cache` | page renders and other working files |
| `READANYDOC_LAYOUT_MODELS` | `~/.cache/readanydoc/layout_models` | downloaded layout weights |
| `READANYDOC_LAYOUT_NO_DOWNLOAD` | off | `1` = never download; fail with the expected path instead (for air-gapped machines) |
