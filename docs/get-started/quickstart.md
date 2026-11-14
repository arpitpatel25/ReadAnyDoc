# Quickstart (Python)

Parse your first document and extract structured data with ReadAnyDoc in about five minutes. No server is needed.

## Prerequisites

- Python 3.12
- An Anthropic API key ([console.anthropic.com](https://console.anthropic.com)). For OpenAI models, an OpenAI key.
- Optional: [Tesseract](https://github.com/tesseract-ocr/tesseract) (`brew install tesseract` or
  `apt install tesseract-ocr`). The engine uses it for word positions on scanned pages when it is installed.

## 1. Install

=== "Engine (recommended)"

    ```bash
    pip install "readanydoc[engine]"
    ```

    Adds the small layout and OCR models that run on your CPU.

=== "Model only"

    ```bash
    pip install readanydoc
    ```

    No local models. Use `mode="model-only"`.

=== "uv"

    ```bash
    uv add "readanydoc[engine]"
    ```

Set your key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

!!! note "The first run downloads models"
    The first engine parse downloads the layout and OCR weights (100 to 200 MB, Apache-2.0) into
    `~/.cache/readanydoc`, so it takes a few minutes longer. ReadAnyDoc prints a line when it starts. Later runs
    start straight away.

## 2. Parse a document

```python
import readanydoc

result = readanydoc.parse("invoice.pdf")

print(result.markdown)            # the whole document as Markdown
for block in result.blocks:       # typed blocks with positions
    print(block.type, block.bbox, block.content[:60])

print(result.usage.cost_usd)      # what the model calls cost
print(result.warnings)            # anything that went wrong on a page
```

`parse` accepts a path, or `bytes` together with `filename=`. See [Parse](../guides/parse.md) for every option.

## 3. Extract into your own types

```python
from typing import Optional
from pydantic import BaseModel
import readanydoc

class Line(BaseModel):
    description: str
    amount: float

class Invoice(BaseModel):
    number: str
    issued: Optional[str]
    total: float
    lines: list[Line]

out = readanydoc.extract("invoice.pdf", schema=Invoice)

out.data                     # an Invoice instance
out.data.total               # 1575.0
out.citations["total"]       # [Citation(content="£1,575.00", bbox=BoundingBox(page=1, left=0.81, ...))]
```

A JSON Schema `dict` works as well as a pydantic class. If the result does not validate against your class,
`out.data` is a plain `dict` and `out.warnings` says why.

## 4. Choose the mode and models (optional)

```python
readanydoc.parse("scan.pdf", mode="model-only")                    # cheapest, fastest
readanydoc.parse("scan.pdf", model="claude-sonnet-5-5")            # a stronger reader
readanydoc.extract("contract.pdf", schema=Terms,
                   extract_model="claude-sonnet-5-5", extract_effort="high")
```

See [Modes](../concepts/modes.md) and [Choosing models](../concepts/models.md).

## 5. Use the command line (optional)

```bash
readanydoc parse invoice.pdf --markdown
readanydoc extract invoice.pdf --schema invoice.schema.json
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `authentication_error` or 401 from the provider | `ANTHROPIC_API_KEY` is missing or wrong in the shell that runs Python |
| A warning that the layout model is not installed | Install the engine extra: `pip install "readanydoc[engine]"` |
| The first parse is slow | Model weights are downloading once; later runs are normal speed |
| A page came back empty with a warning about content filtering | The model provider declined to return that page's text. Try another model for that document |

## What's next

- [Parse](../guides/parse.md), [Extract](../guides/extract.md): every option.
- [Quickstart (server)](server-quickstart.md): the Reducto-compatible API.
- [Benchmarks](../resources/benchmarks.md): how it compares with Reducto.
