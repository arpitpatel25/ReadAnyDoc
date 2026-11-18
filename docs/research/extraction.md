# Extraction research: structured output, cost, page parts, citations, one stage

15-19 November 2026. What we tested after releasing Extract 0.2.0, what we found, what we chose and why. Every
number comes from a fresh run (no answers reused from an earlier run), its own per-call trace, and a scorer that
reads the answer key or the page's own words. Nothing here was judged by an AI model except where it says so.

**In short**

- Extraction is now enforced by the API on every call, whatever the schema's size, with one fixed answer format
  that lets every call share one cached copy of the document. No second call is ever made just to fix the shape.
- The document is split into page parts by tokens (30,000 by default). The limits of each model are looked up
  from a public list by model ID, never guessed.
- Citations are still found by code (no model), but they now follow the row the model read, point worked-out
  values (a period, a scale) at the printed words they came from, and no longer box category words at random.
- A one-stage mode (`parse=False`), in which the model reads the file itself, is about half the cost and 2.5 times
  faster, but less accurate; it ships off by default.
- Results move a lot between two runs of the same setup (99 and 129 values on one report). Several decisions below
  rest on one run each and say so.

| | ReadAnyDoc (now) | Plain Haiku (one call) | Reducto |
|---|---|---|---|
| Contract pack, 5 order forms: fields right | **99.1%** | 98.3% | 93.3% |
| Contract pack: cost per form | $0.013 | $0.003 | ~$0.04 (list price) |
| 43-page 10-Q, 153 values: found | **129** / 99 (two runs) | 111 | 72 |
| 10-Q: cost | $0.22-0.24 | $0.09 | ~$0.86 (list price) |
| 10-Q: numbers boxed on the right row | 93-94% | no citations | 81% |

---

## 1. Test material and how it was scored

| Set | What it is | Scored by |
|---|---|---|
| Contract pack | 5 order forms from a user (Zenskar), 534 fields, a 20-type billing schema, planted ambiguities | The pack's own rules (`score.py`, read, not run, plus our copy) |
| ExtractBench | Contextual AI's benchmark: 35 documents, 2,076 pages, five schemas (10-Q, credit agreement, research paper, resume, swimming results), human-checked answers | The suite's own grader, with its Sonnet 5.5 judge for text fields |
| ADP 10-Q | One ExtractBench document: 43 pages, about 90,000 tokens of text, a schema the API refuses as one call; 153 gold values | Our free scorer: metric, period, segment and value times scale within 1% |

- **Systems:** ReadAnyDoc, Reducto (its real API responses), and plain Haiku (the PDF and the schema in one request
  to Claude Haiku 5.5, high effort).
- **Fresh runs:** an empty ReadAnyDoc cache folder and no call cache for every measured run. Caching *inside* one
  run (the same document read by several calls) is allowed, because a real user's first run gets it too; reusing
  anything from an earlier run is not.
- **Reducto's cost** is its list price: $0.02 per page for Extract (Deep Extract $0.04). A contract may be cheaper.
- **Citations** were first scored by reading the words inside each box from the PDF and checking they show the
  value. Section 9 explains why that check was too lenient and what replaced it.

## 2. Where we started: Extract 0.2.0

Extract 0.2.0 (18 November) asked the model for the user's schema only, through a rewritten "reply schema": every
field required, "" for absent, numbers as text, converted back to the user's types afterwards. A schema too big
for one call was split into groups of fields.

ExtractBench and the contract pack, all fresh:

| | ReadAnyDoc 0.2.0 | Reducto | Plain Haiku |
|---|---|---|---|
| ExtractBench mean (35 docs) | **0.878** | 0.803 | 0.860 |
| 10-Q (7) | **0.671** | 0.485 | 0.626 |
| Credit agreement (10) | 0.872 | 0.904 | **0.920** |
| Research (6) | **0.965** | 0.813 | 0.828 |
| Resume (7) | 0.950 | 0.905 | **0.954** |
| Swimming (5) | **0.975** | 0.888 | **0.975** |
| Contract pack, fields right | **98.5%** | 93.3% | 98.3% |
| Citations on the right words (printed values) | 94.6% ExtractBench, 99.2% pack | 65.7%, 75.7% | none |
| Cost, 35 docs | $6.86 | credits | $3.01 |
| Time, 35 docs | 252 min | 31 min | 14 min |

Accurate, but expensive and slow. The rest of this page is about why, and what we changed.

## 3. Where the money went

One fresh run of the ADP 10-Q, traced call by call:

| | ReadAnyDoc 0.2.0 | Plain Haiku |
|---|---|---|
| Cost | $0.46 | $0.09 |
| Model calls | 251 | 1 |
| Time | 10 min | 46 s |

- **Extraction was 67% of the cost.** 89 calls each sent the whole document again, one per group of about 40
  fields, plus 52 one-token checks of whether the API would compile each group's schema.
- **Parsing was 33%:** page reads 16.5%, table re-reads 15%.

**The principle we set:** parsing (reading the pages) should be most of the cost, about 80%, and extraction about
20%. Reading the document is the hard part; filling a schema from text already read should be cheap.

## 4. Structured output: the central problem

### What "enforcement" means here

Anthropic's structured outputs (`output_config.format` with a JSON Schema) make the API constrain the model's
output to the schema's shape. The shape is then guaranteed; the values are not. Without it, a model can write a
bracket too many, a field one level off, or an invented key, and every such reply needs repair or a second call.

### Why it was hard

- **The API refuses big schemas.** Published limits: 24 optional fields and 16 union types per schema. Beyond
  them, and beyond an unpublished size of the compiled grammar, it answers "Schema is too complex for
  compilation" (or times out after 180 seconds of compiling). The contract pack's schema and every 10-Q schema
  are refused.
- **A different format in each call breaks the prompt cache.** Anthropic's docs: changing `output_config.format`
  invalidates the cache. 0.2.0 gave each group its own schema, so no group could reuse the document another group
  had already paid for. That is why the document was "sent 89 times" at full price.
- **0.2.0 also dropped enforcement** when a schema was split into more than one group: the groups went without a
  format, and a check-and-repair loop caught the mistakes. That loop sometimes asked again without enforcement and
  replaced a whole part of the answer.

### What we considered

| Option | Verdict |
|---|---|
| No enforcement, repair the JSON afterwards | Rejected: the product must never return a broken shape, and repairs mean more calls |
| Several passes: extract freely, then a second call to fit the schema | Rejected: doubles the calls; the user's rule was one call per piece of work |
| Limit schema size, as some tools do | Rejected: Reducto takes schemas of any size, and so must we |
| One fixed format for every call ("coded") | **Chosen** |

### The coded format

Every leaf field of the schema gets a code (`f1`, `f2`...). Every call answers in the same small format:

```json
{"values": [{"code": "f17", "item": "0.1", "value": "4200"}]}
```

- `code` is an enum of the schema's own codes, so the API rejects an invented field;
- `item` gives the list positions, one number per list on the field's path ("" outside lists);
- `value` is text, converted to the user's types afterwards, like every reply-schema answer.

Each call lists only its own fields ("f17 | balance_sheet.cash[].value | ..."), but the format, the system prompt
and the document are identical in every call. So:

- **every call is enforced**, at any schema size;
- **the document is cached once** and read by every other group at a tenth of the price;
- code rebuilds the user's exact shape from the entries (`readanydoc/tasks/coded.py`), counting entries it had to
  drop (unknown code, wrong number of list positions, duplicates) in the trace.

A schema the API accepts whole (most small ones) is still asked in one enforced call in the user's own shape.

### Bugs the first coded run found

| Problem | Effect | Fix |
|---|---|---|
| An "either-or" of two objects (`quantities.source`: fixed or metered) was treated as plain text | Fields missing on the pack (99 of 112 on one form) | Both forms' fields are merged into one object and matched back to the right form afterwards: 107, then 111 of 112 |
| A "list looks short" re-ask ran without enforcement and replaced whole parts | Good answers overwritten | Disabled for enforced answers; a short list is reported as a warning instead |
| Groups of 150 fields | The model skipped whole metrics (operating income) | Groups of 40 fields; a list's fields always stay in one group. 10-Q: 97 -> 110 values |

### The remaining shape caveat

The structure always follows the user's schema: every key and nesting level, the right type for every value given,
enum fields only with allowed values. One exception, chosen on purpose: **a value the document does not give is
`null`**, even for a field the schema marks required and not nullable. The alternative is inventing a value
(`false`, `""`, a made-up email), which is worse. Of 15 outputs checked strictly against their schemas, 9 failed
only for this reason (for example `other_disclosures` on a 10-Q that has none). Reducto does the same.

## 5. Prompt caching inside one run

- The document goes first in every extraction prompt, marked with `cache_control`; everything after it (the
  field list, the instructions) changes per call.
- Cache writes cost 1.25 times the input price, reads 0.1 times. Haiku 5.5 caches prompts of 512 tokens or more.
- Calls sent at the same moment cannot read a cache another call is still writing, and the API does not allow a
  zero-token "warm-up" call with a format. So the first group goes alone and the rest go together after it.
- Caching never crosses runs: a document is always paid for in full the first time a run sees it.

On 12 documents (the pack and seven 10-Qs), groups with their own formats cost $4.26 (pack 96.1%); the same with
caching $2.67 (97.4%). The coded format then made caching work for every call.

## 6. Generic instructions

Every instruction change had to be generic: true of any document, never written for one test file.

| Instruction | Why | Measured |
|---|---|---|
| A value stated once for a table, column, section or page (a unit, a scale such as "in thousands", a currency, a period in a heading) applies to every item it covers: write it on each | Rows came back without their scale or period, so the values were off by a factor of 1,000 or undated | 10-Q: 110 -> 130 correct |
| Of two copies of the same list item, drop the one with fewer fields filled (code, not prompt) | Page parts and groups each wrote the same row; one copy lacked the scale | 10-Q precision 49.6% -> 59.9%, no value lost |
| Copy names, titles and labels exactly as printed, every word | One-stage reads of a page image shortened "Platform Standard" to "Platform" | Added last; not yet measured |

## 7. Page parts: how big should each part be?

A long document is split into parts, each extracted separately, then merged.

- **The old rule** split by characters: above 120,000 characters, parts of at most 8 pages and about 40,000
  characters (14,000-19,000 tokens). The ADP 10-Q became 6 parts.
- **The question:** bigger parts mean fewer calls and less repeated thinking. What limits the size?
  - the model's context window (1,000,000 tokens for Haiku 5.5);
  - its **price jump**: above 100,000 input tokens the whole request is billed at 5 times the rate, cached
    tokens included;
  - and, as it turned out, accuracy.
- **Model limits are looked up, never guessed.** `readanydoc/data/model_limits.json` is a snapshot of LiteLLM's
  public model list (MIT licence; about 4,300 chat models: context, output limit, prices, price jump), refreshed at
  release with `scripts/refresh_model_limits.py`. A user's own file (`READANYDOC_MODEL_LIMITS`) overrides it, and
  an OpenAI-compatible server's `/models` route is asked next. A model none of them knows is an error naming the
  setting to give (`part_tokens`); we chose that over a cautious default.

ADP 10-Q, fresh runs, 153 gold values:

| Largest part (tokens) | Parts (pages) | Found | Values written | Total cost | Extraction share |
|---|---|---|---|---|---|
| ~17K (old rule) | 6 | 130 | 262 | $0.29 | 47% |
| **30K** | 2 (25 + 19) | **129** | 163 | $0.22 | 31% |
| 45K | 2 (37 + 7) | 89 | 122 | $0.21 | 28% |
| 79K (largest under the price jump) | 1 (43) | 85 | 106 | $0.19 | 22% |

- **Accuracy falls once a part goes past about 25 pages.** One 43-page part met the 80/20 cost split exactly, and
  lost 45 values.
- **Chosen: 30,000 tokens by default** (`part_tokens`, capped by the model's own limit, overridable per request).
  It kept the accuracy of small parts, wrote far fewer duplicate rows, and cost less.
- Short documents are one part at any of these settings: every contract-pack form is.
- One measurement per size. A later run of the 30K setting found 99 (section 11), so read the table as a trend.

**A mistake on the way:** we first said the whole ADP report was about 22,000-40,000 tokens. It is about 90,000. The
part sizes above are measured, not estimated.

## 8. Logging

- **One line per run with every setting chosen:**
  - the model, effort, structured outputs, caching;
  - the model's limits and where they came from;
  - the part size and its source, and the number of parts with their pages;
  - the fields per group and the output limit;
  - citations, alternatives, and the other extras.
- **The trace** (`READANYDOC_TRACE=<file.jsonl>`) writes one line per model call. Each line holds:
  - the stage that made the call and why (`single`, `coded group 3/12`);
  - input tokens, cache reads and writes, output tokens, cost and seconds;
  - the model's raw reply.

  It also writes one line for each decision that is not a call: the plan chosen, groups rebuilt, partial copies
  dropped, lists that look short.

Every number on this page was read from these traces.

## 9. Citations

### How they work

Citations are found by code after extraction, with no model call. Each extracted value is looked up in the parsed
pages, whose every word carries its position on the page:

- a value printed once is boxed there;
- a value printed in several places is boxed where the other values of its object (its table row) were found, or in
  the block the model named for that object;
- a value not printed at all gets no box, or its block with low confidence.

"Low confidence" on a citation means the value is printed in more than one place; it says nothing about the value.

### The audit

We drew every saved citation on the page in an interactive viewer (click a box to see its field, click a field to
see its box) and audited it as a person checking a value would. The question was not "is the value right?" but "does
the box show where it came from?".

| Problem | How much | Cause |
|---|---|---|
| Category values ("company", "actual", "shares") boxed wherever the word first appears, such as the cover page | 366 of 528 boxes on the 10-Q | They are choices from the schema's list, not printed text, yet code searched for the word anywhere |
| Repeated numbers boxed on the wrong copy | Right row: 33 of 34 with one part, 67 of 76 with two | The model's "this row came from table r12" label was thrown away whenever a document had more than one part |
| Short numbers matched across gaps ("2.1" on "2 — 1", "6 2", "2 $ 4") | A handful per report | Digits were matched as loose tokens |
| Worked-out values (a period from "Three Months Ended December 31, 2024", a scale from "In millions", a country code from "USA", an end date from a term) | No box, or a box around a whole section | Their text is not printed as written |
| Our own citation score | Too lenient | It checked that the box contains the value's text, so any copy of a number passed |

The data itself was never affected: a missing or misplaced box does not change the value returned.

### The fixes, all code except one field

1. **The model's block label is kept across page parts.** The label for each object is matched to the final rows by
   their values (paths change when parts are merged and copies dropped), not by position.
2. **A `_from` note per object.** For each value not printed word for word, the model names the printed words it
   comes from ("data_period: Three Months Ended December 31, 2024"). It rides along in the extraction call that is
   made anyway, so no extra call. Code boxes those words, and only if it finds them on the page.
3. **A category chosen from the schema's list** is boxed only where its object's other values are, never on the
   same word elsewhere. No box is better than a wrong one.
4. **A number is matched only as a whole number**, never as loose digits.
5. **A short value printed in several places with nothing to choose by** ("1", "7") is not boxed at a guess.
6. **A stricter scorer:** a number's box must sit on a row whose label names the field (or the row's segment).

### Results

Same 10-Q and contract pack, fresh:

| | Before | After |
|---|---|---|
| Category boxes on the 10-Q cover page | 128 | 0 |
| Periods and scales boxed on their printed words | 0 | ~210 |
| 10-Q numbers on the right row (stricter scorer) | 93% | 94% (Reducto: 81%) |
| Contract pack: boxes around a whole section | 179 | 49 (computed end dates, which point at the start date and term they came from) |
| Contract pack: country code, payment terms, cadence | whole-section boxes | "USA", "Net 30", "Monthly" |
| Extraction cost per pack form | $0.010 | $0.013 (the `_from` words) |

The 94.6% and 99.2% citation scores of 0.2.0 were measured with the lenient scorer. The comparison with Reducto
stays fair (Reducto was scored the same way), but those absolute figures are too high.

## 10. Calls, thinking, and why a competitor needs only two calls

The user's own pipeline (Zenskar) reported 2 calls and $0.008-0.013 per form. Ours, one contract-pack form:

| Step | Calls | Cost |
|---|---|---|
| Reading the 2 pages (page reads, table re-reads, one close-up) | 5 | $0.0043 |
| Checking whether the schema fits one call | 1 | $0.0001 |
| Extraction (the schema is refused whole, so 4 coded groups) | 4 | $0.0089 |
| **Total** | **10** | **$0.0133** |

- **Thinking is the cost, not the number of calls.** The 4 extraction calls wrote about 15,000 output tokens; the
  answers are about 2,500. The rest, about 83%, is Haiku thinking at high effort, and output tokens cost 5 times
  input tokens. With the document cached, an extra call costs little to send; what costs is thinking again.
- **The groups are badly packed:** 45, 20, 63 and 2 fields. A call with all its thinking for 2 fields is waste.
  Not fixed yet.
- **Why two calls are possible elsewhere:** most likely the model reads the PDF and answers in one step, with no
  enforced shape and no word-level boxes. Our extra calls buy those two things, and accuracy on long documents.

## 11. One stage: let the model read the file itself

### The idea

Skip parsing. Send the file to the extraction call the way it is sent to the page reader, and ask for the schema
directly, still enforced and still grouped.

Citations still need every word with its position, and that needs no model:

| Input | The model reads | Words for citations come from |
|---|---|---|
| Digital PDF | The PDF (split into page parts) | The PDF's own text layer |
| Scanned PDF | The PDF (it sees the page images) | Local OCR (Tesseract, then PP-OCR) |
| Image, photo, TIFF, HEIC | The page images | Local OCR |
| Word, Excel, PowerPoint, email, text | The file's text, read without a model | That text |

A PDF page sent to the model also costs its page image (about 1,600 tokens), so at the same 30,000-token part size
the 10-Q became 4 parts of 12 pages instead of 2 parts of 25.

### Results

ADP 10-Q:

| | One stage | Two stages | Plain Haiku |
|---|---|---|---|
| Runs | 4 | 2 | 1 |
| Values found (of 153) | 102, 110, 103, 108 (mean 106) | 129, 99 | 111 |
| Cost per run | ~$0.12 | ~$0.23 | $0.09 |
| Time per run | ~110 s | ~270 s | 46 s |
| Calls | 44 extraction (4 parts x 11 groups) | 114 page reads + 24 extraction (2 parts x 12 groups) | 1 |
| Citation boxes | ~190 | ~530-850 | none |
| Numbers on the right row | 92-97% | 93-94% | - |

Contract pack:

| | One stage | Two stages | Plain Haiku |
|---|---|---|---|
| Fields right | 83.9% | 99.1% | 98.3% |
| Cost per form | $0.0085 | $0.013 | $0.003 |

- **About 2 times cheaper and 2.5 times faster** on the 10-Q, steadier between runs, but it never reached two
  stages' best and it does not beat plain Haiku.
- **On the pack it lost 15 points**, from about 5 real mistakes: reading the page image, the model shortened product
  names ("Platform" for "Platform Standard", "Onboarding" for "Onboarding Services"), and the pack's scorer matches
  a line item's fields by its name, so each wrong name fails the whole item. The scanned-PDF version of the same
  page kept the full names. The names instruction (section 6) was added for this; not yet measured.
- **3-4 times fewer boxes:** without parsed blocks there is no table label to choose between repeated numbers, so
  more of them are left unboxed rather than guessed.
- Handwriting in one stage is read by Haiku (two stages sends it to Sonnet) and placed by local OCR, which reads
  handwriting badly. Not tested.

**Chosen:** two stages stays the default. One stage ships as `extract(..., parse=False)`, off by default, for users
who want lower cost and speed and accept fewer citations.

### Why parsing first is worth its cost

- **Accuracy where layout matters:** clean tables help most on long, table-heavy documents (129 against 111 on the
  10-Q for parsed text against the raw PDF in one call).
- **Better citations:** the parsed blocks give the row labels that pick the right copy of a repeated number.
- **Something to inspect:** the parsed text shows what the model read, so a reading mistake is visible.
- **Scans:** a model has to read a scan anyway.

## 12. Run-to-run variance

The same two-stage setup found 129 and then 99 values on the same 10-Q. The second run included the `_from` field,
so the two are not quite the same code. In the 99 run, the group holding operating income and operating margin
skipped both fields in both parts: the report has no line called "operating income", so the model chose not to
write one. In the same run the model listed every current-liability line instead of the total (2 rows became 22).

- Decisions here that rest on one run: the part size, the `_from` field's effect on accuracy, the names instruction.
- One-stage runs spread over 8 values (102-110); two stages over 30.
- Settling a difference of this size needs 2-3 runs per setting, about $0.25 each on this report.

## 13. Mistakes and reversals

| What happened | What we learned |
|---|---|
| Said the ADP report was ~22K-40K tokens; it is ~90K | Measure token counts; do not estimate from page counts |
| A part-size test spent ~$0.30 reading the report, then failed: `part_tokens` was forwarded to the API as a model setting | Extract options and model options are different things; `part_tokens` is now an extract setting and an `extract()` argument |
| First thought groups returning `{"values": []}` were failures | They were parts whose pages hold none of the group's fields (the 7-page tail of a 45K split); legitimate |
| The stricter citation checker first called segment revenue rows "wrong row" | A checker's own limits must be ruled out before blaming the product |
| Kept a run's cache folder to make later citation checks free | Only local work is cached there, not model reads; a rerun still pays to read the pages |
| A test planned to show a guessed box for "2" | "2" was printed only once in the test page; boxing it was correct |

## 14. Money spent

| What | Approximate cost |
|---|---|
| ExtractBench and pack runs for 0.2.0 (ours $6.86, plain Haiku $3.01) | $9.87 |
| Mode comparison on 12 documents (formats per group, caching) | $8.46 |
| Coded format, instructions, page parts, citations, one stage (traced runs on the 10-Q and the pack, including ~$0.30 lost to the `part_tokens` bug) | ~$4.0 |
| Reducto | Its own credits |

## 15. Open

- Pack the coded groups better: no call for 2 fields (about a quarter of the pack's extraction cost).
- Measure medium effort for extraction: thinking is about 80% of the extraction cost.
- One more two-stage run of the 10-Q, to tell whether 99 was chance or the `_from` field.
- Ask the whole schema in one call in one-stage mode when the API accepts it (how plain Haiku did well on forms).
- Measure the names instruction on the pack.
- A `missing_fields` list naming every required field that came back null.

Code: `readanydoc/tasks/coded.py` (coded format), `readanydoc/tasks/extract.py` (plan, parts, citations),
`readanydoc/tasks/llm.py` (caching, PDF and image blocks), `readanydoc/model_limits.py`, `readanydoc/textlayer.py`
(one stage), `readanydoc/trace.py`. Decisions: [../decisions.md](../decisions.md).
