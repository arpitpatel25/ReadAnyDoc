# Choosing models

Every model the pipeline uses is your choice, with its effort level and any further request options. There are
three roles:

| Role | Default | Python argument | Server setting |
|---|---|---|---|
| Page reader | Claude Haiku 5.5, high effort | `model=`, `effort=`, `model_options=` | `READANYDOC_READER` |
| Handwritten pages (engine only) | Claude Sonnet 5.5 | `handwriting_model=`, `handwriting_effort=`, `handwriting_options=` | `READANYDOC_HANDWRITING_READER` |
| Extraction (fills your schema) | the page reader's model | `extract_model=`, `extract_effort=`, `extract_options=` | `READANYDOC_TASK_LLM` |

!!! info "What is benchmarked"
    The published numbers use Claude Haiku 5.5 (Sonnet 5.5 for handwriting). Other models work; their accuracy has
    not been measured here.

## Providers

| You write | Provider | Key |
|---|---|---|
| `claude-haiku-5-5`, `claude-sonnet-5-5`, any `claude-*` | Anthropic API | `ANTHROPIC_API_KEY` |
| `gpt-5`, any `gpt-*`, `o3`, `o4-*` | OpenAI API | `OPENAI_API_KEY` (and `OPENAI_BASE_URL` for a proxy or gateway) |
| `openai_compatible:<model>@<base_url>` | Any OpenAI-compatible server: vLLM, SGLang, Ollama, LM Studio, a hosted gateway | none, or set by the server |
| `<adapter>:<model>` | Explicit adapter, see [Model specs](../reference/model-specs.md) | as above |

## Examples

```python
import readanydoc

# a stronger page reader
readanydoc.parse("scan.pdf", model="claude-sonnet-5-5", effort="high")

# extra request fields, sent to the provider as given
readanydoc.parse("long.pdf", model_options={"max_tokens": 32000})

# handwriting read like any other page
readanydoc.parse("form.pdf", handwriting_model=None)

# a cheap reader, a strong extractor
readanydoc.extract("contract.pdf", schema=Terms,
                   model="claude-haiku-5-5",
                   extract_model="claude-sonnet-5-5", extract_effort="high")

# OpenAI
readanydoc.parse("doc.pdf", model="gpt-5", effort="medium")

# your own model server, nothing leaves your network
readanydoc.parse("doc.pdf", mode="model-only",
                 model="openai_compatible:Qwen/Qwen2.5-VL-7B-Instruct@http://localhost:8001/v1")
```

On the server, use the same specs in environment variables:

```bash
READANYDOC_READER="anthropic_api:claude-sonnet-5-5#high" \
READANYDOC_TASK_LLM="openai:gpt-5#medium" \
readanydoc serve
```

## Effort

`effort` is passed to providers that support it: Anthropic's effort setting and OpenAI's `reasoning_effort`.
Higher effort is slower and costs more output tokens. On our measurements high effort was worth it for page reading;
lower effort was tried and dropped. For `openai_compatible` servers effort is ignored.

## Request options

`model_options`, `handwriting_options` and `extract_options` are added to the provider's request body as given, so
every provider setting is available (for example `max_tokens`, `temperature` where the model allows it,
`inference_geo`, `service_tier`). The fields ReadAnyDoc itself owns are refused with an error, because the pipeline
could not read the reply otherwise: `model`, `messages`, `system`, `stream`, `tools`, `tool_choice`, `prompt`,
`input`.
