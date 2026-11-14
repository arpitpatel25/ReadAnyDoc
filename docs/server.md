# Running the server

`readanydoc serve` (or the Docker image) exposes Reducto's v3 HTTP API. Point a Reducto client at it by changing one
base URL; see the README for a short example.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `READANYDOC_API_KEY` | unset | When set, every API route needs `Authorization: Bearer <key>`. Unset = open server, for local use. |
| `READANYDOC_DATA_DIR` | `.cache/server` | SQLite job database, uploads, results. |
| `READANYDOC_PROFILE` | `engine` | `engine` or `model-only` (see the README). |
| `READANYDOC_READER` | Claude Haiku 5.5, high effort | Page-reading model as a spec, e.g. `anthropic_api:claude-sonnet-5-5#high` or `openai:<model>`. |
| `READANYDOC_HANDWRITING_READER` | Claude Sonnet 5.5 | Reader for handwritten pages (engine only). |
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

## Compatibility with Reducto's API

Route by route against `reference/openapi.json` (20 routes). "Full" = documented request fields accepted
with their defaults, response validates against Reducto's schema, behaviour implemented. Unknown fields
are ignored, as on Reducto.

| Route | Level | Notes |
|---|---|---|
| `POST /upload` | full | Multipart `file`, `?extension=`. Without a file: returns a presigned-style `PUT` URL on this server. |
| `DELETE /upload/{file_id}` | full | |
| `POST /parse` | partial | See the field table below. Inputs that work out of the box: PDF (also password-protected), PNG / JPG / GIF / BMP / WEBP / TIFF (multi-page), DOCX, XLSX / XLSM, CSV, PPTX, TXT, EML, MSG. DOC / RTF / ODT need LibreOffice or macOS `textutil`; XLS / PPT need LibreOffice (`brew install --cask libreoffice`, or `apt install libreoffice`); HEIC needs a decoder on the machine (`pillow-heif`, macOS `sips` or `heif-convert`). Without the tool the answer is 415 with that advice. An `async` block queues the job and returns `{job_id}`, as the spec allows. |
| `POST /parse_async` | partial | Same options. `async.priority`, `async.metadata`, `async.webhook` implemented; `queue_priority` accepted and ignored. |
| `POST /extract`, `/extract_async` | full route, quality depends on `readanydoc.tasks.extract` | The route parses (or reuses `jobid://`), calls the task, returns `V3ExtractResponse`. Additions that Reducto's API does not have (see "Extract additions" below): a `warnings` list in every response, and three opt-in request fields. |
| `POST /split`, `/split_async` | full route, as above | `SplitResponse`. |
| `POST /classify`, `/classify_async` | full route, as above | `ClassifyResponse`. |
| `POST /edit`, `/edit_async` | full route, as above | `EditResponse`; `document_url` is a 24 h link on this server. Fillable PDFs: the file's own fields are filled. Flat and scanned PDFs: every detected (or given) box becomes a real form field named `temp_<n>`, text or checkbox, filled in `edit_options.color`, so the output stays fillable, as Reducto's does (W-2 scan: 69 fields against Reducto's 68, 24 filled against 23; CMS-1500: 180 against 167, 14 filled on both). `edit_options.flatten` bakes the values into the page; `edit_options.flat_fields: false` (ours) draws text instead of adding fields. |
| `POST /pipeline`, `/pipeline_async` | accepted, 501 | Deprecated by Reducto. The body is validated, then a 501 error tells you to chain `/parse` and `/extract`. |
| `GET /job/{job_id}` | full | Sync calls are stored as jobs too. `status` uses Reducto's four values (running = `Pending`, cancelled = `Failed` with a reason) unless `READANYDOC_JOB_STATUS_DETAIL=1`. |
| `DELETE /job/{job_id}` | full | 202 deleted, 409 still running, 410 already deleted or expired, 404 unknown. `include_persisted` accepted; deletion always removes everything. |
| `POST /cancel/{job_id}` | full | Pending jobs are cancelled at once; a running job finishes its current work and its result is discarded. 409 if already finished. |
| `GET /jobs` | full | `cursor`, `limit` (1-500, default 100), `exclude_configs`. Newest first. Reports all six statuses. |
| `GET /version` | full | Returns `"readanydoc-<version>"`. |
| `POST /configure_webhook` | partial | There is no Svix portal. Returns (and with `?url=` sets) the default webhook URL as a string. |

Parse request fields. Every row was run against Reducto's API and this server with the same request on
2026-11-12. "Same" means the
status code and the option's effect match; text differs wherever the two readers differ.

| Field | Level | Against Reducto's real response |
|---|---|---|
| `input`: `reducto://` id, upload object, http(s) URL, `jobid://<id>`, list of those | full | a list is parsed in order into one response with pages renumbered |
| `retrieval.chunking.chunk_mode` = `disabled`, `page`, `block`, `section`, `page_sections` | full | same rules: fed Reducto's own blocks, our shaper returns its chunk text character for character (35 of 35 cases; the 34 filter, marker, page-range and merge cases too). Chunks repeat the headings they sit under with " (cont.)", as Reducto's do |
| `chunk_mode` = `variable`, `chunk_size` | full | same grouping in 16 of 19 cases (null size = about 1000 characters, upper bound size + 25%, tables never split). Reducto also cuts by "visual context" (in front of page footers); that is not reproduced |
| `chunk_overlap` | full | N characters of the previous and of the next chunk, as Reducto returns it |
| `retrieval.filter_blocks` | full | same: filtered types leave `content` / `embed`, the blocks stay listed. Depends on block typing |
| `retrieval.embedding_optimized` | full | `embed` carries a model-written summary of each table instead of its HTML (one text call per table, about 1,400 input + 950 output Haiku tokens). Without a live model: `header: value` lines |
| `formatting.add_page_markers` | full | same: `[[START OF PAGE n]]` / `[[END OF PAGE n]]` in content and as `Page Number` blocks with a zero box |
| `formatting.table_output_format` = `html`, `md`, `json`, `csv`, `dynamic` | full | same formats in chunk and block `content`. Markdown is unpadded (`\| a \| b \|`); Reducto pads the columns |
| `formatting.table_output_format` = `jsonbbox` | full | differs: Reducto rejects it with r-1 (400); this server answers it |
| `formatting.merge_tables` | full | same rule (consecutive tables with the same column count merge; fragments listed in `merged_tables`). The outcome depends on how many columns each reader saw: equal on 5 of 7 test files |
| `formatting.include` | partial | `hyperlinks`, `comments`, `change_tracking` for DOCX / PPTX / PDF annotations; DOCX hyperlinks show without it, as on Reducto. `highlight`, `signatures`, `ignore_watermarks` accepted, ignored (r-1 ignores them too) |
| `settings.page_range` (object, list of objects, list of ints, sheet names) | full | same pages and `original_page`; a range outside the document is 400 INVALID_CONFIG on both. Reducto ignored a numeric range on a workbook and on a multi-page TIFF; this server honours it |
| `settings.document_password` | full | same: right password parses, wrong or missing is 442 DOCUMENT_PASSWORD_PROTECTED |
| `settings.return_images` = `figure`, `table` | full | same: `image_url` on Figure and Table blocks (a PNG crop in this server's file store, link valid for `READANYDOC_RESULT_TTL_HOURS`). Figure boxes come from the page image, mean overlap with Reducto's boxes 0.74 on 7 figures. Pictures the reader did not describe (logos, stamps, seals, QR codes) get a Figure block with the shape option `layout_figures` (off by default; needs the engine profile; turn on with `READANYDOC_STAGE_OPTIONS='{"shape": {"layout_figures": true, "layout_figures_describe": true}}'`). On the 22 business pages that hold Reducto's 23 Figure blocks: 8 Figure blocks without the option, 24 with it; Reducto's figures matched by one of ours (IoU >= 0.5) 4 -> 15 of 23, mean IoU 0.91; every other block unchanged. `layout_figures_describe` adds a one-sentence description per new figure (one small image call each, about 120 input + 120 output tokens); without it the new block has a box, an optional crop and empty content |
| `settings.return_images` = `page` | accepted | Reducto's response showed no page images either |
| `enhance.agentic[{scope: "figure", prompt}]` | full | same: the Figure block shows the model's answer to the prompt (one call per figure, about 1,000 input + 150 output Haiku tokens) |
| `enhance.agentic[{scope: "figure", advanced_chart_agent}]`, `enhance.advanced_chart_agent` | partial | `chart_data` (a list with one link to a JSON file in Reducto's shape), `extra.is_chart`, block content = legend + data table. One Haiku read per figure (about 1,300 input + 6,000 output tokens for a 144-point chart), values within 2.3 on a mean of 65 of Reducto's on the test chart, pie labels exact. Verification pass: free consistency checks on the data (values inside the axis range, pie adds up, series lengths, repeated x values); one more read only when a check fails; result in `extra.chart_verified` (Reducto's key) and `extra.chart_verification`. Reducto verifies by re-drawing the chart; no `extra.chart_reconstruction` image here |
| `enhance.agentic[{scope: "table", prompt}]` | full | same effect on the two test pages ("write every dollar amount without the dollar sign": 23 -> 0 and 2 -> 0 dollar signs in tables on both). One text-only Haiku call per table that returns the changed cells; cells are edited in place, so rows, columns and spans cannot change. `mode` is accepted; with a prompt every table is processed. Without a prompt nothing extra runs |
| `enhance.agentic[{scope: "text", prompt}]` | full | same effect on the two test pages ("write dates as YYYY-MM-DD": the same 2 and 1 dates rewritten, `usage_breakdown.prompted_blocks` 1 on both). One text-only Haiku call per page for its text, form, header and footer blocks; a change that rewrites a block instead of reformatting it is dropped. Word citations of a changed block keep their boxes but not exact character offsets |
| `enhance.summarize_figures`, `enhance.intelligent_ordering`, `settings.ocr_system`, `settings.extraction_mode` | accepted, ignored | legacy-model knobs; r-1 ignores them too |
| `settings.return_ocr_data` | partial | `result.ocr.words` / `lines` in Reducto's shape. About the same word count on digital pages (131-456 against 134-406); on the scanned test page 378 words against Reducto's 888, and fewer lines on tables |
| `settings.return_citations` | full | `extra.word_citations` on text blocks (72 of 73 in the test; Reducto 76 of 76) and `cell_citations` on every table, in Reducto's shape |
| `settings.extract_document_properties` | full | same values on PDF and DOCX |
| `settings.force_url_result`, `settings.persist_results` | full | |
| `settings.model` | full | `usage_breakdown.parse_model` reports the requested model as Reducto does (`R-1`, `R-1 Mini`, `Legacy`; legacy counted in `legacy_parse_credits`). `r-1-mini` and `legacy` run a cheaper tier of the configured engine: `engine` -> `model-only` (one page read, no zoom vote, no second model, no repair calls) at the API's default effort instead of high (measured at 35% cheaper and twice as fast, equal on text pages, slightly worse on tables). `usage.model_usage.profile` / `.reader` say what ran. `READANYDOC_PROFILE_R_1_MINI`, `READANYDOC_PROFILE_LEGACY`, `READANYDOC_READER_MINI` override; `READANYDOC_MINI_SAME_PROFILE=1` = one engine for all three |
| `settings.embed_pdf_metadata`, `embed_pdf_metadata_dpi` | full | `pdf_url` is a searchable copy: our words (the reader's text on its word boxes, OCR words elsewhere) as an invisible text layer; any script. Pages with a good text layer are untouched; a page with an old unusable layer is replaced by its picture first (at `embed_pdf_metadata_dpi`, default 150); image inputs become a PDF. On a scanned W-2: 431 words in our layer, 219 (line fragments without spaces) in Reducto's; 88% of the characters of Reducto's layer are in ours and 90% the other way; 84 of 85 distinct words of our response can be found in our layer. Pages turned before reading get no layer |
| `spreadsheet.clustering`, `split_large_tables`, `include`, `exclude`, `max_cell_count` | full | same table counts for split sizes and hidden sheets, same 422 CELL_COUNT_EXCEEDED, cell colours and formulas as cell attributes. `clustering=accurate` uses the rule-based clustering. Table boxes are page-relative here, cell coordinates on Reducto |
| `settings.timeout`, `tenant_throttling`, `force_file_extension`, `hybrid_vpc` | accepted, ignored | |
| v2-only fields (`options`, `advanced_options`, `experimental_options`, top-level `webhook` / `priority`) | accepted, ignored | v2 `document_url` is read as `input` |
| invalid enum values (`chunk_mode`, `filter_blocks`, `table_output_format`) | full | 422 on both |

File types, same file sent to both (words in common = token-set similarity of all text):

| Type | Works | Against Reducto |
|---|---|---|
| PDF | yes | see the option table above |
| PNG, JPG, GIF, BMP, WEBP | yes | 94-97% words in common on the test page (two different readers) |
| TIFF, multi-page | yes | 96%; one page per frame |
| HEIC | yes, with a decoder on the machine | 97% (decoded by macOS `sips` in the test) |
| DOCX | yes, read natively | 100%, same 11 blocks, 2 pages, table, links, properties. Block boxes are estimates (no rendering) |
| XLSX / XLSM | yes, read natively | 100%, same sheets, sheet-name titles and split tables; a single-row range is a Table here and Text on Reducto; 532 non-empty cells counted against 528 |
| CSV | yes | 100%, one table |
| PPTX | yes, read natively | 100%; a bullet list is one block here, one block per bullet on Reducto |
| TXT | yes | 100%; a form feed starts a new page here, not on Reducto |
| EML | yes | 100%; headers and body, attachments listed by name |
| MSG | yes (`olefile`) | 89% on a public sample message; same subject, sender, date, attachment count |
| RTF, ODT | with LibreOffice, or macOS `textutil` | 100% (through `textutil` in the test) |
| DOC | with LibreOffice, or macOS `textutil` | 100% of the words through `textutil`, but the table arrives as paragraphs (Reducto: 1 table) |
| XLS, PPT, ODS, ODP | with LibreOffice only | not run: LibreOffice is not installed on the build machine. The conversion path is unit-tested with a stand-in converter. Without LibreOffice the 415 error names the format, the program that is missing (or the `READANYDOC_SOFFICE` path that does not exist) and the install command for the server's system (`brew install --cask libreoffice`, `apt-get install -y libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress`, `winget install TheDocumentFoundation.LibreOffice`) |

Response differences from Reducto: `usage.credits` is always null (no billing), `studio_link` is null,
`pdf_url` is set for PDF inputs only, `usage.model_usage` (tokens per model) is extra.

### Extract additions (not in Reducto's API)

A client that sends none of these gets the same behaviour as before plus the `warnings` list.

| Field | What it does |
|---|---|
| response `warnings` (always present) | One line per required field (`required` in the schema, at any level) that came back empty: `required field 'governing_law' was not found in the document`, `required field 'items[].price' was not found in 3 of 12 items`. Also notes about examples and failed model calls. |
| `instructions.examples` | Up to 5 example outputs (objects that follow the schema, or JSON strings). They are shown to the model as format examples from other documents. A returned value that equals an example value and cannot be found in the document is removed and reported in `warnings`. |
| `settings.relevance_filter: true` | For documents of 4 pages or more: one text-only model call sees a digest of every page (headings, first lines, table headers) and the schema's fields, and picks the pages to extract from. A top-level field that is still empty afterwards is looked for once in the pages that were left out. Measured: 80% cheaper with the same answers on an 86-page packet with a field-style schema; no saving on a 10-page document or on row-list schemas (every page is picked). `usage.extract_pages.relevance` lists the pages used. Never used by Parse. |
| `settings.request_pages: true` | The model starts from the pages the relevance step picked and may ask for more before it answers: `{"need": [{"page": 12}, {"search": "Schedule B"}]}`, at most 3 rounds. `usage.extract_pages.page_requests` lists what it asked for. Meant for documents with cross-references that do not fit one call: on a 6-page contract it matched the single-call answer (24 of 24) where fixed one-page chunks got 18 of 24, at twice the cost of the single call. Not combined with `array_extract` / `deep_extract` segmenting. |

### Errors

Errors use Reducto's documented body plus FastAPI's `detail`, so both styles of client work:

```json
{"error": {"message": "...", "type": "file_download_failure", "code": 400, "name": "PROCESSING_FAILED"},
 "detail": "..."}
```

400 bad URL / page range / config, 401 bad key, 403 bad file token, 404 unknown file or job, 409 job state,
410 deleted or expired job, 413 too large, 415 unsupported or damaged file, 422 validation
(`HTTPValidationError`), 442 password needed, 500 internal, 501 not implemented, 502 model provider error.
Failed jobs carry `error: {code, name, message, job_id}`.

### Webhooks

`async.webhook = {"mode": "direct", "url": "https://..."}` gets one `POST` when the job completes or fails:
`{"job_id": "...", "status": "Completed" | "Failed", "metadata": <async.metadata>}`. Non-2xx answers are
retried 3 times with backoff. `mode: "svix"` is accepted and sent the same way to the default URL
(`POST /configure_webhook?url=...` or `READANYDOC_WEBHOOK_URL`).

With `READANYDOC_WEBHOOK_SECRET` set, each request has
`X-Readanydoc-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>`. Verify it:

```python
from readanydoc.api.webhooks import verify
ok = verify(raw_body_bytes, request.headers["X-Readanydoc-Signature"], secret)   # rejects stamps older than 5 min
```

## Development

```bash
uv run pytest -q                 # everything
uv run pytest -q tests/api       # API: contract tests from the OpenAPI spec, end to end, webhooks, auth, SDK
uv run readanydoc parse <pdf | image | cached stem> --profile cached-haiku
uv run readanydoc bench --profile cached-haiku cached-glm --metrics
```

Layout: `readanydoc/api` (HTTP layer), `readanydoc/store` (SQLite jobs, file store), `readanydoc/pipeline`
(the eight stages), `readanydoc/tasks` (extract, split, classify, edit), `readanydoc/readers` and
`readanydoc/layout` (model adapters), `readanydoc/serialize` (Reducto response shape).
The task call contract is documented in `readanydoc/api/contracts.py`.

Licence: Apache-2.0.
