# Extract

Extract fills in a schema you define: a pydantic class or a JSON Schema. You get your own type back, with a citation
for every value.

## Python

```python
from typing import Optional
from pydantic import BaseModel
import readanydoc

class Party(BaseModel):
    name: str
    role: str

class Contract(BaseModel):
    title: str
    effective_date: Optional[str]
    governing_law: Optional[str]
    parties: list[Party]
    total_value: Optional[float]

out = readanydoc.extract(
    "contract.pdf",
    schema=Contract,
    instructions="Dates as YYYY-MM-DD. Amounts as numbers without currency signs.",
    array_extract=False,          # True for long documents with long lists: read in parts, merge the lists
    extract_model="claude-sonnet-5-5",
)

out.data              # a Contract instance
out.citations         # {"title": [...], "parties[0].name": [...], ...}
out.usage.cost_usd
out.warnings          # e.g. "required field 'governing_law' was not found in the document"

# opt-in extras (see "Extras" below)
out = readanydoc.extract("contract.pdf", schema=Contract,
                         review=True,          # values to check, found by code: no model call
                         alternatives=True,    # other values the document gives for a field (a special term)
                         derived_values=False) # printed values only, never one worked out or assumed
out.review            # [{"field": "effective_date", "flag": "ambiguous_date", "detail": "printed as 03/04/2025 ..."}]
out.alternatives      # {"total_value": [{"value": "$4,500", "note": "fee table; special term 1 gives $4,200"}]}
```

| Argument | Meaning |
|---|---|
| `schema` | pydantic class or JSON Schema dict |
| `instructions` | system prompt for the extraction model |
| `citations` | default `True` |
| `array_extract` | process long documents in parts and merge their arrays |
| `mode`, `model`, `effort`, `model_options` | how pages are read (see [Choosing models](../concepts/models.md)) |
| `extract_model`, `extract_effort`, `extract_options` | the model that fills the schema; default the reader's model |
| `pages`, `filename`, `timeout` | as in Parse |
| `review`, `alternatives`, `derived_values` | opt-in extras, see below |
| `fill_required` | which required fields always get a value, see below |
| `settings` | any further Reducto extract setting |

## HTTP

```bash
curl -s -X POST http://localhost:8000/extract -H "content-type: application/json" -d '{
  "input": "reducto://...pdf",
  "instructions": {
    "schema": {"type": "object", "properties": {"total": {"type": "number"}}, "required": ["total"]},
    "system_prompt": "Be precise."
  },
  "settings": {"citations": {"enabled": true}, "array_extract": false}
}'
```

`input` can also be `jobid://<parse job id>` to reuse an earlier parse without reading the document again.

## What the model writes, and what it costs

The model writes your schema's fields and nothing else: no source list, no notes per value. Citations are found by
code, by looking each value up on the parsed pages (its exact text, then the other ways a number or a date is printed:
`$4,200.00`, `03/04/2025`, `March 4, 2025`). When a value is printed in several places, the one next to the other
values of its object (its table row, its address) is cited; for that, each object in the reply carries one short
`_block` label, which is removed before you see the result. A value that is not in the document gets no citation.

## How ReadAnyDoc makes the output fit

Your schema is rewritten in code before the call so that the Anthropic API can enforce it (structured outputs), for
any size of schema: every field is required, an empty string stands for "not in the document" (so nothing is ever
forced to hold a guess), and numbers and yes/no answers are written as text. The answer is turned back into your
types (`"4,200"` to `4200`, `"yes"` to `true`, `""` to `null`) and checked against your schema, patterns and ranges
included; what breaks it is asked for again a bounded number of times, then set to null. You only ever see your own
schema. A schema too big for one call (more than 60 fields) is split into groups of fields before any call; the
groups run in parallel and each one sees the whole document.

A reply that cannot be read (broken, or cut off at the length limit) is asked for in parts: lists apart from the
other fields first, then halves of the fields, with all pages in view, so a price on page 1 is still read against a
special term on page 2. Only a single long list is split by pages. A field whose reply still cannot be read is named
in a warning; a field that a later call filled is not.

## Required fields: `fill_required`

A field your schema requires (and does not allow null) can still be missing from a document. What happens then is
up to you, per field:

| `fill_required` | What always gets a value | When the document does not give it |
|---|---|---|
| `"choices"` (default) | required yes/no and enum fields | the model picks the answer the document's terms mean ("Net 30": last day of month = `false`); numbers and text stay `null` |
| `True` | also every required number and text field | the model writes its best value, which may be a guess |
| a list of field paths, e.g. `["customer.tax_id", "line_items[].end_date"]` | the choices, and these fields (required or not) | as for `True`, for these fields only |
| `False` | nothing | `null` (each missing required field is listed in `warnings`) |

Forcing numbers or text makes the model guess when the document does not give a value, and a wrong guess looks like
a real value. On a pack of 5 order forms, `True` filled end dates for one-time fees that the answer key leaves
empty (98.3% of fields right, against 100% with the default). Turn it on only for fields where a best guess is
acceptable. With `review=True`, a filled value that is not printed in the document is flagged
`filled_because_required`. An optional section the document does not have stays `null`, and no list item is made
up to hold forced answers. CLI: `--fill-required` (every field), `--fill-required customer.tax_id` (those
fields), `--fill-required none`.

## Values the output left out

**The issue.** The model sometimes leaves out something the document clearly prints. A user testing on their own
order forms saw it: the line "Billing Contact: Priya Natarajan" was printed, but `contacts` came back `[]`. It does
not happen every run (in another run a different form lost its contact), and nothing said so. For billing that is
the worst kind of miss: the output looks complete, so nobody knows to check it. The parser had read the line; the
model filling the schema skipped it.

**What we do about it.** With `review=True`, code compares what the document prints with what the output holds,
after extraction, with no model call:

| Flag | When | Example |
|---|---|---|
| `printed_but_empty` | a field came back empty, and the document prints a label and value that no output value took, whose label matches the field (its name, title, description, the fields of a list's items, or the value's shape: an email for an email field, a date for a date field) | `customer.contacts`: "Billing Contact: Jordan Blake, Head of Procurement" is printed on page 1, and customer.contacts is empty |
| `row_not_extracted` | a list took some rows of a table, and another item row of that table is in no output value | `items`: table row "Shipping \| $75.00" (page 1) is not in the output, though 2 other rows of its table are |

A printed value counts as taken only if an output value matches it (as text, number or date), not because its box
was cited: a contact whose email is also the customer's email is still caught. Labels whose words name something
else do not match a field ("Credit Date" is not the due date). Headers, footers, total rows and table header rows
are never flagged. The rules use only what the parser gives every document (form fields, tables) and your schema;
there is no list of words for any document type.

**How well it works.** Measured on 47 documents of five kinds (order forms, invoices, receipts, resumes, research
papers), with misses planted on purpose in correct outputs (a list emptied, one item removed, one printed field
blanked) and on fresh documents not used to build the rules:

| | Order forms | Invoices | Receipts |
|---|---|---|---|
| Emptied list caught | 100% (9 of 9) | 84% (21 of 25) | 67% (2 of 3) |
| Removed list item caught | - | 100% (23 of 23) | 67% (2 of 3) |
| Blanked field caught | 95% (39 of 41) | 89% (56 of 63) | 80% (4 of 5) |
| Flags per document on real outputs | 0.1 | 0.1 | 0 |

On the real outputs it caught the model's own misses (a dropped contact; a resume with 10 of its 18 jobs), and
raised 2 borderline flags in 47 documents (a date of the second invoice in one file, a payment row on a supplier
statement).

**What it does not catch.** No flag is not a guarantee that nothing is missing.

- A label in words your schema does not use: "Provider" printed, the field called `business_entity.name`. Adding
  the document's wording to the field's description ("the seller; printed as Provider") lets the check match it.
- A whole list lost from a table without a header row (most receipts).
- A value partly taken: an address whose first line is missing while its city and postcode are there.

**A stronger fix we did not build.** A second model call could read the document and the answer and list what was
left out. It would understand meaning, so it would catch the cases above, but it is a model too (it can miss), and
it costs one more call on every document, nearly all of which miss nothing. A cheaper version would call the model
only for a flagged field (the document is already cached), to fill it rather than only flag it. Neither is built:
the flag tells a reviewer exactly where to look, at no cost.

## Extras

All off by default; the response holds only what you ask for.

| Option | Python / HTTP setting | What you get |
|---|---|---|
| Review | `review=True` / `settings.review: true` | `review`: values a reader should check, found by code (no model call, no extra output): `not_found_on_page` (worked out, converted or assumed), `ambiguous_date`, `parentheses`, `scale` ("in thousands"), `percent_scaled`, `struck_through`, `void_page`, `filled_because_required` (see above), `printed_but_empty` and `row_not_extracted` (see "Values the output left out" below), `segments_disagree`, `unreadable` |
| Alternatives | `alternatives=True` or a list of field paths / `settings.alternatives` | `alternatives`: other values the document gives for a field (a special term, an amendment, a crossed-out or second total), each with a note and, with citations on, where it is printed. The model writes them in one extra list, only when there are any |
| Printed values only | `derived_values=False` / `settings.derived_values: false` | The model is told not to work out or assume values (an end date from a term, a country from an address); such fields come back null |

You can also ask for any of these in your own schema: a field `price_alternatives` of type array, for example.

## Additions over Reducto

These are opt-in; a client that sends none of them behaves as on Reducto, plus the `warnings` list.

| Field | What it does |
|---|---|
| `warnings` (response) | One line per required field that came back empty, at any depth, and parts whose reply could not be read |
| `review`, `alternatives`, `derived_values` (settings) | See "Extras" above |
| `instructions.examples` | Up to 5 example outputs, shown to the model as format examples. A returned value that equals an example and is not in the document is removed and reported |
| `settings.relevance_filter: true` | For documents of 4+ pages, one cheap call picks the pages that matter for your schema first. 80% cheaper with the same answers on an 86-page packet |
| `settings.request_pages: true` | The model may ask for more pages or search for a phrase (up to 3 rounds) before it answers. For cross-referenced contracts |

## Tips

- Name fields the way the document names them, and describe units and formats in the field description.
- Use `Optional[...]` for fields that may be absent; mark truly required fields as required so you get a warning
  when they are missing.
- For long lists (statements, logs), set `array_extract=True`.
