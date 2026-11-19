# Building an open-source Reducto alternative on a general-purpose model: the full record

Every stage of the project from 11 to 14 November 2026: what we set out to prove, what we built, each idea we tested, what we kept, what we threw away and why, and what the complete public benchmarks finally said.

All numbers are our own measurements unless marked "Reducto published". They come from the project's report files and its experiment ledger (272 entries, listed in Appendix C). Per-document scores for the final benchmarks are in [`results/`](https://github.com/arpitpatel25/ReadAnyDoc/tree/main/results).

## The short version

We set out to build an open-source, self-hostable, drop-in replacement for Reducto, a YC-backed document parsing API, on a general-purpose model (Claude Haiku 5.5) instead of a purpose-trained one. The thesis was that a general model plus a good software layer around it can match a specialised model, at lower cost, and keep improving as the general models improve.

> 5,300+pages across 5 public benchmarks, run on all three systems

> 272experiments in the ledger: 75 dropped, 80 kept, 55 options, 62 checks and combine steps

> ≈ 1/2our engine's price per page against Reducto r-1's $0.01

> 1/5 – 1/9plain Haiku's price per page against Reducto r-1

- **Plain Haiku already reads pages as well as Reducto's own model.** On the complete public benchmarks it is level on tables and general pages, and better on text, for a fifth to a ninth of the price.
- **Where plain Haiku loses is page structure:** where each block sits, what type it is, which form answer belongs to which question, and which text is a header or footer.
- **Our engine closes most of that structure gap** (layout +23 to +35 points over plain Haiku, forms +6, general pages +2), at about half Reducto's price. It is still about 6 points behind Reducto on layout, behind on formulas and on headers and footers, and several times slower.
- **Most clever ideas failed.** Of everything tried, the large majority either did nothing or made things worse. Several gains seen on small samples disappeared on the full benchmarks.
- **The biggest single correction:** on 100 tables our engine looked ahead of Reducto; on all 1,000 tables it was level with plain Haiku and slightly behind Reducto.

## Final comparison table (complete public benchmarks)
Our engine = profile `engine-final-4`, shipped as `engine`. Plain Haiku = one Haiku 5.5 read per page at high effort, shipped as `model-only`. Reducto = r-1 through its live API. Green = best in the row. "Level" means the gap is inside the 95% interval.

### Reading the content

| Benchmark | Measure | Reducto r-1 | Plain Haiku | Our engine | Verdict |
|---|---|---|---|---|---|
| RD-TableBench (1,000 tables) | table score | **88.9** | 88.2 | 88.2 | level |
| olmOCR-bench (1,403 pages) | **overall** (official score) | 80.9 | 82.2 | **84.2** | engine ahead of both |
| olmOCR-bench | multi-column pages | 65.6 | 79.8 | **81.0** | both ours far ahead |
| olmOCR-bench | long tiny text | 84.2 | 93.2 | **93.9** | both ours ahead |
| olmOCR-bench | maths (arXiv) | 85.3 | 87.7 | **88.3** | ours ahead |
| olmOCR-bench | maths in old scans | 80.8 | **87.6** | 86.9 | level |
| olmOCR-bench | old scans | 49.0 | 47.5 | **50.4** | level |
| olmOCR-bench | tables | **92.7** | 91.7 | 92.2 | level |
| OmniDocBench (1,651 pages) | text error (lower is better) | 0.095 | **0.081** | **0.081** | ours ahead |
| OmniDocBench | tables | **87.9** | 85.4 | 87.0 | level |
| OmniDocBench | formula error (lower is better) | **0.133** | 0.158 | 0.156 | Reducto ahead |
| OmniDocBench | reading-order error (lower is better) | **0.161** | 0.167 | 0.170 | level |

### Structure of the page

| Benchmark | Measure | Reducto r-1 | Plain Haiku | Our engine | Verdict |
|---|---|---|---|---|---|
| olmOCR-bench | headers and footers left out | **90.1** | 70.4 | 81.2 | Reducto ahead; engine +11 over plain |
| FUNSD (199 scanned forms) | question paired with its answer | **78.6** | 71.6 | 77.4 | engine level with Reducto; +6 over plain |
| FUNSD | words read | **97.6** | 97.2 | 97.3 | level |
| OmniDocBench | layout: block types right | **84.6** | 55.6 | 78.3 | Reducto ahead; engine +23 over plain |
| OmniDocBench | layout: boxes in the right place | **67.2** | 26.5 | 61.1 | Reducto ahead; engine +35 over plain |

### Extraction from long documents (the same 10 documents, standard Extract, no deep mode)

| Measure | Reducto r-1 | Plain Haiku | Our engine | Verdict |
|---|---|---|---|---|
| rows found | 53.9 | 75.4 | **75.9** | not conclusive on 10 documents |
| wrong rows avoided (precision) | 90.8 | **93.1** | 92.4 | level |
| values correct in found rows | **92.9** | 92.8 | 90.6 | level |

### Cost and speed per page

| Benchmark | Reducto $ | Plain Haiku $ | Engine $ | Reducto s | Plain Haiku s | Engine s |
|---|---|---|---|---|---|---|
| RD-TableBench | 0.0100 | **0.0012** | 0.0044 | **4.1** | 8.4 | 50.8 |
| olmOCR-bench | 0.0100 | **0.0018** | 0.0029 | **5.4** | 28.3 | 23.6 |
| FUNSD | 0.0100 | **0.0011** | 0.0035 | **4.6** | 9.3 | 21.8 |
| OmniDocBench | 0.0100 | **0.0021** | 0.0045 | **5.8** | 26.0 | 34.7 |
| LongExtract | 0.0200 | **0.0152** | 0.0191 | **66 / doc** | 2,382 / doc | 2,136 / doc |

Times are median seconds per page (per document for LongExtract), measured on a heavily loaded laptop, so ours are slower than a quiet server would be. Pages that failed for both our systems (12 oversized images, 40–47 pages Anthropic's content filter blocked) count as empty in these scores.

## The lessons

> **1. Small samples lie, in both directions.** On 100 tables the engine beat Reducto (89.8 / 91.8 against 89.2 / 91.5). On 1,000 it was level with plain Haiku and 0.7 behind Reducto. The tuned gains were partly tuning to those 100 tables. Rule we adopted: a claim needs the complete benchmark, and the part of it we never looked at.

> **2. AI judges are noisy and sometimes plainly wrong.** A hand check found the Sonnet judge misread 27,174,839 as 27,174,339 and gave a page 10/10, and it scored a wrong NPI column as a minor issue. The same output moved 0.3 points between judge runs. We switched to official deterministic graders and answer keys.

> **3. Measure cost through the real API, not a CLI.** Claude Code's CLI adds about 33,000 tokens of overhead per call, which made Haiku look about 4 times more expensive than it is ($0.006 against $0.0008 for the same page).

> **4. Asking the same model twice does not fix its mistakes.** Two Haiku reads repeat the same errors, so voting between them mostly fails. What did work was a different view of the same page: zoomed strips, where a digit changes only when two zoomed reads agree against the page read.

> **5. Fight the model's format and you lose accuracy.** Pointer output, compact table syntax, smaller images and OCR-evidence prompting all scored below a plain read. Unfamiliar output formats raise the model's hidden thinking and lower its accuracy.

> **6. The model is already good at reading; the gaps are structure.** Haiku almost never misreads characters on digital pages (1 serious text error in 74 pages). Its losses were table structure, checkboxes, block types and layout positions: exactly the things a software layer can supply.

> **7. Know your noise floor before celebrating.** Two identical runs of plain Haiku differ by 2 to 14 points on some measures (business grid 2.5–4.7, answer-key precision up to 13.8, OmniDocBench table score up to 12.8). A gain smaller than that is not a gain.

> **8. "Never worse than vanilla" needs a guard on every rule.** Almost every structure rule that helped somewhere hurt somewhere else (forms flattening real tables, rotation turning handwritten letters upside down, handwriting routing hurting printed forms). The kept versions all carry guards.

## 1. The question and the rules
11 November 2026, morning

**The question:** can an open-source layer on a general-purpose model replace Reducto, whose product rests on its own trained model (r-1)?

**The thesis:** general-purpose models keep improving for free, so a great layer on a general model should beat a purpose-built model over time. The work is the layer, not training a model.

### Rules set at the start

- It cannot be a subpar product: every Reducto feature, the same API, a "change one URL" switch.
- Accuracy first, then cost, then speed.
- Anthropic as the only frontier lab; open-source models allowed; open source and self-hostable.
- Route by what a page looks like (digital or scanned, tables, handwriting), never by industry or document type.
- "Never make it worse": nothing may lower the plain model's own quality.
- No monetisation for now.

## 2. Researching Reducto
11 November, 06:58–07:20 UTC

| Topic | What we found |
|---|---|
| Prices (Reducto published) | r-1 $0.01 per page; r-1 mini $0.001 (launched 5 Nov 2026); Extract $0.02 per page including parsing; Deep Extract $0.04; Split $0.02; Classify $0.0075; Edit $0.06 |
| Products | Parse, Extract, Split, Classify, Edit, Upload, async jobs, webhooks: 20 routes in a public OpenAPI spec. Pipelines deprecated. |
| Openings for an open alternative | r-1 cannot run on-premises; self-hosting is Enterprise-only; zero data retention and HIPAA are paid tiers; users cannot choose the model |
| Licence traps in open tools | Marker, Surya, Chandra have restricted weights; MinerU 2.5 weights AGPL; PyMuPDF and Chunkr AGPL. Excluded from the product. |

## 3. First signal: RD-TableBench, 100 tables
Before the build · Reducto's own table benchmark and official grader

| System | Score |
|---|---|
| Haiku 5.5 | 91.4 |
| Sonnet 5.5 | 91.4 |
| Reducto (their saved 2024 outputs) | 91.0 |
| GPT (Codex gpt-6-luna) | 88.1 |
| Azure | 82.4 |
| AWS Textract | 78.4 |
| GPT-4o (2024) | 75.1 |

> **Later correction**The 91.4 for Haiku was one lucky draw with a table-specific prompt. Re-measured through the API it was about 88.9, and on all 1,000 tables plain Haiku scored 88.2. This was the first of several sample results that did not survive.

## 4. First neutral benchmark: 140 olmOCR-bench pages
11 November, 07:21–09:05 · 28 pages each of tables, old scans, multi-column, tiny text, headers/footers

| System | Overall | Tables | Multi-column | Tiny text | Headers/footers | Old scans |
|---|---|---|---|---|---|---|
| Haiku 5.5 | **83.6** | **92.7** | **88.8** | 83.9 | 91.7 | **45.1** |
| Reducto r-1 | 79.4 | 87.8 | 69.2 | **87.9** | 93.1 | 41.5 |
| Reducto r-1 mini | 77.9 | 88.6 | 69.2 | 87.1 | **94.4** | 30.3 |

- Haiku's biggest win was multi-column reading order (88.8 against 69.2). It held on the full benchmark (79.8 against 65.6).
- Everyone was bad on old handwritten scans.
- The PDF text layer was unreliable: only 56 of 93 pages had a usable one.
- A cheap check (flag pages where two sources disagree) caught 77% of failures on 28% of pages.

> **Sample vs full**On the full 1,403 pages the lead narrowed: plain Haiku 82.2 against Reducto 80.9, inside the margin.

## 5. Small open models on a rented GPU
11 November, 09:40–11:26 · RunPod RTX 4090

**Why:** Haiku looked (through the CLI) like about $0.007 a page, above r-1 mini's $0.001. Could small open models carry a cheap tier?

| System | 5-category mean | Cost per page | Speed |
|---|---|---|---|
| Haiku 5.5 | **80.4** | ≈ $0.007 (CLI, overstated) | ≈ 25 s |
| Reducto r-1 | 75.9 | $0.010 | ≈ 0.3 s |
| Reducto r-1 mini | 73.9 | $0.001 | ≈ 0.35 s |
| GLM-OCR (0.9B, MIT) | 71.3 | ≈ $0.0002 | ≈ 0.75 s |
| PaddleOCR-VL-1.6 (0.9B, Apache) | 70.4 | ≈ $0.0004 | ≈ 1.8 s |

- Simulated "small model, escalate hard pages to Haiku": escalating 31% of pages gave 75.4 at $0.0024 a page, tying r-1 at a quarter of its price.
- No mix beat r-1 mini on price and quality at the same time.
- PaddleOCR's word boxes covered 79% of words on scans, against Tesseract's 57%. That word-box finding returned later as the OCR used for citations.

> **Verdict**[dropped] Small open models are a cost play only; they never matched Haiku's quality. Also dropped: the GPU itself (later RunPod had no capacity), so everything since runs on a laptop CPU.

## 6. The strategy: a layer around the model
11 November, 11:30–11:58

The project lead's point: everything so far was plain model inference, while Reducto is a pipeline plus a product. Agreed principles:

- The value is the layer around the model: preparing the input, routing, checking the output, repairing only what fails.
- Route on what the page looks like, never on industry. A healthcare document can be a typed lab report or a handwritten form.
- Never make the model's own reading worse.

> **Finding: image size matters**Rendering pages at exactly the largest size Claude keeps lifted Haiku on tiny text from 84 to 95, and overall from 80.4 to 81.6. [kept] in every profile since. It also exposed that Haiku's table output varies between runs.

## 7. Building the product in an afternoon
11 November, 12:00–14:12 · a "spine" agent, then 5 parallel Fable 5.2 agents · Apache-2.0

| Piece | What was built | Result |
|---|---|---|
| Spine | document model (document → page → region → line → word), swappable model adapters, an 8-stage pipeline, Reducto serializer, benchmark harness | reproduced every baseline exactly |
| API | all 20 Reducto routes, SQLite job queue, webhooks, Docker | **Reducto's own Python SDK works unchanged against our server**, changing only `base_url` |
| Tasks | Extract (schema guarantees, citations, deep extract), Split, Classify, Edit (fillable PDFs, flat PDFs with a schema, DOCX) | Extract got 12/12 rows with all 34 values cited |
| Citations | word and cell positions for every block | ≈ 93% coverage on digital pages, 80–82% on scans |
| Assembly and formats | 6 chunk modes, table formats, tables across pages, DOCX / XLSX / PPTX / CSV / EML input | passes its own synthetic set |
| Reading quality | evidence-based choice between reads, table checks, escalation | 84.3 on the 140 pages (plain 81.6, r-1 75.9), tuned on the same pages |

Tests grew from 50 to 630.

## 8. Reducto's own benchmarks and judge
11 November, 13:25–13:33

The project lead asked whether 75.9 was Reducto's number. It was ours. So we collected what Reducto itself publishes:

| Benchmark (Reducto published) | What it says | Could we run it? |
|---|---|---|
| ParseArena (2,000 pages, AI judge) | r-1 1st of 21 with a 63.3% win rate; LlamaParse agentic+ 58.0%, Mistral OCR 41.4%, Azure 32.7%, Textract 22.1%. Claude and GPT not included. | No: private data |
| K1Bench (196 tax forms, human-graded) | r-1 1st: 85.2% of pages free of material error | No: private data |
| LongExtract | Deep Extract 99.3% field accuracy | Yes, later (section 25) |
| RD-TableBench | 90.2% for their 2024 pipeline; no r-1 number | Yes (sections 23, 26) |
| Parse Judge | the open-sourced judge behind ParseArena | Ported, run with Sonnet as judge |

We also built a 147-page public business-document set: 45 finance, 42 tax, 22 legal, 13 insurance, 12 healthcare, 8 government; 85 digital and 62 scanned.

## 9. Judge round 1: our layer made Haiku worse
11 November, 14:30–17:45 · score out of 10; "clean" = no serious error

| System | Score | Clean pages |
|---|---|---|
| Plain Haiku | **8.01** | **74.1%** |
| Reducto r-1 | 7.85 | 71.4% |
| Ours, version 1 | 7.56 | 66.7% |
| Reducto r-1 mini | 6.99 | 59.2% |

> **Why the first layer lost**A table swap without a layout model (19 losses), and markdown conversion flattening merged cells (12 losses). Both were fixed; the net gain afterwards was small.

Further attempts: Haiku finding tables then re-reading crops (changed 3 pages); Sonnet as tie-breaker between two reads (helped). The final round-1 version scored 8.14 with 77.6% clean pages against plain Haiku's 7.98 and r-1's 7.85, but at **$0.09 a page**, nine times r-1, three quarters of it Sonnet.

> **Verdict**[dropped] two reads plus a Sonnet arbiter: the gain was small and the cost absurd. Lesson: two reads by the same model repeat the same mistakes.

## 10. Redesign and the night engine
11 November 17:52 → 12 November 01:41

**Landscape check:** MarkItDown, PaddleOCR, MinerU, Docling, Marker, olmOCR, Unstructured, LiteParse, Zerox and Chunkr are all parsers. None is a full Reducto replacement: no Reducto-compatible API, no Extract / Split / Classify / Edit, no citations as standard.

**Agreed design:** Haiku reads every page. The layer gives better input, checks the output against independent evidence, and repairs only what fails. Plain Haiku stays the permanent baseline.

### The night engine (Haiku only)

1. One Haiku read at native size.
2. A form-aware second read when checkboxes appear inside tables.
3. On scans: enlarged strips read twice; a character changes only when both zoomed reads agree against the page read. This fixed about 18–19 of 24 known wrong digits.

[dropped] correcting text from the PDF's own text layer: only 2 of 17 errors on digital pages were misread characters.

| Pages (judge, CLI) | Engine | Plain Haiku | r-1 |
|---|---|---|---|
| All 147 | **8.24, 83.0% clean** | 7.86, 73.5% | 7.68, 72.1% |
| Digital (85) | **8.65, 92.9%** | 8.13, 81.2% | 8.05, 80.0% |
| Scanned with tables (33) | **7.39, 63.6%** | 7.00, 51.5% | 6.00, 39.4% |
| Scanned without tables (29) | 8.03 | 8.03 | **8.52 (old cursive)** |
| Handwriting (18) | 6.00 | 6.11 | 5.83 |

Where r-1 really beat Haiku: 1830s cursive promissory notes (9/10/9 against 5/3/6). Enlarging the image did not help.

## 11. The real API: cost, effort and handwriting
12 November, 06:38–08:10 · first runs through Anthropic's API instead of the CLI

> **Cost was overstated 4 times**Haiku 5.5 costs $0.10 per million input tokens and $0.50 per million output. Through the API a bank-statement page cost $0.0008 and 3.4 s, against about $0.006 through the CLI. The CLI adds about 33,000 tokens per call.

| Setting | Score | Cost per page | Verdict |
|---|---|---|---|
| Engine, default (medium) effort | – | 0.0052 | – |
| Engine, low effort | 7.64 | 27% cheaper | [dropped] below plain Haiku |
| Engine, high effort | 8.09 | 0.0070 | [kept] high effort |

### The key honest finding

| System (API, high effort, judged like for like) | Score | Clean | Serious errors | Cost per page |
|---|---|---|---|---|
| Plain Haiku | **8.22** | 79.6% | 62 | **$0.0033** |
| Engine | **8.22** | **81.6%** | **55** | $0.0070 |
| Reducto r-1 | 7.69 | 70.1% | 91 | $0.0100 |

Most of the lead over r-1 came from the model, not the layer. The engine helped only on scanned tables (64% against 48% clean). [dropped] the form-aware second read: no measurable benefit at double the cost.

### Handwriting: Sonnet on 18 pages (judged by Opus to avoid self-grading)

| System | Score | Clean | Serious errors |
|---|---|---|---|
| Sonnet 5.5 | **8.06** | **72.2%** | **12** |
| Reducto r-1 | 5.67 | 33.3% | 46 |
| Engine (Haiku) | 5.61 | 22.2% | 54 |
| Plain Haiku | 5.61 | 22.2% | 51 |

Sonnet costs about $0.029 a handwritten page. [kept] as the handwriting route. Caveat: 18 pages, judged by an AI, a method dropped later.

## 12. A parallel track: layers that did not help
12 November · Reducto's Parse Judge, Sonnet 5.5

| Idea | What it does | Result | Verdict |
|---|---|---|---|
| Pointer output | Haiku points at numbered text-layer segments instead of retyping | 7.30 against plain 8.14 (74 digital pages); left 23% of segments unplaced; 56 s against 25 s | [dropped] |
| Pointer + fill | re-inserts unplaced segments | 7.09 | [dropped] |
| OCR evidence + model patches (scans) | OCR words as the base, Haiku patches them | 5.65 against plain 7.71 (62 scans); 2.6× cost, 3× slower | [dropped] |
| Smaller image (1,400 px) | cheaper input | 7.38 against 8.25; 19 serious table errors | [dropped] |
| Compact table syntax | fewer output tokens | below a plain read | [dropped] |
| Low effort | less hidden thinking | 8.25 against 8.06, 29% cheaper, 37% faster (digital pages) | promising there; later rejected on tables in the judge-free set |
| No-model rules route for prose pages | plain text pages handled without a model | 9.08 against Haiku 9.88; covered 35% of a 10-K | noted, not shipped |

> **Why pointing lost**The PDF text layer itself lies: words without spaces, doubled glyphs from fake bold, watermarks mixed in, struck-through text in redlines, adjacent form values merged. On digital pages plain Haiku almost never misreads characters (1 serious text error in 74 pages), so pointing solves a problem Haiku does not have and imports the layer's defects.

## 13. End-to-end reality check
12 November, 08:14–10:26

The project lead pushed back: the judge only scores text and table structure. It does not score boxes, block types, chunking or multi-page behaviour, which are what Reducto actually returns. So we compared our real JSON to r-1's real JSON on the same pages:

| Aspect | Result |
|---|---|
| Response structure, every field 3 levels deep | **identical** |
| Reading order | 97% agreement |
| Bounding boxes | median overlap 0.80; 70% overlap by half or more; 10% of blocks without a real box |
| **Block types** | **only 50% agreement**: we labelled almost everything Text and never produced Header, Footer or Key Value (r-1: 244, 260, 306) |
| **Speed** | **115–427 s per file against Reducto's 4–15 s**, about 30× slower |

The project lead: "what is the USP of our product? It's almost non-existent." Agreed answer: the reading layer added little over plain Haiku; the value is the whole product. A real technical edge would have to come from page structure.

## 14. Building everything end to end, against Reducto's real responses
12 November · four workstreams: structure, parse options and file types, tasks, handwriting route and multi-page · harness sends identical HTTP requests to Reducto, our engine and plain Haiku

| 147 business pages | First pass | After the structure work |
|---|---|---|
| Block types agreeing with r-1 | 50% | **75%** |
| Header / Footer / Key Value blocks produced | 0 / 0 / 0 | 106 / 121 / 98 |
| Whole-page boxes (a block with no real position) | 9% | 3% |
| Median box overlap with r-1 | 0.80 | 0.82 |
| Handwriting pages (judge) | 5.67, 22% clean | **7.22, 44% clean (Sonnet route)** |
| Consistent table grids | ours 89%, plain 87%, Reducto 95% |  |

Judge on all 147 pages after this work: ours 8.24 (82% clean), plain 8.03 (77%), r-1 7.71 (73%). Median time per page: Reducto 4.8 s, ours 23.6 s.

## 15. Extract, Split, Classify and Edit against Reducto's real responses
12 November · 29 cases · deterministic comparisons

| Task | Cases | Result | Cost of one pass |
|---|---|---|---|
| Extract | 10 business forms + 3 long documents with answer keys | values agree on 10 of 10 forms except small wording (e.g. "Bank of America, N.A." against "BANK OF AMERICA"); long documents: all 694 / 279 / 155 rows found by both, our leaf accuracy 100% | Reducto $2.92, ours $0.50 |
| Split | 5 document packets | 5 of 5 correct for both |  |
| Classify | 6 | 6 of 6 correct for both |  |
| Edit (fill a form) | 5 (fillable, flat scan, flat digital, DOCX) | all expected values present for both; ours wrote real form fields only after a later fix |  |

Added afterwards as options: a relevance filter for long documents, asking for nearby pages, example outputs, warnings for required fields not found.

## 16. Why we dropped the AI judge
12–13 November

Looking at sample pages by hand exposed the judge:

- On a 1935 Federal Reserve page the judge misread 27,174,839 as 27,174,339 and gave our output 10/10.
- On a CMS-1500 healthcare claim our NPI column was wrong and the judge scored it as minor.
- The same output moved between judge runs (8.14 → 8.06; r-1 8.42 → 8.14), so differences under about 0.3 on 72 pages were noise.
- Questions of "is this a Key Value block or a table?" were judged inconsistently.

The project lead: "we don't want a model to judge… we want a proper benchmark." From here every number comes from official graders and answer keys.

## 17. The judge-free development set
13 November  · one command scores everything, no model called while scoring

| Component | Items | Scored by |
|---|---|---|
| RD-TableBench sample | 100 tables | Reducto's official table grader |
| olmOCR-bench sample | 140 pages | official test classes |
| OmniDocBench sample | 100 pages | official evaluation code, unmodified |
| DocLayNet | 102 pages | block types and boxes against human labels |
| FUNSD | 50 forms | question–answer links |
| LongExtract | 3 documents | row recall and leaf accuracy |
| Business pages | 147 | table grid consistency, printed totals, header/footer text kept, block-type answer key (30 pages, grown to 100 later, model-written, not human-checked) |

**Method rules:** each item is assigned to "dev" or "held" by its hash; tune on dev, report both, never tune on held. Every idea is a switch, off by default. "Never worse than plain Haiku beyond noise" is the bar for keeping it. Saved model reads are replayed, so a downstream change costs nothing to re-test.

### Baseline

| Measure (dev / held) | Reducto r-1 | Plain Haiku | Engine before this round |
|---|---|---|---|
| RD-TableBench | **89.2 / 91.5** | 87.7 / 89.4 | 87.8 / 89.4 |
| olmOCR mean | 77.6 / 74.4 | 77.2 / 79.2 | 77.3 / 79.0 |
| DocLayNet type recall | **84.5 / 84.4** | 72.9 / 64.4 | 72.9 / 64.4 |
| FUNSD links | **87.6 / 86.8** | 80.6 / 82.8 | 79.5 / 79.4 |
| Business consistent grids | **95.8 / 94.2** | 87.6 / 84.7 | 90.5 / 87.2 |
| Business answer-key recall | **97.2 / 93.3** | 65.7 / 66.0 | 68.5 / 67.0 |
| Cost per business page | $0.0100 | **$0.0033** | $0.0087 / $0.0101 |

> **The noise floor**Two identical runs of plain Haiku on independent reads differed by 0–1 point on tables and olmOCR, 3–6 points on business grids, types and answer keys, and 8–14 points on OmniDocBench table scores and the new answer key's precision. Every later verdict was judged against this.

## 18. Round 1: five workstreams in parallel
13 November · every idea scored alone on top of plain Haiku · full list in the appendix

### Workstream 1: layout (an open layout model on CPU)

| Idea | Result | Verdict |
|---|---|---|
| Docling "heron" layout model | 94% type recall, box overlap 0.91 on DocLayNet dev | [kept] as the detector |
| PP-DocLayout-plus | 74% recall, 0.75 overlap; label set maps worse | [dropped] |
| Block types from layout, unguarded | +17 type recall, but losses on FUNSD and business key | [option] |
| Block types from layout, guarded | +14.5 / +12.8 type recall, +11 / +5 headers/footers, nothing worse | [kept] |
| Layout boxes | box overlap +0.03 / +0.02 | [kept] with guarded types |
| Split blocks along layout regions | FUNSD links −2.7, business key precision −7.6 (cut form fields apart) | [dropped] then a later guarded version kept |
| Re-read form regions from crops, replacing blocks | flattened 21 business tables into lines | [dropped] |
| Fill missing text from crop reads | 935 crops, 841 already present; reading order worse | [dropped] |
| Assemble the page from region reads | best layout numbers, but tiny text −11.8, old scans −2 | [option] |

### Workstream 2: forms

| Idea | Result | Verdict |
|---|---|---|
| Fillable-PDF values and checkbox states from the file | on 26 filled forms: typed values 2–3% → 100%, checkbox states 57–64% → 100%; no model call | [kept] |
| Checkbox crop re-reads | the page read was already 94–99.5% right; crops fixed 1, broke 1–2 | [dropped] |
| Form grids → Key Value (kv_rules) | right on answer-key grids, but r-1 itself keeps many form grids as tables | [option], row version kept later |
| Label: value lines joined (kv_lines:join) | FUNSD +0.8, free | [kept] |
| Form region re-read | FUNSD +1.9 dev, business losses | [dropped] |

### Workstream 3: checks and repairs

| Idea | Result | Verdict |
|---|---|---|
| Table grid repair with the draft table in the prompt, exact acceptance | 35 of 47 broken tables repaired with no cell text changed; business grids 87.6 → 98.6 / 84.7 → 95.3 (r-1 95.8 / 94.2) | [kept] |
| Grid repair by fresh crop read | only 15 of 47; a fresh read differs in some digit | [dropped] |
| Padding broken grids | +10 points at no cost, but proves nothing about which column a value belongs to | [option] |
| Printed-totals check with zoomed cell re-read | never worse; 2 real fixes on the whole set | [kept] |
| Completeness from PDF text / crops / scans | few lines restored, no measure moved; one wrong insertion | [dropped] |
| Flags for doubtful tables | tables with a grid flag score 79.8 against 89 unflagged | [kept] (no content change) |

### Workstream 4: OCR, preparation, speed

| Idea | Result | Verdict |
|---|---|---|
| Tesseract, with PP-OCR filling what it cannot place | blocks with a measured box on hard scans 76% → 91% | [kept] |
| PP-OCR alone or first | looser boxes (median overlap 0.84 → 0.70) | [dropped] |
| Turn sideways pages upright | 0 wrong turns on 128 test images (but see section 19) | [kept] with a guard |
| Deskew | angle right, reading not better | [option] |
| Zoom vote gated by numbers and legibility, started early | keeps all 9 verified digit corrections; scans 39 s → 27 s | [kept] |
| Medium effort instead of high | 35% cheaper but business grids −8.2, answer-key recall −9.9, totals 176 against 235 | [dropped] |
| Medium effort only for easy pages | loose threshold failed; strict threshold inside noise | [option] |
| 16 pages read in parallel, shared rate-limit backoff | no rate limit at about 60 calls in flight; a document 43 s → 24 s | [kept] |
| Batch API for async jobs | exactly half price, but 2,576 s instead of 31 s | [option] |

## 19. Round 1 combined: engine-final
13 November · pieces added one at a time on the same saved reads

1. **Step 0:** everything off = plain Haiku (637 of 639 responses identical).
2. **Step 1, OCR and preparation:** the rotation method turned 5 *upright* pages upside down (handwritten letters, vertical Chinese handwriting). Workstream 4's "0 wrong turns" had been measured without handwriting letters. A guard was added; after it, exactly the 3 truly sideways pages are turned. OmniDocBench layout box overlap 0.30 → 0.61.
3. **Step 2, checks:** business grids 87.6 → 98.6 / 84.7 → 95.3. Workstream 1's own table repair was compared on the same reads and lost (89.0 / 87.3); left out.
4. **Step 3, layout:** DocLayNet type recall +14.7 / +12.6, headers/footers +13.2 / +5.3, nothing worse.
5. **Step 4, forms:** fillable values and joined lines. An interaction was found and fixed: a crop of the page render cannot show values typed into fields, so such regions are never re-read.
6. **Step 5, handwriting route to Sonnet:** 66 of 639 pages routed, including 13 printed FUNSD forms with one "[illegible]" mark; FUNSD links −3.4. A print guard cut routed pages to 52.
7. **Steps 6–7:** form-grid rows rule and a guarded layout split, both kept.

An incident: the test disk filled (each server kept 0.5–1.2 GB), runs died half-way and were repeated; this later became the automatic clean-up job.

### Result against plain Haiku and r-1 (dev / held)

| Measure | Plain Haiku | engine-final | Reducto r-1 |
|---|---|---|---|
| RD-TableBench | 87.7 / 89.4 | 87.8 / 89.5 | **89.2 / 91.5** |
| olmOCR mean | 77.2 / 79.2 | **80.5 / 80.6** | 77.6 / 74.4 |
| OmniDocBench layout type recall | 57.5 / 58.4 | 82.2 / 65.3 | **85.7 / 65.7** |
| OmniDocBench layout box overlap | 0.301 / 0.269 | 0.636 / 0.486 | **0.657 / 0.504** |
| Business answer key (100 pages), precision / recall | 71.8 / 64.8 | 78.3 / 71.6 | **91.5 / 91.8** |

> **The first answer key flattered us**On the first 30 hand-picked pages plain Haiku scored 85.9 / 84.5 precision; on 70 new pages, 60.5 / 70.3. The largest confusion: 134 form fields typed as Text where the key says Key Value.

## 20. Round 2: tables, forms and product gaps
13 November · three workstreams plus a combine step · result: `engine-final-2`

### A. Tables and formulas

| Idea | Result | Verdict |
|---|---|---|
| Re-read every table on its own; keep the second read only if its cells agree | 277 tables re-read, 105 accepted; RD-TableBench +2.06 / +0.21; olmOCR table tests +6.3 | [kept] (see section 23) |
| Re-read tables the page read missed, with a forcing prompt | 3 accepted, all real tables | [kept] |
| Formula matrices re-read | 1 region; formula error 0.184 → 0.070 | [kept] |
| Clean-up of table markup | RD +0.04 / +0.43, free | [kept] |
| Merge tables by layout or continuation | joins tables the key keeps apart; OmniDocBench tables −5 to −6 | [dropped] |
| Accept re-reads by "fit", "strict" or "blind" | blind swaps errors; strict accepts almost nothing | [dropped] |
| Read tables and forms from region crops | olmOCR table tests +12.7 but printed totals −5 / −14 | [dropped] |
| Zoom only the table area | lost 2 of 9 verified corrections | [dropped] |

### B. Forms and structure

| Idea | Result | Verdict |
|---|---|---|
| Split one-line running heads into word groups (hfsplit) | answer-key recall +3.6 / +1.9 on three read sets | [kept] |
| Read form regions from a crop as evidence only (formread) | FUNSD links +4.7 / +1.0 | [kept] |
| Key Value lines are Text on pages without forms (kv=page) | small gains, nothing lower | [kept] |
| Fill missing boxes from free layout regions (boxfill) | small gains | [kept] |
| Field-like text inside any form region → Key Value | FUNSD +4.5 but OmniDocBench types −7.5 (exercise lists look like forms) | [dropped] |
| Headers by position or type size | recall and r-1 agreement lower | [dropped] |
| Figure blocks from layout detections | DocLayNet +1, but OmniDocBench precision lower on held | [option] |
| Print guard for the handwriting route | no FUNSD form goes to Sonnet any more | [kept] |

### C. Product gaps

Automatic disk clean-up (one machine had 18 GB of page renders that were never deleted), real form fields in Edit output for flat and scanned PDFs, Figure blocks, and other response fields.

### Combined result (engine-final-2)

- RD-TableBench 89.8 / 91.8 against r-1 89.2 / 91.5. **Ahead on the sample**, which turned out not to hold (section 23).
- olmOCR table tests 93.7 / 90.0 against r-1 87.3 / 88.3.
- FUNSD links 86.6 / 84.1 against r-1 87.6 / 86.8.
- Business block types still 16–23 points behind r-1.
- Cost per business page $0.0119 (r-1 $0.0100); median 29 s.

## 21. Round 3: cheap fixes on a separate branch
13 November · only changes that add no model cost or reduce it

| Idea | Result | Verdict |
|---|---|---|
| Header/footer detection (hfdet), page-number split (pnsplit), one-column "tables" are prose, header tables, "(a)" labels | business answer-key recall +6.7 / +1.9, nothing lower | [kept] |
| Caption / list / long / figure label rules | nothing, or every moving measure down | [dropped] |
| Second zoom pass only over disputed strips | identical output, 19 fewer strip calls | [kept] |
| Zoom strips at low or medium effort | lost 2 of 9 corrections, made one wrong change | [dropped] |
| Start form reads together with the page read | identical output; form pages wait 39 s → 27 s | [kept] |
| Re-read only tables that show a warning sign | half the re-reads but RD dev −1.3 | [option] |
| Per-page spending cap | works; steps skipped are recorded | [option] |
| Only the handwritten region to Sonnet | the layout model finds no region on 10 of 15 handwritten ledgers | [dropped] |
| Sonnet at lower effort on handwriting | not run (owner's priorities) | [not done] |

Result: business answer-key recall 81.0 / 76.7 (r-1 92.7 / 91.2). Profile `engine-final-3-full`.

## 22. The form-grid probe: is this the main lever?
13 November · offline, saved responses only, no model call

Question: Haiku often writes a form as a big table. If we split those form tables into fields, would we close the block-type gap with r-1?

> **Answer: no**Splitting mixed form tables was worth about +1 point on dev and 0 on held. Even a perfect split of every table and Key Value block would stop at about 86%, 5–7 points under r-1. About half of all missed blocks were not inside our tables at all. [parked] by the owner.

## 23. Full RD-TableBench: the sample gains vanish
14 November, 01:38–02:37 · all 1,000 tables, official grader, live API calls, no cached reads

| System | All 1,000 | 900 never in the sample | 100 sample tables | No table returned | $ per table | Median s |
|---|---|---|---|---|---|---|
| Reducto r-1 | **88.85** | **88.68** | 90.38 | 2 | 0.0100 | **4.1** |
| Engine (engine-final-3-full) | 88.21 | 88.04 | 89.76 | 7 | 0.0050 | 45.4 |
| Plain Haiku | 88.20 | 88.18 | 88.37 | 4 | **0.0012** | 8.4 |

> **What it means**Engine minus plain Haiku: +0.01. Engine minus Reducto: −0.63 (interval −1.57 to +0.28). Plain Haiku ties r-1 at an eighth of its price, and the engine added nothing on unseen tables. About 10 of the engine's 15 worst losses were self-inflicted: structure rules turning real tables into Key Value blocks or splitting them, and 57 printed tables wrongly sent to Sonnet as handwriting.

The run also exposed a server crash under parallel requests. The project lead: "does this tell us that the whole experiment failed?" Our answer: no for the product, yes for the idea that the reading layer improves tables. This led to the conclusion "plain Haiku is good enough as the reader; invest in the software around it", tested on the other benchmarks before deciding.

## 24. Round 4: the crash and the guards
14 November · tag `final-round-4` · profile `engine-final-4`

| Problem | Cause found | Fix |
|---|---|---|
| Server crash under parallel requests | not a race: OpenCV 5's arm64 resize reads one row past the end of the source image on a slight downscale, and dies when the image ends at a memory-page edge | resize works on a padded copy (pixels identical); one worker thread per local model; a test reproduces the crash |
| Real tables turned into Key Value | the form-grid rule judged cell text only; counts and checkboxes looked like fields (5 of 1,000 tables, −0.41 on the mean) | leave a table alone where the layout model sees a table; columns of amounts count as table evidence |
| Printed tables sent to Sonnet | the handwriting signal fired on faint print | route only if the second OCR cannot read the page as print: 52 → 14 of the 57 |
| Tables "cut into pieces" | the page read itself, not the engine | none needed |

The re-read of every table, measured on 250 tables: +1.25 points (interval +0.24 to +2.44), but only +0.76 on 150 random unseen tables, inside noise. Estimated full-engine score about 89.1. Proof: 437 mixed requests at 16 at once, 0 failures.

## 25. Four more complete public benchmarks
14 November · Reducto, plain Haiku and the engine on every page · full numbers in the final table above

### olmOCR-bench (1,403 pages)

Engine 84.2, plain Haiku 82.2, Reducto 80.9. Engine minus Reducto +3.3 (interval +1.8 to +4.9); engine minus plain +2.0 (+1.2 to +2.9); the same on the 1,263 pages never in our sample (+3.0 and +1.8). Almost all of the engine's gain is the free header/footer step (+10.8 over plain). Reducto stays ahead on headers and footers (90.1 against 81.2).

### FUNSD (199 scanned forms)

Engine 77.4, plain Haiku 71.6, Reducto 78.6. On the 149 forms never seen: engine 74.9, plain 68.8, Reducto 76.3. The form gain held on unseen forms (+6.1, interval +1.9 to +10.3). Engine level with Reducto (−1.4, interval −4.7 to +1.6).

### OmniDocBench (1,651 pages)

Layout is where the engine earns its keep: block types 78.3 (plain 55.6, Reducto 84.6), boxes 61.1 (plain 26.5, Reducto 67.2). The same on the 1,551 pages never in the sample. Text: both ours 0.081 against Reducto's 0.095. Formulas: Reducto better.

Plain Haiku beat Reducto on text for handwritten notes, slides, research reports, newspapers and Chinese text. Reducto won on magazines, academic papers, textbooks, exam papers and English text.

### LongExtract (whole documents)

The 50 documents total 19,528 pages (one alone is 11,622). Reducto ran on the 32 shortest, ours on the 10 shortest.

- Reducto's rows found on its 32 documents: 37.2%. It returned only part of long tables (e.g. 258 of 1,560 rows, 37 of 1,295) and no rows at all on 5 documents.
- On the 10 common documents: rows found 53.9 (Reducto), 75.4 (plain), 75.9 (engine), with wide intervals.

### Failures found along the way

- Reducto rejected 8 oversized images; our server rejected 12 (our 50-megapixel limit).
- **40–47 pages came back empty from our side** because Anthropic's content filter blocked the output ("Output blocked by content filtering policy"), mostly textbooks, newspapers and magazines. Reducto had none. Our server returns these silently as empty with no fallback; the owner set this aside for now.
- A one-hour stall of the server mid-run, with no error in the log; pages later retried.

## 26. Full RD-TableBench on the final engine
14 November, 17:00–20:06 · separate server pinned to `final-round-4`

| System | Score on 1,000 tables | $ per table |
|---|---|---|
| Reducto r-1 | **88.85** | 0.0100 |
| Plain Haiku | 88.20 | **0.0012** |
| Engine, round 4 (shipped) | 88.17 | 0.0044 |
| Engine, round 3 | 88.21 | 0.0050 |

> **The estimate did not hold**The fix agent's estimate of about 89.1 rested on 250 tables, mostly the 100 the engine had been tuned on. The final engine is level with plain Haiku on tables (−0.02) and 0.7 behind Reducto, inside the margin. Four different table measurements now agree: on tables, all three systems are level.

## 27. Product checks: citations, confidence, structured output
14 November

### Citations, against Reducto's real response on the same certificate

| Aspect | Before the fix | Reducto | After the fix |
|---|---|---|---|
| Values | 8 of 8 identical | – | 8 of 8 identical |
| Cited text | the whole row or block | just the value | **just the value** |
| Effective-date box | covered both dates (a repeated "01" matched the neighbouring date) | exact | **exact** |
| Block type | "Table" everywhere | Key Value / Text | unchanged (parked form work) |
| Time | 42 s | 7.7 s | – |
| Cost | $0.004 | $0.02 list | $0.004 |

### Confidence

Ours is a fixed tier (0.95 / 0.8 / 0.7 / 0.3) based on how cleanly the text was found on the page; Reducto's varies per field. Anthropic does not expose token probabilities, so a real score would combine free checks with agreement between two extractions. Planned as a measurement first; not built.

### Anthropic's built-in structured output

- Saves no passes: our own loop made the same number of model calls.
- Our switch for it was broken: 10 of 13 schemas rejected until adjusted; then values came back empty with citations on, because the built-in schema forbids the source tags we need.
- A complaint from a team building extraction in-house, confirmed in Anthropic's docs: at most 24 optional fields and 16 "value or empty" fields per request, and "Schema is too complex for compilation" beyond internal limits. A large extraction schema will not fit.
- [dropped] as the default; our check-and-repair loop stays.

## 28. What was decided

- **Default:** the engine on Haiku 5.5 at high effort, exactly as benchmarked, with Sonnet 5.5 for handwritten pages. Shipped as `engine`; the plain read ships as `model-only`.
- **Users choose:** mode, page model, handwriting model, extraction model, effort and other model options, with any Anthropic model and OpenAI-style models. Benchmarks claimed only for Haiku 5.5.
- **Compare against Reducto only:** Mistral OCR and LlamaParse were not run.
- **Set aside for now:** the fallback for pages Anthropic blocks, the long-list extraction instability (one run lost about 650 of 694 rows), form block types, formulas, speed.
- **Next:** clean-up for open source (two profiles instead of 25, research code out of the package), reliability (time limits per page, warnings, concurrency), a Python library, per-request model choice, cost reporting.

## Appendix A: sample vs full, every reversal

| Claim on a sample | On the complete data |
|---|---|
| Haiku 91.4 against Reducto 91.0 on 100 tables (one draw, table prompt) | Plain Haiku 88.2 against Reducto 88.85 on 1,000 |
| Engine 89.8 / 91.8 against Reducto 89.2 / 91.5 on the 100-table sample (round 2) | Engine 88.2 against Reducto 88.85 on 1,000; level with plain Haiku |
| Round-4 estimate of about 89.1 for the full engine (250 tables) | 88.17 on all 1,000 |
| Haiku 83.6 against Reducto 79.4 on 140 olmOCR pages | 82.2 against 80.9 on 1,403: the lead narrowed into the margin |
| Plain Haiku business answer key 85.9 / 84.5 precision on the first 30 pages | 60.5 / 70.3 on 70 new pages |
| Workstream 4: "0 wrong turns" for page rotation on 128 test images | 5 upright handwritten pages turned upside down on the full development set |
| Judge: engine 8.24 against plain 7.86 (CLI, default effort) | Through the API at high effort: 8.22 against 8.22 |
| Low effort "costs nothing in quality" (judge, digital pages) | Judge-free: business grids −8.2, answer-key recall −9.9; rejected |
| Engine forms: FUNSD 86.6 / 84.1 against r-1 87.6 / 86.8 (sample) | Held: 77.4 against 78.6 on 199 forms, +6 over plain Haiku on unseen forms |
| Engine layout: close to r-1 on 100 OmniDocBench pages | Held: 78.3 against 84.6 types, 61.1 against 67.2 boxes on 1,651 |

> **What survived**The engine's gains on layout, forms, headers and footers, and general pages all held on the complete benchmarks, including the parts never used for tuning. The table gains did not.

## Appendix B: what the shipped engine contains

| Step | What it does | Model cost | Where it was proven |
|---|---|---|---|
| Native-size page image | render at the largest size the model keeps | none | tiny text 84 → 95 (sample) |
| Haiku 5.5 page read, high effort | the reading itself | 1 call per page | medium effort lost on tables |
| Rotation with guard | turn sideways pages upright | none | 3 of 639 pages turned, all correctly |
| Open layout model (Docling heron, CPU) | block types and boxes as evidence | none | layout +23 / +35 (full OmniDocBench) |
| Tesseract + PP-OCR word positions | citations and box placement | none | boxes on hard scans 76% → 91% |
| Zoom vote on scans | a digit changes only when two zoomed reads agree | strip reads on gated scans | 9 verified digit corrections |
| Handwriting route | Sonnet 5.5 for handwritten pages, with a print guard | ≈ $0.03 per handwritten page | 18 pages (judge); weakest evidence |
| Fillable-PDF values | typed values and checkbox states from the file | none | 2–3% → 100% on 26 filled forms |
| Form rules and form-region reads | pair labels with values | 1 crop read per form region | FUNSD +6 (full, unseen) |
| Header/footer and page-number rules | separate running heads, feet and numbers | none | olmOCR headers/footers +11 (full) |
| Grid repair, totals check, flags | fix broken table grids; check printed totals | calls only for broken tables | business grids 87.6 → 98.6 |
| Table re-read (every table) | second read kept only if cells agree | ≈ $0.0015 per table | not significant on full RD-TableBench |
| Formula matrices, missed tables | targeted re-reads | rare | small sample gains |

## Appendix C: all 272 experiments in the ledger

Every idea scored on the judge-free development set, in the order it was run, with the verdict and the reason recorded at the time. "dev / held" numbers are the two halves of the set. Earlier stages (sections 3–15) were judged by an AI or measured directly and are described above.

**272 experiments:** [kept 80] [dropped 75] [option 55] [other 62] (checks, baselines, combine steps)

| # | Date | Workstream | Experiment | What changed | Verdict | Why | Biggest moves vs plain Haiku |
|---|---|---|---|---|---|---|---|
| 1 | 2026-11-12 | ws1-layout | `ws1_model_heron` | DOCFORGE_REGIONS=(model alone): Docling heron layout model alone on the DocLayNet pages, no reader (blocks = detections) | [keep (as detector)] | 94% type recall and IoU 0.91 on dev, far above r-1; chosen detector | dev dln.type_recall +21.5; dev dln.type_precision +17.9; held dln.type_recall +26.8; held dln.iou50 +21.1 |
| 2 | 2026-11-12 | ws1-layout | `ws1_model_ppdoclayout_plus` | DOCFORGE_REGIONS=(model alone): PP-DocLayout_plus-L alone on the same pages | [drop] | type recall 74% and IoU 0.75: its label set and box conventions do not map to DocLayNet/Reducto types as well as heron | dev dln.type_recall_lenient +7.2; dev dln.iou50 +4.4; held dln.type_recall_lenient +7.4; held dln.iou50 +4.4 |
| 3 | 2026-11-12 | ws1-layout | `ws1_off` | DOCFORGE_REGIONS=: layout-api profile through the HTTP server with no switch (must equal vanilla) | [check] | identical to vanilla on the DocLayNet dev pages | dev cost.doclaynet.s_page +26.7; dev dln.type_recall +0.0 |
| 4 | 2026-11-12 | ws1-layout | `ws1_a_off` | DOCFORGE_REGIONS=: in-process replay with no switch on all dev components (must equal vanilla) | [check] | no measure differs from vanilla: replay tool is faithful | dev cost.doclaynet.s_page -0.4; dev tables.score +0.0 |
| 5 | 2026-11-12 | ws1-layout | `ws1_a_types` | DOCFORGE_REGIONS=types: block types from the matched layout region (every type change allowed); reading untouched | [option] | +17 points DocLayNet type recall, but small losses on FUNSD links and the business answer key | dev cost.funsd.s_page +19.7; dev dln.type_recall +17.4 |
| 6 | 2026-11-12 | ws1-layout | `ws1_a_types_keepfurn` | DOCFORGE_REGIONS=types,deny=Header>*\|Footer>*: types from layout, but a block the reader typed Header/Footer keeps that type | [option] | olmOCR headers_footers +11 but business key recall -2.2 | dev cost.funsd.s_page +19.7; dev cost.business.s_page -17.2 |
| 7 | 2026-11-12 | ws1-layout | `ws1_a_boxes` | DOCFORGE_REGIONS=boxes: block box from the one-to-one matched layout region; types untouched | [needs-work] | IoU +0.03 but IoU>=0.5 share -0.2 on dev | dev cost.funsd.s_page +19.7; dev cost.business.s_page -17.2 |
| 8 | 2026-11-12 | ws1-layout | `ws1_a_both` | DOCFORGE_REGIONS=types,boxes: types and boxes from layout (DocLayNet dev only) | [needs-work] | sum of the two; same small IoU50 loss as boxes | dev dln.type_recall +17.4; dev cost.doclaynet.s_page +17.0 |
| 9 | 2026-11-12 | ws1-layout | `ws1_a_split` | DOCFORGE_REGIONS=types,boxes,split: types + boxes + text blocks cut at line breaks between layout regions | [drop] | best DocLayNet numbers but FUNSD links -2.7 and business key precision -7.6 | dev cost.funsd.s_page +19.9; dev dln.type_recall +17.5 |
| 10 | 2026-11-12 | ws1-layout | `ws1_b_grid` | DOCFORGE_REGIONS=tables=grid: every detected table re-read from its crop; replaces the page read when the crop grid is consistent | [option] | small gains on tables/olm/omni, but 5 fewer reconciling totals on business | dev cost.tables.s_page +22.7; dev cost.funsd.s_page +20.5 |
| 11 | 2026-11-12 | ws1-layout | `ws1_b_numbers` | DOCFORGE_REGIONS=tables=numbers: as grid, and the crop numbers must agree better with independent words (PDF text / OCR) | [drop] | RD-TableBench -0.4, nothing gained elsewhere | dev cost.tables.s_page +22.6; dev cost.funsd.s_page +20.6 |
| 12 | 2026-11-12 | ws1-layout | `ws1_b_broken` | DOCFORGE_REGIONS=tables=broken: crop re-read only where the page read has an inconsistent grid; accepted when the crop grid is consistent | [keep candidate] | grid_ok +1.4 on business, no measure worse on dev | dev cost.funsd.s_page +19.7; dev cost.omni.s_page +17.7 |
| 13 | 2026-11-12 | ws1-layout | `ws1_c_forms` | DOCFORGE_REGIONS=forms: form-like regions re-read from a crop with a form prompt, guarded (keeps words, more label:value lines) | [drop] | FUNSD links +1.7 but it flattens 21 business tables into lines (same_type -3.8) | dev cost.funsd.s_page +22.7; dev cost.business.s_page +21.7 |
| 14 | 2026-11-12 | ws1-layout | `ws1_c_forms_nt` | DOCFORGE_REGIONS=forms=notables: as forms, but never where the page read has a table in the region | [drop] | the guard removes every replacement: no change at all | dev cost.funsd.s_page +20.3; dev cost.business.s_page +17.4 |
| 15 | 2026-11-12 | ws1-layout | `ws1_e_complete` | DOCFORGE_REGIONS=complete: text regions with no matching output text are re-read from a crop and inserted | [drop] | 458 crop calls on dev for +0.08 FUNSD word recall; reading order on OmniDocBench worse (+0.018) | dev cost.tables.s_page +25.5; dev cost.funsd.s_page +22.6 |
| 16 | 2026-11-12 | ws1-layout | `ws1_tr_1` | DOCFORGE_REGIONS=types,allow=Text>List Item: only this type change from layout: Text>List Item | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.business.s_page -17.2 |
| 17 | 2026-11-12 | ws1-layout | `ws1_tr_2` | DOCFORGE_REGIONS=types,allow=Title>Section Header: only this type change from layout: Title>Section Header | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.business.s_page -17.2 |
| 18 | 2026-11-12 | ws1-layout | `ws1_tr_3` | DOCFORGE_REGIONS=types,allow=Key Value>Text: only this type change from layout: Key Value>Text | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.business.s_page -17.2 |
| 19 | 2026-11-12 | ws1-layout | `ws1_tr_4` | DOCFORGE_REGIONS=types,allow=Text>Section Header: only this type change from layout: Text>Section Header | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.business.s_page -17.2 |
| 20 | 2026-11-12 | ws1-layout | `ws1_tr_5` | DOCFORGE_REGIONS=types,allow=Section Header>Text: only this type change from layout: Section Header>Text | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.business.s_page -17.2 |
| 21 | 2026-11-12 | ws1-layout | `ws1_tr_6` | DOCFORGE_REGIONS=types,allow=List Item>Text: only this type change from layout: List Item>Text | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.business.s_page -17.2 |
| 22 | 2026-11-12 | ws1-layout | `ws1_tr_7` | DOCFORGE_REGIONS=types,allow=Header>Text: only this type change from layout: Header>Text | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.business.s_page -17.2 |
| 23 | 2026-11-12 | ws1-layout | `ws1_tr_8` | DOCFORGE_REGIONS=types,allow=Footer>Text: only this type change from layout: Footer>Text | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.business.s_page -17.2 |
| 24 | 2026-11-12 | ws1-layout | `ws1_tr_9` | DOCFORGE_REGIONS=types,allow=Text>Header: only this type change from layout: Text>Header | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.doclaynet.s_page +17.0 |
| 25 | 2026-11-12 | ws1-layout | `ws1_tr_10` | DOCFORGE_REGIONS=types,allow=Title>Header: only this type change from layout: Title>Header | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.doclaynet.s_page +17.0 |
| 26 | 2026-11-12 | ws1-layout | `ws1_tr_11` | DOCFORGE_REGIONS=types,allow=Title>Text: only this type change from layout: Title>Text | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.doclaynet.s_page +17.0 |
| 27 | 2026-11-12 | ws1-layout | `ws1_tr_12` | DOCFORGE_REGIONS=types,allow=Section Header>List Item: only this type change from layout: Section Header>List Item | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.doclaynet.s_page +17.0 |
| 28 | 2026-11-12 | ws1-layout | `ws1_tr_13` | DOCFORGE_REGIONS=types,allow=Text>Footer: only this type change from layout: Text>Footer | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.doclaynet.s_page +17.0 |
| 29 | 2026-11-12 | ws1-layout | `ws1_tr_14` | DOCFORGE_REGIONS=types,allow=Key Value>List Item: only this type change from layout: Key Value>List Item | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.doclaynet.s_page +17.0 |
| 30 | 2026-11-12 | ws1-layout | `ws1_tr_15` | DOCFORGE_REGIONS=types,allow=Key Value>Footer\|Key Value>Header\|Key Value>Section Header: only this type change from layout: Key Value>Footer / Key Value>Header / Key Value>Section Header | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.doclaynet.s_page +17.0 |
| 31 | 2026-11-12 | ws1-layout | `ws1_tr_16` | DOCFORGE_REGIONS=types,allow=Section Header>Header\|Section Header>Footer\|List Item>Footer\|List Item>Header: only this type change from layout: Section Header>Header / Section Header>Footer / List Item>Footer / List Item>Header | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.doclaynet.s_page +17.0 |
| 32 | 2026-11-12 | ws1-layout | `ws1_tr_17` | DOCFORGE_REGIONS=types,allow=Header>Section Header\|Header>Key Value\|Footer>Section Header\|Footer>List Item: only this type change from layout: Header>Section Header / Header>Key Value / Footer>Section Header / Footer>List Item | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.doclaynet.s_page +17.0 |
| 33 | 2026-11-12 | ws1-layout | `ws1_tr_18` | DOCFORGE_REGIONS=types,allow=Text>Key Value\|Section Header>Key Value: only this type change from layout: Text>Key Value / Section Header>Key Value | [see sweep] | single transition, dev only | dev cost.funsd.s_page +19.7; dev cost.doclaynet.s_page +17.0 |
| 34 | 2026-11-12 | ws2-forms | `checkbox_crops` | each checkbox group read twice from enlarged crops; a mark changes only when both crop reads disagree with the page read | [drop] | on 347 boxes with exact truth (flattened filled forms) the page read is 94.3/99.5% right; crops changed 2 of 262 paired marks, fixed 1 and broke 1-2, at $0.0025 per form page |  |
| 35 | 2026-11-12 | ws2-forms | `fillable (provisional)` | fillable PDFs: field values and checkbox/radio states read from the file; marks and values corrected in the read, <empty/> after empty fields, boxed tables with 3+ located fields and blocks holding a field become Key Value | [keep] | provisional: no measure lower on either split, same_type +0.4/+4.3; on 26 filled forms values 2-3% -> 100% and mark states 57-64% -> 100%; no model call | dev bus.tables -12.0; dev cost.business.s_page +4.1; held bus.tables -22.0; held cost.business.s_page +6.8 |
| 36 | 2026-11-12 | ws2-forms | `kv_rules (earlier rule, provisional)` | table that is a form grid -> one Key Value block, no model call | [needs-work] | provisional: bus.same_type -0.33 on dev although 37 tables became Key Value; current rule not yet run on business | dev bus.tables -30.0; dev cost.business.s_page +18.2 |
| 37 | 2026-11-12 | ws2-forms | `kv_lines (provisional)` | open label: takes the next short block as its value; CAPS LABEL value lines typed Key Value on form pages | [needs-work] | provisional: FUNSD links +1.55 and key precision +0.6 on dev, nothing lower; held and other components not run yet | dev cost.business.s_page +18.6; dev cost.funsd.s_page +8.9 |
| 38 | 2026-11-12 | ws2-forms | `region_reread (provisional)` | run of form-grid tables re-read from a crop with the form prompt, accepted only when the words agree | [needs-work] | provisional: FUNSD links +1.94 on dev but bus.same_type -0.86 and grid_ok -0.77 | dev bus.tables -16.0; dev cost.business.s_page +13.2 |
| 39 | 2026-11-12 | ws4-ocr-prep-speed | `zoom_always (vanilla + zoom vote on every scan)` | read option zoom_vote on vanilla-api: each scanned page is read again as 3 enlarged strips, and as 4 more when anything differs; digits change only on a 2-of-3 agreement | [option] | no measure below vanilla; +1 reconciled total on held (475 vs 474 all), 18 numbers changed on 12 pages; costs +0.0029 USD/page (+0.010 on scanned tables) and +8 s/page of model time | dev cost.business.s_page +57.7; dev bus.same_type +0.2; held cost.business.s_page +69.8; held bus.totals_ok +1.0 |
| 40 | 2026-11-12 | ws4-ocr-prep-speed | `zoom_gate=table_numbers` | strips only when the page read has a table with 4+ multi-digit numbers | [option] | same output as zoom_always on every measure and every changed number; saves 8% of its cost (0.0058 vs 0.0063 USD/page), all of it on scans without tables | dev cost.business.s_page +36.3; dev bus.same_type +0.2; held cost.business.s_page +37.7; held bus.totals_ok +1.0 |
| 41 | 2026-11-12 | ws4-ocr-prep-speed | `zoom_gate=ocr_disagree` | strips only when a number of the page read is not confirmed by a local OCR word | [drop] | Tesseract misses numbers on almost every scan, so the gate almost never closes: 0.0061 vs 0.0063 USD/page, same accuracy | dev cost.business.s_page +15.2; dev bus.same_type +0.2; held cost.business.s_page +16.7; held bus.totals_ok +1.0 |
| 42 | 2026-11-12 | ws4-ocr-prep-speed | `zoom_gate=table_and_disagree` | strips only when both: a numeric table and an unconfirmed number | [option] | same accuracy as zoom_always at the lowest cost of the four (0.0057 USD/page); only 0.0001 below table_numbers and depends on OCR quality | dev cost.business.s_page +13.7; dev bus.same_type +0.2; held cost.business.s_page +15.9; held bus.totals_ok +1.0 |
| 43 | 2026-11-12 | ws4-ocr-prep-speed | `zoom_prefetch=1 (first strip set starts with the page read)` | the 3 strips of a scan are started together with the page read instead of after it | [keep] | identical content to zoom_always; model time on scans 27.3 vs 39.2 s/page (mean), cost +0.00001 USD/page from strips that were not needed; only matters when the zoom vote is on | dev cost.business.s_page +14.2; dev bus.same_type +0.2; held cost.business.s_page +15.3; held bus.totals_ok +1.0 |
| 44 | 2026-11-12 | ws4-ocr-prep-speed | `reader effort medium instead of high (all pages)` | DOCFORGE_READER=anthropic_api:haiku#medium | [drop] | dev only: cost -35% and about half the time, olmOCR +1.8, but business reconciled totals 176 vs 235, grid_ok -8.2, answer-key recall -9.9, OmniDocBench table TEDS -8.9, RD-TableBench -0.4: tables get worse | dev bus.totals_found -85.0; dev bus.totals_ok -59.0 |
| 45 | 2026-11-12 | 3-verify | `grid_norm` | Verify check grid=norm: tables whose rows differ in width are normalised deterministically where only one reading exists (single-cell row widened, trailing empty cells removed); no model call | [keep] | Free and never worse on any measure, but repairs only 3 of 47 broken tables (business grid_ok +0.7 dev / +1.3 held); useful as the first step before the repair prompt. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 46 | 2026-11-12 | 3-verify | `grid_pad` | Verify check grid=pad: norm plus empty cells appended to short rows (the grid a browser draws); no model call | [option] | Business grid_ok +10.3 dev / +11.3 held at zero cost and no measure worse, but padding proves nothing about which column a value belongs to; keep as an opt-in for consumers that need rectangular tables. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 47 | 2026-11-12 | 3-verify | `grid_crop` | Verify check grid=crop: a broken table is re-read from a full-resolution crop with the strict table prompt; accepted only if the grid is consistent and numbers/words match the page read | [drop] | Only 15 of 47 broken tables repaired (grid_ok +3.4 dev / +4.7 held): a fresh crop read usually differs in some digit or word from the page read and is rejected; same cost as the repair prompt, which repairs far more. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 48 | 2026-11-12 | 3-verify | `grid_draft` | Verify check grid=draft (acceptance words>=0.9, the first setting): crop re-read with the broken draft in the prompt (repair prompt); accepted only if consistent and same numbers, >=90% same words | [needs-work] | Business grid_ok 87.6->99.3 dev / 84.7->96.7 held (r-1 95.8 / 94.2), RD-TableBench +0.0; but at words>=0.9 three of 21 repaired dev tables lost one or two words, so acceptance was tightened to exact (see grid_norm_draft_strict); same_type -0.1 dev is one repaired UB-04 header table that no longer fuzzy-matches r-1's flattened version. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 49 | 2026-11-12 | 3-verify | `grid_norm_draft` | grid=norm+draft at acceptance words>=0.9: deterministic normalisation first, repair prompt for the rest | [needs-work] | Business grid_ok 100.0 dev / 96.7 held; superseded by the exact-words acceptance rule (three dev repairs lost a word or two at 0.9). | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 50 | 2026-11-12 | 3-verify | `numbers` | Verify check numbers=1: printed column/row totals that miss by a digit-sized amount -> zoomed re-read of the rows involved; a cell changes only if the re-read differs and the change makes the total reconcile without breaking another; else flagged | [keep] | Never worse on any quality measure; 12 crop reads on the whole devset ($0.018): 0 cells changed on dev, 2 cells on held (1935 Fed bulletin p044: 5,390->5,399 and 396->399, both make a row total reconcile), business totals_ok 239->241 held (r-1 223). Small effect because vanilla has few digit-sized misses. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 51 | 2026-11-12 | ws4-ocr-prep-speed | `words=tesseract>rapidocr (PP-OCR fills what Tesseract cannot place)` | Ground uses Tesseract words first and PP-OCRv6 (RapidOCR, ONNX, Apache-2.0) words for the remaining blocks; second OCR only on pages without a trusted text layer | [keep] | measured boxes 76.4 -> 90.7% of blocks on hard scans, estimated 20.5 -> 8.2%, IoU>=0.5 with r-1 70.9 -> 78.0%, median IoU 0.839 -> 0.845; costs about 8x Tesseract CPU per scanned page |  |
| 52 | 2026-11-12 | ws4-ocr-prep-speed | `words=tesseract+rapidocr (PP-OCR first)` | Ground tries PP-OCR words before Tesseract | [drop] | more measured boxes (90.4%) but median IoU with r-1 falls 0.839 -> 0.696: PP-OCR word boxes are looser where Tesseract also works |  |
| 53 | 2026-11-12 | ws4-ocr-prep-speed | `words=rapidocr (PP-OCR replaces Tesseract)` | PP-OCR words for every stage | [drop] | same looser boxes (median IoU 0.696) and fewer measured boxes than the pair (89.0%) |  |
| 54 | 2026-11-12 | 3-verify | `complete_text` | Verify check complete=text: on born-digital PDFs, lines of the trusted text layer (triage text_layer_exact, not doubled, on the page, seen by local OCR at the same place) that are absent from the read and are plain words outside tables/figures are restored from the file text | [drop] | 9 lines restored on 4 of 389 PDF pages, no measure improved (header/footer text retained stays 95.2 / 90.9), and one restored bottom-margin legend line typed Footer lowers business key_precision dev 85.9 -> 85.3. The Haiku read almost never drops text that a trusted text layer has. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 55 | 2026-11-12 | 3-verify | `complete_crop` | Verify check complete=crop: same pointer as complete=text, but the place is re-read by Haiku from an enlarged crop and only the model's line is inserted | [drop] | 8 crop reads ($0.005), 3 lines restored on one page (labels of a diagram), no measure moved in either direction. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 56 | 2026-11-12 | 3-verify | `complete_scan` | Verify check complete=scan: on pages without a trusted text layer, Tesseract lines (confident words) with no match in the read point at a band; Haiku re-reads the band from an enlarged crop; a crop line is inserted only if the read lacks it and the OCR line agrees with it | [drop] | 40 crop reads ($0.015), 19 lines restored on 9 of 639 pages: 4 running headers (correct), 13 lines that are text inside screenshots/advertisements (not text by the answer keys: DocLayNet type_precision 70.6 -> 69.9 dev, 67.2 -> 66.8 held; 2 of 3 OmniDocBench inserts not in its key), header/footer text retained unchanged. Guarded margins-only variant measured separately. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 57 | 2026-11-12 | 3-verify | `flags` | Verify check flags=1 alone (detection only, no model call): blocks with an inconsistent table grid, a digit-sized totals miss, or (in this run) OCR/text-layer lines without a match get confidence low and are listed in extra.checks | [keep] | Changes no content and no measure; 109 flags on 80 of 639 pages. RD-TableBench tables with a grid flag score 79.8 (n=4) and with a totals flag 73.5 (n=1) against 89.2 unflagged (n=90): those flags mark worse tables. Completeness flags (52 of the 109) do not: flagged pages have fewer failed olmOCR tests (0.44 vs 0.90 per page) and lower OmniDocBench text edit distance, so they are now off by default (flags_completeness=true turns them on). | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 58 | 2026-11-12 | ws1-layout | `ws1_model_heron` | DOCFORGE_REGIONS=(model alone): Docling heron layout model alone on the DocLayNet pages, no reader (blocks = detections) | [keep (as detector)] | 94% type recall and IoU 0.91 on dev, far above r-1; chosen detector | dev dln.type_recall +21.5; dev dln.type_precision +17.9; held dln.type_recall +26.8; held dln.iou50 +21.1 |
| 59 | 2026-11-12 | ws1-layout | `ws1_model_ppdoclayout_plus` | DOCFORGE_REGIONS=(model alone): PP-DocLayout_plus-L alone on the same pages | [drop] | type recall 74% and IoU 0.75: its label set and box conventions do not map to DocLayNet/Reducto types as well as heron | dev dln.type_recall_lenient +7.2; dev dln.iou50 +4.4; held dln.type_recall_lenient +7.4; held dln.iou50 +4.4 |
| 60 | 2026-11-12 | ws1-layout | `ws1_a_off` | DOCFORGE_REGIONS=: in-process replay with no switch on all six components, both halves (must equal vanilla) | [check] | no measure differs from vanilla on dev or held: the replay tool is faithful | dev cost.doclaynet.s_page -0.4; dev tables.score +0.0; held cost.omni.s_page -1.9; held cost.doclaynet.s_page -1.1 |
| 61 | 2026-11-12 | ws1-layout | `ws1_http_default` | DOCFORGE_REGIONS=types=guarded,boxes,tables=broken,forms=notables: the recommended default through the real HTTP server on port 8831 (DocLayNet, FUNSD, business dev) | [check] | 151 of 151 results identical to the replay responses | dev dln.type_recall +14.5; dev dln.type_precision +13.1 |
| 62 | 2026-11-12 | ws1-layout | `ws1_a_types` | DOCFORGE_REGIONS=types: block types from the matched layout region, every type change allowed; reading untouched | [option] | DocLayNet type recall +17 dev / +15 held, but worse on the business answer key (-0.7/-1.2 precision), r-1 agreement on held (-1.4) and one FUNSD link per half | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held dln.type_precision +16.9 |
| 63 | 2026-11-12 | ws1-layout | `ws1_a_types_keepfurn` | DOCFORGE_REGIONS=types,deny=Header>*\|Footer>*: types from layout, but a block the reader typed Header/Footer keeps that type | [drop] | fixes olmOCR headers_footers (+11 / +5) but the business key and FUNSD losses stay; superseded by types=guarded | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held cost.olm.s_page +15.3 |
| 64 | 2026-11-12 | ws1-layout | `ws1_a_guarded` | DOCFORGE_REGIONS=types=guarded: types from layout only where the content agrees or the detection is confident (rules in structure.guarded, chosen on dev from the 18-transition sweep and three references) | [keep (core of the default)] | no measure worse on dev or held; DocLayNet type recall +14.5 / +12.8, type precision +13 / +14, olmOCR headers_footers +11 / +5, business key and r-1 agreement up | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held cost.olm.s_page +15.3 |
| 65 | 2026-11-12 | ws1-layout | `ws1_a_boxes` | DOCFORGE_REGIONS=boxes: block box from the one-to-one matched layout region, except page footers and captions (word box is tighter there); Figure blocks take the picture box; types untouched | [keep (with guarded)] | IoU +0.03 / +0.02; alone it loses one block on the lenient type measure on dev (-0.17), not together with guarded types | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held cost.olm.s_page +15.3 |
| 66 | 2026-11-12 | ws1-layout | `ws1_a_both` | DOCFORGE_REGIONS=types,boxes: all type changes + boxes | [option] | as types: the unguarded type changes lose on business and FUNSD | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held dln.type_precision +17.0 |
| 67 | 2026-11-12 | ws1-layout | `ws1_a_gboxes` | DOCFORGE_REGIONS=types=guarded,boxes: guarded types + layout boxes | [keep (core of the default)] | no measure worse on dev or held; guarded gains plus IoU +0.03 / +0.02; no model call, only the layout model | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held cost.olm.s_page +15.3 |
| 68 | 2026-11-12 | ws1-layout | `ws1_a_split` | DOCFORGE_REGIONS=types,boxes,split: all type changes + boxes + text blocks cut at line breaks between layout regions (first version of split, before the form guard) | [drop] | FUNSD links -2.7 / -1.0, business key precision -7.6 / -1.4: it cut form fields apart | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +19.8; held dln.type_recall +19.4; held cost.omni.s_page +18.2 |
| 69 | 2026-11-12 | ws1-layout | `ws1_a_gsplit` | DOCFORGE_REGIONS=types=guarded,boxes,split: guarded types + boxes + first version of split | [drop] | FUNSD links -1.9 / -0.7 (fields cut apart) | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +19.8; held cost.omni.s_page +18.1; held dln.type_recall +16.0 |
| 70 | 2026-11-12 | ws1-layout | `ws1_a_gsplit2` | DOCFORGE_REGIONS=types=guarded,boxes,split: split with the form guard (Key Value blocks and boxes inside a form are never cut; pipe tables untouched) | [superseded] | no loss on dev; on held one OmniDocBench page got a paragraph cut before its last line (text edit +0.001) | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held dln.type_recall +15.6 |
| 71 | 2026-11-12 | ws1-layout | `ws1_a_gsplit3` | DOCFORGE_REGIONS=types=guarded,boxes,split: split, and two boxes of one type cut a block only when both score >= 0.8 | [superseded] | no loss, but drops most of the held gain of split (IoU +0.028 instead of +0.044) | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held cost.olm.s_page +15.4 |
| 72 | 2026-11-12 | ws1-layout | `ws1_a_gsplit4` | DOCFORGE_REGIONS=types=guarded,boxes,split: split as shipped: form guard; an unsure same-type box cuts only where the text ends a sentence or a list item starts | [option (held not clean)] | no measure worse on dev or held and the best layout numbers (held: type recall +15.4, IoU +0.043, IoU>=0.5 +4.3), but its last rule was written after seeing one held page, so the held figures are not an untouched test | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held dln.type_recall +15.4 |
| 73 | 2026-11-12 | ws1-layout | `ws1_b_grid` | DOCFORGE_REGIONS=tables=grid: every detected table re-read from its crop; replaces the page read when the crop grid is consistent | [option] | mixed: grid_ok +1.4 / +2.7 and small table gains, but totals_ok -5 on dev, olmOCR table_tests -1.7 and multi_column -1.3 on held: a second read of a table swaps errors | dev cost.tables.s_page +32.7; dev cost.business.s_page +24.7; held cost.tables.s_page +29.1; held cost.omni.s_page +23.4 |
| 74 | 2026-11-12 | ws1-layout | `ws1_b_numbers` | DOCFORGE_REGIONS=tables=numbers: as grid, and the crop numbers must agree better with the independent words (PDF text / OCR) | [drop] | RD-TableBench -0.4 on dev; almost nothing replaced elsewhere | dev cost.tables.s_page +32.8; dev cost.business.s_page +24.5; held cost.tables.s_page +29.1; held cost.omni.s_page +23.4 |
| 75 | 2026-11-12 | ws1-layout | `ws1_b_broken` | DOCFORGE_REGIONS=tables=broken: crop re-read only where the page read has an inconsistent grid; accepted when the crop grid is consistent | [keep (part of the default)] | never worse; business grid_ok +1.4 / +2.7; 15 crops on 639 pages, 8 repairs accepted | dev cost.tables.s_page +25.7; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held cost.olm.s_page +15.3 |
| 76 | 2026-11-12 | ws1-layout | `ws1_b_broken_numbers` | DOCFORGE_REGIONS=tables=broken_numbers: broken, and the crop numbers must agree with the word evidence at least as well | [option] | never worse, but accepts 6 of the 8 repairs that broken accepts (grid_ok +1.4 / +2.0 against +1.4 / +2.7): the number check only removes good repairs here | dev cost.tables.s_page +25.6; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held cost.olm.s_page +15.3 |
| 77 | 2026-11-12 | ws1-layout | `ws1_c_forms` | DOCFORGE_REGIONS=forms: form-like regions re-read from a crop with a form prompt, guarded (keeps words, more label:value lines) | [drop] | FUNSD links +1.7 / +0.3 but it turns business tables into label lines: 21 / 10 tables lost, r-1 agreement -3.8 / -1.9, key precision -6.0 on held | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +22.6; held cost.omni.s_page +18.7; held cost.olm.s_page +15.6 |
| 78 | 2026-11-12 | ws1-layout | `ws1_c_forms_nt` | DOCFORGE_REGIONS=forms=notables: as forms, but never where the page read has a table in the region | [keep (small; part of the default)] | never worse; no change on dev, held FUNSD links +0.7 and business key +0.4; 49 crops on 639 pages, 6 accepted | dev cost.tables.s_page +25.3; dev cost.funsd.s_page +20.2; held cost.omni.s_page +18.8; held cost.olm.s_page +15.5 |
| 79 | 2026-11-12 | ws1-layout | `ws1_e_complete` | DOCFORGE_REGIONS=complete: text regions with no matching output text are re-read from a crop and inserted | [drop] | 935 crops on 639 pages, 841 found the text already present; 76 insertions make OmniDocBench reading order worse (+0.018 dev), DocLayNet precision -0.2, business key precision -0.3 held | dev cost.tables.s_page +25.5; dev cost.funsd.s_page +22.6; held cost.omni.s_page +30.4; held cost.olm.s_page +23.4 |
| 80 | 2026-11-12 | ws1-layout | `ws1_d_assemble` | DOCFORGE_REGIONS=assemble: page built only from parallel region reads (the page read is not used); olmOCR and DocLayNet only | [option] | best layout numbers of all (DocLayNet type recall 92.5, IoU 0.88 on dev: above r-1) and olmOCR tables +12.7, but long_tiny_text -11.8 and old_scans -2.9 on dev; 3 to 4 times the cost of the page read | dev cost.olm.s_page +29.4; dev cost.doclaynet.s_page +23.6; held cost.olm.s_page +64.3; held cost.doclaynet.s_page +23.7 |
| 81 | 2026-11-12 | ws1-layout | `ws1_x_gb_broken` | DOCFORGE_REGIONS=types=guarded,boxes,tables=broken: combination: guarded types + boxes + broken-grid table repair | [keep] | sum of the parts, no measure worse on dev or held | dev cost.tables.s_page +25.7; dev cost.funsd.s_page +19.7; held cost.omni.s_page +18.1; held cost.olm.s_page +15.3 |
| 82 | 2026-11-12 | ws1-layout | `ws1_x_gb_broken_forms` | DOCFORGE_REGIONS=types=guarded,boxes,tables=broken,forms=notables: combination: guarded types + boxes + broken-grid table repair + guarded form crops | [keep: recommended default] | no measure worse on dev or held; every gain of the single switches adds up; verified through the real HTTP server (151 pages identical to the replay) | dev cost.tables.s_page +25.6; dev cost.funsd.s_page +20.2; held cost.omni.s_page +18.8; held cost.olm.s_page +15.5 |
| 83 | 2026-11-12 | ws1-layout | `ws1_x_all` | DOCFORGE_REGIONS=types=guarded,boxes,split,tables=broken,forms=notables: recommended default + split | [option (held not clean)] | no measure worse and the best numbers without extra reads, but split had one rule written after a held page was seen | dev cost.tables.s_page +25.6; dev cost.funsd.s_page +20.2; held cost.omni.s_page +18.8; held cost.olm.s_page +15.6 |
| 84 | 2026-11-12 | 3-verify | `grid_norm_draft_strict` | Verify check grid=norm+draft with exact acceptance (the default now): deterministic normalisation, then a crop re-read with the broken draft in the prompt; a repair is accepted only if every row has the same width and the number multiset, word multiset and checkbox marks equal the page read's | [keep] | 35 of 47 broken tables repaired with no cell text changed; business grid_ok 87.6 -> 97.2 dev, 84.7 -> 95.3 held (r-1 95.8 / 94.2), RD-TableBench +0.0 / +0.0, nothing else moves except same_type dev 73.4 -> 73.3, which is one repaired two-level header table that no longer fuzzy-matches r-1's flattened table (not a type error). Cost +$0.0007 dev / +$0.0012 held per business page (44 crop reads on 639 pages). | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 85 | 2026-11-12 | 3-verify | `grid_norm_draft_crop_strict` | grid=norm+draft+crop with exact acceptance: plain crop re-read as a third step for tables the repair prompt did not fix | [drop] | The third step repairs no further table (still 35 of 47) and adds 10 calls. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 86 | 2026-11-12 | 3-verify | `grid_norm_draft_strict` | CORRECTION of the previous grid_norm_draft_strict line (its text said grid_ok 97.2 dev; the measured value is 98.6): grid=norm+draft with exact acceptance (same number multiset, word multiset and checkbox marks as the page read) | [keep] | 35 of 47 broken tables repaired with no cell text changed; business grid_ok 87.6 -> 98.6 dev, 84.7 -> 95.3 held (r-1 95.8 / 94.2); RD-TableBench +0.0 / +0.0; same_type dev 73.4 -> 73.3 is one repaired two-level header table that no longer fuzzy-matches r-1's flattened table; +$0.0007 dev / +$0.0012 held per business page. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 87 | 2026-11-12 | 3-verify | `numbers_all` | numbers=1;numbers_only_near=false: every printed total that does not reconcile (not only digit-sized misses) gets the zoomed re-read; same acceptance rule | [drop] | 61 crop reads ($0.115) instead of 12 ($0.018) for exactly the same two accepted cell changes (business totals_ok 239 -> 241 held, dev unchanged): totals that miss by more than a digit are totals the rule does not understand, not misreads. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 88 | 2026-11-12 | ws4-ocr-prep-speed | `zoom with gates: pre-read OCR numbers >= 20, prefetch 1, post-read table_legible` | read options zoom_vote + zoom_pre_gate=20 (strips only when local OCR sees 20+ numbers) + zoom_prefetch=1 (first 3 strips start with the page read) + zoom_gate=table_legible (second strip set and vote only when the read has a numeric table and no [illegible] mark) | [keep] | no measure below vanilla on 5 components, both halves; keeps all 9 hand/OCR-verified digit corrections of the always-on vote on printed scans and none of its 9 unverifiable changes on handwriting; OmniDocBench held text edit 0.136 -> 0.116 and table TEDS 41.5 -> 43.7; business 0.0055 USD/page vs 0.0063 always-on and 0.0034 vanilla; model time 23.6 s/page vs 29.9 always-on and 21.8 vanilla | dev cost.business.s_page +12.2; dev cost.olm.s_page +7.9; held cost.business.s_page +13.1; held cost.olm.s_page +8.7 |
| 89 | 2026-11-12 | 3-verify | `complete_scan_margins` | complete=scan;complete_margins_only=true: the scan completeness check restricted to the top and bottom 7.5% of the page (guard against text inside pictures) | [option] | The guard removes the DocLayNet loss: no quality measure is worse. But only 4 running headers are restored on 639 pages (0 on dev; 3 olmOCR and 1 OmniDocBench page on held, all confirmed by r-1 or the key) for 26 crop reads ($0.009), and no measure improves (header/footer text retained stays 95.2 / 90.9). Not worth a default. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 90 | 2026-11-12 | 3-verify | `combo_grid_numbers_flags` | RECOMMENDED: Verify checks grid=norm+draft;numbers=1;flags=1 on top of vanilla (table grid repair with exact acceptance, printed-totals check with zoomed re-read, confidence flags for what stays unresolved); extract cases included | [keep] | Business grid_ok 87.6 -> 98.6 dev, 84.7 -> 95.3 held (r-1 95.8 / 94.2); totals_ok 235 / 239 -> 235 / 241 (r-1 230 / 223); RD-TableBench +0.0; extract 100 / 100 unchanged; every other quality measure identical to vanilla except same_type dev 73.4 -> 73.3 (one correctly repaired two-level header table no longer fuzzy-matches r-1's flattened one). 56 crop reads on 639 pages: +$0.0008 dev / +$0.0014 held per business page, under +$0.0003 elsewhere. 15 pages keep a low-confidence flag. | dev bus.grid_ok +11.0; dev cost.extract.s_page +7.2; held bus.grid_ok +10.7; held cost.extract.s_page +5.8 |
| 91 | 2026-11-12 | ws4-ocr-prep-speed | `FINAL: reader effort medium (the API default) instead of high, all pages` | DOCFORGE_READER=anthropic_api:haiku#medium; supersedes the provisional dev-only line | [drop] | 35% cheaper, but business tables get worse far beyond read-to-read noise (dev: consistent grid -8.2 vs noise +2.5, answer-key recall -9.9 vs -2.2, totals 176 vs 235; held: grid -6.0, tables found -14, RD-TableBench -1.2); text pages equal or better (olmOCR +1.8 / +0.1) | dev bus.totals_found -85.0; dev bus.totals_ok -59.0; held cost.business.s_page +55.3; held cost.omni.s_page -17.9 |
| 92 | 2026-11-12 | ws4-ocr-prep-speed | `NOISE FLOOR: a second independent high-effort read of the dev half (same engine as vanilla)` | reader sample dev2 instead of dev1; nothing else changes | [option] | not a variant: shows how far two identical runs differ. Differences of this size between a variant and vanilla are not evidence: grid +2.5, key recall -2.2, key precision -1.2, olm multi_column -3.6, old_scans +2.9, OmniDocBench table TEDS -8.1, formula edit -0.106; RD-TableBench +0.1, totals 0 | dev cost.omni.s_page -15.8; dev omni.table_teds_page -10.4 |
| 93 | 2026-11-12 | ws4-ocr-prep-speed | `effort_route=medium for easy pages (fewer than 15 numbers and number share < 0.06 in the pre-read words)` | read option effort_route: pages whose text layer / OCR words hold few numbers are read at medium effort, the rest at high (composite of saved medium and high reads; 31% of pages routed) | [option] | every difference from vanilla is inside read-to-read noise on both halves (held: grid -2.4, RD-TableBench -1.1, key recall -0.8; dev: grid +1.9, key recall +2.3), but it saves only 4 to 9% of the cost: not worth a default | dev omni.table_teds_page -9.1; dev omni.table_teds -5.5; held omni.table_teds_page +15.5; held cost.business.s_page +11.6 |
| 94 | 2026-11-12 | ws4-ocr-prep-speed | `effort_route=medium for easy pages (fewer than 40 numbers and share < 0.12)` | same switch, wider rule (56% of pages routed) | [drop] | saves 13 to 20% but business held falls beyond noise: consistent grid -4.8, tables found -16, answer-key recall -4.7, precision -2.6 | dev omni.table_teds_page -9.4; dev cost.business.s_page -7.4; held cost.business.s_page +31.4; held bus.tables -16.0 |
| 95 | 2026-11-12 | ws2-forms | `fillable` | fillable PDFs: field values and checkbox/radio states read from the file as exact evidence; marks and values corrected in the read, <empty/> after empty fields, boxed tables with 3+ located fields and blocks holding a field become Key Value | [keep] | final: no measure lower on any component or split (same_type +0.38/+4.34, key precision 0/+1.22, grid_ok +1.89/+0.49); on 26 filled forms typed values 2-3% -> 100% and checkbox states 57-64% -> 100%; no model call | dev bus.tables -12.0; dev cost.business.s_page +4.1; held bus.tables -22.0; held cost.business.s_page +6.8 |
| 96 | 2026-11-12 | ws2-forms | `kv_rules` | flat/scanned forms: a table whose cells are self-contained fields (checkbox, blank, box number, label line over value; no header row, no numeric column) becomes one Key Value block; no model call | [option] | final: bus.same_type -0.32/-0.35 because r-1 itself keeps 24 of the 55 form grids as Table (31 Key Value); right on all 4 answer-key grids it converts, converts no table on RD-TableBench/OmniDocBench/olmOCR/DocLayNet; fails the never-worse bar on agreement with r-1 | dev bus.tables -30.0; dev cost.business.s_page +3.6; held bus.tables -29.0; held cost.business.s_page +3.0 |
| 97 | 2026-11-12 | ws2-forms | `kv_rules:rows` | as kv_rules but one Key Value block per row of the converted grid (r-1's block size) | [option] | held key recall +3.9 and key precision +1.2 with r-1's Key Value block count, but bus.same_type -0.92 on held | dev bus.tables -30.0; dev bus.matched -7.5; held bus.tables -29.0; held bus.matched -7.1 |
| 98 | 2026-11-12 | ws2-forms | `kv_lines` | open label: takes the next short block as its value; CAPS LABEL value lines typed Key Value on form pages | [drop] | final: FUNSD links +1.55/+0.68 but on held business key precision -1.22, key recall -1.06, same_type -0.33 (the caps half) | dev cost.doclaynet.s_page +10.3; dev cost.business.s_page +10.0; held cost.business.s_page +10.6; held cost.omni.s_page +8.4 |
| 99 | 2026-11-12 | ws2-forms | `kv_lines:caps` | only: CAPS LABEL value lines typed Key Value on form pages | [drop] | FUNSD links +0.78/+0.68 but held business key precision -1.22, key recall -1.06, same_type -0.29 | dev cost.business.s_page +10.5; dev cost.funsd.s_page +3.5; held cost.business.s_page +10.4; held cost.funsd.s_page +3.5 |
| 100 | 2026-11-12 | ws2-forms | `kv_lines:join` | only: a Key Value block ending in an open label: takes the next short text block as its value | [option] | FUNSD links +0.78 and key precision +0.61 on dev, nothing on held except one block of r-1 agreement lost (same_type -0.04); free | dev cost.omni.s_page +12.6; dev cost.business.s_page +6.8; held cost.omni.s_page +12.3; held cost.business.s_page +6.7 |
| 101 | 2026-11-12 | ws2-forms | `region_reread` | run of form-grid tables re-read from a crop with the form prompt, merged only when the words agree (recall>=0.92, new<=0.15) | [drop] | final: FUNSD links +1.94 dev / 0 held; business held key precision -2.07, matched -1.03, same_type -0.85/-0.37; 28 of 53 crop reads rejected; $0.053 for 53 crops | dev bus.tables -16.0; dev cost.business.s_page +4.9; held bus.tables -22.0; held cost.business.s_page +10.0 |
| 102 | 2026-11-12 | ws2-forms | `fillable + kv_lines:join` | both switches | [option] | passes the bar: no measure lower than vanilla on any component or split (same_type +0.38/+4.27, FUNSD dev +0.78, key precision +0.61/+1.22); join adds only dev gains over fillable alone, so default stays fillable | dev bus.tables -12.0; dev cost.business.s_page +11.3; held bus.tables -22.0; held cost.business.s_page +10.5 |
| 103 | 2026-11-12 | ws2-forms | `fillable + kv_rules` | both switches | [option] | held same_type +4.95, grid_ok +2.3/+1.9, answer-key Key Value hits 14/17 and 30/47, but dev same_type -0.32 (kv_rules against r-1's Tables) | dev bus.tables -36.0; dev cost.business.s_page +5.7; held bus.tables -38.0; held cost.business.s_page +5.6 |
| 104 | 2026-11-12 | ws2-forms | `fillable + kv_rules + kv_lines:join` | three switches | [option] | sum of the parts: FUNSD dev +0.78, held same_type +4.88, but dev same_type -0.32 | dev bus.tables -36.0; dev cost.business.s_page +10.0; held bus.tables -38.0; held cost.business.s_page +10.1 |
| 105 | 2026-11-12 | 3-verify | `grid_crop` | CORRECTION of the text of the earlier grid_crop line (same measures): plain crop re-read of a broken table with the strict table prompt | [drop] | 15 of 47 broken tables repaired (grid_ok +3.4 dev / +4.7 held). Of the 32 rejected re-reads 14 came back with an inconsistent grid again, 10 with digits that differ from the page read, 5 tables could not be located, 3 other; the repair prompt with the draft repairs far more at the same cost. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 106 | 2026-11-12 | 3-verify | `flags` | CORRECTION of the text of the earlier flags line (same measures): flags=1 alone, detection only | [keep] | 108 flags (not 109) on 80 of 639 pages, 52 of them completeness flags; RD-TableBench with a grid flag 79.8 (n=4), totals flag 73.5 (n=1), unflagged 89.2 (n=90); completeness flags do not mark worse pages and are off by default now. | dev extract.leaf_accuracy -100.0; dev extract.row_recall -100.0; held extract.leaf_accuracy -100.0; held extract.row_recall -100.0 |
| 107 | 2026-11-12 | ws4-ocr-prep-speed | `COMBINED ws4 profile vanilla-api-ws4: words tesseract>rapidocr + prepare rotate + gated zoom with prefetch + 16 page workers` | profile vanilla-api-ws4 (all kept ws4 switches together) on the 147 business pages | [keep] | no measure below vanilla; 9 verified digit corrections; the two sideways pages are read upright (held answer-key precision +1.6, recall +0.7; same type as r-1 +0.7 / +0.9); blocks with a measured box 88.3 -> 93.5%, estimated 9.7 -> 5.7%; 0.0055 USD/page (vanilla 0.0034, current engine 0.0094, r-1 0.0100) | dev cost.business.s_page +2.5; dev bus.same_type +0.7; held cost.business.s_page +9.7; held bus.key_precision +1.6 |
| 108 | 2026-11-12 | ws4-ocr-prep-speed | `triage.prepare=rotate (turn pages upright before reading)` | 90/180/270 turn from the PDF text layer, EXIF, or PP-OCR + Tesseract orientation on pixels; PDF pages get /Rotate | [keep] | 0 wrong turns on 128 test images and 197 devset pages; 91 of 96 turned images put upright (5 left as is); the two sideways business pages are fixed (words 0.971 -> 0.998, measured boxes 3/21 -> 17/17); upright pages keep their bytes and reads |  |
| 109 | 2026-11-12 | ws4-ocr-prep-speed | `triage.prepare=deskew (straighten skewed scans)` | skew angle from a projection profile; a skewed scan is replaced by its straightened raster | [option] | angle estimate is right (116/128 within 0.5 degrees, the rest are natively skewed forms) but reading does not improve beyond read-to-read noise: recall 0.985 -> 0.989, order 0.924 -> 0.962 (noise 0.998 / 0.987), measured boxes 84% -> 83% |  |
| 110 | 2026-11-12 | ws4-ocr-prep-speed | `read.page_workers=16 (DOCFORGE_PAGE_WORKERS) with shared rate-limit backoff` | pages of one document read 16 at a time instead of 6; on 429/529 all threads wait for retry-after | [keep] | no rate limit seen with about 60 calls in flight; Fed H.4.1 43.0 -> 23.8 s, USPS 10-K unchanged (31 s, bound by its slowest page); scheduled from measured call seconds: 23.5 -> 17.9 s and 13.3 -> 13.3 s |  |
| 111 | 2026-11-12 | ws4-ocr-prep-speed | `read.zoom_prefetch=2 (both strip sets start with the page read)` | all 7 strips of a scan start with the page read | [drop] | the second set is needed on few pages: cost 0.0035 -> 0.0060 USD/page on Brown v. Board and no time gain over prefetch 1 (91 s -> 133 s on the loaded machine; 10.5 s for both when scheduled from call seconds) |  |
| 112 | 2026-11-12 | ws4-ocr-prep-speed | `DOCFORGE_BATCH_ASYNC=1 (Message Batches API for async jobs)` | async non-priority parse jobs send their reads as one batch per round through POST /v1/messages/batches | [option] | real batch of 10 page reads: 10 succeeded, cost exactly half (0.00667 vs 0.01334 USD), but 2,576 s from submit to results against 31 s through the normal API |  |
| 113 | 2026-11-12 | ws4-ocr-prep-speed | `FINAL COMBINED ws4 profile vanilla-api-ws4 (supersedes the earlier combined line: boxes of turned pages are now reported on the original page)` | profile vanilla-api-ws4: words tesseract>rapidocr + triage.prepare=rotate + zoom_pre_gate=20 + zoom_prefetch=1 + zoom_gate=table_legible + 16 page workers; 147 business pages | [keep] | no measure below vanilla; 9 verified digit corrections; the two sideways pages read upright and their boxes match r-1 again (IoU 0.5 to 0.9 instead of about 0); measured boxes 88.3 -> 93.5%, estimated 9.7 -> 5.7%, IoU>=0.5 with r-1 73.2 -> 75.9%; 0.0055 USD/page (vanilla 0.0034, current engine 0.0094, r-1 0.0100) | dev cost.business.s_page +3.5; dev bus.same_type +0.7; held cost.business.s_page +3.6; held bus.key_precision +1.6 |
| 114 | 2026-11-12 | combine | `noise: second independent vanilla read (sample dev2), whole devset` | vanilla-api profile, reader sample dev2 instead of dev1: an identical run on independent page reads; 237 dev reads were WS4's, 395 new reads for held and for DocLayNet/FUNSD (/bin/zsh.77) | [check] | the spread between cb0_off (dev1) and cb_read2 (dev2) is the noise floor of every measure: see field noise; e.g. business grid 2.5/4.7, key recall 2.2/6.0, OmniDocBench table TEDS 8.1/12.8, DocLayNet type recall 4.4/2.2, FUNSD links 2.1/1.4; RD-TableBench 0.0/0.2 and totals 0/1 are stable | dev bus.key70_precision +13.8; dev omni.table_teds_page -10.4; held cost.omni.s_page -16.1; held omni.table_teds +15.0 |
| 115 | 2026-11-12 | combine | `step 0: engine-final with every piece switched off` | profile engine-final, DOCFORGE_WORDS=tesseract and stage options that switch off rotate, zoom, handwriting route, forms, checks, regions; DOCFORGE_NO_LIVE=1 | [check] | 637 of 639 responses identical to the saved vanilla responses; the other 2 (omni_jiaocaineedrop_chap02.pdf_16, dln_laws_and_regulations_dbf12799fa21) had a failed page read in the baseline run and a complete cached read now, so the baseline vanilla is understated on those; cb0_off is the same-reads base of every later step | dev cost.business.s_page +2.7; dev dln.iou50 +2.0; held omni.table_teds_page +6.7; held cost.business.s_page +2.7 |
| 116 | 2026-11-13 | combine | `step 1 as recommended by WS4: words tesseract>rapidocr + prepare=rotate (method auto) + gated zoom` | profile vanilla-api-ws4's switches on the same reads as cb0_off | [needs-work] | rotation method auto turned 5 UPRIGHT pages by 180 or 270 degrees (olmOCR old_scans 84, 85, 35, 91: handwritten letters, Tesseract orientation detection wrong with confidence 2.4 to 10.3; one OmniDocBench page of vertical Chinese handwriting) and their new reads are mostly [illegible]: olmOCR old_scans held -2.8 (noise 1.4), OmniDocBench held text edit +0.011 (noise 0.002). Everything else of the set is a gain: OmniDocBench layout IoU 0.30 -> 0.61 dev, 0.27 -> 0.46 held from the PP-OCR fill. Guard: prepare_method=safe (next line) | dev omnilay.iou50 +36.5; dev omnilay.type_recall_lenient +20.5; held omnilay.iou50 +23.5; held cost.omni.s_page +21.0 |
| 117 | 2026-11-13 | combine | `step 1: + WS4 set (words tesseract>rapidocr, prepare=rotate with prepare_method=safe, gated and prefetched zoom, 16 page workers)` | on cb0_off's reads: PP-OCR fill for word positions, guarded rotation, zoom vote with pre-gate 20 / prefetch 1 / gate table_legible | [keep] | nothing lower beyond noise. OmniDocBench layout (new measure): box IoU 0.30 -> 0.61 dev and 0.27 -> 0.46 held, type precision +14 / +19, type recall +20 / +6: Tesseract cannot place Chinese text, PP-OCR can. Business key70 +0.8/+2.7 precision, totals +0/+1, olmOCR old_scans +1.4/+1.4 (zoom corrections). Guarded rotation turns exactly the 3 sideways pages of 639 (auto turned 5 upright ones as well); on 96 turned copies safe = auto (90 right, 4 left, 0 wrong). Only minus: DocLayNet held IoU -0.005 and IoU>=0.5 -0.7 (noise 0.032 / 4.8): on pages with an untrusted text layer the fill sometimes gives a block a larger box (7 pages up, 6 down) | dev omnilay.iou50 +36.5; dev omnilay.type_recall_lenient +20.5; held omnilay.iou50 +23.4; held omnilay.type_precision +19.8 |
| 118 | 2026-11-13 | combine | `step 2: + WS3 checks (grid=norm+draft;numbers=1;flags=1)` | step 1 plus Verify checks: grid repair with the draft in the prompt and exact acceptance, zoomed re-read of digit-sized total misses, confidence flags | [keep] | business consistent grids 87.6 -> 98.6 dev and 84.7 -> 95.3 held (noise 2.5 / 4.7; r-1 95.8 / 94.2); all components 292 -> 309 of 315 and 262 -> 279 of 287; totals +0 / +1; no measure lower; 19 new crop reads (/bin/zsh.075) because the zoom vote had changed 9 drafts | dev omnilay.iou50 +36.5; dev omnilay.type_recall_lenient +20.5; held omnilay.iou50 +23.4; held omnilay.type_precision +19.8 |
| 119 | 2026-11-13 | combine | `table repair alternative: step 1 + WS1 tables=broken instead of WS3 checks` | step 1 plus ground.regions=tables=broken (crop re-read of a table with a broken grid, accepted when the crop grid is consistent), no Verify checks | [drop] | same reads as cb2_ws3: business consistent grids 89.0 / 87.3 against 98.6 / 95.3 with WS3's repair (all components 295 and 267 against 309 and 279), and its acceptance does not require the same cell texts. WS3's repair is the one in engine-final; tables=broken stays a switch | dev omnilay.iou50 +36.5; dev omnilay.type_recall_lenient +20.5; held omnilay.iou50 +23.4; held omnilay.type_precision +19.8 |
| 120 | 2026-11-13 | combine | `step 3: + WS1 layout (types=guarded,boxes,forms=notables; heron on CPU), without its table repair` | step 2 plus the Docling heron layout model: guarded block types, layout boxes, guarded form crops | [keep] | no measure lower. DocLayNet type recall +14.7 / +12.6 (noise 4.4 / 2.2), type precision +13.1 / +14.1, IoU +0.031 / +0.025; OmniDocBench layout (not seen by the layout model's training the way DocLayNet was) type recall +3.7 / +1.2, precision +3.5 / +3.0, IoU +0.027 / +0.022; olmOCR headers_footers +13.2 / +5.3; business type agreement with r-1 +3.9 / +1.6, key precision +2.1 / +1.2, second key +1.6 / +0.4. Interaction with the OCR fill: on vanilla word boxes the same layout switches lowered OmniDocBench layout type recall on held by 5.6 (58.4 -> 52.8, inside noise 6.3); with the fill they raise it | dev omnilay.iou50 +37.4; dev omnilay.type_recall_lenient +24.8; held omnilay.iou50 +24.2; held omnilay.type_precision +22.8 |
| 121 | 2026-11-13 | combine | `interaction: both table repairs (WS3 grid repair, then WS1 tables=broken on what is left)` | step 3 with ground.regions=types=guarded,boxes,tables=broken,forms=notables | [drop] | no devset measure differs from step 3: after WS3's repair the WS1 repair has nothing left that it can fix; it only adds crop reads. Left out | dev omnilay.iou50 +37.4; dev omnilay.type_recall_lenient +24.8; held omnilay.iou50 +24.2; held omnilay.type_precision +22.8 |
| 122 | 2026-11-13 | combine | `step 4a: + WS2 fillable` | step 3 plus read.forms=fillable (values and checkbox states from the PDF's form fields) | [keep] | both answer keys together (100 pages): precision +2.7 / +3.0, recall +1.8 / +2.5; second key precision +3.7 / +6.8; agreement with r-1 -0.4 dev (noise 0.5), +3.7 held. Interaction with WS1 forms=notables: once fillable has turned a form's tables into Key Value blocks, the layout form-crop re-read is no longer held back by its no-tables guard and re-reads the region (insurance_dol_ca1_p002: 4 blocks -> 35; first key held precision -4.5 on that one page, recall +6.4). A crop of the render cannot show values typed into the fields, so regions with filled fields are now never re-read (regions/forms.py guard, unit-tested) | dev omnilay.iou50 +37.4; dev omnilay.type_recall_lenient +24.8; held omnilay.iou50 +24.2; held omnilay.type_precision +22.8 |
| 123 | 2026-11-13 | combine | `step 4: + kv_lines:join` | step 4a plus read.forms=fillable,kv_lines:join | [keep] | FUNSD links +0.8 / 0.0, first key precision +0.6 / 0.0, nothing lower beyond 0.3 (second key recall held -0.3: one block); costs nothing | dev omnilay.iou50 +37.4; dev omnilay.type_recall_lenient +24.8; held omnilay.iou50 +24.2; held omnilay.type_precision +22.8 |
| 124 | 2026-11-13 | combine | `step 5: + handwriting route to Sonnet 5.5 (unguarded, as in engine-api)` | step 4 plus read.handwriting_route | [needs-work] | 66 of 639 pages go to Sonnet. Gains: OmniDocBench held text edit 0.116 -> 0.098 (noise 0.002), olmOCR old_scans +1.4 dev. Losses beyond noise: FUNSD links -1.0 / -3.4 (noise 2.1 / 1.4; 13 printed FUNSD forms with one or two [illegible] marks are re-read by Sonnet, which pairs fewer labels with values), business agreement with r-1 -1.0 dev (noise 0.5). Guard in the next line | dev omnilay.iou50 +37.2; dev omnilay.type_recall_lenient +25.5; held omnilay.iou50 +24.0; held omnilay.type_precision +23.3 |
| 125 | 2026-11-13 | combine | `step 6: + kv_rules:rows (on top of step 5)` | step 5 plus read.forms=fillable,kv_lines:join,kv_rules:rows (form grids -> one Key Value block per row) | [keep] | decided on the answer keys: both keys together strict precision +3.7 / +1.3 and strict recall +1.4 / +1.8, lenient +0.5 / +0.1 and +0.6 / +1.0; the second key (written without looking at any output) strict +5.7 / +0.7 and +2.3 / +2.2; no data table converted (RD-TableBench, olmOCR tables, OmniDocBench tables, totals unchanged); agreement with r-1 -0.3 dev (noise 0.5), +0.6 held; consistent grids +0.8 / +0.4 | dev bus.tables -38.0; dev omnilay.iou50 +37.2; held bus.tables -38.0; held omnilay.iou50 +24.0 |
| 126 | 2026-11-13 | combine | `step 7: + layout split (on top of step 5)` | step 5 plus ground.regions=types=guarded,boxes,split,forms=notables (text blocks cut at line breaks between layout regions; no character changes) | [keep] | fresh evidence, never used to tune split: second answer key precision +1.4 / +0.4 and recall +3.8 / +1.0; OmniDocBench layout IoU +0.004 / +0.005 and IoU>=0.5 +0.4 / +0.8. Also DocLayNet IoU +0.019 / +0.023, IoU>=0.5 +2.6 / +3.4, type recall 0 / +2.5; FUNSD links unchanged (form guard holds). Minus: agreement with r-1's types -0.5 / -0.3 (noise 0.5 / 4.3), OmniDocBench layout type precision -0.4 dev (noise 5.0): none beyond noise | dev omnilay.iou50 +37.6; dev omnilay.type_recall_lenient +25.5; held omnilay.iou50 +24.8; held omnilay.type_precision +23.3 |
| 127 | 2026-11-13 | combine | `step 5g: handwriting route with the print guard (handwriting_illegible_max_agreement=0.5)` | step 5 with the guard: an [illegible] mark routes a page to Sonnet only when local OCR does not confirm the page read | [keep] | 52 pages go to Sonnet instead of 67 (FUNSD 5 instead of 13, business 16 instead of 21; 15 of the 18 business pages marked as handwriting still go). Against no route (step 4): FUNSD links +0.2 dev, -2.3 held (unguarded -1.0 / -3.4; noise 2.1 / 1.4), agreement with r-1 -0.6 / -0.1 (noise 0.5 / 4.3), OmniDocBench held text edit 0.116 -> 0.098 (noise 0.002), olmOCR old_scans +1.4 dev, long_tiny_text -1.3 dev (noise 2.6). The held FUNSD loss that remains is on faint forms that OCR cannot read either; kept because the route was asked for and its handwriting gain was judged earlier (no scripted handwriting truth in the devset) | dev omnilay.iou50 +37.2; dev omnilay.type_recall_lenient +25.5; held omnilay.iou50 +24.0; held omnilay.type_precision +23.3 |
| 128 | 2026-11-13 | combine | `engine-final: step 5g + kv_rules:rows + split (the final profile)` | profile engine-final as committed: words tesseract>rapidocr, guarded rotation, gated zoom, WS3 checks, WS1 guarded types + boxes + split + form crops, WS2 fillable + kv_lines:join + kv_rules:rows, guarded handwriting route | [keep] | against same-reads vanilla nothing is lower beyond noise on both halves; FUNSD links held -1.7 (noise 1.4) from the handwriting route. Against r-1: ahead on olmOCR mean, long tiny text, multi column, OmniDocBench text edit, printed totals; behind on RD-TableBench (-1.4 / -2.0), FUNSD links, both answer keys, OmniDocBench layout boxes, DocLayNet lenient types; rest level or mixed. Business 0.0098 USD/page (r-1 0.0100), 31.5 model s/page mean. Extract 100 / 100. Filled-form truth set: 100% of values and marks | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +24.8 |
| 129 | 2026-11-13 | combine | `engine-final live: business pages on fresh reads (sample live1) through the HTTP server, 4 requests at a time` | same profile, reader sample live1 (no cached read); plus 3 multi-page documents | [check] | 147 of 147 responses, none empty, no crash; 28.7 s/page wall mean, 20.0 median; 0.0093 USD/page. Against the replay: most measures inside the noise floor; beyond it on dev: second key recall -9.9 (noise 4.8), totals -6, grid -3.0 (noise 2.5), second key precision +12.8: a third read differs from the first by more than the first two differed. Multi-page: USPS 10-K 10 pages 16.6 s, scan packet 10 pages 42.6 s (5 pages by Sonnet), Brown 14 pages 13.3 s (12 of 29 calls replayed) | dev bus.totals_found -29.0; dev bus.key70_precision +20.4; held bus.tables -28.0; held bus.grid_ok +12.9 |
| 130 | 2026-11-13 | r2b-forms | `engine-final at tag final-round-1 from the private snapshot, real HTTP server on port 8852 (baseline of round 2 B)` | nothing; base regions types=guarded,boxes,split,forms=notables | [check] | 639 of 639 responses identical to cb_final; scores equal except OmniDocBench held table TEDS 45.6 vs 43.7 (same responses: the official matcher is not deterministic on dense pages under load) | dev cost.omni.s_page +42.7; dev bus.tables -37.0; held cost.omni.s_page +45.8; held bus.tables -38.0 |
| 131 | 2026-11-13 | r2b-forms | `the same by in-process replay after Ground (r2b/replay.py)` | nothing | [check] | 636 of 639 responses identical to the HTTP run; the 3 others are the 3 turned pages (boxes differ by 0.002 to 0.02); base of every replayed variant | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.7 |
| 132 | 2026-11-13 | r2b-forms | `hfsplit: one-line running heads and feet cut into their word groups` | regions +hfsplit | [keep (provisional, one read set)] | dev/held: both keys recall +3.6/+1.9 (noise 3.8/4.6), first key recall +3.9/+1.4 (noise 2.2/6.0), DocLayNet IoU +0.009/+0.012, IoU>=0.5 +1.0/+1.7, OmniDocBench layout IoU +0.008/+0.008 (noise 0.003/0.010); lower inside noise: DocLayNet type precision -0.2/-0.6 (a wrong Header block cut in two counts twice), held first key precision -0.6 | dev bus.tables -37.0; dev dln.type_recall +14.5; held bus.tables -38.0; held dln.type_recall +15.7 |
| 133 | 2026-11-13 | r2b-forms | `kvsplit, first version (any Key Value block; pieces retyped by the layout rules)` | regions +kvsplit | [superseded] | dev first key precision -3.5 (noise 1.2), held OmniDocBench layout precision -1.3 (noise 0.5): the pieces of a Key Value block match text detections one to one and the guarded rule made them Text; fixed in r2b_kvsplit2 | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.9 |
| 134 | 2026-11-13 | r2b-forms | `kv=form+any: field-like Text inside any detected form region becomes Key Value` | regions +kv=form+any | [drop] | FUNSD links +4.5/+2.4 but OmniDocBench layout type precision -7.5/-3.5 and recall -7.1/-0.8 (noise 5.0/0.5, 1.5/6.3): the layout model calls exercise and note pages form regions | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.7 |
| 135 | 2026-11-13 | r2b-forms | `kv=form: the same only where the page read itself shows a field in the region` | regions +kv=form | [keep (provisional, one read set)] | FUNSD links +1.7/+2.0 (noise 2.1/1.4), both keys precision +10.8/+0.5 (noise 10.3/2.7), same type as r-1 +0.5/+0.3; nothing moves on OmniDocBench, DocLayNet, olmOCR, tables; held key recall -1.6 (noise 4.6): short notes inside form regions that the key calls Text | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.7 |
| 136 | 2026-11-13 | r2b-forms | `kv=form+heads: also Header / Section Header made from Text by a position or type-size rule` | regions +kv=form+heads | [drop] | against kv=form: dev both keys precision +1.4 but recall -0.4/-0.6 and same type as r-1 -1.0/-0.7 | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.7 |
| 137 | 2026-11-13 | r2b-forms | `kv=form+page: on a page with no form region and no checkbox the reader's Key Value lines are Text` | regions +kv=form+page | [keep (provisional)] | against kv=form nothing lower: DocLayNet type recall +0.2/+0.1, precision +0.2/+0.1, OmniDocBench layout held recall +2.3, precision +0.5; business and FUNSD identical | dev bus.tables -37.0; dev dln.type_recall +14.5; held bus.tables -38.0; held dln.type_recall +15.9 |
| 138 | 2026-11-13 | r2b-forms | `figures, first version (Figure block with the text [Figure])` | regions +figures | [superseded] | DocLayNet type recall +1.5/+1.1, OmniDocBench layout recall +0.7/+0.9 and IoU +0.014/+0.010, but olmOCR old_scans held -4.2 (noise 1.4) and business key precision -0.5/-1.8: the placeholder text; r2b_figures2 has no content | dev bus.tables -37.0; dev dln.type_recall +15.8; held bus.tables -38.0; held dln.type_recall +16.8 |
| 139 | 2026-11-13 | r2b-forms | `boxfill: blocks without a located box take the free layout region with their words` | regions +boxfill | [keep (small)] | second key recall +0.6/+0.3, DocLayNet held IoU +0.001; held DocLayNet type precision -0.1 (1 block); nothing else moves | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.7 |
| 140 | 2026-11-13 | r2a-tables | `engine-final replayed (no round-2 switch)` | profile engine-final, in-process replay from documents saved after the plain Ground step; OmniDocBench TEDS timeout raised | [baseline] | 639 of 639 responses identical to cb_final (HTTP); omni held table TEDS 43.7 -> 45.6 and per-page 67.1 -> 73.0 only because one large table had timed out in the official TEDS code on the loaded machine and scored 0 (now 0.87) | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 141 | 2026-11-13 | r2a-tables | `tables: clean=1` | cell hygiene, deterministic: picture/icon descriptions removed from table cells, ul/ol/li and p inside cells become plain lines with a bullet | [keep] | RD-TableBench +0.04 / +0.43 (noise 0.04 / 0.19); no other measure moves on any component; no model call | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 142 | 2026-11-13 | r2a-tables | `tables: merge=layout` | two consecutive table blocks joined when one layout table region holds both and the column counts match | [drop] | the layout model also draws one region around tables the keys keep apart: RD-TableBench dev -0.56, OmniDocBench held table TEDS -4.9 and table edit +0.019 (noise 0.011); 15 joins on 8 pages, none of them the split RD table | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -43.0; held omnilay.iou50 +23.0 |
| 143 | 2026-11-13 | r2a-tables | `tables: merge=cont` | merge=layout plus: a table with the same columns directly below, without a column header of its own, is joined | [drop] | joins the split RD table (held +0.82) but RD dev -0.56, OmniDocBench table TEDS -6.2 / -4.9 and struct -6.6 on dev (noise 0.6): stacked tables with equal columns are usually separate tables | dev bus.tables -40.0; dev omnilay.iou50 +37.4; held bus.tables -43.0; held omnilay.iou50 +23.0 |
| 144 | 2026-11-13 | r2a-tables | `formulas=tag` | equation number written as \quad (n) at the end of a displayed formula becomes \tag{n} (deterministic) | [drop] | formula edit distance held 0.1325 -> 0.1321: no effect (the official code already scores the formula text almost the same) | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 145 | 2026-11-13 | r2a-tables | `tables: reread=missed` | layout shows a table or index region where the page read has running text: region re-read with a table prompt that may answer NOT_A_TABLE; accepted only as a real grid with the page read's words whose cells stand in columns on the page | [option] | 8 candidates on 639 pages, 2 accepted, both right (RD menu table 0 -> 0.91: RD held +1.78, no_table 1 -> 0; a DocLayNet index page); superseded by missed_prompt=force, which also finds the olmOCR table the model declined | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 146 | 2026-11-13 | r2a-tables | `tables: reread=missed;missed_prompt=force` | as reread=missed with the plain table prompt (no NOT_A_TABLE way out): the acceptance rules alone decide | [keep] | 8 candidates, 3 accepted, all three real tables: RD held +1.76 (no_table 1 -> 0), olmOCR table tests held +6.7 (83.3 -> 90.0; r-1 88.3), DocLayNet held type precision +2.0; the 5 text regions (two olmOCR index pages, one OmniDocBench page, two DocLayNet regions) are rejected by the rules; no measure worse beyond noise; 8 calls, 0.009 USD on 639 pages | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 147 | 2026-11-13 | r2a-tables | `tables: reread=dropped` | layout shows a table where the page read has no table and OCR words there are missing from the page read: crop read added when OCR confirms its words | [drop] | 11 candidates (the newspaper pages), 0 accepted: Tesseract on newspaper print confirms under 60% of the crop's words, or the model says it is not a table; nothing moves | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 148 | 2026-11-13 | r2a-tables | `tables: reread=dropped;dropped_precision=0.4` | the same with OCR confirmation lowered to 40% | [drop] | a few listings added on one newspaper page; OmniDocBench held table TEDS -0.2, struct +0.7, all inside noise: no gain | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 149 | 2026-11-13 | r2a-tables | `tables: reread=broken` | tables still broken after workstream 3's repair: table-only re-read, accepted when consistent with the same numbers and 95% of the same words | [keep] | 3 candidates left after WS3's repair, 2 repaired (1 RD, 1 olmOCR): RD dev +0.35; nothing else moves; additive to verify.checks grid=norm+draft | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 150 | 2026-11-13 | r2a-tables | `formulas=matrix` | a matrix the page read wrote as an HTML table inside a layout formula region (no table region there) is read again from the crop with a formula prompt; accepted when digits and letters are the same | [keep] | 1 region on 639 pages: OmniDocBench dev formula edit 0.184 -> 0.070 (r-1 0.073; noise 0.106), reading order -0.011; held unchanged; 1 call | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 151 | 2026-11-13 | r2a-tables | `formulas=lines` | tall displayed formulas re-read from the crop to keep printed line breaks | [option] | 3 candidates, 2 accepted on a DocLayNet page (no formula measure there), the OmniDocBench one rejected (crop held other text): no measured effect | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 152 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=content;min_area=0.5` | tables that cover half the page or more are read again from the whole page image with the table-only prompt; accepted with a consistent grid, exactly the same numbers and 95% of the same words (both ways) | [option] | RD-TableBench +2.06 / +0.17 (noise 0.04 / 0.19); nothing worse beyond noise; superseded by the same rule for every table | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 153 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=content` | every table with a matching layout region is read again with the table-only prompt (crop, or whole page when the table covers half of it); accepted with a consistent grid, exactly the same numbers and 95% of the same words | [keep] | 277 tables re-read, 105 accepted: RD-TableBench +2.06 / +0.21, olmOCR table tests dev +6.3 (87.3 -> 93.7), no measure worse beyond noise on any component, printed totals and business grids unchanged; +0.0008 USD per page on average | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.0 |
| 154 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=evidence` | accept=content plus: where numbers differ, the re-read is taken on a born-digital page when the exact text layer agrees better with its numbers | [option] | 3 more business tables accepted (page read had numbers the text layer lacks); every measure equal to accept=content: correct by construction but not measured by the devset | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 155 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=fit` | accept=content and the re-read's located cells must fit columns and rows of the page at least as well | [drop] | the fit of located cells does not tell the better grid: RD +0.64 / -0.07 against +2.06 / +0.21 for content alone | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.0 |
| 156 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=strict` | accept=content and the cells must fit strictly better | [drop] | almost nothing accepted (12 of 277): RD +0.35 / 0.0 | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 157 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=blind` | reference: any consistent re-read grid of comparable size replaces the page read | [drop] | swaps errors: RD dev +2.18 but held -0.74, printed totals dev -5, olmOCR table tests +9.5 / -1.7, multi_column held -1.3 | dev omnilay.iou50 +37.5; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.0 |
| 158 | 2026-11-13 | r2a-tables | `regions: route=tables` | page read kept for text; every layout table region read from its crop (strict crop prompt) replaces the page-read blocks in it, no acceptance rule | [drop] | olmOCR table tests +12.7 / +5.0 (100 / 88.3) but printed totals -5 / -14, business grids -2.8 / -4.7, FUNSD word recall dev -0.7: crop reads of scanned tables change digits | dev bus.tables -38.0; dev omnilay.iou50 +37.5; held bus.tables -42.0; held omnilay.iou50 +23.0 |
| 159 | 2026-11-13 | r2b-forms | `formread: form regions read from a crop as evidence only (types and label/value joins on the page read's blocks)` | regions +formread; 186 crop reads on 639 pages, 184 replayed from the round-1 cache | [keep (provisional, one read set)] | FUNSD links +4.7/+1.0 (noise 2.1/1.4), both keys precision +1.2/+1.6, first key precision +0.7/+2.3; lower inside noise: OmniDocBench layout precision -0.2/-0.4, held second key recall -0.8 | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.7 |
| 160 | 2026-11-13 | r2a-tables | `regions: route=tables,route_guard` | the same; a crop must keep 90% of the page read's words | [drop] | olmOCR table tests +12.7 / +6.7 but printed totals still -5 / -14 and RD held -0.42: a word-level guard does not protect digits | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -42.0; held omnilay.iou50 +23.0 |
| 161 | 2026-11-13 | r2a-tables | `regions: route=tables+forms` | table and form regions from crops, rest from the page read | [drop] | as route=tables, plus RD held -1.7 (2 tables lost), agreement with r-1 types -2.4 / -2.6, FUNSD word recall dev -0.9 | dev bus.tables -59.0; dev omnilay.iou50 +39.1; held bus.tables -53.0; held omnilay.iou50 +23.2 |
| 162 | 2026-11-13 | r2a-tables | `regions: route=full,route_min=0.4` | pages whose table and form regions cover 40% of the page (known before the read) are built from region reads only (assemble); other pages unchanged | [drop] | FUNSD links +5.2 / +3.7 and business key recall +16.6 / +3.5, but header/footer text -6.2 / -6.1, key precision held -15.8, printed totals -5 / -14, RD held -2.4, agreement with r-1 -4.1 / -6.6; 5.4 calls per page on those pages | dev bus.tables -74.0; dev omnilay.iou50 +39.4; held bus.tables -57.0; held omnilay.iou50 +23.4 |
| 163 | 2026-11-13 | r2a-tables | `tables: reread=missed+broken+all;accept=content;missed_prompt=force;clean=1;formulas=matrix` | the kept switches together (replay) | [keep] | RD-TableBench 89.8 / 91.8 (+2.06 / +2.39; r-1 89.2 / 91.5), no_table 0 / 0, olmOCR table tests 93.7 / 90.0 (+6.3 / +6.7; r-1 87.3 / 88.3), formula edit dev 0.070 (r-1 0.073); no measure worse beyond noise; USD per page +0.0009 / +0.0008 | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 164 | 2026-11-13 | r2b-forms | `forms (round-1 switch without its table guard): the crop read REPLACES the region's blocks; comparison for formread` | base regions with forms instead of forms=notables | [drop] | FUNSD links +0.6/-0.3, same type as r-1 -1.3/-1.4 (noise 0.5/4.3), both keys precision -2.2/-2.2, header/footer text dev -1.3: evidence-only (formread) is better on every one of them | dev bus.tables -48.0; dev dln.type_recall +14.3; held bus.tables -43.0; held dln.type_recall +15.7 |
| 165 | 2026-11-13 | r2a-tables | `engine-final without verify grid repair (reference)` | verify.checks = numbers=1;flags=1 (workstream 3's grid=norm+draft switched off) on the 26 pages where it acted | [baseline] | reference for the comparison below: business consistent grids 100.0 / 97.3 -> 89.8 / 88.4 | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 166 | 2026-11-13 | r2a-tables | `tables: reread=broken INSTEAD of verify grid=norm+draft` | broken tables re-read with the plain table-only prompt (no draft), accepted when consistent with the same numbers and 95% of the same words | [drop] | repairs 8 of 14 candidates: business grids 91.7 / 90.2 against 100.0 / 97.3 for workstream 3's draft repair (23 repaired): the draft prompt stays the default; reread=broken is kept only as a second step after it (RD dev +0.35) | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 167 | 2026-11-13 | r2b-forms | `kvsplit, final: only Key Value blocks inside a detected form region; the pieces stay Key Value` | regions +kvsplit | [option (provisional)] | dev: both keys recall +2.0, precision +2.9, first key recall +3.3 (noise 2.2); held: both keys precision +1.1, recall -0.1; lower inside noise: same type as r-1 held -0.6, DocLayNet type precision held -0.6, OmniDocBench layout precision -0.2/-0.2, first key precision dev -0.4 | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.9 |
| 168 | 2026-11-13 | r2b-forms | `figures without content (a box-only Figure block), min area 0.15% of the page` | regions +figures | [superseded] | olmOCR and business unchanged now; DocLayNet type recall +1.5/+1.1, OmniDocBench layout recall +0.7/+0.9, IoU +0.014/+0.010; OmniDocBench layout precision -0.9/-1.4 (noise 5.0/0.5) | dev bus.tables -37.0; dev dln.type_recall +15.8; held bus.tables -38.0; held dln.type_recall +16.8 |
| 169 | 2026-11-13 | r2b-forms | `figures, final: box-only Figure block, min area 0.5% of the page` | regions +figures | [option] | DocLayNet type recall +1.0/+0.8, lenient +1.2/+0.8 (noise 0.9/1.9), IoU +0.009/+0.008; OmniDocBench layout recall +0.9/+0.7, IoU +0.013/+0.008 (noise 0.003/0.010); OmniDocBench layout type precision -0.5/-1.3 (held beyond noise 0.5): ornaments that its labels count as part of a title or header. Same idea as workstream C's shape option layout_figures: keep one of the two | dev bus.tables -37.0; dev dln.type_recall +15.3; held bus.tables -38.0; held dln.type_recall +16.6 |
| 170 | 2026-11-13 | r2b-forms | `kv=form+page with formread (first code)` | regions +kv=form+page,formread | [superseded] | FUNSD links +5.0/+2.0, both keys precision +11.2/+1.0; held both keys recall -2.1 (noise 4.6) | dev bus.tables -37.0; dev dln.type_recall +14.5; held bus.tables -38.0; held dln.type_recall +15.9 |
| 171 | 2026-11-13 | r2b-forms | `hfsplit + kv=form+page + formread + boxfill (first code)` | regions +hfsplit,kv=form+page,formread,boxfill | [superseded] | see r2b_core3 | dev bus.tables -37.0; dev dln.type_recall +14.7; held bus.tables -38.0; held dln.type_recall +15.9 |
| 172 | 2026-11-13 | r2b-forms | `all six switches (first code)` | regions +hfsplit,kvsplit,kv=form+page,formread,boxfill,figures | [superseded] | see r2b_all3 | dev bus.tables -37.0; dev dln.type_recall +16.2; held bus.tables -38.0; held dln.type_recall +17.1 |
| 173 | 2026-11-13 | r2b-forms | `kv=form+page, final field test (a note that ends in a full stop and a URL are not fields)` | regions +kv=form+page | [keep (provisional, one read set)] | FUNSD links +1.6/+2.0 (noise 2.1/1.4), both keys precision +11.2/+0.4, same type as r-1 +0.9/+0.6, OmniDocBench layout held recall +2.3, precision +0.5, DocLayNet precision +0.2/+0.1; held key recall -1.6 (noise 4.6), second key recall held -2.0 (noise 3.5) | dev bus.tables -37.0; dev dln.type_recall +14.5; held bus.tables -38.0; held dln.type_recall +15.9 |
| 174 | 2026-11-13 | r2b-forms | `hfsplit + kv=form+page + formread + boxfill (before the 4-line rule of formread)` | regions +hfsplit,kv=form+page,formread,boxfill | [superseded] | see r2b_core3 | dev bus.tables -37.0; dev dln.type_recall +14.7; held bus.tables -38.0; held dln.type_recall +15.9 |
| 175 | 2026-11-13 | r2b-forms | `all six switches (before the 4-line rule of formread)` | regions +hfsplit,kvsplit,kv=form+page,formread,boxfill,figures | [superseded] | see r2b_all3 | dev bus.tables -37.0; dev dln.type_recall +15.7; held bus.tables -38.0; held dln.type_recall +16.8 |
| 176 | 2026-11-13 | r2b-forms | `handwriting print guard: read.handwriting_illegible_sure_agreement=0.33 (min density 0.03) and handwriting_max_ocr_agreement=0.33` | read options; base regions | [keep] | no FUNSD form goes to Sonnet (5 -> 0); FUNSD links -0.2/+2.4 (noise 2.1/1.4): held 83.4, no longer behind vanilla (82.8); manifest handwriting pages to Sonnet 15 of 18 -> 15 of 18; 2 other printed pages no longer routed (CMS-1500 sample, one RD-TableBench image); nothing else moves beyond 0.3; no new model call | dev bus.tables -36.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.7 |
| 177 | 2026-11-13 | r2c-product-gaps | `layout_figures: Figure blocks from layout picture detections` | shape stage option layout_figures (off by default): every heron picture detection that no block accounts for becomes a Figure block; an unlocated described figure takes the nearest free picture box | [option] | on the 22 business pages that hold all 23 of r-1's Figure blocks: Figure blocks 8 -> 24, r-1's figures matched at IoU >= 0.5 4 -> 15 of 23, every other block unchanged on 22 of 22 pages, no model call; not scored on the devset and 61 business pages not replayed, so not proposed as a default yet |  |
| 178 | 2026-11-13 | r2a-tables | `engine-final replayed (no round-2 switch) [batch]` | profile engine-final, in-process replay from documents saved after the plain Ground step; OmniDocBench TEDS timeout raised | [baseline] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. the reference for every r2a line below; differs from cb_final on 6 pages because another workstream's change altered engine-final's default output between 11:45 and 12:40, and on one OmniDocBench table whose TEDS had timed out (0 -> 0.87) | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 179 | 2026-11-13 | r2a-tables | `tables: clean=1 [batch]` | cell hygiene, deterministic: picture/icon descriptions removed from table cells, ul/ol/li and p inside cells become plain bulleted lines | [keep] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. RD-TableBench +0.04 / +0.43 (noise 0.04 / 0.19); no other measure moves; no model call | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 180 | 2026-11-13 | r2a-tables | `tables: merge=layout [batch]` | two consecutive table blocks joined when one layout table region holds both and the column counts match | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. the layout model draws one region around tables the keys keep apart: RD-TableBench dev -0.56, OmniDocBench held table edit +0.019 (noise 0.011) | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -43.0; held omnilay.iou50 +23.1 |
| 181 | 2026-11-13 | r2a-tables | `tables: merge=cont [batch]` | merge=layout plus: a table with the same columns directly below without a column header of its own is joined | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. joins the split RD table (held +0.82) but RD dev -0.56, OmniDocBench table TEDS structure dev -6.6 (noise 0.6), table edit +0.044 / +0.019 | dev bus.tables -40.0; dev omnilay.iou50 +37.4; held bus.tables -43.0; held omnilay.iou50 +23.1 |
| 182 | 2026-11-13 | r2a-tables | `formulas=tag [batch]` | equation number at the end of a displayed formula becomes \tag{n} (deterministic) | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. no measure moves | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 183 | 2026-11-13 | r2a-tables | `formulas=matrix [batch]` | a matrix the page read wrote as an HTML table inside a layout formula region is read again from the crop with a formula prompt; accepted when digits and letters are the same | [keep] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. 1 region on 639 pages, 1 call: OmniDocBench dev formula edit 0.184 -> 0.070 (r-1 0.073; noise 0.106); held unchanged; nothing worse | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 184 | 2026-11-13 | r2a-tables | `formulas=lines [batch]` | tall displayed formulas re-read from the crop to keep printed line breaks | [option] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. 3 candidates, 2 accepted on a DocLayNet page, the OmniDocBench one rejected: no measured effect | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 185 | 2026-11-13 | r2a-tables | `tables: reread=missed [batch]` | layout table / index region where the page read has running text: re-read with a prompt that may answer NOT_A_TABLE; accepted only as a real grid with the page read's words whose cells stand in columns | [option] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. 8 candidates, 2 accepted, both right: RD held +1.76, no_table 1 -> 0; superseded by missed_prompt=force | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 186 | 2026-11-13 | r2a-tables | `tables: reread=missed;missed_prompt=force [batch]` | the same with the plain table prompt: the acceptance rules alone decide | [keep] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. 8 candidates, 3 accepted, all real tables: RD held +1.76 (no_table 1 -> 0), olmOCR table tests held +6.7 (83.3 -> 90.0; r-1 88.3); the 5 text regions are rejected; nothing worse beyond noise; 0.009 USD on 639 pages | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 187 | 2026-11-13 | r2a-tables | `tables: reread=dropped [batch]` | layout shows a table where the page read has nothing and OCR sees words the read lacks: crop read added when OCR confirms its words | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. 11 candidates on the newspaper pages, 0 accepted; nothing moves | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 188 | 2026-11-13 | r2a-tables | `tables: reread=dropped;dropped_precision=0.4 [batch]` | the same with OCR confirmation lowered to 40% | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. no measure moves beyond noise | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.4 |
| 189 | 2026-11-13 | r2a-tables | `tables: reread=broken [batch]` | tables still broken after workstream 3's repair: table-only re-read, accepted when consistent with the same numbers and 95% of the same words | [keep] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. 2 of the 3 remaining candidates repaired: RD dev +0.35; nothing else moves | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 190 | 2026-11-13 | r2a-tables | `engine-final without verify grid repair (reference) [batch]` | verify.checks = numbers=1;flags=1 on the 26 pages where grid=norm+draft acted | [baseline] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. business consistent grids 100.0 / 97.3 -> 89.8 / 88.4 | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 191 | 2026-11-13 | r2a-tables | `tables: reread=broken INSTEAD of verify grid=norm+draft [batch]` | broken tables re-read with the plain table-only prompt (no draft) | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. business grids 91.7 / 90.2 against 100.0 / 97.3 for workstream 3's draft repair: the draft repair stays the default | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 192 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=content;min_area=0.5 [batch]` | tables covering half the page or more re-read from the whole page image; accepted with a consistent grid, exactly the same numbers and 95% of the same words | [option] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. RD-TableBench +2.06 / +0.17; nothing worse; superseded by the same rule for every table | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 193 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=content [batch]` | every table with a matching layout region re-read with the table-only prompt; accepted with a consistent grid, exactly the same numbers and 95% of the same words | [keep] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. 277 re-read, 105 accepted: RD-TableBench +2.06 / +0.21, olmOCR table tests dev +6.3 (87.3 -> 93.7), no measure worse beyond noise, printed totals and business grids unchanged; +0.0009 / +0.0007 USD per page | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 194 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=evidence [batch]` | accept=content plus: where numbers differ, a born-digital page's exact text layer decides | [option] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. 3 more business tables accepted; every measure equal to accept=content | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 195 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=fit [batch]` | accept=content and the located cells must fit columns and rows at least as well | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. RD +0.64 / -0.07 against +2.06 / +0.21 for content alone | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 196 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=strict [batch]` | accept=content and the cells must fit strictly better | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. RD +0.35 / 0.0: almost nothing accepted | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 197 | 2026-11-13 | r2a-tables | `tables: reread=all;accept=blind [batch]` | reference: any consistent re-read grid of comparable size replaces the page read | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. swaps errors: RD +2.18 / -0.74, printed totals dev -5, olmOCR table tests +9.5 / -1.7, multi_column held -1.3, FUNSD word recall dev -0.4 | dev omnilay.iou50 +37.5; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 198 | 2026-11-13 | r2a-tables | `regions: route=tables [batch]` | page read kept for text; every layout table region read from its crop replaces the page-read blocks in it, no rule | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. olmOCR table tests +12.7 / +5.0 but printed totals -5 / -14, business grids -2.8 / -4.7, FUNSD word recall dev -0.75, multi_column held -1.3 | dev bus.tables -38.0; dev omnilay.iou50 +37.5; held bus.tables -42.0; held omnilay.iou50 +23.0 |
| 199 | 2026-11-13 | r2a-tables | `regions: route=tables,route_guard [batch]` | the same; a crop must keep 90% of the page read's words | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. olmOCR table tests +12.7 / +6.7 but printed totals -5 / -14, RD held -0.42, OmniDocBench held table edit +0.018 | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -42.0; held omnilay.iou50 +23.0 |
| 200 | 2026-11-13 | r2a-tables | `regions: route=tables+forms [batch]` | table and form regions from crops, rest from the page read | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. printed totals -5 / -14, RD held -1.7, key precision -1.9 / -14.9, agreement with r-1 -2.4 / -2.6, FUNSD word recall dev -0.9; business key recall dev +10.5 | dev bus.tables -59.0; dev omnilay.iou50 +39.1; held bus.tables -53.0; held omnilay.iou50 +23.2 |
| 201 | 2026-11-13 | r2a-tables | `regions: route=full,route_min=0.4 [batch]` | pages whose table and form regions cover 40% of the page are built from region reads only; other pages unchanged | [drop] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. FUNSD links +5.2 / +3.7, business key recall +16.6 / +3.5, but header/footer text -4.8 / -6.1, key precision held -15.8, printed totals -5 / -14, RD held -2.4, agreement with r-1 -3.7 / -6.6 | dev bus.tables -73.0; dev omnilay.iou50 +39.4; held bus.tables -57.0; held omnilay.iou50 +23.4 |
| 202 | 2026-11-13 | r2a-tables | `read: zoom_regions=gate [batch]` | whole-page zoom strips as before, but none on pages where the layout shows neither a table nor a form region (57 pages re-prepared with the switch, the rest unchanged by construction) | [keep] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. no measure moves; all 9 verified digit corrections on business scans kept; one word correction on an olmOCR page lost (Panks -> Banks); USD per page -0.0004 / -0.0005 (olmOCR 0.0058 -> 0.0051, OmniDocBench 0.0061 -> 0.0052) | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 203 | 2026-11-13 | r2a-tables | `read: zoom_regions=tables_only (HTTP, port 8851)` | zoom strips cut to the layout table regions (k and k+1 strips per table), none on pages without a table region | [drop] | real HTTP run started 12:30 (not part of the batch). no devset measure moves and cost falls (business 0.0100 -> 0.0087, scanned with tables 0.0249 -> 0.0211) but only 7 of the 9 verified digit corrections are made (16,683 and 62588 lost) and 3 new unverified changes appear, one wrong (2,302 -> 2.302): other strips, other votes | dev cost.omni.s_page +52.3; dev omnilay.iou50 +37.6; held cost.omni.s_page +51.4; held bus.tables -38.0 |
| 204 | 2026-11-13 | r2a-tables | `tables: reread=missed+broken+all;accept=content;missed_prompt=force;clean=1;formulas=matrix [batch]` | the kept table and formula switches together (replay) | [keep] | re-measured in one batch on one state of the working tree (baseline replayed first and last, identical); supersedes the earlier r2a-tables line of the same target. RD-TableBench 89.8 / 91.8 (+2.06 / +2.39; r-1 89.2 / 91.5), no_table 0 / 0, olmOCR table tests 93.7 / 90.0 (+6.3 / +6.7; r-1 87.3 / 88.3), olmOCR mean +1.3 / +1.3, formula edit dev 0.070 (r-1 0.073); no measure worse; USD per page +0.0009 / +0.0007 | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 205 | 2026-11-13 | r2a-tables | `tables: reread=missed;missed_prompt=force with layout thresholds lowered to 0.2 (stress test)` | the missed-table rules with unsure layout detections admitted as candidates (table and index score 0.2 instead of 0.5 / 0.6) | [drop] | 13 candidates instead of 8, 5 accepted instead of 3: the two extra ones (layout score 0.45 and 0.49) are two-column list-like text on a DocLayNet laws page that its labels do not call a table (held type recall -2.2, noise 2.2). The acceptance rules alone do not keep list-like text out; the layout score threshold is part of the rule and stays at 0.5 / 0.6 | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 206 | 2026-11-13 | r2b-forms | `FINAL hfsplit: one-line running heads and feet cut into their word groups` | regions +hfsplit | [keep (recommended)] | both keys recall +3.6/+1.9 on the round-1 reads, +3.4/+2.4 on the live-run reads, +3.0/+2.1 on the second vanilla reads (noise of one pair 3.8/4.6; same sign six times); first key recall +3.9/+1.4; DocLayNet IoU +0.009/+0.012 and IoU>=0.5 +1.0/+1.7; OmniDocBench layout IoU +0.008/+0.008; held OmniDocBench text edit -0.007 (better). Lower inside noise, same sign on every set: first key precision held -0.3 to -0.6, DocLayNet type precision -0.2/-0.6 (a wrong Header block cut in two counts twice). No model call | dev bus.tables -37.0; dev dln.type_recall +14.5; held bus.tables -38.0; held dln.type_recall +15.7 |
| 207 | 2026-11-13 | r2b-forms | `FINAL formread: form regions read from a crop as evidence only (4+ label lines; types, label/value joins, plain-line marks)` | regions +formread; one Haiku crop read per form region, 186 on 639 pages | [keep (recommended)] | FUNSD links +4.7/+1.0 on the round-1 reads and +3.3/+0.7 on the second reads (noise 2.1/1.4); both keys precision +6.4/+2.1, +0.6/+0.6, +1.4/+0.7 on the three read sets; recall within 0.4 except +2.5 held on the third set; agreement with r-1 has no stable sign (+0.5/+0.6, +0.2/-0.5, -0.6/-1.9); nothing moves on tables, olmOCR, OmniDocBench text. Cost +0.0003 USD/page over the devset (+0.001 on business pages) | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.7 |
| 208 | 2026-11-13 | r2b-forms | `FINAL kv=page: on a page without any form region or checkbox the reader's Key Value lines are Text` | regions +kv=page | [keep (recommended, small)] | DocLayNet type precision +0.2/+0.1 and +0.2/+0.3, OmniDocBench layout precision 0/+0.5 and +0.2/+0.4, held recall +2.3 and +0.7 on the two read sets; business, FUNSD and everything else identical | dev bus.tables -37.0; dev dln.type_recall +14.5; held bus.tables -38.0; held dln.type_recall +15.9 |
| 209 | 2026-11-13 | r2b-forms | `FINAL boxfill: blocks without a located box take the free layout region with their words` | regions +boxfill | [keep (recommended, small)] | both keys recall +0.4/+0.1, 0/+0.1, 0/+0.1 on the three read sets; DocLayNet held IoU +0.001; held DocLayNet type precision -0.1 (1 block); nothing else moves. Few blocks qualify: most blocks without a box are short numbers that occur twice on the page | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.7 |
| 210 | 2026-11-13 | r2b-forms | `FINAL kvsplit: a Key Value block inside a form region is cut between layout regions where the next line starts a field` | regions +kvsplit | [option (lead's call)] | both keys precision +2.9/+1.1, +1.5/+0.5, +0.5/+0.8 and recall +2.0/-0.1, +1.8/+0.3, +1.4/+1.0 on the three read sets; costs with the same sign on every set, each inside noise: DocLayNet type precision held -0.6/-0.6, OmniDocBench layout precision -0.2, first key precision dev -0.4/-0.5/-1.0, agreement with r-1 held -0.6/-0.1/-0.8 (more Key Value blocks, which the strict layout maps always count as wrong) | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.9 |
| 211 | 2026-11-13 | r2b-forms | `FINAL kv=form: field-like Text inside a form region that the page read shows fields in becomes Key Value` | regions +kv=form | [option (not recommended)] | FUNSD links +1.6/+2.0 and 0/+1.4 on two read sets and both keys precision +11.2/+0.4 (one blank T4 slip), but held key recall -1.6/-1.3/-0.6 and held first key precision -0.7/-0.7/-0.8 on all three read sets: short captions and notes inside form regions that the key calls Text. With formread on it adds only FUNSD held +1.0 | dev bus.tables -37.0; dev dln.type_recall +14.3; held bus.tables -38.0; held dln.type_recall +15.7 |
| 212 | 2026-11-13 | r2b-forms | `FINAL figures: box-only Figure block for a picture detection no block covers (min 0.5% of the page)` | regions +figures | [option (not recommended as default)] | DocLayNet type recall +1.0/+0.8, IoU +0.009/+0.008; OmniDocBench layout recall +0.9/+0.7, IoU +0.013/+0.008; OmniDocBench layout type precision held -1.3 and -1.1 on two read sets (noise 0.5): ornaments its labels count as part of a title. Duplicates workstream C's shape option layout_figures: keep one | dev bus.tables -37.0; dev dln.type_recall +15.3; held bus.tables -38.0; held dln.type_recall +16.6 |
| 213 | 2026-11-13 | r2b-forms | `FINAL recommended region set: hfsplit + kv=page + formread + boxfill` | regions +hfsplit,kv=page,formread,boxfill | [keep (recommended)] | three read sets: both keys recall +4.3/+1.8, +3.6/+2.1, +2.8/+4.7; precision +7.0/+1.9, +0.5/+0.4, +1.2/+0.8; FUNSD links +4.7/+1.0 and +3.3/+0.7; DocLayNet IoU +0.009/+0.013 and +0.014/+0.016; OmniDocBench layout precision +0.3/+0.6 and +0.5/+0.4. No measure lower beyond noise on the round-1 reads. No stable sign: agreement with r-1 (+1.1/+0.9, +0.3/-0.1, -1.1/-1.5) and first key precision held (+1.6, -0.1, -1.9) | dev bus.tables -37.0; dev dln.type_recall +14.7; held bus.tables -38.0; held dln.type_recall +15.9 |
| 214 | 2026-11-13 | r2b-forms | `recommended set + kvsplit` | regions +hfsplit,kv=page,formread,boxfill,kvsplit | [option (lead's call)] | against the recommended set: both keys recall dev +2.0/+1.9/+1.5, held 0/+0.3/0; precision +1.9/+1.0, +1.3/+0.5, +0.7/+0.8; DocLayNet type precision held -0.5/-0.6; agreement with r-1 -0.1/-0.6, +0.1/-0.1, -0.2/-0.3 | dev bus.tables -37.0; dev dln.type_recall +14.7; held bus.tables -38.0; held dln.type_recall +16.0 |
| 215 | 2026-11-13 | r2b-forms | `recommended set + kv=form` | regions +hfsplit,kv=form+page,formread,boxfill | [option (not recommended)] | against the recommended set: FUNSD held +1.0/+0.7, dev both keys precision +6.3/+0.6/+0.5; held both keys recall -0.9/-0.9/-1.0 and held precision -0.2/-0.9/-1.2 on the three read sets | dev bus.tables -37.0; dev dln.type_recall +14.7; held bus.tables -38.0; held dln.type_recall +15.9 |
| 216 | 2026-11-13 | r2b-forms | `all six region switches` | regions +hfsplit,kvsplit,kv=form+page,formread,boxfill,figures | [option] | sum of the parts: best dev numbers (both keys recall +6.3, precision +14.7) but OmniDocBench layout precision held -0.8 (figures), held key recall +0.9 instead of +1.8 (kv=form) | dev bus.tables -37.0; dev dln.type_recall +15.7; held bus.tables -38.0; held dln.type_recall +16.8 |
| 217 | 2026-11-13 | r2b-forms | `recommended set without round 1's replacing form crops (forms=notables off)` | regions types=guarded,boxes,split,hfsplit,kv=page,formread,boxfill | [drop] | against the recommended set: held first key recall -7.1, both keys recall -3.4, FUNSD -0.2/-0.3; forms=notables stays | dev bus.tables -37.0; dev dln.type_recall +14.7; held bus.tables -38.0; held dln.type_recall +15.9 |
| 218 | 2026-11-13 | r2b-forms | `FINAL: recommended region set + handwriting print guard (= profile engine-final-r2b), by replay` | regions +hfsplit,kv=page,formread,boxfill; read.handwriting_illegible_sure_agreement=0.33, handwriting_max_ocr_agreement=0.33 | [keep (recommended)] | against engine-final: FUNSD links 86.0/84.1 (+4.5/+3.0; r-1 87.6/86.8; vanilla 80.6/82.8), both keys recall 73.3/75.0 (+3.8/+1.8), precision 79.9/83.9 (+6.7/+1.9), first key recall +4.4/+1.4, DocLayNet IoU +0.009/+0.013, OmniDocBench layout precision +0.3/+0.6; lower inside noise: DocLayNet type precision -0.1/-0.6, FUNSD word recall -0.1/-0.1 | dev bus.tables -36.0; dev dln.type_recall +14.7; held bus.tables -38.0; held dln.type_recall +15.9 |
| 219 | 2026-11-13 | r2a-tables | `tables: reread=missed+broken;missed_prompt=force;clean=1;formulas=matrix (the kept set without reread=all)` | the kept switches without the every-table re-read: for deployments where cost matters more than table structure | [option] | RD-TableBench 88.1 / 91.6 (+0.39 / +2.19; r-1 89.2 / 91.5), olmOCR table tests 87.3 / 90.0, formula edit dev 0.070; nothing worse; USD per page +0.00007 / +0.00002 (reread=all adds +1.7 on RD dev, +6.3 on olmOCR table tests dev, +0.2 on RD held and 0.0008 USD per page) | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 220 | 2026-11-13 | r2b-forms | `FINAL through the real HTTP server: profile engine-final-r2b (recommended region set + handwriting print guard), whole devset, port 8852` | profile engine-final-r2b = engine-final + regions hfsplit,kv=page,formread,boxfill + read.handwriting_illegible_sure_agreement=0.33, handwriting_max_ocr_agreement=0.33 | [keep (recommended)] | 636 of 639 responses identical to the replay r2b_finalF (the 3 others are the turned pages; DocLayNet held differs by 0.1). Against engine-final on the same reads: FUNSD links 86.0/84.1 (+4.5/+3.0; noise 2.1/1.4; r-1 87.6/86.8), both keys recall 73.3/75.0 (+3.8/+1.8), precision 79.9/83.9 (+6.7/+1.9), first key recall 71.8/75.9 (+4.4/+1.4), DocLayNet IoU +0.009/+0.013, OmniDocBench layout precision +0.3/+0.6; nothing lower beyond noise against engine-final or vanilla; +0.00013 USD/page | dev bus.tables -36.0; dev cost.funsd.s_page +28.6; held bus.tables -38.0; held cost.funsd.s_page +31.3 |
| 221 | 2026-11-13 | r2a-tables | `profile engine-final-r2a through the real HTTP server (port 8851), 18:00` | engine-final + ground.tables reread=missed+broken+all;accept=content;missed_prompt=force;clean=1;formulas=matrix;prefetch=1 + read.zoom_regions=gate; real requests, model reads replayed from the call cache | [keep] | RD-TableBench 89.8 / 91.8 (+2.06 / +2.39; r-1 89.2 / 91.5), no_table 0 / 0, olmOCR table tests 93.7 / 90.0 (+6.3 / +6.7; r-1 87.3 / 88.3), formula edit dev 0.070 (r-1 0.073); no measure worse beyond noise on dev or held; all 9 verified zoom digit corrections kept; USD per page 0.0058 / 0.0061 (engine-final 0.0052 / 0.0057). A first HTTP run at 13:25 missed the olmOCR table and one formula region because Ground had typed a block inside the region as Footer / List Item; the candidate rule now ignores furniture types there (fixed, re-run). All replay variants were re-run on documents prepared again on the final tree: same numbers as the [batch] lines | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.1 |
| 222 | 2026-11-13 | r2a-tables | `the same set replayed in-process (documents prepared again on the final tree)` | ground.tables as above on the documents prepared with zoom_regions=gate for the 57 pages it touches | [keep] | every measure equal to the HTTP run; 613 of 639 responses identical to it, 17 differ in a box by under 0.01, 9 in how a text block is cut or typed (5 of them the pages on which replay and server have differed since another workstream's Shape commits); no table differs | dev omnilay.iou50 +37.6; dev bus.tables -37.0; held bus.tables -38.0; held omnilay.iou50 +23.3 |
| 223 | 2026-11-13 | combine-r2 | `engine-final-2 = engine-final + A's table/formula/zoom set + B's form/structure set, first run (real HTTP server, port 8861, reads dev1)` | profile engine-final-2: ground.tables reread=missed+broken+all;accept=content;missed_prompt=force;clean=1;formulas=matrix;prefetch=1, read.zoom_regions=gate, regions +hfsplit,kv=page,formread,boxfill, handwriting print guard 0.33 | [check] | Additive: every measure equals the better of the two workstreams alone or is inside noise of it; 0 new model calls. Found: a form region is read twice with the same prompt (round-1 form crop and round-2 form evidence), 85 regions on 639 pages. | dev omnilay.iou50 +38.6; dev bus.tables -36.0; held bus.tables -38.0; held cost.omni.s_page +29.4 |
| 224 | 2026-11-13 | combine-r2 | `engine-final-2 with one shared form crop read per region (regions/crops.py shared_reads)` | the round-1 guarded form crop and the round-2 form evidence read use ONE read of a form region (was two identical calls; in production two independent draws that could disagree) | [keep] | 639 of 639 responses identical to the first run (types, text, boxes); 85 fewer calls (1700 -> 1615), 0.0061 -> 0.0060 USD per page. This is the candidate final profile. | dev omnilay.iou50 +38.6; dev bus.tables -36.0; held bus.tables -38.0; held omnilay.iou50 +24.5 |
| 225 | 2026-11-13 | combine-r2 | `engine-final-2 + kvsplit (reads dev1, whole devset)` | regions +kvsplit: a Key Value block inside a form region is cut between layout regions where the next line starts a field | [evidence] | On top of the combined profile, same reads: both keys (100 pages) precision +2.0 / +1.0, recall +2.0 / 0.0, first key recall +3.3 / 0.0 (noise 2.2 / 6.0); lower inside noise: first key precision dev -0.5, DocLayNet type precision held -0.6, OmniDocBench layout type precision -0.2 / -0.2, agreement with r-1 held -0.6. 0 model calls. | dev omnilay.iou50 +38.5; dev bus.tables -36.0; held bus.tables -38.0; held omnilay.iou50 +24.5 |
| 226 | 2026-11-13 | combine-r2 | `engine-final-2 + C's shape option layout_figures as committed (reads dev1, whole devset)` | shape.layout_figures: a box-only Figure block for every layout picture detection that no block accounts for | [superseded] | OmniDocBench layout type precision -1.3 / -1.4 (noise 5.0 / 0.5); gains: DocLayNet type recall +1.7 / +1.0, IoU +0.015 / +0.007, OmniDocBench layout IoU +0.009 / +0.015, figure label recall 71 -> 83 / 63 -> 97. 13 of the 16 wrong figures on OmniDocBench dev are icons under 1% of the page on the line of a running head. | dev omnilay.iou50 +39.6; dev bus.tables -36.0; held bus.tables -38.0; held omnilay.iou50 +26.2 |
| 227 | 2026-11-13 | combine-r2 | `engine-final-2 + B's regions switch figures (reads dev1, whole devset)` | regions +figures: box-only Figure block for a picture detection (score 0.6+, 0.5% of the page) that no block covers | [drop] | Same held loss as C's switch (OmniDocBench layout type precision -0.5 / -1.2) with smaller gains (DocLayNet type recall +1.0 / +0.8, OmniDocBench IoU +0.013 / +0.009) and no part in the product (no crops, no description, no box for a described figure). At most one figure switch is kept: C's. | dev omnilay.iou50 +40.3; dev bus.tables -36.0; held bus.tables -38.0; held omnilay.iou50 +25.7 |
| 228 | 2026-11-13 | combine-r2 | `engine-final-2 + layout_figures with the running-head rule (reads dev1, whole devset)` | shape.layout_figures + layout_figures_running=0.5: a picture under 1% of the page with half its height on the line of a detected running head or foot is not made a Figure | [option] | Rule chosen on dev: OmniDocBench layout type precision dev -1.3 -> -0.5, no gain lost. Held still -1.3 (noise 0.5): the rule removes 2 of the 21 held figures the labels do not call a figure. Not a default; the one figure switch that is kept as an option. | dev cost.omni.s_page +51.4; dev omnilay.iou50 +39.5; held cost.omni.s_page +57.3; held bus.tables -38.0 |
| 229 | 2026-11-13 | combine-r2 | `engine-final-2 on the round-1 live reads (sample live1), business pages` | profile engine-final-2, reads of the round-1 live run; base = cb_live (engine-final on the same reads) | [check] | Against engine-final on the same reads: both keys recall +3.6 / +2.1, first key recall +5.0 / +1.4, precision +0.5 / +0.4; grids +0.7 / 0; totals unchanged. 101 new table re-reads (0.31 USD). | dev bus.totals_found -26.0; dev bus.key70_precision +20.2; held bus.tables -28.0; held bus.grid_ok +12.9 |
| 230 | 2026-11-13 | combine-r2 | `engine-final-2 + kvsplit on the live1 reads, business pages` | regions +kvsplit | [evidence] | Against engine-final-2 on the same reads: both keys precision +1.3 / +0.5, recall +1.8 / +0.3; first key precision dev -0.6 (noise 1.2). | dev bus.totals_found -26.0; dev bus.key70_precision +22.7; held bus.tables -28.0; held bus.grid_ok +12.9 |
| 231 | 2026-11-13 | combine-r2 | `engine-final-2 + layout_figures (running-head rule) on the live1 reads, business pages` | shape.layout_figures | [option] | No business measure moves on this read set (the +0.6 first-key gain of the dev1 reads is not repeated). | dev bus.totals_found -26.0; dev bus.key70_precision +20.2; held bus.tables -28.0; held bus.grid_ok +12.9 |
| 232 | 2026-11-13 | combine-r2 | `engine-final (round 1) on the second independent reads (sample dev2), whole devset` | profile engine-final, page reads / strips / verify / form crops of sample dev2; handwriting pages reuse the Sonnet read of sample dev1 | [check] | Base for the round-2 change on a second draw. olmOCR old_scans held 37.5 against vanilla's 40.3 on these reads (2 tests; noise 1 test): the handwriting route of round 1, not a round-2 piece. | dev omnilay.iou50 +39.5; dev bus.tables -39.0; held bus.tables -30.0; held omnilay.iou50 +25.2 |
| 233 | 2026-11-13 | combine-r2 | `engine-final-2 without kvsplit on the dev2 reads, whole devset` | combined profile before the kvsplit decision and before the Key Value / missed-table fix | [check] | Against engine-final on the same reads: RD-TableBench +1.6 / +0.3 (the held menu table is NOT recovered: this read typed it Key Value), olmOCR table tests +6.3 / +6.7, FUNSD links +2.7 / +1.7, both keys recall +2.2 / +1.6. | dev omnilay.iou50 +39.9; dev bus.tables -39.0; held bus.tables -30.0; held omnilay.iou50 +26.6 |
| 234 | 2026-11-13 | combine-r2 | `engine-final-2 + kvsplit on the dev2 reads, whole devset` | regions +kvsplit | [evidence] | Against the combined profile on the same reads: both keys precision +0.7 / +0.8, recall +1.4 / 0.0; DocLayNet type precision -0.7 / -0.6, first key precision dev -1.0 (inside noise). | dev omnilay.iou50 +39.7; dev bus.tables -39.0; held bus.tables -30.0; held omnilay.iou50 +26.6 |
| 235 | 2026-11-13 | combine-r2 | `FINAL: profile engine-final-2 by name (A + B + kvsplit, shared form read, Key Value / missed-table fix), whole devset + extract, reads dev1` | DOCFORGE_PROFILE=engine-final-2, nothing else set | [keep] | 639 of 639 responses identical to the env-switched kvsplit run. Against round 1 ahead beyond noise on both halves: RD-TableBench 89.8 / 91.8, olmOCR table tests 93.7 / 90.0, olmOCR mean, FUNSD links 86.6 / 84.1; nothing behind round 1 or vanilla beyond noise on both halves. Extract 100 / 100. | dev omnilay.iou50 +38.5; dev bus.tables -36.0; held bus.tables -38.0; held omnilay.iou50 +24.5 |
| 236 | 2026-11-13 | combine-r2 | `FINAL profile on the dev2 reads, whole devset` | engine-final-2 with the Key Value / missed-table fix; handwriting pages reuse the Sonnet read of sample dev1 | [keep] | Against engine-final on the same reads: RD-TableBench +1.6 / +2.1 (89.9 / 91.2; the fix recovers the held menu table, +1.8), olmOCR table tests +6.3 / +6.7, FUNSD links +2.9 / +1.7, both keys precision +2.7 / +2.8 and recall +3.6 / +1.6; nothing lower beyond noise. | dev omnilay.iou50 +39.7; dev bus.tables -39.0; held bus.tables -30.0; held omnilay.iou50 +26.6 |
| 237 | 2026-11-13 | combine-r2 | `FINAL profile on the live1 reads, business pages` | engine-final-2 (final code) | [keep] | Against engine-final on the same reads (cb_live): both keys recall +5.5 / +2.4, precision +1.8 / +0.9, first key recall +7.2 / +1.8. Identical scores to the env-switched kvsplit run. | dev bus.totals_found -26.0; dev bus.key70_precision +22.7; held bus.tables -28.0; held bus.grid_ok +12.9 |
| 238 | 2026-11-13 | combine-r2 | `FINAL profile LIVE on fresh reads (sample live2), business pages, 4 requests at a time` | engine-final-2 through the HTTP server, nothing replayed | [check] | 147 of 147 responses, none empty, no crash. 0.0119 USD per page, wall 39.7 s mean / 29.3 s median (round-1 live run: 0.0093, 28.7 / 20.0). | dev bus.tables -30.0; dev bus.key70_precision +19.3; held bus.tables -31.0; held bus.matched -15.1 |
| 239 | 2026-11-13 | combine-r2 | `vanilla-api on the fresh live reads (sample live2), business pages` | profile vanilla-api replaying the page reads of the live run | [check] | Vanilla on the same reads as the live run of the final profile. | dev bus.key70_precision +7.4; dev bus.key100_precision +6.4; held bus.key70_precision +9.7; held bus.tables +8.0 |
| 240 | 2026-11-13 | combine-r2 | `engine-final (round 1) on the fresh live reads (sample live2), business pages` | profile engine-final replaying the reads of the live run | [check] | Against it the final profile on the same reads: both keys precision +10.4 / +1.9, recall +3.9 / +1.3, first key recall +6.0 / +1.4, grids +0.9 / 0; header/footer text, totals and everything else equal. | dev bus.tables -30.0; dev bus.grid_ok +9.8; held bus.tables -31.0; held bus.key70_precision +12.9 |
| 241 | 2026-11-13 | combine-r2 | `server default: settings.model r-1 -> engine-final-2 when DOCFORGE_PROFILE is not set (tried in memory)` | MODEL_TO_PROFILE r-1 = engine-final-2 | [drop] | 5 of 120 existing API tests fail (they parse through the default profile cached-haiku without a key); the default is left; select the final engine with DOCFORGE_PROFILE=engine-final-2. Product tag final-round-2. | dev omnilay.iou50 +38.5; dev bus.tables -36.0; held bus.tables -38.0; held omnilay.iou50 +24.5 |
| 242 | 2026-11-13 | r3a-structure | `hfdet alone` | regions switch hfdet: a block whose words lie in two or more page-header/page-footer layout detections standing side by side (gap >= 0.05 page widths) is cut into one block per detection; tokens grounding left out are looked up in every word source | [keep] | business both-keys recall +2.0 dev / +1.0 held, nothing lower beyond noise on business, OmniDocBench and DocLayNet layout, olmOCR unchanged; 0 tables changed; no model call |  |
| 243 | 2026-11-13 | r3a-structure | `pnsplit alone` | regions switch pnsplit: a page number at the start or end of a running head/foot in the top 12% / bottom 14% becomes its own block when the page words show it clear of the rest (gap 0.03 with the word page, 0.06 bare number on a one-line block of 2+ words), on another printed line, or in its own furniture detection | [keep] | business both-keys recall +1.4 dev / +1.3 held, OmniDocBench layout recall +0.4 / +0.2, nothing lower beyond noise; 0 tables changed; no model call |  |
| 244 | 2026-11-13 | r3a-structure | `hftable alone` | regions switch hftable: a table of at most 4 short cells in the top/bottom band whose located cells all lie in page-header/page-footer detections, with no table detection, becomes one block per cell | [option] | fires on 1 page of 639 (2 tables, dev): business both-keys recall +0.6 dev, 0 held; 0 tables changed on RD-TableBench, olmOCR, OmniDocBench, DocLayNet, FUNSD. Right on the page it fires on, too rare to confirm on held |  |
| 245 | 2026-11-13 | r3a-structure | `prose alone (one-column table is prose)` | regions switch prose: a one-column table (every row one cell) without checkbox or blank, 40+ words mostly in lines of 8+ words with a sentence, where the layout model sees no table, becomes one block per printed line; types from the markup and the layout | [keep] | fires on 2 business pages (redline dev, civil cover sheet held): first-key recall +9.9 dev, both-keys recall +3.6 dev, held 0; 0 tables changed on RD-TableBench, olmOCR, OmniDocBench, DocLayNet, FUNSD (those sets have no one-column table); r-1 writes no one-column table at all |  |
| 246 | 2026-11-13 | r3a-structure | `hfdet+pnsplit+hftable+prose+lab=paren together (business only)` | the five switches together | [keep] | business both-keys recall +6.7 dev / +1.9 held, precision +1.3 / +0.1, first key recall +13.3 / +1.1, same type as r-1 +2.1 / +0.5; dev missed key blocks 122 -> 89 of 494 |  |
| 247 | 2026-11-13 | r3a-structure | `lab=cap alone` | a bold-line Section Header matched to a caption detection becomes Text | [drop] | 8 blocks on 5 pages: DocLayNet type recall +0.5 dev (noise 4.4), OmniDocBench precision 0 / +0.2 (noise 0.5), agreement with r-1 -0.2 / -0.3: nothing clears the noise floor; the OmniDocBench captions typed Section Header are section_header for the layout model too |  |
| 248 | 2026-11-13 | r3a-structure | `lab=list alone` | a one-line List Item matched to a section-header detection becomes Section Header | [drop] | 6 blocks: every measure that moves goes down (DocLayNet type recall -0.2 dev, OmniDocBench -0.1 held): sub-figure captions (a) (b) are section_header for the layout model |  |
| 249 | 2026-11-13 | r3a-structure | `lab=paren alone` | a Header made from Text by the position rule alone whose whole text is bracketed or parenthesised (a unit line over a table) is Text again | [keep] | 8 blocks on 7 pages: agreement with r-1 +0.5 dev / +0.1 held, first key precision and recall +0.6 dev, OmniDocBench +0.1 dev; nothing lower anywhere; all inside the noise floor of two reads, kept as part of the furniture set because it only undoes a position rule |  |
| 250 | 2026-11-14 | r3a-structure | `lab=long alone` | a Section Header that is a paragraph (40+ words with a sentence) becomes Text | [drop] | fires on 1 block of 349 pages and that one lowers DocLayNet held by 0.1 |  |
| 251 | 2026-11-14 | r3a-structure | `lab=fig alone` | 3+ short Text blocks inside one confident picture detection (labels read out of a diagram) are typed Figure | [option] | fires on 2 dev pages only (24 blocks): OmniDocBench type precision +0.9 dev, DocLayNet +2.4 dev, both inside dev noise (5.0 / 2.5); 0 held pages, so no confirmation; off |  |
| 252 | 2026-11-14 | r3a-structure | `recommended set (engine-final-3: hfdet,pnsplit,hftable,prose,lab=paren), whole devset, dev1 reads` | the five switches on top of engine-final-2, 639 pages | [keep] | nothing lower on any measure (official OmniDocBench text/table/order/formula measures, RD-TableBench, olmOCR, FUNSD unchanged; scores/r3a_final.json against scores/r3a_base.json); business both-keys recall +6.7 dev / +1.9 held, first key recall +13.3 / +1.1, same type as r-1 +2.1 / +0.5, OmniDocBench layout IoU +0.005 / +0.002; 0 tables changed outside business; 0 model calls |  |
| 253 | 2026-11-14 | r3b-cost | `tables all_if=header2 (table re-read only for tables whose page-read grid shows the sign)` | composed from r2a_combo (every table) and r2a_combo_noall (none) on tables, olm, business | [drop] | no gain over no re-read |  |
| 254 | 2026-11-14 | r3b-cost | `tables all_if=empty (table re-read only for tables whose page-read grid shows the sign)` | composed from r2a_combo (every table) and r2a_combo_noall (none) on tables, olm, business | [drop] | loses the olmOCR table-test gain and RD held -0.1 |  |
| 255 | 2026-11-14 | r3b-cost | `tables all_if=spans (table re-read only for tables whose page-read grid shows the sign)` | composed from r2a_combo (every table) and r2a_combo_noall (none) on tables, olm, business | [option] | half the re-reads; RD dev -1.3 against every table (noise 0.0), held level, olmOCR table tests kept; triggered pages wait about 10 s more (no prefetch) |  |
| 256 | 2026-11-14 | r3b-cost | `tables all_if=spans+header2 (table re-read only for tables whose page-read grid shows the sign)` | composed from r2a_combo (every table) and r2a_combo_noall (none) on tables, olm, business | [option] | same result as spans |  |
| 257 | 2026-11-14 | r3b-cost | `tables all_if=spans+header2+empty (table re-read only for tables whose page-read grid shows the sign)` | composed from r2a_combo (every table) and r2a_combo_noall (none) on tables, olm, business | [option] | 71% of the re-reads; RD dev -0.5 against every table, held level; triggered pages wait about 10 s more |  |
| 258 | 2026-11-14 | r3b-cost | `no zoom vote (read.zoom_vote=false)` | 96 pages with strip calls requested | [option] | every devset measure of business and olmOCR equal to engine-final-2 (totals 235 / 241, grids 100.0 / 97.3, olmOCR table tests 93.7 / 90.0, old_scans 50.0 / 43.1); but 8 of the 9 hand-verified digit corrections are gone; saves 0.0075 USD per scanned-table page |  |
| 259 | 2026-11-14 | r3b-cost | `zoom strips at low effort (read.zoom_effort=low)` | 30 business and olm pages with strips; 134 new strip reads | [drop] | 8 of 9 verified corrections (18 -> 16 lost) and one number changed against r-1 (37129 -> 37128); every devset measure of business and olmOCR equal to engine-final-2 (totals 235 / 241, grids 100.0 / 97.3, olmOCR table tests 93.7 / 90.0, old_scans 50.0 / 43.1) |  |
| 260 | 2026-11-14 | r3b-cost | `zoom strips at medium effort (read.zoom_effort=medium)` | 30 business and olm pages with strips; 138 new strip reads | [drop] | same loss and same wrong change as low effort; every devset measure of business and olmOCR equal to engine-final-2 (totals 235 / 241, grids 100.0 / 97.3, olmOCR table tests 93.7 / 90.0, old_scans 50.0 / 43.1) |  |
| 261 | 2026-11-14 | r3b-cost | `second zoomed pass only over disputed strips (read.zoom_second=disputed)` | 30 business and olm pages with strips; no new read | [keep] | 287 of 287 business and olmOCR responses identical to engine-final-2; 9 of 9 corrections; 19 strip calls fewer on the 33 scanned-table pages |  |
| 262 | 2026-11-14 | r3b-cost | `second zoomed pass only for digit disputes and only over those strips (read.zoom_second=digits+disputed)` | 30 business and olm pages with strips; no new read | [option] | 9 of 9 corrections; 1 response differs (the wrong word change Preliminary -> Prelimary is no longer made); not chosen because word corrections on other components would also stop |  |
| 263 | 2026-11-14 | r3b-cost | `verify grid repair tries the prefetched table read first (verify.checks grid=norm+pre+draft)` | 29 pages with verify calls | [option] | the table-only read passes the repair rule for 3 of 26 broken tables only; 2 responses differ with the same scores (RD-TableBench 89.8 / 91.8, grids 100.0 / 97.3, totals 235 / 241); 3 calls saved on 639 pages |  |
| 264 | 2026-11-14 | r3b-cost | `form region reads start with the page read (read.form_prefetch)` | 40 form pages (24 business, 16 FUNSD), DOCFORGE_NO_LIVE=1 | [keep] | 40 of 40 responses identical; model seconds a page waits: business form pages median 38.9 -> 26.7 s, FUNSD 15.8 -> 8.9 s; 7 of the started reads were not used, hence form_prefetch_min_area=0.05 in the final profile |  |
| 265 | 2026-11-14 | r3b-cost | `per-page spending cap (read.page_cap_usd / settings.page_cap_usd)` | 7 dense pages through the per-request settings field | [option] | cap 0.010: all 7 pages at or under 0.0096 USD (0.0064 to 0.0717 without); cap 0.005: 3 pages over only by their page read (recorded); skipped steps listed in usage.model_usage.spend_cap |  |
| 266 | 2026-11-14 | r3b-cost | `handwritten regions only to Sonnet` | analysis of the 45 routed pages, no model call (r3b/hw_regions.py) | [drop] | the layout model finds no region at all on 10 of the 15 routed business pages (handwritten ledgers) and 24 of 45 routed pages are 70% or more handwriting by region area: nothing to cut out |  |
| 267 | 2026-11-14 | r3b-cost | `Sonnet at lower effort on handwriting pages` |  | [not done] | stopped by the owner priorities before it ran; the devset has no handwriting truth beyond olmOCR old_scans (1 to 2 tests) |  |
| 268 | 2026-11-14 | r3a-structure | `recommended set (engine-final-3: hfdet,pnsplit,hftable,prose,lab=paren), whole devset, dev2 reads` | the five switches on top of engine-final-2, 639 pages, second independent read set | [keep] | confirms dev1: business both-keys recall +6.3 dev / +1.3 held, first key recall +13.3 / +1.1, same type as r-1 +1.7 / +0.6, OmniDocBench layout type recall +1.5 / +0.4; no measure lower (bus.key70_precision held -0.1, noise 6.7); RD-TableBench, olmOCR, FUNSD and the official OmniDocBench measures identical; hftable turns 2 running-head tables of one OmniDocBench page into Header/Footer blocks (table measures unchanged); 0 model calls |  |
| 269 | 2026-11-14 | r3a-structure | `hftable: verdict after the dev2 pass` | no code change; supersedes the verdict "option" of the line "hftable alone" | [keep] | fires on 1 business page on dev1 and on 1 business page + 1 OmniDocBench page (2 running-head tables) on dev2; right on all 3, no table measure moves on any set |  |
| 270 | 2026-11-14 | r3b-cost | `engine-final-3-cost (engine-final-2 + zoom_second=disputed + form_prefetch + prefetch_score=0.6;prefetch_area=0.02), whole devset, dev1 reads` | second zoomed pass only over disputed strips; form region reads started with the page read (regions of 5%+ of the page, not on turned pages); table reads started early only for regions with score 0.6+ and 2%+ of the page | [keep] | 639 of 639 responses identical to engine-final-2 (r2_final), so every measure is equal; 9 of 9 verified digit corrections; USD per page 0.0060 -> 0.0059, business 0.0119 -> 0.0116; model seconds a business page waits, median 33.9 -> 23.9 (forms 39.1 -> 26.8, FUNSD 15.9 -> 9.1) | dev bus.tables -36.0; dev dln.type_recall +14.7; held bus.tables -38.0; held dln.type_precision +16.2 |
| 271 | 2026-11-14 | r3b-cost | `engine-final-3-cost, whole devset, dev2 reads` | as above, second independent reads (Sonnet reads of dev1 reused as in round 2) | [keep] | 639 of 639 responses identical to engine-final-2 on the same reads (r2_d2); USD per page 0.0060 -> 0.0059, business 0.0119 -> 0.0116; business wait median 26.6 -> 18.4 s | dev bus.tables -39.0; dev dln.type_precision +12.7; held bus.tables -30.0; held omni.table_teds_page +20.1 |
| 272 | 2026-11-14 | r3b-cost | `engine-cheapest (one page read, every no-call piece of engine-final-2), business + tables + FUNSD, dev1 reads` | no strips, no handwriting reader, no verify re-reads, no table or form reads | [option] | exactly 1.00 model calls per page, DOCFORGE_NO_LIVE=1; business 0.0033 USD and 16.9 s median; both-keys type recall 75.3 / 72.6 (vanilla 63.0 / 66.1, engine-final-2 75.3 / 75.0), grids 90.8 / 88.4 (100.0 / 97.3), RD-TableBench 87.8 / 89.9 (89.8 / 91.8), FUNSD links 81.4 / 82.8 (86.6 / 84.1), totals 235 / 239 (235 / 241) | dev bus.tables -36.0; dev bus.key_recall +8.8; held bus.tables -38.0; held bus.matched -10.8 |

## Glossary

| **Reducto r-1** | Reducto's own document model, $0.01 a page; r-1 mini is its cheaper tier |
|---|---|
| **Plain Haiku / vanilla / model-only** | one Claude Haiku 5.5 read per page at high effort, nothing added |
| **Engine** | our layer around the model: layout model, OCR word positions, checks, repairs, routing |
| **RD-TableBench** | Reducto's 1,000-table benchmark and grader |
| **olmOCR-bench** | Allen AI's 1,403-page benchmark of pass/fail tests |
| **OmniDocBench** | a 1,651-page benchmark of text, tables, formulas, reading order and layout |
| **FUNSD** | 199 scanned forms with labelled question–answer links |
| **DocLayNet** | pages with human-labelled block types and boxes |
| **LongExtract** | 50 long documents with structured-extraction answer keys |
| **Dev / held** | the two halves of the development set: tune on dev, check on held |
| **Noise floor** | how much two identical runs differ; a smaller gain is not evidence |
| **Zoom vote** | enlarged strips of a scan read twice; a token changes only when both agree against the page read |
| **Effort** | Anthropic's setting for how much the model thinks before answering |

Generated from the project's reports and ledger on 14 November 2026. Product code: `reducto-test/product`, tags `final-round-1` to `final-round-4-citations`.
