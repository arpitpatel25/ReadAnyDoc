# Model specs

Wherever a model is configured (server settings, `model=` with an explicit provider), ReadAnyDoc uses one string
format:

```text
<adapter>:<model>[#<effort>][?<json options>]
```

| Part | Example | Meaning |
|---|---|---|
| adapter | `anthropic_api` | which provider integration to use |
| model | `claude-haiku-5-5` | the provider's model name |
| `#effort` | `#high` | effort level, for adapters that support it |
| `?json` | `?{"max_tokens":32000}` | extra request fields, sent as given |

## Adapters

| Adapter | Provider | Effort | Key |
|---|---|---|---|
| `anthropic_api` | Anthropic Messages API | yes | `ANTHROPIC_API_KEY` |
| `anthropic_batch` | Anthropic Message Batches (half price, slow; for jobs that can wait) | yes | `ANTHROPIC_API_KEY` |
| `openai` | OpenAI API (or `OPENAI_BASE_URL`) | yes (`reasoning_effort`) | `OPENAI_API_KEY` |
| `openai_compatible` | any OpenAI-compatible server: `openai_compatible:<model>@<base_url>` | no | none |

In the Python library you can write bare names: `claude-*` (and `haiku`, `sonnet`, `opus`) map to `anthropic_api`;
`gpt-*`, `o1`, `o3`, `o4`, `chatgpt-*` map to `openai`. Anything else needs `<adapter>:`.

## Examples

```text
anthropic_api:claude-haiku-5-5#high                     the default page reader
anthropic_api:claude-sonnet-5-5                         the default handwriting reader
anthropic_api:claude-haiku-5-5#high?{"max_tokens":32000}
openai:gpt-5#medium
openai_compatible:Qwen/Qwen2.5-VL-7B-Instruct@http://localhost:8001/v1
```

## Reserved options

`model`, `messages`, `system`, `stream`, `tools`, `tool_choice`, `prompt` and `input` are set by ReadAnyDoc and
refused in options with a `ModelSpecError`.
