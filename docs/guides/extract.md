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

## Extras

All off by default; the response holds only what you ask for.

| Option | Python / HTTP setting | What you get |
|---|---|---|
| Review | `review=True` / `settings.review: true` | `review`: values a reader should check, found by code (no model call, no extra output): `not_found_on_page` (worked out, converted or assumed), `ambiguous_date`, `parentheses`, `scale` ("in thousands"), `percent_scaled`, `struck_through`, `void_page`, `filled_because_required` (see above), `list_may_be_incomplete` (fewer items than the table's rows with an amount), `segments_disagree`, `unreadable` |
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
