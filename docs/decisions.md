# Design decisions

One line per decision, with the reason, grouped by area in the order they were made. Later entries override
earlier ones; the first section lists what changed for the first public release. Add a line here when you change
the document format, a default, or a public interface.

## Extract: enforced at any size, cached document, citations by marks, one stage (2026-11-19)

- A schema the API refuses as one structured-output call is asked in one fixed "coded" format
  (`{"values": [{"code", "item", "value"}]}`, codes an enum of the schema's own fields), the same in every call, so
  every call is enforced and every group after the first reads the document from the prompt cache. No re-ask for
  shape: what still breaks the user's schema is reported. Groups of 40 fields; a group of 150 skipped whole metrics.
- The document goes first in every extraction prompt and is cached (`cache_control`); within a run only.
- Page parts are sized in tokens (`part_tokens`, default 30,000, capped by the model's own limit). On a 43-page
  10-Q (153 values): ~17K-token parts found 130, 30K 129, 45K 89, one 79K part 85. Model limits (context, output,
  price jump, prices) come from a snapshot of LiteLLM's public model list (`readanydoc/data/model_limits.json`,
  `scripts/refresh_model_limits.py`), then the server's `/models`; an unknown model is an error naming the
  setting, never a guess. Every run logs one line with all its settings (`extract settings: {...}`); the trace
  (`READANYDOC_TRACE`) records every call with tokens, cache reads and writes, cost and why it was made.
- A value stated once for a table, column or page (a unit, "in thousands", a period in a heading) is written on
  every item it covers; of two copies of a list item, the less complete one is dropped. Names, titles and labels
  are copied in full (one-stage reads of a page image shortened "Platform Standard" to "Platform").
- Citations: the block label the model gives for each object is kept across page parts (matched to the final
  objects by their values; it was dropped whenever a document had more than one part); a `_from` note per object
  names the printed words of a value that is not printed as written (a period from a column heading, a scale from
  "in millions"); a category chosen from the schema's list is never boxed on the same word elsewhere; numbers are
  never matched as loose digits ("2.1" on "2 — 1"); a short value printed in several places is not boxed at a
  guess. On the 10-Q: category boxes on the cover page 128 -> 0, periods and scales boxed on their words 0 -> ~210,
  numbers on the right row 93-94% (Reducto 81%); on the contract pack, whole-section boxes 179 -> 49.
- `extract(..., parse=False)`: one stage, off by default. The model reads the file itself (a PDF, or page images
  for scans and photos); words for citations come from the PDF's own text or local OCR, with no model. On the
  10-Q, 4 runs: 102-110 values (two stages: 129 and 99), about half the cost ($0.12 vs $0.23) and 2.5 times
  faster (110 s vs 270 s), 3-4 times fewer citation boxes; on the contract pack 83.9% vs 99.1% (names shortened).
- Same setup, same document: 99 and 129 values in two runs. Decisions from one run are uncertain.

## Extract 0.2.0: the model writes only the schema (2026-11-18)

- The model writes only the fields of the user's schema. Per-value source notes (0.1.8) are asked for only by
  `deep_extract`: in 0.1.8 they tripled output, hit the output cap on large schemas and set off a cascade of
  split re-asks (11 calls, 158 s and $0.024 per contract on a test pack, against 20 s and $0.005 now).
- Structured outputs for every object schema: code rewrites the schema into a reply schema (every field required,
  "" for absent, numbers and yes/no as text, constraints kept as description text) and converts the answer back to
  the user's types, so the API can enforce the shape at any size without making the model fill a field.
- A schema too big for one call is split into groups of fields before the call (40 leaf fields, a list never split
  inside its items); a 1-token check finds groups the API will not compile, which are split further, and the
  verdicts are cached on disk. Each group is told which fields are asked separately: alone, a group holding only
  the seller was filled with the buyer.
- "" means the document neither gives a value nor makes one clear; a value given in another form ("USA" for a
  country code) is written in the field's form. A required yes/no the document does not answer stays empty
  rather than a guessed false. An absent optional object comes back as null.
- Citations are found by code from the values (unique match, then the object's other values, then the block label
  the model gave, then a low-confidence first match); a value not in the document gets no citation.
- The field report is removed: output holds only what was asked for. Opt-in: `review` (code-only flags),
  `alternatives` (other values the document gives for a field), `derived_values=False` (printed values only).
- Test pack (5 contracts, 534 fields, scored by the pack's rules): 0.2.0 98.5%, 0.1.8 98.7%, 0.1.6 73.4%;
  with `alternatives`, 4 of 4 planted ambiguities flagged (0.1.8: 3).
- 12-case gate against 0.1.8 and 0.1.6 (same page reading): extraction $0.0300 / 194 s in total (0.1.8: $0.0776 /
  534 s; 0.1.6: $0.0236 / 182 s); fields agree with 0.1.8 on 96.5%, the differences checked by hand (0.2.0 found
  a line item and discount terms the others missed, and no longer copies a form's placeholder text); citations
  on the right words 93.6% (0.1.8: 84.7%), by a judge that reads the page's own words.

## First public release (2026-11-14)

- Two shipped profiles, `engine` (default) and `model-only`, defined explicitly in `readanydoc/profiles.py`, and a test
  checks that they equal the configurations that produced the published benchmark numbers: a default that has not
  been measured is not shipped.
- Research profiles live in `research/readanydoc_research` and are loaded only through `READANYDOC_PROFILE_MODULES`;
  `register()` refuses to replace a shipped name: experiments must not change what users run.
- Modes are named after what runs, with the model in brackets ("engine (Haiku 5.5)"), not "fast" / "accurate":
  the model is the user's choice, so speed and quality labels would be wrong as soon as they change it.
- Defaults: Claude Haiku 5.5 at high effort reads pages, Claude Sonnet 5.5 reads handwritten pages, Haiku 5.5
  fills extraction schemas. These are the measured configurations; anything else is the user's choice.
- Model specs are `adapter:model[#effort][?{json options}]`; options are passed to the provider as given, except the
  fields the pipeline owns (model, messages, system, stream, tools, tool_choice, prompt, input), which are refused:
  every provider option is available without ReadAnyDoc having to know about it, and nothing can break the request
  the pipeline builds.
- OpenAI models have their own adapter (`openai:`, `max_completion_tokens`, `reasoning_effort`); any other
  OpenAI-compatible server uses `openai_compatible:<model>@<url>`. Only Haiku 5.5 is benchmarked, and the docs say so.
- A Python library (`readanydoc.parse`, `readanydoc.extract`) runs the pipeline in-process; the server is optional.
  `extract` accepts a pydantic class and returns an instance of it: most users want typed values, not a server.
- One `engine` extra (onnxruntime, numpy, rapidocr) instead of separate layout/ocr/bench extras: users choose a mode,
  not a list of components. Without it the engine still runs and reports in `warnings` that layout is missing.
- Secure server defaults: private and loopback URLs are refused unless `READANYDOC_BLOCK_PRIVATE_URLS=0`; a document
  has a time limit (`READANYDOC_DOCUMENT_TIMEOUT_SECONDS`, 1800, answered with 504); 4 workers and 32 model requests
  in flight by default. A public server should be safe without reading the configuration first.
- The task model defaults to the Anthropic API, not the Claude CLI: a library must not depend on a CLI being logged in.
- Pages the provider declines to return (content filtering) produce a warning naming the page instead of a silent
  empty page; no automatic fallback to another model (owner's decision, revisit after launch).
- Benchmark code and experiment notes moved to `research/`: the package ships only what users run.

## Foundations: package, document model, pipeline (2026-11-11)

Project
- Package name `readanydoc`, Python 3.12, `uv`, hatchling build, Apache-2.0: hatchling because it needs no layout changes.
- Runtime deps are only pydantic, pypdfium2, pdfplumber, Pillow, rapidfuzz, httpx, jsonschema: a small dependency set; no torch/paddle/numpy needed yet.
- Scorer deps (fuzzysearch, beautifulsoup4, tqdm) live in the `bench` extra and the dev group: the product itself must not need them.
- `.cache/` and `.venv/` are not committed: caches are rebuildable.
- Spike data is read in place through `READANYDOC_SPIKE_DIR` (default `../spike`), never copied: 9 MB+ of data and a third-party scorer do not belong in the product repo.
- The olmOCR scorer is imported from `spike/src/olmocr`, not vendored: keeps the repo small and the scorer provably the official one.

Document format (`readanydoc/model/document.py`)
- Boxes are normalised 0-1, top-left origin, with a 0-based `page` index; the serializer adds 1 for Reducto: one convention internally, conversion in one place.
- `Confidence` defaults to `low` with no score: unverified text is not trusted ("do not guess"); Verify raises it.
- `Region.content` holds markdown/HTML as shown to the user and `text` the plain text: citations need offsets into exactly what is returned.
- `Region.candidates` keeps every model output (reader and layout pipeline text); nothing overwrites them: "never make it worse" needs the original.
- `Page.words` holds page-level word lists per source (`pdf`, `ppocr`): Verify and Ground both need them and they are the independent evidence.
- `Page.layout_regions` keeps the raw detector output: Read re-segments `regions`, provenance must survive.
- `Document.chunks` is written by Shape and read by the serializer: lets the serializer use the Document only.
- `Document.notes` records every fallback and failure: stages report instead of raising where they can.
- Models use `extra="forbid"`: a typo in a field name fails loudly; free-form data goes in the `meta`/`properties` dicts.

Interfaces
- `Reader.read(image, hint, context) -> ReadResult`; `needs_image=False` on replay adapters: cached runs never render pages.
- Adapters are chosen by spec strings (`cached:haiku`, `claude_cli:haiku`, `anthropic_api:haiku`, `openai_compatible:<model>@<url>`): config stays plain JSON.
- `WordBoxProvider` is a separate interface from `Reader` (`cached:ppocr`): word boxes are not a transcription.
- `anthropic_api` uses raw HTTP through httpx, not the `anthropic` SDK: one small dependency (httpx) and mocked-HTTP tests; the whole adapter is one file if the owner prefers the SDK later.
- `anthropic_api` sends no `temperature` and no `thinking` field, default `max_tokens` 16000, and returns empty text on `stop_reason: refusal`: current Haiku/Sonnet reject non-default sampling and run adaptive thinking by default.
- Model ids `claude-haiku-5-5` / `claude-sonnet-5-5`: same as the spike; tiers map fast/strong/expert in config.
- `claude_cli` strips `ANTHROPIC_API_KEY` and `CLAUDECODE`, runs in the temp dir with only the Read tool: it must not use the user's API key or tools.
- GLM layout boxes are treated as 0-1000 normalised: max coordinate seen is 995 on 1241+ px pages, and a test checks IoU > 0.8 against PP-DocLayout boxes.
- `layout/claude.py` is a named stub: spec wants a Claude layout fallback; to be done with reading quality.

Pipeline
- Stage registry (`register_stage`) plus `StageConfig.impl`: a worker replaces a stage by editing its file or registering another class, without touching the runner.
- Intake is `required` and ignores `enabled=False`: nothing can run without pages.
- Triage text-layer quality = symmetric bag agreement between pdfplumber words and PP-OCR words (the spike's `agree`), usable at >= 0.8: reproduces the spike's count (94 pages with a layer, 56 usable).
- A text layer with no OCR to check against is marked not usable: never trust an unchecked layer.
- Page kind: scanned if an image covers >= 70% of the page or there is no text; photo detection is not attempted: no reliable signal without pixels work.
- pdfplumber words are cached on disk under `.cache/pdfwords`: pdfplumber dominated run time (2 s/page).
- Layout stage stores the detector's own text only as a candidate: it is a second model's read, useful as evidence, not as content.
- Read maps page-level markdown onto regions by splitting it into blocks (blank lines; tables and fences kept whole) and matching blocks to layout regions by token containment >= 0.6: all cached systems are page-level; the split never edits a character, so the score cannot drop.
- Reading order is the model's order, not the layout detector's: Haiku's order scores 88.8 on multi-column.
- Blocks of 1-2 tokens match a layout region only on exact equality: stops "3" matching any box that contains a 3.
- A block takes the layout box directly only on a 1:1 match (>= 0.8 both ways); otherwise the layout box is a search area and Ground computes the box: several paragraphs often share one layout box.
- `cached-glm` uses `spike/out/glm_pdf` text with PP-DocLayout regions, not `glm_json` blocks: `glm_json` is from the image-input run (matches `glm_gpu` on 103/140 pages), the 71.3 baseline is the PDF-input run.
- `read.layout_furniture` (retype a model block as Header/Footer/Page Number when the layout says so) ships OFF: on 140 pages it scored 80.1 vs 80.4 (Haiku) and 71.0 vs 71.3 (GLM); restricted to a 12% edge band it is +0.2 on Haiku but still -0.3 on GLM.
- Header/Footer/Page Number regions the reader omitted are added from the layout detector with their own text and source: Reducto returns those blocks; they are filtered from scored content.
- Verify only records evidence and confidence (high at >= 0.85 agreement); it never edits content: repair needs re-reads, which belongs to reading quality.
- Ground aligns with `difflib.SequenceMatcher` on alphanumeric tokens inside the region's box, or on OCR lines that share >= 60% of their tokens when there is no box: simple, deterministic, good enough to measure.
- Ground uses PDF words when triage says the layer is usable, else PP-OCR words: spec stage 6.
- Table cells are parsed from the HTML with regexes (rowspan/colspan grid) and boxed by the union of their grounded words: spec 4.3 stage 6.
- Assemble records cross-page joins as links and does not merge content: merging belongs to assembly.
- Shape implements chunk modes disabled/page/block; variable/section/page_sections fall back to disabled with a warning: not implemented yet.
- Table output formats are passed through: the models already emit HTML; conversion belongs to output formats.

Serializer and bench
- `parse_mode` is emitted (null) although it is not in the OpenAPI schema: the real r-1 response has it and the schema allows extra keys.
- `merged_tables` is omitted from blocks: the real response omits it too.
- `enriched` is null and `enrichment_success` false: we do not enrich; Reducto's real value there is a copy of content.
- `document_properties` is null unless `extract_document_properties` is set: matches Reducto's default.
- A region with no box serialises as the whole page with low confidence: the schema requires a box and that is the honest one.
- `job_id` defaults to the Document id and `duration` to the measured pipeline time; the API layer may pass its own.
- Bench output = chunk content with `filter_blocks=[Header, Footer, Page Number]`: the same filter the spike applied to Reducto's output.
- Score = mean of the 5 category pass rates (no baseline test): the benchmark's own headline number.
- Dev subset = 6 pages per category, `random.Random(20261111)`, saved in `bench/dev30.json`: fixed once, never resampled.
- Bench code lives in `research/readanydoc_research/bench/` and its outputs in `research/results/` (moved from the package for the first release).
- Ablations are written for all 140 pages: with cached outputs the full run costs 3 minutes and no tokens.
- The Claude CLI reader was smoke-tested live on one page only (23 s, 0.98 similar to the cached output): frugality.

## Server API, jobs and storage (2026-11-11)

API surface
- Request models are hand-written pydantic classes (`readanydoc/api/schemas.py`) and a contract test compares each one with `reference/openapi.json` (field names, required set, defaults): the spec lives outside the package, so generating at import time would make the server depend on a file it does not ship; the test catches drift.
- Unknown fields are ignored everywhere (also in `PipelineSettings`, which the spec marks `additionalProperties: false`): at least as permissive as Reducto; v2-only knobs (`options`, `advanced_options`, `experimental_options`, top-level `webhook`/`priority`) fall under this, and v2 `document_url` is read as `input`.
- `POST /parse` and `/extract` queue the job and return `{job_id}` when the body has an `async` block: the spec declares the body as `oneOf [Sync..., Async...]` and the response as either shape.
- Sync calls are stored as jobs too: Reducto's sync responses carry a `job_id` that works with `GET /job` and `jobid://`.
- `GET /job` reports only Reducto's four statuses by default (InProgress/Completing -> `Pending`, Cancelled -> `Failed` with reason "Job was cancelled"); `READANYDOC_JOB_STATUS_DETAIL=1` shows all six; `GET /jobs` always shows all six: the spec and the official SDK type `GET /job` as Pending/Completed/Failed/Idle, and client polling loops (`while status == "Pending"`) would stop early on `InProgress`; `SingleJob` in the spec does list the six.
- Error body is `{"error": {message, type, code, name}, "detail": message}`: Reducto's error-codes page shows the `error` object, FastAPI clients read `detail`, `name` is the spec's `ErrorCode` enum. Validation errors keep FastAPI's `HTTPValidationError` (it is in the spec).
- Status codes: 400 URL/page range/config, 401, 403 bad file token, 404 unknown file or job, 409 delete-while-running and cancel-after-finish, 410 deleted or expired job, 413, 415 unsupported/damaged, 442 password, 501 pipelines and missing tasks, 502 reader failures: follows Reducto's error-codes page; 410/409 follow the deletion doc summary in RESEARCH.md.
- Pipelines answer 501 after validating the body: deprecated by Reducto; the message says to chain `/parse` with `jobid://`.
- `/configure_webhook` stores and returns a default webhook URL (string): there is no Svix portal to link to; `svix` mode is delivered directly to that URL and the job records a note.
- Webhook signature header `X-Readanydoc-Signature: t=<ts>,v1=<HMAC-SHA256(secret, "<ts>." + body)>`, sent only when `READANYDOC_WEBHOOK_SECRET` is set: the server never invents a secret; timestamp in the MAC allows replay rejection. 1 attempt + 3 retries, exponential backoff, delivery runs off the worker thread so a slow receiver does not block the queue.
- Auth compares the bearer token with `READANYDOC_API_KEY` read from the environment per request (constant-time compare); `/version`, `/health` and token-carrying file links stay open: file links must work like presigned URLs for clients that fetch them with a plain HTTP GET.
- File links carry a random per-file token stored in SQLite, not an HMAC of a server key: no signing key to create or keep.
- "Presigned" uploads are `PUT /upload/presigned/<id>?token=...` on this server: same client flow as S3 without an object store.
- Uploads keep their original (sanitised) file name on disk under `files/<id>/`: the cached adapters key on the file stem, so an uploaded benchmark PDF gets its cached reads.
- Local paths as `input` are refused unless `READANYDOC_ALLOW_LOCAL_PATHS=1`; bare cached bench stems are accepted: a path input is an arbitrary file read for any API caller; stems only resolve inside the benchmark index.
- URL inputs are fetched with no private-address check unless `READANYDOC_BLOCK_PRIVATE_URLS=1`: the default deployment is a local single-user server where localhost URLs are useful; a shared deployment should turn it on.
- A list `input` on `/parse` is parsed item by item and merged into one response (chunks appended, `bbox.page` renumbered, usage summed): the spec allows lists there and says nothing about the shape.
- `jobid://` on `/parse` returns the stored result under a new job id; on tasks it loads the stored internal `Document` (saved next to every parse result): tasks must run on the Document, not on response JSON.
- Result-as-URL stores the `result` object (`{"type": "full", "chunks": ...}`) as the file body: assumed to match Reducto; not verified against a real >6 MB response.
- `settings.persist_results` skips the 12 h expiry; `DELETE /job` always removes everything, so `include_persisted` changes nothing.
- `usage.credits` is null on every response: no billing; the spec allows null.
- `settings.model` -> profile through `profiles.MODEL_TO_PROFILE`, overridable by env (`READANYDOC_PROFILE*`, `READANYDOC_READER`, `READANYDOC_READER_MINI`, `READANYDOC_LAYOUT`, `READANYDOC_WORDS`): the API must not hard-code reader adapters.

Jobs and storage
- One SQLite file (WAL) behind a process lock, one connection; job results and Documents are JSON files under `jobs/<id>/`, not blobs: results can be megabytes and are read whole.
- Queue order is `priority DESC, seq ASC`; `queue_priority` (auto/standard/batch) is stored and otherwise ignored.
- Worker threads live in the API process (`READANYDOC_WORKERS`, default 1, now 4); after a restart interrupted async jobs go back to Pending and interrupted sync jobs become Failed.
- Cancel of a running job sets a flag; the pipeline is not interrupted, the result is discarded when it returns: stages have no cancellation points.
- Expiry is enforced on read (expired files resolve as missing) and by a janitor every 5 minutes in the worker loop.

Tasks contract
- `readanydoc/api/contracts.py` documents the contract. The adapter inspects `run`'s signature and supplies the task keyword arguments (`llm`, `source_path`, `output_path`); a plain `run(document, config)` also works. The LLM for tasks is `READANYDOC_TASK_LLM` (default `claude_cli:haiku`); tests set `fake` or register doubles in `contracts.OVERRIDES`.
- Edit's `document_url` returned by the task is treated as a local path when it is not http(s) and is copied into the file store.

SDK
- Official Python SDK `reductoai` 0.26.0 accepts `base_url=` and the `REDUCTO_BASE_URL` env var. Run from a throwaway venv (`.cache/sdkcheck/venv`) against a real server process: upload, parse.run, parse.run_job, job.get, job.get_all, job.cancel, job.delete, delete_upload, api_version, webhook.run, extract.run, split.run, classify.run work (tasks with the fake LLM); pipeline.run gets our 501. The SDK requires an `api_key` string even when the server is open. Node SDK not checked.

Files touched outside the API package
- `pyproject.toml`: appended `fastapi`, `uvicorn`, `python-multipart`. `readanydoc/cli.py`: one `serve` subcommand. `.dockerignore` added for the Docker build context.

## Extract, split, classify, edit (2026-11-11)

- `run(document, config, *, llm, options=None)` per task, Edit adds `source_path` / `output_path`; the contract is in `readanydoc/tasks/__init__.py`: one signature the API can inspect and call.
- Added `pikepdf` and `python-docx` to runtime deps: both are on the spec's allowed list and Edit needs them.
- Task LLM is a separate text interface (`tasks/llm.py`), not `Reader`: readers take one image and return a transcription; tasks need system + user text and JSON back.
- `claude_cli` sends the prompt on stdin and the system prompt with `--system-prompt`: long documents exceed argv limits, and the default Claude Code system prompt is wasted tokens.
- `json_schema` is advisory in every adapter and structured outputs are opt-in on the API adapter: user schemas use keywords the API's structured-output mode rejects, and we validate every reply ourselves anyway. *(Changed 2026-11-17: structured outputs are now on when the schema fits the API's limits; see "Extract replies that cannot be read".)*
- Enforcement = deterministic conform, then bounded repair re-asks, then null out what still fails: the guarantee must hold even when the model never complies.
- Scalar conversion is limited to unambiguous spellings ("$1,575.00" -> 1575.0, "yes" -> true, 2041 -> "2041"): format change, not a guess; anything else becomes null.
- Missing arrays are null, not []: Reducto's rule is "null when missing".
- Citations use short region labels (`r1`, `r2`, ...) and wrapped leaves `{"v", "r"}`: fewer tokens and fewer mistakes than uuid ids or a separate path map. *(Changed 2026-11-17: a flat source list, because the wrapping broke on deep schemas; see "Extract replies that cannot be read".)*
- A cited block that does not contain the value yields a block-level citation marked `low`; a value with no locatable source yields `[]`: never fabricate, and never hide the model's claim.
- An unlabelled value is cited only when it occurs exactly once in the selected pages: an ambiguous position would be a guess.
- Array de-duplication needs equal content AND a shared cited block (or neighbouring segments when labels are missing), and one earlier item absorbs at most one later item: identical rows are real in statements.
- Region labels are requested whenever a document is segmented, even with citations off: de-duplication needs provenance.
- Deep-extract re-asks are accepted only when the number of failed checks drops: "never make it worse".
- `page_range` is matched against `Page.original_page` for a single document: the API parses only the requested pages and still passes the range on.
- `deep_split` returns `SplitResult`, not `DeepSplitResult`: Reducto's docs describe `splits[]` with `conf` and `partitions`; UNVERIFIED against a real Reducto deep-split response.
- `partitions` is null for a section without a `partition_key` and a list otherwise: the schema allows both; UNVERIFIED against Reducto.
- Split conflicts resolved by a rule (overlap off, partition page outside its section) are reported `conf: low`: the answer was changed without evidence.
- Classify `accurate` = evidence quote checked against the page text, in one call: a deterministic check instead of a second opinion from the same model.
- `usage.num_fields` = declared leaf fields of the schema: Reducto does not document the count.
- Edit writes its own Helvetica appearance streams instead of relying on QPDF's generator, and sets `NeedAppearances` when not flattening: flatten must work deterministically, viewers still redraw with the form's fonts.
- `form_schema` has one entry per widget (each radio button separately) while the model sees one entry per field: matches Reducto's widget list and keeps the mapping prompt short.
- Flat PDFs without a `form_schema` raise 501: detection is not implemented, and a wrong box is worse than an error.
- DOCX `color` is applied as run shading (highlight); `options={"docx_color_mode": "text"}` gives font colour: RESEARCH.md calls it a highlight, the OpenAPI text is ambiguous.

## Reading quality (2026-11-11)

Shared files touched (additive only)
- `profiles.py`: added `quality` and `cheap`; `cached-haiku` / `cached-glm` are unchanged and still byte-identical to the raw model output (the spine test relies on it).
- `bench/run.py`: extra ablation rows per profile and cost/escalation side metrics; `cli.py`: the default ablation list now includes `quality` and `cheap` (one line).
- `readers/__init__.py`: registered `crop_cache`; new files `readers/sizing.py`, `readers/crop_cache.py`; crop prompts appended to `readers/prompts.py`.
- Document model: no schema change. New data lives in the existing free-form dicts: `Page.properties` (`reads`, `read_selection`, `table_checks`, `escalation`, `text_layer_class`, `text_layer_exact`, `ocr_confidence`, `handwriting_likely`, `rotation_suspect`, `fusion_edits`), `Region.meta` (`tier`, `furniture_by`, `table_selected`, `escalated`, `fusion_edits`), `Document.meta["cost"]`, and `est_cost_usd` / `escalation` / `area` keys on `Document.usage` entries.
- `Page.text_layer_usable` keeps its meaning (agrees with OCR at >= 0.8) because Ground uses it to pick boxes.

Triage
- A text layer on a scanned page is classed `ocr_layer` / `stale_ocr`, never `trustworthy`: on the bench such layers changed correct model text to OCR misreads ("Düsseldorf" -> "Diisseldorf"); only a born-digital layer that agrees with OCR is `text_layer_exact`.
- `handwriting_likely` = scanned page with mean PP-OCR word score < 0.85: handwriting pages on the bench sit at 0.67-0.82, print at 0.87+. It is recorded, not yet used for routing.
- `rotation_suspect` = 90 when most long OCR words are taller than wide; 180 degrees is not detectable from boxes and is not attempted.

Read
- All page reads are kept in `Page.properties["reads"]`; exactly one is chosen per page and reads are never merged at page level: a merged read cannot be traced to one model output.
- Selection score = recall of evidence words + 0.5 x precision, default (first) read replaced only at a 0.02 margin: recall catches reads that dropped a column or stopped early; the margin keeps near-ties on the default.
- Evidence = trusted PDF layer when there is one, else PP-OCR words; under 30 evidence tokens there is no selection.
- Quality profile reads the native-size image first and the 150-dpi image second: native size is the measured better default (81.6 vs 80.4).
- Furniture by consensus: a block is typed Header/Footer/Page Number only when the layout detector labels it so AND another read omitted it; the layout label alone lowers the score (spine finding), the two together do not.
- Table verification (`reading/tables.py`): no table, one row/column, ragged rows, cells not backed by the words in the box (< 0.8), box words missing from the cells (< 0.8), second read disagreeing on cells (< 0.8) or shape.
- Table choice: best evidence score wins by a 0.03 margin; when evidence cannot separate candidates (same cells, different row structure), two reads that agree on shape and cells beat ours; otherwise ours stays.
- A table is replaced only when our read has blocks mapped to that layout table, or when the table's words are absent from our read: otherwise the content sits somewhere we cannot locate and replacing would duplicate it.
- Region crops are rendered at the page's native scale (`crop_zoom` 1.0), not blown up to the model's limit: only then do visual tokens, and cost, scale with area as the cost model assumes. A larger zoom is an option, untested.
- Crop reads are cached under `spike/out/haiku_crops/` keyed by stem and box, and a missing crop is read live only with `READANYDOC_LIVE=1`: no accidental model calls; without the crop the fast read is kept.
- Cheap profile escalates a page when two fast reads (GLM page read vs the layout pipeline's PaddleOCR-VL text) dispute >= 150 tokens with agreement < 0.9: a page re-read costs the same whatever is on it, so the trigger is the amount of text in doubt, not a ratio.
- An escalated strong page read is discarded if it covers 15 points fewer of the page's words than the fast read: never worse.
- Handwriting pages are not escalated in the cheap profile: about 16 of 140 pages would cost $0.0009/page for roughly one test each.
- `native_size` is the default for readers that need an image: the model sees the page at the size it was printed.
- pdfium rendering is serialised with a lock: three concurrent crop renders killed the process during the live run.

Verify
- Token fusion is off by default and on only in `cheap`: it raised cheap by one test and quality by none.
- A strong-tier region is corrected only by an exact text layer, or by another strong read that both the OCR words and the layout model's text confirm: with only "fast read + OCR" as the vote, Haiku was changed to wrong text ("alive" -> "allve", "Banks" -> "Panks").
- OCR alone never overwrites a model token; digits are never changed; pure prefix/suffix differences are skipped (line-end hyphenation in the word source); accent-for-accent swaps from a PDF layer are skipped (font-encoding noise, "ń" -> "ñ").
- Retry policy: re-ask up to 3 reads, stop early when the reader returns the same text again (cached/deterministic), cut a repetition loop after its first copy, fall back to an exact PDF text layer when nothing usable comes back. No temperature schedule: current Claude models reject non-default sampling.
- Runs of `&nbsp;`, rules and dot leaders are not repetition loops: one cached Haiku page was wrongly cut before this rule.

Profiles and cost
- Cost model used in early experiments: fast $0.0002/page, Haiku $0.008/page, crop = Haiku x area (minimum 2% of a page). PP-DocLayout, PP-OCR and the PaddleOCR-VL text that comes with the cached layout are not priced by this model.

## Grounding: word positions and citations (2026-11-11)

- Internal `Word` nodes stay one per alphanumeric token (offsets into `Region.content`); the serializer groups them into whitespace-delimited content words for `word_citations`: Extract needs sub-word boxes ("1,575.00"), Reducto cites words.
- Content words exclude HTML tags/comments, entities, LaTeX command names, link targets and the reader's own notes (`[Figure: ...]`, `[illegible]`): they are not on the page; the bench also reports the spine's all-token measure so runs stay comparable.
- Word sources are tried in trust order (usable PDF layer, PP-OCR, Tesseract, unusable PDF layer with exact runs of 3+ only) and may be mixed inside one block; `Word.bbox_source` records which: a match is itself the evidence that two reads agree.
- A source word is cited by one block only; larger blocks claim first, 1-2 token blocks last by exact text and the gap between their reading-order neighbours; with several equal candidates and nothing to rank them there is no citation.
- Fuzzy pairs need similarity >= 0.6 (>= 0.66 for 3-4 letters), never for tokens of 1-2 characters or two different numbers; positional "slot" pairs need matched neighbours on both sides on one source line; weak matches without an exact neighbour are dropped.
- Tesseract runs from the local binary when page coverage is under 85% and is cached under `.cache/tesseract`: `spike/words2` has Tesseract text without boxes, so the cached data could not be used.
- No box is invented: a block without word evidence keeps `bbox=None` plus `meta["bbox_estimate"]`; the serializer emits the estimate (or the whole page) with `extra.bbox_source` and forces confidence `low`, `parse_confidence` <= 0.5.
- Cells without located words and empty cells get no `cell_citations` entry: a grid-inferred box would be a guess.
- Ground overwrites `Region.confidence` with the combined score and keeps Verify's in `meta["verify_confidence"]`: final confidence is decided by grounding; with Ground disabled Verify's value stands.
- Files touched outside grounding: `pipeline/base.py` `ParseOptions` gained `return_citations` and `return_ocr_data` (additive, default false); `cli.py` gained `parse --citations --ocr-data` and `bench --ground [--json]`; new `bench/ground.py`. `extra.bbox_source` is a new key on every block (values under `extra` are free-form).
- `to_parse_response(..., return_citations=None, return_ocr_data=None)`: explicit arguments win, else `doc.meta["parse_options"]`; citations are off by default like Reducto.
- Bench correctness uses IoU >= 0.5 as asked and also reports "box inside the PDF word" and "centres agree": PP-OCR splits words at punctuation and its boxes are line-height tall, so IoU alone understates whether the right word was cited.

Reading-quality additions after benchmark priorities changed (Parse Judge error types)
- Fusion substitutes only from an exact (born-digital, OCR-checked) PDF text layer. A token disputed by "second read + OCR words" is flagged (`Region.meta["disputed_tokens"]`, confidence low) and left as the model wrote it; `verify.fusion_second_read` restores substitution. Cost on olmOCR: one test in `cheap`. Reason: the judge treats autocorrection and changed text as errors, and that vote was sometimes wrong.
- No token containing a digit is ever changed by fusion.
- Page selection has a numeric veto: a non-default read is not chosen if the evidence backs its number tokens less than the default's (tolerance 0.02, needs 5+ number tokens). Cost on olmOCR: one multi-column test in `quality`.
- Table choice counts cells that hold digits twice (general cell support + numeric cell support + word recall).
- Nothing in read/verify normalises text: no LaTeX unwrapping, no spelling or case repair, no whitespace clean-up was added (a LaTeX-unwrap idea that would have gained table tests was dropped for this reason).

## Assembly, output formats, chunking (2026-11-11)

- Options the spine's `ParseOptions` lacks travel as the raw Reducto request in the stage option `request` (`shaping.options.apply_request`): I do not own `pipeline/base.py` or `api/`, and one helper call is the smallest hook.
- Shape never rewrites `Region.content`; the shown form goes to `meta["shaped_content"]` and the chunk text: grounding offsets and extract citations point into the original content.
- Merged cells are repeated into every covered slot for md / json / csv: each row stays self-describing; UNVERIFIED against Reducto.
- `json` = array of row arrays of strings, `jsonbbox` = the same with `{"text", "bbox"}`; jsonbbox without cell boxes degrades to json: UNVERIFIED shapes.
- `dynamic` keeps HTML when cells carry attributes (formulas, colours): markdown would drop them.
- Variable chunking default target 1000 chars (750-1250), following the docs summary rather than the OpenAPI description (250-1500).
- `chunk_overlap` applies to every multi-chunk mode except `block`; page markers use the 1-based position in the parsed document, not `original_page`: both UNVERIFIED.
- Embedding-optimized tables: hook plus a `header: value` fallback, no model call in the pipeline.
- Paragraph joins are links only by default (`shape.join_paragraphs` off): no benchmark or real document shows the gain. Table merges follow `formatting.merge_tables`.
- A second table whose header differs in any column is a new table; a `<th>` first row that looks like data is treated as data: precision before recall.
- Heading levels change only on numbering or PDF-layer type-size evidence; OCR box heights are not used.
- Furniture detection needs 3+ pages and 40% of pages; a repeated bare number is a page number only if it counts up.
- Digital-native pages are parked in `doc.meta["_native_pages"]` between Intake and Assemble/Shape so the model stages see no pages: the alternative was editing the runner or five stages I do not own.
- Intake gives Read a lazily built reader for native files: building `cached:*` fails without the spike data and would turn a DOCX parse into a 502.
- `spreadsheet.max_cell_count` raises an exception that is both `IntakeError` and the API's `ApiError` (422 CELL_COUNT_EXCEEDED): the API maps `IntakeError` by message text only.
- Intake error messages no longer embed library exception text: "Package not found" in a python-docx error was mapped to NOT_FOUND by the API's message matching.
- DOCX pages come from stored page breaks; DOCX / TXT / EML boxes are estimated and flagged; XLSX boxes are grid positions; PPTX boxes are real.
- Spreadsheet markup: `data-formula`, `style="background-color:#RRGGBB"`, `data-dropdown` on the cell: Reducto's markup is not documented.
- `clustering: accurate` runs the rule-based clustering and adds a note. HEIC and MSG are refused; legacy Office needs `soffice`, which is absent on this machine.
- Added `openpyxl` and `python-pptx` (with `uv add`): allowed list has openpyxl; python-pptx is pure Python and small.

## Evaluation: Parse Judge and business pages (2026-11-11)

- The judge prompt is not retyped: `bench/judge/prompt_template.txt` is generated from `benchmarks/parsejudge/packages/core/src/prompt.ts` by `sync_prompt.py` (only the JavaScript unescape of backticks is applied), its sha256 is pinned in `prompt.py`, and a test compares it with the TypeScript source. JUDGE_VERSION `compare-v4`, parsejudge commit `14e04178`.
- Blinding copies judge.ts: labels parse_a, parse_b, ... go by ascending sha256("pageId:systemId"), so the shuffle is deterministic per page and needs no seed.
- Judge transport is the Claude Code CLI, so three things differ from Parse Judge and are part of the cache key: a short preamble telling the judge where the page image file is (the CLI cannot attach an image), a one-line system prompt (Parse Judge sends none; the CLI always has one), and `--json-schema` in place of OpenAI `response_format` (same schema). `--effort medium` mirrors Parse Judge's `reasoning_effort: medium`.
- `--safe-mode --tools Read --system-prompt ...` on the judge call: keeps the owner's CLAUDE.md, skills and plugins out of the judge and cut the measured cost per call from $0.19 to about $0.08.
- Cache key = JUDGE_VERSION + prompt hash + CLI wrapper hash + judge model + effort + page id + page image sha256 + hash of all (system, parse) pairs. Stored in `product/.cache/judge/` (`READANYDOC_JUDGE_CACHE` overrides). A changed parse of one system re-judges that page for all systems, as in Parse Judge (one call scores all parses of a page).
- A missing parse file counts as a failed page: left out of that system's mean and error rate, scored 1 in head-to-head (summary.ts rule). An empty parse file is sent to the judge.
- Win rate versus baseline is reported three ways (ties are not wins; ties count half; decided pages only) because Reducto has not published how ParseArena counts ties. The headline is the strict one.
- Answers that needed any repair are kept but flagged `well_formed: false`; unknown severity, a score outside 1-10 or a missing parse fail the call and are retried once.
- One edit outside my folders: `readanydoc/cli.py`, three lines at the end of the `bench` arguments (`--judge` and its options; dispatch to `bench/judge/run.py`).
- bizdocs: downloads are opened only with pypdfium2 and Pillow; scripts live in `bizdocs/tools/`, downloads in `bizdocs/src/`. Page properties are assigned by hand per source and checked on contact sheets, not detected automatically.
- bizdocs includes documents that are public but not public domain (Berkshire 10-K pages, sample bank statements, state and city samples of the copyrighted CMS-1500 / UB-04 / ACORD 25 forms, CC BY / CC BY-SA scans from Wikimedia Commons). Each manifest row states the basis. Do not redistribute the set as a whole; publish scores only.
- Left out on purpose: tax returns of named living people, House financial disclosures and FEC filings (legal limits on reuse or personal data), death certificates of named people, and pages whose text is blurred.
- `dynamic` deviates from Reducto's stated rule (md at <= 30 cells and <= 4 merged cells): markdown only when the table has <= 30 cells, no rowspan/colspan, no caption or cell attributes, and re-parsing the markdown gives the same cell texts in the same grid; otherwise the canonical HTML is kept. Reason: on the 147-page business set Reducto's judge penalised 12 pages only because merged cells were flattened and their text duplicated. Explicit `md` / `json` / `csv` still flatten.

## Parse options and file types (2026-11-12)
- Every rule below was read off Reducto's real responses for the same request (`e2e/cases_options.jsonl`, `e2e/cases_filetypes.jsonl`; comparison: `e2e/compare_options.py`), not off the docs.
- Chunking: `section` / `page_sections` cut at every Title / Section Header (a lone heading is its own chunk); every mode except `disabled` prefixes a chunk with the headings it sits under plus " (cont.)"; `chunk_overlap` is N raw characters of both neighbours with no separator; `enriched` repeats the chunk text. Replaying Reducto's own blocks through our shaper reproduces its chunk text exactly in all block / section / page / page_sections / marker / filter cases and in 16 of 19 variable cases. Not reproduced: Reducto's "visual context" in `variable` (it cuts in front of a run of page footers).
- `variable` keeps a text block whole up to twice the upper bound (Reducto kept a 708-character block at chunk_size 500); only longer blocks are cut at sentence ends. What Reducto does with much longer blocks was not measured.
- Page markers are pieces in the block stream (so a marker sits in the chunk where its page starts or ends) and become `Page Number` blocks with a zero box in the response, as on Reducto. Their ids (`__page_start_<i>`) appear in `Chunk.region_ids`; consumers must skip ids that are not regions (the serializer already does).
- `merge_tables` follows Reducto: consecutive tables with the same column count merge, whatever lies between them, also on one page; rows are concatenated, repeated header rows included. This is cruder than Assemble's continuation logic (which stays in charge of the joins it accepts); it is what the option does on Reducto.
- `table_output_format=jsonbbox`: Reducto rejects it with r-1 (400). We keep answering it (cell boxes where grounded); a client that sends it gets more, not an error.
- `settings.page_range` outside the document is 400 INVALID_CONFIG (was an empty 200). Numeric page ranges on spreadsheets and multi-page TIFFs are honoured here; Reducto ignored them in the test (it returned every sheet / frame).
- Encrypted PDFs: Intake writes a decrypted working copy (`.cache/decrypted/`) and points later stages at it. Before this the right password opened the file in Intake and every later stage failed silently (empty 200). The password is not stored in the document.
- Figures: no layout model. The drawing is found on the page image as ink that is not text (PDF text-layer words and grounded word boxes erased, connected blobs on a 160-cell grid, bordered boxes with text treated as frames and searched inside). Pillow only. A block that is only the reader's description of a drawing and is matched to a drawing on the page is typed Figure and gets that box: the located drawing is the independent evidence. Measured against Reducto's figure boxes on 3 pages: mean IoU 0.74, 5 of 7 at 0.5 or more (the two below are two pie charts our reader described in one block).
- Figure prompts, chart data and embedding table summaries call the task model once per figure / table and only when the request asks (`enhance.agentic[figure]`, `advanced_chart_agent`, `retrieval.embedding_optimized`). Chart data is one Haiku read of the crop into Reducto's chart JSON shape; there is no pixel-to-value pipeline, no verification pass and no reconstruction image.
- New dependency: `olefile` (BSD-2, pure Python, 100 KB) to read Outlook .msg files.
- HEIC: not bundled. `pillow-heif` wheels ship libheif and libde265 (LGPL-3) and x265 (GPL-2), checked in a scratch environment; that is not a permissive decoder, so it is not a dependency. Intake uses, when present: `pillow_heif` if the operator installed it, `sips` (macOS), `heif-convert` (libheif tools). Otherwise 415 with that advice.
- Legacy office formats: LibreOffice when installed (not installed on the build machine; the path is tested with a stand-in that has soffice's command line). On macOS without LibreOffice, `textutil` converts DOC / RTF / ODT to DOCX; it flattens tables into paragraphs (measured on a DOC with one table: all words kept, the table is lost as a table). XLS and PPT need LibreOffice.
- Workbooks: each sheet starts with a Title block holding the sheet name, and `non_empty_cell_count` counts cells with a value (a formula without a cached value is not counted), both as on Reducto. DOCX / PPTX hyperlinks are written as markdown links without `formatting.include`, as Reducto's default output does.
- Shared files touched: `readanydoc/api/service.py` (one additive block in `_parse`: `shaping.response.finish`), `pyproject.toml` / `uv.lock` (olefile).

## Tasks checked against Reducto's real responses (2026-11-12)
- Task model through the Anthropic API runs at effort `high` by default, and a reply cut off at `max_tokens` is never parsed: a salvaged fragment produced an all-null Extract with status 200 on a dense 8-page table.
- Response shapes follow what Reducto actually returns, not only the OpenAPI spec: Split leaves out sections with no pages and sets `section_mapping` to null; `deep_split` returns `DeepSplitResult`; Extract `usage.num_fields` counts returned leaf values; Edit `form_schema` entries carry `value` and `filled`; DOCX edits colour the text (`docx_color_mode: highlight` keeps the old shading).
- `deep_split` page `confidence` is "high" only when the model's evidence quote is found on the page (Reducto returns null there): a quote we cannot find is not evidence.
- Flat-PDF field detection is one Haiku call per page on the page image; the model's box is only a search area, the text goes into the largest ink-free rectangle (or blank band) inside it and a checkbox snaps to the printed square: pixels are the independent evidence, no new dependency (Pillow only).
- Flat-PDF values are drawn on the page, not added as form fields: works in every viewer and when flattened; Reducto adds real fields, recorded as a difference in STATUS.md.
- Extract segments carry context instead of always running in sequence: segments run in parallel, and a segment is asked again with the previous segment's last row only when its first rows lack a value that row has.
- An Extract in which every model call failed is a 502, not an all-null object: nulls mean "not in the document".
- `readanydoc/api/service.py` (shared): Classify without `page_range` parses pages 1 to 5 only; one additive line.
- No new runtime dependency.

## OCR, page preparation, speed and cost (2026-11-12)
- New optional extra `ocr` (not a core dependency): `rapidocr` (Apache-2.0) with the PP-OCRv6 small models it ships
  (Apache-2.0), on `onnxruntime` (MIT); pulls `opencv-python-headless` (Apache-2.0), `shapely` (BSD), `pyclipper` (MIT).
  About 155 MB installed. Without it the word providers fall back to Tesseract and rotation detection to Tesseract OSD.
- Word positions: `words="tesseract>rapidocr"`: Tesseract first, PP-OCR only for blocks Tesseract cannot place, and
  only on pages without a trusted PDF text layer. PP-OCR first was measured worse (looser word boxes).
- Page preparation lives in Triage (`prepare`), before any word is read: a turned PDF page gets `/Rotate` so its text
  layer survives; only a skewed scan is rasterised. Unchanged pages keep their bytes (same cached reads).
- Message Batches go through raw HTTP like `anthropic_api.py` (no SDK dependency in this package), behind
  `READANYDOC_BATCH_ASYNC=1`, async non-priority jobs only.
- Shared files, additive only: `readanydoc/pipeline/read.py` (options `zoom_prefetch`, `zoom_gate`, `zoom_pre_gate`,
  `effort_route`, env `READANYDOC_PAGE_WORKERS`), `readanydoc/api/service.py` (`READANYDOC_STAGE_OPTIONS`, `batch=` for async jobs).

## Layout model on CPU (2026-11-12)
- Detector: Docling layout model "heron" (RT-DETRv2) through ONNX Runtime, spec `onnx:heron`, code in `readanydoc/layout/onnx.py`. Chosen over PP-DocLayout_plus-L on the 47 DocLayNet dev pages, model alone: type recall 94.4% vs 73.9%, mean IoU 0.907 vs 0.746 (heron was trained on DocLayNet's train split and has list-item, form, key-value and checkbox labels; PP-DocLayout has none of those four).
- Licence: weights `docling-project/docling-layout-heron-onnx` Apache-2.0 (Hugging Face licence tag, checked 2026-11-12; the PyTorch original `docling-project/docling-layout-heron` is Apache-2.0 too). Our adapter is our own code; no Docling code is imported. Runtime: `onnxruntime` MIT, `numpy` BSD-3-Clause. PP-DocLayout_plus-L (`PaddlePaddle/PP-DocLayout_plus-L_onnx`) is Apache-2.0 as well; it stays selectable (`onnx:ppdoclayout_plus`) but is not used.
- Install size: weights 171 MB (downloaded on first use to `~/.cache/readanydoc/layout_models/heron/`, or `READANYDOC_LAYOUT_MODELS`; `READANYDOC_LAYOUT_NO_DOWNLOAD=1` forbids the download), onnxruntime 76 MB and numpy 24 MB in site-packages. Both packages are an optional extra (`uv sync --extra layout`); without them, or without the weights, the pipeline notes "layout unavailable" and returns the vanilla result.
- CPU time per page (640x640 input, Apple-silicon laptop, 8 cores): 4.3 CPU-seconds per page with 1 thread, measured while other jobs held the machine at load 50 (so an upper figure); 8.1 CPU-seconds with 4 threads at load 69 (ONNX Runtime threads spin while they wait). Shortest wall time seen for one page with 2 threads: 1.3 s; page render for the detector 0.2 s; memory about 520 MB. A clean measurement on an idle machine was not possible during the round. Tool: `e2e/devset/ws1/layout_time.py`.
- The layout stage never replaces the reader's segmentation: `detect_only` stores detections in `page.properties["layout_detections"]` and every use is a switch applied after grounding (`readanydoc/pipeline/regions/`, stage implementation `ground_regions`). Reason: the earlier design (layout regions first, reader text mapped onto them) lost reading order; and with all switches off the output must be exactly vanilla.
- `types=guarded` instead of taking every layout type: each of the 18 type transitions was scored alone on the dev half against three references (DocLayNet labels, the 30-page business answer key, r-1's types). Kept: Text -> List Item only when the text starts with a bullet or enumerator; Text -> Section Header; anything -> Header / Footer / Title; Footer -> Text; List Item / Key Value / Section Header -> Text only on a one-to-one match with score >= 0.93. Never: a reader Title demoted (r-1 and the answer key keep page titles as Title; DocLayNet calls them section headers), a reader Header changed (olmOCR headers_footers lost 4 to 6 points), anything turned into Key Value.
- `boxes` skips page footers and captions: on DocLayNet dev the word-union box was tighter there (IoU down with the detector's box).
- `split` never cuts a Key Value block or inside a detected form (first version cut "label / value below" apart: FUNSD links -2.7), and an unsure box (score < 0.8) of the same type cuts a block only where the text ends a sentence or a list item starts. That last rule was written after seeing one held page, so split is an option and not part of the recommended set.
- Profile `layout-best-api` = the switches that were never worse than vanilla on dev and held (`types=guarded,boxes,tables=broken,forms=notables`). It is not a default: `layout-api` (no switch) and every other profile are unchanged.
- Shared files touched (additive): `readanydoc/profiles.py` (profiles `layout-api`, `layout-best-api`), `readanydoc/layout/__init__.py` (spec `onnx`), `pyproject.toml` / `uv.lock` (extra `layout`).

## Fallbacks and bounds for the known issues (2026-11-15)
- A refused page (content filtering) used to come back empty. Now: a second reader if `READANYDOC_REFUSAL_READER` / `refusal_reader` is set (off by default, because a second provider is the user's choice), else the page's OCR words as plain lines (`ocr_page_text` in `readanydoc/pipeline/read.py`). Every block from OCR text is `low` confidence (`ocr_fallback` in region meta, applied in Ground). Only the primary page read falls back; a refused handwriting, form or escalation read still keeps the first read. Never changes a page the model did read.
- Table re-reads per page: at most 16 (`max_rereads`; the old limits allowed 6 tables plus 30 dropped-table reads) and a 180-second page budget (`max_seconds`, `READANYDOC_TABLE_SECONDS`). A re-read that does not finish keeps the page read's table, which is what happens when a re-read fails. Not benchmarked (cost): output changes only on a page with more than 16 table re-reads or re-reads slower than 180 seconds; the cached test suite is unchanged.
- Long lists: a segment whose item count is below half of the expected count (median items-per-table-row of the other segments; all rows when there is one segment) and whose tables have at least 10 rows is extracted again in halves; kept only when it finds more items; a remaining gap is a warning. Not run on the long-document benchmark (cost); unit-tested with a fake model.

## Extract replies that cannot be read (2026-11-17)
Reported by a user (Zenskar): two order forms extracted into a 22 KB billing schema with 20 nested types came back
as all nulls with "not found in the document" warnings, with citations on. Haiku had read everything; its reply had
one `}` too many inside a list, and `parse_json` kept the longest piece that parsed (the customer block) as the
whole answer. Changes, each with tests in `tests/tasks/test_extract_robust.py`:
- `parse_json` never returns a fragment: a piece of JSON is used only when nothing around it is part of the answer
  (an object key, an unclosed bracket before it, a stray closer after it), and an object with a repeated key is
  broken JSON. Broken JSON is never repaired there.
- `repair_brackets` removes a closer of the wrong kind (the Zenskar mistake). Extract uses it only when the repaired
  value has exactly the schema's shape with nothing moved, dropped or invalid, because the same text can come from a
  missing opener. Otherwise the model is asked again.
- A reply that parses but lacks the schema's top-level fields is asked for again once, never used. A reply that keeps
  breaking (or is cut off) is asked for in two halves of the top-level fields. Fields that still cannot be read get
  the warning "could not be read ... may be in the document", not "not found"; nothing readable at all is a 502.
- Sources: the reply is `{"data": <plain value>, "sources": [{"path": "a.b[0].c", "block": "r12"}]}` instead of
  wrapping every value. The wrapping put an object around every value (nearly three times the brackets on that
  schema). A source given for an object or a row applies to the values inside it; values with no usable source are
  still found by searching the blocks (`build_citations`), never invented. The old wrapped format is still read.
- Fields written one level off (seen live from Haiku 5.5: `line_items` next to `contract` instead of inside it) are
  moved back when exactly one place fits (`schema.relocate`); otherwise they are reported, never dropped silently.
- Structured outputs on the Anthropic API adapter are on when the schema fits the published limits (24 optional
  fields, 16 union types) and uses only features the API supports (`api_schema`): every object closed, nothing made
  required that the user did not mark required, unsupported constraints dropped (validated by us anyway). A schema
  the API refuses is remembered and sent without a format. Large schemas like Zenskar's do not fit; the changes above
  cover them.
- Checked with a property test (hypothesis: any JSON with one bracket added or removed, or a stray comma: the reader
  returns the whole original answer or nothing) and a seeded test of 14 kinds of model mistakes in three reply
  formats, with and without recovery on the next try: every value returned is correct, and every empty field is
  explained. Live on Haiku 5.5: one call for the 20-type schema, every value with a citation; one call with
  structured outputs on a small invoice schema. Not run on the benchmarks (cost); parse output is unchanged.

## Field report (2026-11-17)
After the Zenskar report (an order form with $4,500 in the price table and "$4,200 for the first year" in special
terms), a review of what a bare value hides, for any document type. Each extracted field now has a status in
`field_report` (`readanydoc/tasks/field_report.py`, tests in `tests/tasks/test_field_report.py`):
- Conflicts: the model lists other values the document gives for a field; each is checked on the page in code and
  dropped when it is not there. Values found differently on overlapping pages of long documents are added too.
  Numbers written differently ("$4,500" and 4500) are the same value.
- Inferred: a number or text that is on no page is `inferred`, even when a block is cited; a choice from an enum needs
  a cited block; a yes/no trusts a cited block unless the model says it inferred it. Schema constants
  (discriminators) are not checked.
- Kinds of empty: `blank` and `not_applicable` come from the model; an empty string or a line of underscores is
  `blank`; `unreadable` comes from the reply handling; anything else null is `not_in_document`.
- Conversions: day/month order when both are 12 or less, parentheses on a positive number, "in thousands" on the page,
  a percentage returned as a fraction, a currency in the printed text. Flags and notes only; values are never changed.
- Crossed-out words: found on the page image (a near-solid line through the middle band of the word box; not an
  underline, not a filled box; single characters only next to a struck neighbour), shown to the model as `~~text~~`,
  and a chosen value that is crossed out is a conflict. Pages whose only text in a block is VOID, CANCELLED and
  similar are reported. Parse output is unchanged: this is read from the page image at extract time, so no benchmark
  is affected.
- Lists: a top-level list (not one nested in a row) with fewer items than the item rows of the table its items cite
  (headers, total, tax and near-empty rows excluded) is asked for once more and kept only when it finds more; a
  remaining gap is reported. A schema may want only some rows, so it is a warning, not an error.
- The report is on by default, also with citations off (sources are asked for internally). It adds output tokens for
  the source list and no calls except the list re-ask. `settings.field_report: false` restores the old behaviour.
- Live on Haiku 5.5: the order form came back as a conflict (4,500 page 1, 4,200 page 2), the date 03/04/2026 flagged,
  a blank PO line as blank, an unstated status as not in the document; the 20-type billing schema in one call.
Also in this release: Python 3.13 (CI tests 3.12 and 3.13), `--instructions @file`, and the "figure blocks could not
be located" note is a warning only when the request asked for figure images, descriptions or chart data.

