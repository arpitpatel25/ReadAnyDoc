# Why ReadAnyDoc exists, and how it was built

## The bet

Document parsing companies train their own models. Reducto, the best known, built r-1 and sells it at $0.01 a
page. Their product is good, and it rests on the idea that reading documents well needs a model made for it.

We made the opposite bet. General-purpose vision models (Claude, GPT and the rest) get better every few months,
for free, for everyone. A specialised model has to be retrained to keep up. So if a general model, wrapped in a
good enough layer of software, can match a specialised one today, it should pull ahead over time, at a lower
price, and anyone should be able to run it.

That gave us two rules before writing any code. The product could not be a toy: every feature Reducto has, the
same API, switching by changing one URL. And it could never make the model worse: nothing the layer adds may lower
what the plain model reads on its own.

## The first surprise: the model was already good at reading

The first measurements were humbling, in a useful way. Plain Claude Haiku 5.5, one read per page, already read
pages about as well as Reducto's own model, and sometimes better. On digital pages it made one serious text error
in 74 pages. It cost a fifth to a ninth of Reducto's price.

So reading was not the problem. What plain Haiku lacked was structure: where each block sits on the page, what
type it is, which answer on a form belongs to which question, which line is a running header and should be left
out, and where each value came from. Those are exactly what a product needs and what a model alone does not
return. That is where the work went.

## Most clever ideas failed

We tried 272 things and wrote every one down. Most did nothing or made things worse.

- Letting the model point at the PDF's own text instead of retyping it lost: the text layer itself lies (missing
  spaces, doubled letters, watermarks mixed in).
- Asking the model twice and voting did not fix its mistakes: two reads by the same model repeat the same errors.
  What worked was a *different view* of the page, zoomed strips, where a character changes only when two zoomed
  reads agree against the first read.
- Making the model write a compact format, or sending smaller images to save money, lowered accuracy. Fighting the
  model's natural way of answering costs more than it saves.
- Rules that helped one kind of page hurt another (form rules flattening real tables, rotation turning handwriting
  upside down). Every rule we kept carries a guard.

## The hardest lesson: small samples lie

On 100 tables, after a lot of tuning, our engine was ahead of Reducto. We were close to announcing it. Then we ran
all 1,000 tables of the benchmark: level with plain Haiku, slightly behind Reducto. The gains had been partly
tuning to those 100 tables.

We had also been using an AI judge to score pages, and a hand check caught it giving 10/10 to a page with a wrong
number. So we stopped. From then on every number came from each benchmark's own grader, on the complete
benchmark, with the pages we had developed on reported separately. The results in [BENCHMARKS.md](../BENCHMARKS.md)
are those runs: about 4,250 pages and 10 long documents, Reducto, plain Haiku and our engine on the same files.

## Where it landed

- Plain Haiku (`model-only`) beats Reducto on text and matches it on tables, for a fraction of the price.
- The engine closes most of the structure gap: page layout +23 to +35 points over plain Haiku, form question and
  answer pairs +6, headers and footers +11, and the best olmOCR-bench score of the three. About half Reducto's price.
- Reducto is still ahead on page layout (by about 6 points), formulas and header removal, and it is much faster.

We publish those losses next to the wins because the point of the project is that you can check every number.

## What we chose to build

The lesson of all this was that the value is not one clever trick in the reading. It is the whole product around
the model: page layout, form fields, table checks, citations down to the word, extraction into your own types,
long documents in parts, async jobs, webhooks, the same API as Reducto, and every model and setting yours to
choose. A team building document extraction in-house gets the first 80% from a prompt; the costly part is what
they discover afterwards (the scanned contract, the 80-page agreement, the table across pages, "where did this
number come from?"). That accumulated handling is what ReadAnyDoc gives away.

And because the model is a setting, not the product, ReadAnyDoc gets better whenever the models do.

The full record, stage by stage, with every number and every dead end: [docs/research/journey.md](research/journey.md).
