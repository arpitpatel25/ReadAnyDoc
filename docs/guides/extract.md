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
out.report            # how each field was found: see "Field report" below
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

## How ReadAnyDoc makes the output fit

Every reply is checked against your schema. Unambiguous spellings are converted (`"$1,575.00"` to `1575.0`, `"yes"` to
`true`); anything that still does not fit is re-asked a bounded number of times and then set to null, never guessed.
Missing values are `null`, as in Reducto. When your schema fits the Anthropic API's structured-output limits, the API
is also told the reply's shape; large schemas with many optional fields work without it.

A reply that cannot be read (broken JSON, cut off, the wrong shape) is never used in part: it is repaired only when
the result fits the schema exactly, otherwise asked for again, then asked for in halves. A field that still cannot be
read is reported as unreadable, never as missing from the document.

## Field report

A bare value hides the cases where someone should look again: a contract that gives two prices, a value the document
never states, a date that could be read two ways. The response has a `field_report` next to the values (`out.report`
in Python), for any document and any schema:

```json
"platform_fee": {
  "value": 4500,
  "status": "conflict",
  "printed_as": "$4,500 / month",
  "candidates": [
    {"value": 4500, "source": {"page": 1, "block": "r2", "type": "Table", "text": "4,500"}},
    {"value": 4200, "source": {"page": 2, "block": "r5", "type": "Text", "text": "4,200"}, "note": "first year only"}
  ],
  "flags": ["currency"]
}
```

| `status` | Meaning |
|---|---|
| `found` | The value is on the page (a yes/no or a choice from your list: the cited block supports it) |
| `conflict` | The document gives different values for the field, or the value is crossed out. Every candidate is listed with its page; each was checked on the page |
| `inferred` | The document does not state the value; it was worked out or assumed |
| `not_in_document` | The document does not mention it |
| `blank` | The form has the field but it is empty |
| `not_applicable` | The field does not apply to this document |
| `unreadable` | The model's reply for this part could not be read; the value may be in the document |

`flags` mark conversions worth a check: `ambiguous_date` (03/04/2025), `parentheses` ((1,200) usually means minus),
`scale` (the page says "in thousands"), `percent_scaled` (10% returned as 0.1), `currency`, `struck_through` (the value
is crossed out on the page), `void_page` (the page is stamped VOID or CANCELLED). `notes` explain them, and carry a
condition on the value such as "first year only".

`field_report.lists` names lists with fewer items than the rows of the table they come from (total and tax rows are
not counted); such a list is asked for once more before it is reported. `field_report.pages.stamped` lists pages
stamped VOID, CANCELLED and similar. Crossed-out words are found on the page image and shown to the model as
`~~text~~`.

Conflicts, inferred values and the conversions above also appear in `warnings`. The report adds a few output tokens
and no extra calls (except the one re-ask for a short list). Turn it off with `settings.field_report: false`.

## Additions over Reducto

These are opt-in; a client that sends none of them behaves as on Reducto, plus the `warnings` list.

| Field | What it does |
|---|---|
| `warnings` (response) | One line per required field that came back empty, at any depth; conflicts, inferred values and conversions to check |
| `field_report` (response) | How each field was found (see above). On by default; `settings.field_report: false` turns it off |
| `instructions.examples` | Up to 5 example outputs, shown to the model as format examples. A returned value that equals an example and is not in the document is removed and reported |
| `settings.relevance_filter: true` | For documents of 4+ pages, one cheap call picks the pages that matter for your schema first. 80% cheaper with the same answers on an 86-page packet |
| `settings.request_pages: true` | The model may ask for more pages or search for a phrase (up to 3 rounds) before it answers. For cross-referenced contracts |

## Tips

- Name fields the way the document names them, and describe units and formats in the field description.
- Use `Optional[...]` for fields that may be absent; mark truly required fields as required so you get a warning
  when they are missing.
- For long lists (statements, logs), set `array_extract=True`.
