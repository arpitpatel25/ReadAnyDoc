# Citations and confidence

Every value ReadAnyDoc returns can say where it came from: the page, a box on that page, and the exact text that
was read there. This is what lets a person check an extracted value in one click.

## In Parse

Each block has a `bbox` (page, left, top, width, height as fractions of the page) and a `confidence`. With
citations on, text blocks also get word positions and tables get cell positions:

=== "Python"

    ```python
    result = readanydoc.parse("statement.pdf", citations=True)
    block = result.blocks[3]
    block.bbox           # BoundingBox(page=1, left=0.08, top=0.31, width=0.84, height=0.05)
    block.confidence     # "high" or "low"
    result.raw           # the full Reducto-shaped response, including word and cell citations
    ```

=== "HTTP"

    ```json
    {"input": "...", "settings": {"return_citations": true}}
    ```

    Text blocks carry `extra.word_citations`, tables carry `cell_citations`, in Reducto's shape.

## In Extract

Citations are on by default in the library. Every leaf value of your schema maps to its sources:

```python
out = readanydoc.extract("invoice.pdf", schema=Invoice)
out.citations["total"]            # [Citation(content="£1,575.00", bbox=..., confidence="high")]
out.citations["lines[0].amount"]  # paths use dots and [index]
```

Rules ReadAnyDoc follows:

- The cited text is the text that matched the value, including punctuation the value itself carries ("Inc.").
- One box per printed word; a value spread over several words gets the boxes that hold it.
- A value whose source block does not contain it gets a block-level citation marked `low`.
- A value with no source that can be located gets an empty list. ReadAnyDoc never invents a position.

## Confidence

Confidence is a tier, `high` or `low`, from how a value was found: agreement between independent reads, a match in
the PDF's own text, a checked total. Use it to decide what a person should look at first.

For extracted values, `granular_confidence.extract_confidence` is also measured: for the common cases it is the
share of such values that were right on our benchmarks (16 10-Q filings checked against their XBRL facts, 100 CUAD
contracts). A number that disagrees with another value for the same thing (same metric, period and segment) is
marked `low`: on 10-Qs those are right about 79% of the time, against 94% for values marked `high`. It is a guide
measured on these document kinds, not a guarantee for yours.
