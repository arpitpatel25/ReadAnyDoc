# Python library

```python
import readanydoc
```

The library runs the same pipeline as the server, in your process. It covers parse and extract; split, classify and
edit are server features.

## `readanydoc.parse`

```python
readanydoc.parse(
    source,                      # path (str or Path) or bytes
    *,
    mode="engine",               # "engine" | "model-only"
    model="claude-haiku-5-5",    # page reader
    effort="high",
    model_options=None,          # dict added to the provider request
    handwriting_model="claude-sonnet-5-5",   # None = read handwriting like any page
    handwriting_effort=None,
    handwriting_options=None,
    pages=None,                  # list of 1-based page numbers
    citations=False,             # word and cell positions
    filename=None,               # needed with bytes
    options=None,                # any Reducto parse option (retrieval, formatting, settings, ...)
    timeout=None,                # seconds
) -> ParseResult
```

## `readanydoc.extract`

```python
readanydoc.extract(
    source,
    schema,                      # pydantic class or JSON Schema dict
    *,
    instructions=None,           # system prompt for the extraction model
    citations=True,
    array_extract=False,
    mode="engine",
    model="claude-haiku-5-5",
    effort="high",
    model_options=None,
    extract_model=None,          # default: model
    extract_effort=None,         # default: effort
    extract_options=None,
    handwriting_model="claude-sonnet-5-5",
    handwriting_effort=None,
    handwriting_options=None,
    pages=None,
    filename=None,
    settings=None,               # any further Reducto extract setting
    timeout=None,
) -> ExtractResult
```

## Result types

All are pydantic models.

| Type | Fields |
|---|---|
| `ParseResult` | `blocks: list[Block]`, `pages: int`, `usage: Usage`, `warnings: list[str]`, `raw: dict`; properties `markdown`, `tables` |
| `ExtractResult` | `data` (your class instance, or a dict if it did not validate), `citations: dict[str, list[Citation]]`, `usage`, `warnings`, `raw` |
| `Block` | `type`, `content`, `bbox: BoundingBox \| None`, `confidence: "high" \| "low" \| None` |
| `Citation` | `content`, `bbox`, `confidence` |
| `BoundingBox` | `left`, `top`, `width`, `height` (fractions of the page), `page` (from 1) |
| `Usage` | `calls`, `input_tokens`, `output_tokens`, `cost_usd` (None when a model has no known price), `seconds`, `by_model` |

## Errors

| Exception | When |
|---|---|
| `readanydoc.pipeline.PipelineTimeout` | the document took longer than `timeout` |
| `readanydoc.modelspec.ModelSpecError` | an unknown model name or a reserved request option |
| `FileNotFoundError` | the path does not exist |
| provider errors | wrong key, rate limits after retries, model not available |
