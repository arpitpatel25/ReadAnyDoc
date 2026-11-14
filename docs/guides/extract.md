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
Missing values are `null`, as in Reducto. Large schemas with many optional fields are fine: ReadAnyDoc does not rely
on the provider's structured-output mode, which limits schema size.

## Additions over Reducto

These are opt-in; a client that sends none of them behaves as on Reducto, plus the `warnings` list.

| Field | What it does |
|---|---|
| `warnings` (response) | One line per required field that came back empty, at any depth |
| `instructions.examples` | Up to 5 example outputs, shown to the model as format examples. A returned value that equals an example and is not in the document is removed and reported |
| `settings.relevance_filter: true` | For documents of 4+ pages, one cheap call picks the pages that matter for your schema first. 80% cheaper with the same answers on an 86-page packet |
| `settings.request_pages: true` | The model may ask for more pages or search for a phrase (up to 3 rounds) before it answers. For cross-referenced contracts |

## Tips

- Name fields the way the document names them, and describe units and formats in the field description.
- Use `Optional[...]` for fields that may be absent; mark truly required fields as required so you get a warning
  when they are missing.
- For long lists (statements, logs), set `array_extract=True`.
