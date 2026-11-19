# How we built ReadAnyDoc

An open-source document parser and extractor, built on a general-purpose model, that works as a drop-in for
Reducto. This page tells the whole story in order: the idea, how we measured, the parser, the extractor, the choice
between two stages and one, where we ended up, and what we learned. Every number on it is a measurement from one of
the records linked at the end; nothing is estimated unless it says so.

**The short version**

- **The bet:** a general-purpose model (Claude Haiku 5.5), left untouched, plus good engineering around it, can match
  a document model that was trained for the job, for less money, and gets better for free with every new model.
- **The bet held for reading.** On the complete public benchmarks, plain Haiku ties Reducto's own model on tables
  and beats it on general pages and text accuracy, at a fifth to a ninth of the price.
- **The engineering is in what the model does not give you:** page structure (block types, positions, forms,
  headers and footers), exact citations for every value, output that always fits your schema, and documents of any
  size. Our parser closes most of the structure gap; our extractor is more accurate than Reducto on every set we
  measured except credit agreements, with citations on the right row more often.
- **What we accept:** speed. A general model read through an API takes seconds per page; a purpose-built model on its
  own hardware takes a fraction of that. We do not expect to catch up there, and we say so.
- **Most ideas failed.** Of 272 experiments on the parser alone, 75 were dropped outright and 55 became
  off-by-default options. Several results that looked like wins on small samples vanished on the full benchmarks.

**Where it stands**

| | Reducto | Plain Haiku | ReadAnyDoc |
|---|---|---|---|
| General pages (olmOCR-bench, 1,403 pages) | 80.9 | 82.2 | **84.2** |
| Tables (RD-TableBench, 1,000 tables) | **88.9** | 88.2 | 88.2 |
| Text error, lower is better (OmniDocBench, 1,651 pages) | 0.095 | **0.081** | **0.081** |
| Layout block types (OmniDocBench) | **84.6** | 55.6 | 78.3 |
| Form question paired with its answer (FUNSD, 199 forms) | **78.6** | 71.6 | 77.4 |
| Extraction, 35 documents (ExtractBench, its own grader) | 0.803 | 0.860 | **0.878** |
| Extraction, a 43-page quarterly report: values found of 153 | 72 | 111 | **129** (best run) |
| Extraction, 5 contract order forms: fields right | 93.3% | 98.3% | **99.1%** |
| Extracted numbers whose citation sits on the right row (the report) | 81% | no citations | **93-94%** |
| Parse price per page | $0.010 | $0.001-0.002 | $0.003-0.005 |
| Parse time per page (median) | **4-6 s** | 8-28 s | 20-50 s |

---

## Part 1. The idea

### Reducto as the starting point

Reducto is one of the best document APIs there is. It turns PDFs and scans into structured text, tables and
fields, and its core is r-1, a model it trained itself. We studied it before building anything:

| | What we found (Reducto published) |
|---|---|
| Prices | r-1 $0.01 per page; r-1 mini $0.001; Extract $0.02 per page including parsing; Deep Extract $0.04; Split $0.02; Classify $0.0075; Edit $0.06 |
| Products | Parse, Extract, Split, Classify, Edit, Upload, async jobs and webhooks: 20 routes in a public OpenAPI spec |
| Openings for an open alternative | r-1 cannot run on your own servers; self-hosting is Enterprise-only; zero data retention and HIPAA are paid tiers; you cannot choose the model |
| Other open tools | MarkItDown, PaddleOCR, MinerU, Docling, Marker, olmOCR, Unstructured, Zerox, Chunkr and others are parsers only: no Reducto-compatible API, no Extract, Split, Classify or Edit, no citations as standard. Several have restricted or AGPL licences |

So Reducto became both the bar to beat and the shape of the product: the same API, so that switching means changing
one URL.

### The philosophy

General-purpose models keep getting better without anyone training them for documents. If you leave the model
untouched and build the right layer around it, you should match a specialised model now and overtake it later,
because every new model release is a free upgrade. And general models are cheap: Haiku 5.5 costs $0.10 per million
input tokens and $0.50 per million output tokens.

The rules, set before any code was written:

- **Don't touch the model.** No training, no fine-tuning.
- **Never make it worse.** Anything we add must beat the plain model, or it goes.
- **A real product, not a demo.** Every Reducto feature behind the same API. Accuracy first, then cost, then speed.
- **Route by what a page looks like** (digital or scanned, tables, handwriting), never by industry or document type.
- **Open source and self-hostable,** on your own model account, with the model as a setting you can change.

**What we accept from the start: speed.** Reducto reads a page in 4 to 6 seconds; a general model reading a page
image through an API takes several times that, and our extra checks add more. A purpose-built model running on its
owner's hardware will stay faster. We chose accuracy, cost, openness and future model upgrades over speed, and every
comparison on this page shows the time next to the score.

## Part 2. How we measured

Measurement shaped every decision, and our first measurements were wrong in three ways.

| What we found | What we changed |
|---|---|
| **Cost:** we had measured through a command-line tool that adds about 33,000 tokens to every call. Through the real API, a page cost $0.0008, not $0.006: a quarter of what we thought | All costs since are measured through the real API, call by call |
| **The AI judge:** checked by hand, our Sonnet judge misread 27,174,839 as 27,174,339 and gave the page 10/10, scored a wrong medical ID column as "minor", and moved 0.3 points between two runs of the same output | No model judges output. Every score comes from a benchmark's own grader or an answer key |
| **Credit:** re-run fairly through the API, our early layer and plain Haiku tied (8.22 each). Our "lead" over Reducto had come from the model, not from us | Plain Haiku is the permanent baseline every idea must beat |

From then on:

- **A judge-free development set:** 642 items from seven public sources (tables, general pages, layout, forms, long
  documents, 147 business pages), split by hash into a half we tune on and a half we never look at.
- **The noise floor:** two identical runs of plain Haiku differ by 0-1 points on tables, 3-6 on business pages and up
  to 8-14 on some measures. A gain smaller than that is not a gain.
- **Every idea is a switch,** off by default, scored on accuracy, cost and speed together.
- **A claim needs the complete benchmark,** including the part never used for tuning.
- **Fresh runs only:** nothing reused from an earlier run when measuring cost or accuracy.

## Part 3. Stage one: the parser

The parser turns any document into text, tables and blocks with positions: what Reducto calls Parse.

### The first signal: the model already reads well

Before building anything we ran plain Haiku against Reducto on 140 pages of olmOCR-bench: Haiku 83.6, Reducto r-1
79.4, and on multi-column pages 88.8 against 69.2. On the complete benchmark the lead narrowed to 82.2 against 80.9,
but it held. **Reading was not the problem.** That changed what we built.

### What failed

| Idea | Result | Why it failed |
|---|---|---|
| Read each page twice and repair the differences | 7.56 against plain 8.01; with Sonnet as referee slightly better, at $0.09 a page, nine times Reducto | The same model twice repeats the same mistakes |
| Small open models (GLM-OCR, PaddleOCR) on a GPU, Haiku only for hard pages | Cheap, never as good as Haiku | Quality |
| Let Haiku point at the PDF's own text instead of retyping it | 7.30 against 8.14 | The PDF text layer lies (missing spaces, doubled letters, watermarks); Haiku almost never misreads digital text (1 serious error in 74 pages) |
| Free OCR as the base, Haiku patching it | 5.65 against 7.71 | Same reason, on scans |
| Smaller page images | 19 serious table errors | Detail lost |
| Less thinking (medium or low effort) | 35% cheaper, table grids 8 points worse | Accuracy |
| Zoom into every checkbox | Fixed one, broke two | The page read was already 94-99.5% right |
| Re-read every table on its own | Gains on the sample, nothing on 1,000 tables | Tuning to the sample |
| Merge tables across pages | Joined tables that should stay apart | |
| Turn sideways pages upright | Passed 128 tests, then turned five upright handwritten letters upside down | Kept only with a guard |

### What worked: supply what the model does not give

Once we accepted that the model reads well, the question became *what does the model not give you?* Every idea that
survived answered that question rather than trying to read better than the model:

| Added | What it does | Measured |
|---|---|---|
| Page image at the model's native size | Render each page at exactly the largest size the model keeps | Tiny text 84 -> 95 |
| A small open layout model on the CPU (Docling "heron") | Says where each block is and what type it is; never replaces the model's text | Layout block types +23, boxes +35 points over plain Haiku |
| Word positions from the PDF text or local OCR | Every word and cell placed on the page, for citations | Blocks with a real box on hard scans 76% -> 91% |
| Zoom vote on scanned digits | A digit changes only when two zoomed reads agree against the page read | 9 verified corrections, none wrong |
| Values read from fillable PDFs | Typed values and checkboxes straight from the file, no model | 2-3% -> 100% |
| Form rules and form-region reads | Pair each question with its answer | FUNSD +6 on unseen forms |
| Header, footer and page-number rules | Separate running heads and feet from content | olmOCR headers and footers +11 |
| Table grid repair | Fixes structure only; rejected if a single cell's text changes | Consistent grids 87.6% -> 98.6% |
| Sonnet for handwritten pages only | With a guard so printed pages stay on Haiku | 7.22 against 5.67 on 18 handwritten pages (judged, the weakest evidence here) |

### The reckoning: the full benchmarks

On our 100-table sample the engine beat Reducto. Then we ran all three systems on every page of five complete public
benchmarks, more than 5,300 pages, live.

- **Tables:** Reducto 88.85, plain Haiku 88.20, our engine 88.21. Our table work added nothing. After fixing a crash
  and rules that broke real tables, the final engine scored 88.17: level.
- **Structure held,** including on thousands of pages we had never seen:

| Same Haiku read underneath | Plain Haiku | Our engine | Gain | Reducto |
|---|---|---|---|---|
| Layout: block types right | 55.6 | 78.3 | **+22.7** | 84.6 |
| Layout: boxes in the right place | 26.5 | 61.1 | **+34.6** | 67.2 |
| Form question paired with its answer | 71.6 | 77.4 | **+5.8** | 78.6 |
| Headers and footers kept apart | 70.4 | 81.2 | **+10.8** | 90.1 |
| olmOCR overall | 82.2 | 84.2 | **+2.0** | 80.9 |

On structure, where plain Haiku was weakest, the engine closes about four fifths of the gap to Reducto on layout and
forms and more than half on headers and footers.

### The parser as shipped

Every page goes through eight stages; only Read calls a model, and most pages need one call:

| Stage | What happens | Model calls |
|---|---|---|
| Intake | File type, decryption, Office and email conversion, pages | none |
| Triage | Digital or scanned, rotation, is the text layer trustworthy | none |
| Layout | Open layout model on CPU: tables, forms, figures, headers, footers | none |
| Read | The model reads each page; zoom vote on scans; handwriting to Sonnet | 1 per page, more for scans and handwriting |
| Verify | Table grids and printed totals checked; problems become warnings | none |
| Ground | Every word and cell placed; block types set; form fields paired | none |
| Assemble | Pages joined; tables across pages linked | none |
| Shape | Blocks, Markdown, chunks, table formats, Reducto's JSON | none |

Two modes, both exactly as benchmarked (a test fails if either changes without new measurements):

| | `engine` (default) | `model-only` |
|---|---|---|
| What runs | All eight stages | One page read and the output |
| Cost per page | $0.003-0.005 | $0.001-0.002 |
| Time per page | 20-50 s | 8-28 s |
| Use it for | Anything a person will check: forms, statements, contracts, scans | Large volumes of clean digital text |

The rule behind every stage: **the model's text is never changed without independent evidence** (a second zoomed read
that agrees, the PDF's own text, a checked total).

## Part 4. Stage two: the extractor

The extractor fills a schema you give it (a JSON Schema or a Python class) from the parsed document, with a
citation for every value: what Reducto calls Extract.

### What real teams need

Teams that built extraction in-house told us where they struggled:

- citations with page positions, so a person can check every value;
- their own large, deeply nested schemas;
- long documents, with amendments attached;
- scans and handwriting;
- a signal for what to check first;
- output their systems can rely on.

### The first versions: make every reply usable

| Version | Problem found | What changed |
|---|---|---|
| 0.1.x | Model replies are text: a bracket too many, a field one level off | A check-and-repair loop against the schema; values never invented |
| 0.1.7 | A user's 20-type billing schema came back all empty: one extra `}` inside a list, and the reader kept only the piece that parsed | A broken reply is never read as a fragment; it is repaired only when the result fits the schema exactly, otherwise asked again; a field that cannot be read says so instead of "not found" |
| 0.1.8 | A bare value hides things (two prices in one contract, an inferred date) | A field report per value: conflicts with every candidate, inferred, blank, not in the document |
| 0.2.0 | The field report tripled the output and multiplied calls on big schemas (11 calls, 158 s per contract) | The model writes only your schema's fields, through a rewritten "reply schema" the API can enforce; citations found by code; review and alternatives opt-in. 20 s and $0.005 per contract |

0.2.0 was more accurate than Reducto across ExtractBench (35 documents, 2,076 pages, five schema types):

| | ReadAnyDoc 0.2.0 | Reducto | Plain Haiku |
|---|---|---|---|
| Mean of 35 documents | **0.878** | 0.803 | 0.860 |
| Quarterly reports (7) | **0.671** | 0.485 | 0.626 |
| Credit agreements (10) | 0.872 | 0.904 | **0.920** |
| Research papers (6) | **0.965** | 0.813 | 0.828 |
| Resumes (7) | 0.950 | 0.905 | **0.954** |
| Swimming results (5) | **0.975** | 0.888 | **0.975** |
| Time for all 35 | 252 min | 31 min | 14 min |

Accurate, but slow and expensive on big documents. On one 43-page quarterly report it made 251 model calls and took
10 minutes, against plain Haiku's 1 call and 46 seconds. Two thirds of the cost was extraction, not reading. We set a
target: reading the document should be most of the cost, about 80%, and filling a schema from it about 20%.

### The central problem: structured output at any size

Anthropic's structured outputs make the API constrain the model to a schema's shape. The shape is then guaranteed;
the values are not. It is the only way to never return broken output without asking twice. Three things stood in the
way:

1. **The API refuses large schemas:** at most 24 optional fields and 16 union types, and "Schema is too complex for
   compilation" beyond an unpublished size. Every quarterly-report schema and the contract schema are refused.
2. **A different format in each call breaks the prompt cache** (Anthropic's documentation). 0.2.0 asked a big schema
   in groups, each with its own format, so the whole document was paid for again by every group: 89 times on that
   report, plus 52 checks of whether each group's schema would compile.
3. **Split schemas lost enforcement** in 0.2.0, and a repair step could re-ask without it.

What we considered:

| Option | Verdict |
|---|---|
| No enforcement, repair the JSON afterwards | Rejected: output must never break, and repairs mean more calls |
| Two passes: extract freely, then fit the schema | Rejected: twice the calls |
| Limit schema size | Rejected: Reducto takes any size, and so must we |
| **One fixed answer format for every call** | **Chosen** |

**The coded format.** Every field of the schema gets a code, and every call answers in the same small format:

```json
{"values": [{"code": "f17", "item": "0.1", "value": "4200"}]}
```

`code` is an enum of the schema's own codes, so the API rejects an invented field; `item` gives the list positions;
`value` is text, converted to your types afterwards. Each call asks for its own group of fields, but the format, the
instructions and the document are identical in every call. So every call is enforced at any schema size, the
document is cached once and read by every other call at a tenth of the price, and code rebuilds your exact shape. A
schema the API accepts whole is still asked in one enforced call in its own shape.

The first coded run found three bugs, each fixed and measured:

| Problem | Fix | Measured |
|---|---|---|
| An either-or of two objects treated as plain text | Both forms' fields merged, matched back afterwards | One contract form 99 -> 111 of 112 fields |
| A "list looks short" re-ask ran without enforcement and replaced good answers | Reported as a warning instead | |
| Groups of 150 fields: the model skipped whole metrics | Groups of 40; a list never split | 97 -> 110 values on the report |

### Caching, instructions, page parts and limits

- **Caching inside one run.** The document goes first in every extraction prompt and is cached; reads cost 0.1 times
  the input price, writes 1.25 times. The first group goes alone so the others can read its cache. Nothing is reused
  between runs. On 12 documents, caching alone cut the cost by 37%.
- **Generic instructions only,** true of any document:
  - a value stated once for a table, column or page (a unit, "in thousands", a period in a heading) applies to every
    row it covers: 110 -> 130 values on the report;
  - of two copies of the same row, the less complete one is dropped: precision 49.6% -> 59.9%;
  - names, titles and labels are copied in full.
- **Page parts sized by tokens.** A long document is split into parts. A model's context window and its price jump
  (above 100,000 input tokens Haiku bills the whole request at 5 times the rate) set the upper limit; accuracy set the
  real one:

| Largest part | Parts | Values found (of 153) | Cost |
|---|---|---|---|
| ~17,000 tokens (old rule) | 6 | 130 | $0.29 |
| **30,000 tokens** | 2 | **129** | **$0.22** |
| 45,000 tokens | 2 | 89 | $0.21 |
| 79,000 tokens (largest under the price jump) | 1 | 85 | $0.19 |

  Accuracy falls once a part goes past about 25 pages, so the default is 30,000 tokens, capped by each model's own
  limit and adjustable per request.
- **Model limits are looked up, never guessed:** a snapshot of a public list of about 4,300 models (context, output
  limit, prices, price jump), your own file, or the model server itself. An unknown model is an error that names the
  setting to give.
- **Everything is logged:** one line per run with every setting chosen, and an optional trace of every model call
  with tokens, cache reads and writes, cost, and why the call was made.

### Citations: a box a person can trust

Citations are found by code after extraction, with no model call: each value is looked up in the parsed pages, whose
every word carries its position. A value printed once is boxed there; a value printed in several places is boxed in
the row the model read it from; a value not printed gets no box rather than an invented one.

We drew every citation on its page in an interactive viewer and audited them the way a reviewer would: not "is the
value right?" but "does the box show where it came from?". The data was right; the boxes had problems:

| Problem | Fix | Measured on the report |
|---|---|---|
| Category values chosen from the schema ("company", "actual") boxed wherever the word first appears, such as the cover page | Boxed only next to their row's values, never elsewhere | 128 cover-page boxes -> 0 |
| The row each value came from was forgotten when a document had more than one part | Kept across parts, matched to the final rows by their values | |
| Worked-out values (a period from a column heading, a scale from "In millions") had no box | The model names the printed words they come from, in the same call; code boxes those words only if it finds them | ~210 periods and scales now boxed on their words |
| "2.1" matched the digits of "2 — 1" | Numbers match only as whole numbers | |
| Short values like "1" boxed at a guess | Not boxed when there is nothing to choose by | |

Numbers on the right row: 93-94% against Reducto's 81%. On the contract forms, boxes covering a whole section fell
from 179 to 49 (the rest are calculated end dates, which point at the start date and term they come from).

We also found that our own citation score had been too lenient: it checked that a box contained the value's text, so
any copy of a number passed. We now also check that the box sits on the row its field names.

### The output contract

- The structure always follows your schema: every key and nesting level, the right type for every value, enum values
  only from your list.
- **A value the document does not give is null**, also for a required field. The alternative is inventing one, and a
  wrong value that looks real is worse. Reducto does the same.

## Part 5. Two stages or one?

### Why our extraction makes more calls

A team's in-house pipeline reported 2 model calls per contract form. Ours, on one form:

| Step | Calls | Cost |
|---|---|---|
| Reading the 2 pages | 5 | $0.0043 |
| Checking whether the schema fits one call | 1 | $0.0001 |
| Extraction (the schema is too big for one enforced call, so 4 groups) | 4 | $0.0089 |
| **Total** | **10** | **$0.0133** |

Thinking, not the number of calls, is the cost. The extraction calls wrote about 15,000 output tokens for answers of
about 2,500; the rest is the model thinking, and output tokens cost 5 times input tokens. A pipeline that sends the
PDF and the schema in one call (plain Haiku: 1 call, about 4,400 output tokens, $0.003, 13-19 seconds) thinks once.
What our extra calls buy: output that always fits the schema, a citation box on every value, documents of any size,
and accuracy (99.1% against 98.3% on these forms, 129 against 111 values on the report).

### One stage: let the model read the file itself

We built a second path that skips parsing: the file goes straight to the extraction call, still enforced and still
split into parts and groups. Citations still need every word with its position, which needs no model:

| Input | The model reads | Words for citations from |
|---|---|---|
| Digital PDF | The PDF | The PDF's own text |
| Scanned PDF, image, photo | The page images | Local OCR |
| Word, Excel, PowerPoint, email | The file's text | That text |

| The 43-page report | One stage (4 runs) | Two stages (2 runs) | Plain Haiku |
|---|---|---|---|
| Values found (of 153) | 102, 110, 103, 108 | 129, 99 | 111 |
| Cost per run | ~$0.12 | ~$0.23 | $0.09 |
| Time per run | ~110 s | ~270 s | 46 s |
| Citation boxes | ~190 | ~530-850 | none |

On the contract forms one stage fell to 83.9% (two stages 99.1%): reading a page image, the model shortened product
names ("Platform" for "Platform Standard"), and every field of a line item is matched by its name.

**Decision:** two stages stays the default. One stage ships as an option (`parse=False`): about half the cost and 2.5
times faster, with fewer citations and lower accuracy. Parsing first earns its cost because clean tables help most on
long, table-heavy documents, the parsed rows let citations pick the right copy of a repeated number, the parsed
text shows what the model read, and a scan has to be read by a model anyway.

## Part 6. Where we stand

**Ahead of Reducto:** general pages, text accuracy, multi-column reading order, extraction accuracy on every set we
measured except credit agreements, citations on the right row, price (parse about half, extraction about a third),
openness (self-hosted, your model account, the model as a setting).

**Level:** tables, forms (inside the margin).

**Behind, and we say so:**

- **Speed:** Reducto 4-6 s per page, ours 20-50 s; on whole documents the gap is larger. This is the trade-off we
  accepted at the start.
- **Fine layout** (about 6 points), **formulas**, **header and footer removal.**
- **Pages the model provider declines to return** (content filtering: 1.5-3% of pages on some benchmarks) come back
  empty with a warning.

**Still open:** packing extraction groups so no call is spent on two fields; medium effort for extraction (thinking
is about 80% of its cost); confidence that is more than a tier; a list of required fields that came back null.

**A caution about our own numbers:** the same two-stage setup found 129 and then 99 values on the same report. Where
a decision rests on one run, the records say so.

## Part 7. What we learned

1. **Small samples lie, in both directions.** Our table lead on 100 tables disappeared on 1,000; plain Haiku's olmOCR
   lead narrowed. A claim needs the complete benchmark and its unseen part.
2. **AI judges are noisy and sometimes plainly wrong.** Use official graders and answer keys.
3. **Measure cost through the real API, call by call.** Our first figures were four times too high.
4. **Asking the same model twice does not fix its mistakes.** A different view (zoomed strips) does.
5. **Fight the model's natural output format and you lose accuracy.** Pointers, compact syntax, smaller images and
   unfamiliar formats all scored below a plain read.
6. **The model already reads well; the gaps are structure.** Supply what it does not give: positions, types, forms,
   grids, citations, enforced shape.
7. **Know your noise floor before celebrating.** Identical runs differ by up to 14 points on some measures, and by 30
   values on one extraction.
8. **Every rule needs a guard.** Almost every structure rule that helped somewhere hurt somewhere else.
9. **Thinking is the cost of extraction.** Fewer repeated reasoning passes matter more than fewer calls once the
   document is cached.
10. **Audit what a person sees.** Our citation score said 94.6%; drawing the boxes on the page showed category words
    boxed on cover pages and periods with no box at all.

## The full records

| Record | What it holds |
|---|---|
| [Parser research record](research/journey.md) | All 28 stages of the parser work, every benchmark breakdown, every reversal, the shipped engine step by step, and all 272 experiments with the reason each was kept or dropped |
| [Extraction research record](research/extraction.md) | Structured output, cost, caching, page parts, citations, one stage against two stages, with every measurement |
| [Benchmarks](resources/benchmarks.md) | Final parser results with method and caveats |
| [Design decisions](decisions.md) | Every decision and default with its reason |
| [Per-document results](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/results) | The scores behind the tables |
