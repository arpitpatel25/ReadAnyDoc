How we built it, 11 to 14 November 2026

# We tried to replace a purpose-built document model with an off-the-shelf one. Here is everything that happened.

Four days, more than 5,300 benchmark pages, 272 experiments and about $165 in total. Most of our ideas failed. The biggest surprise came at the very end. What we ended up with is an open-source document API that works as a drop-in for Reducto, runs on a general model you can swap out, and costs a half to a ninth of the price.
Chapter 1

## The bet

Reducto is one of the best document parsers there is. It turns PDFs and scans into structured text, tables and fields, and its core is r-1, a model it trained itself, at $0.01 a page.

Our bet went the other way. General-purpose models like Claude Haiku keep getting better without anyone training them for documents. If you leave the model untouched and build a good layer around it, you should be able to match a specialised model now and overtake it later, because every new model release is a free upgrade.

We set three rules before writing any code:

- **Don't touch the model.** No training and no fine-tuning; it gets used as it is.
- **Never make it worse.** Any addition has to beat the plain model or it goes.
- **It has to be a real product.** Every Reducto feature behind the same API, so switching means changing one URL. Accuracy first, then cost, then speed.
Chapter 2

## We measured before we built

Before writing any code we put plain Haiku up against Reducto on public benchmarks that are graded by code, not by opinion.
**Expected**Reducto's trained model would be well ahead, and we would spend weeks catching up. **Got**Plain Haiku was already level. On 140 olmOCR pages it scored 83.6 against r-1's 79.4, and it read multi-column pages in the right order far more often (88.8 against 69.2).  The reading itself was not the problem. That changed everything we did next.

We later found that this first sample flattered Haiku. Chapter 7 is about that.
Chapter 3

## Our first layer made the model worse

We built a layer that read each page twice, compared the two reads and repaired the differences. Then we scored it.
**Expected**Two reads and a referee would beat one read. **Got**The layer scored *below* plain Haiku (7.56 against 8.01 out of 10). Adding Sonnet as a referee lifted it slightly above, but at **$0.09 a page, nine times Reducto's price**. **Learned**Asking the same model twice repeats the same mistakes, and trying to fix a model's output with its own second opinion mostly just moves errors around. We threw the whole approach away.  Meanwhile, the productThe full product was built that same afternoon: all 20 Reducto API routes, background jobs and webhooks, Extract / Split / Classify / Edit, and citations that point to the exact place on the page for every value. **Reducto's own Python SDK works against our server unchanged; you only change the URL.**   Chapter 4

## We stopped trusting our own numbers

Three discoveries in a row made us doubt every measurement we had taken.
**Cost**We had been measuring cost through a command-line tool that adds about 33,000 tokens to every call. Through the real API, Haiku cost **a quarter** of what we thought: $0.0008 a page instead of $0.006. **Judge**We had been scoring pages with an AI judge. Checked by hand, it misread 27,174,839 as 27,174,339 and gave the page 10/10, and it called a wrong medical ID column "minor". The same output could move 0.3 points between runs. **Credit**Re-run fairly through the API, the engine and plain Haiku tied (8.22 each). Most of our "lead" over Reducto had come from the model, not from our layer.

So we rebuilt how we measure. We put together a 642-item test set from seven public sources, every item scored by its official grader or an answer key, with no AI judging anywhere. The set is split by hash into a half we tune on and a half we never look at. We ran the plain model twice to measure the **noise floor**: on some measures two identical runs differ by up to 14 points, so a gain smaller than that is not a gain.
Every idea after this became a switch, off by default, scored on accuracy, cost and speed together, and kept only if it was never worse than the plain model.   Chapter 5

## 272 ideas, and most of them failed

Once we accepted that the model reads well, there was a better question to ask: *what does the model not give you?* We tested 272 ideas against that question. 75 were dropped outright and 55 became options. We came at every part of the problem from several directions:

**How to read the page.** Small open models (GLM-OCR, PaddleOCR) on a rented GPU, alone and as "small model first, Haiku for hard pages": cheap, but never as good as Haiku. Free OCR as the base with Haiku only patching it: 5.65 against 7.71. Correcting Haiku from the PDF's hidden text layer: only 2 of 17 errors were misread characters. Reading the page region by region and stitching it together: the best layout numbers, but tiny text fell 11.8 points. Re-reading every table on its own: a gain on the sample, nothing on the full benchmark. Rendering at exactly the model's native image size: tiny text 84 → 95, kept everywhere.

**How to find the layout.** Two open layout models head to head (heron 94% against PP-DocLayout 74%). Block types taken from layout with and without guards. Splitting blocks along layout regions, which cut form fields apart. Headers found by position or type size, which was worse. Merging tables across pages, which joined tables that should stay separate. And an offline probe that showed splitting form tables could never close the gap to Reducto.

**How to check the output.** Printed totals checked against the rows, with zoomed re-reads of the cells that don't add up. Completeness checks for text the read might have missed. Flags on doubtful tables (flagged tables really were worse: 79.8 against 89). Table repair by a fresh read (15 of 47 fixed) against repair that must keep every cell's text exactly (35 of 47). A cheap disagreement check that caught 77% of failures while looking at only 28% of pages.

**How to make it cheaper and faster.** Batch pricing (half price, but 80 times slower). 16 pages read in parallel (a document from 43 s to 24 s). A spending cap per page. Lower effort only on easy pages. Sending only the handwritten region to Sonnet (the layout model could not find it on 10 of 15 ledgers). A second zoom pass only where the first one disagreed (the same output with fewer calls).

These failures taught us the most:

- ✗ **Let Haiku point at the PDF's own text instead of retyping it.** It scored 7.30 against 8.14. The text stored inside PDFs lies: missing spaces, doubled letters, watermarks mixed in. Haiku almost never misreads digital text (1 serious error in 74 pages), so this fixed a problem it doesn't have.
- ✗ **Send smaller images to save money.** It caused 19 serious table errors.
- ✗ **Let the model think less.** It was 35% cheaper, but broke table grids by 8 points.
- ✗ **Zoom into every checkbox.** The page read was already 94–99.5% right, and the zoomed reads fixed one checkbox and broke two.
- ✗ **Turn sideways pages upright.** It passed 128 tests, then turned five upright handwritten letters upside down. It went back in only with a guard.

The ideas that worked all had one thing in common. None of them tried to read better than the model; each one supplied something the model doesn't:

- ✓  **A small open layout model on the CPU** to say where each block sits and what type it is.
- ✓  **Table grid repair** that only fixes structure and is rejected if a single cell's text changes. Consistent grids went from 87.6% to 98.6%.
- ✓  **A zoom vote on scanned digits:** a digit changes only when two zoomed reads agree against the page read.
- ✓  **Values read straight from fillable PDFs:** typed fields went from 2% to 100% correct, with no model call at all.
- ✓  **Sonnet for handwriting only,** with a guard so printed pages stay on Haiku.
The model reads the page. Our engine adds the structure around it: positions, block types, forms and grids.

On our 100-table sample the engine now beat Reducto, 89.8 / 91.8 against 89.2 / 91.5. We thought tables were won.
Chapter 6

## Meanwhile, making it usable in real work

While the experiments ran, we kept building the product around what teams actually ask for. One team building contract extraction in-house told us their hard requirements: citations with page positions so a person can check every value, a confidence signal, long contracts with amendments attached, scans and handwriting, and their own large schemas.

| Need | What we built |
|---|---|
| Switch without rewriting anything | Reducto's API shape, so its own SDK works unchanged |
| Proof for the reviewer | Every extracted value cited with its exact words and box on the page; matched Reducto's own citations on a real certificate |
| Large custom schemas | Our own check-and-repair loop. We tested Anthropic's built-in structured output: it caps a request at 24 optional fields and fails on complex schemas. |
| Forms, scans, handwriting, long documents | Form values, OCR word positions, a handwriting route, long documents processed in parts and merged |
| Choose your own price | Two modes: the full engine, or the plain model alone |
| Next year's model | The model is a setting. When a better one ships, you change one line. |
| Data stays with you | Open source, self-hosted, your own Anthropic account, no middleman |

Extract, Split, Classify and Edit were each tested against Reducto's real responses on the same files, and ours matched on all of them.
Chapter 7

## Then the full benchmarks overturned our results

A sample is not proof, so we ran all three systems on the **complete** public benchmarks: 1,000 tables, 1,403 olmOCR pages, 1,651 OmniDocBench pages, 199 scanned forms and 10 long documents. More than 5,300 pages, every one sent to all three systems live.
**Expected**The engine's table lead would hold. **Got**On all 1,000 tables: Reducto 88.85, plain Haiku 88.20, our engine 88.21. **Our table work added nothing.** The sample gains had partly been tuning to those 100 tables. We fixed what we found (a crash, and rules that broke real tables), re-ran all 1,000, and got 88.17. Still level.

It was a hard result to take. But look at what it says.
Plain Haiku, untouched, ties Reducto's purpose-built model on its own benchmark, at an eighth of the price. That is the bet from Chapter 1, confirmed.

And the engine's real job, adding structure on top of the plain model, **held up**, including on the thousands of pages we had never seen. The same Haiku reads the page in both columns below; the only difference is our engine:

| What the engine adds on top of plain Haiku | Plain Haiku | Our engine | Engine gain | Reducto r-1 |
|---|---|---|---|---|
| Layout: block types right (OmniDocBench) | 55.6 | 78.3 | **+22.7** | 84.6 |
| Layout: boxes in the right place | 26.5 | 61.1 | **+34.6** | 67.2 |
| Form question paired with its answer (FUNSD) | 71.6 | 77.4 | **+5.8** | 78.6 |
| Headers and footers kept apart (olmOCR) | 70.4 | 81.2 | **+10.8** | 90.1 |
| olmOCR overall | 82.2 | 84.2 | **+2.0** | 80.9 |

On structure, where plain Haiku was weakest, the engine closes most of the gap to Reducto: about four fifths on layout and forms, and more than half on headers and footers. Reducto is still ahead there, and we say so. On general pages the engine goes past it. All of this costs about half of Reducto's price.
And because the engine never touches the model, it gets better on its own: every improvement in the next Haiku arrives with the structure work already on top.   Chapter 8

## Where we landed

| Complete public benchmarks | Reducto r-1 | Plain Haiku | Our engine |
|---|---|---|---|
| Tables (RD-TableBench, 1,000) | **88.9** | 88.2 | 88.2 |
| General pages (olmOCR, 1,403) | 80.9 | 82.2 | **84.2** |
| Text error, lower is better (OmniDocBench, 1,651) | 0.095 | **0.081** | **0.081** |
| Layout block types (OmniDocBench) | **84.6** | 55.6 | 78.3 |
| Forms (FUNSD, 199) | **78.6** | 71.6 | 77.4 |
| Long-document rows found (10 documents) | 53.9 | 75.4 | **75.9** |
| **Price per page** | $0.010 | **$0.001–0.002** | $0.003–0.005 |

- **The thesis holds.** Plain Haiku, untouched, matches Reducto's purpose-built r-1 on tables and beats it on general pages and text accuracy, for **a fifth to a ninth of the price**. For many documents, the plain model alone is enough.
- **The engine beats plain Haiku wherever structure matters:** +23 to +35 on layout, +6 on forms, +11 on headers and footers, +2 on general pages. It costs about half Reducto's price, and it improves as the model improves.
- **Reducto still wins** on fine layout, formulas and headers and footers, and it is several times faster. We say so.
- **The product is complete:** the same API, every task, citations, you choose the model, and it's open source.
We didn't build a better model. We built everything around one, so you can swap in the next one.

Every number here comes from official graders on public data, run live on all three systems. The full record, with all 28 stages, every benchmark breakdown and all 272 experiments with the reason each was kept or dropped, is in journey.html.
