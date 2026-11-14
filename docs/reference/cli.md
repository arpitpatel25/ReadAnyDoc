# Command line

Installing the package adds a `readanydoc` command.

## `readanydoc parse`

```bash
readanydoc parse FILE [--mode engine|model-only] [--model M] [--effort E] [--model-options JSON]
                      [--handwriting-model M|none] [--pages 1,2,5] [--citations] [--markdown]
```

Prints the Reducto-shaped JSON, or the document as Markdown with `--markdown`.

```bash
readanydoc parse invoice.pdf --markdown
readanydoc parse scan.pdf --mode model-only --model claude-sonnet-5-5
readanydoc parse long.pdf --model-options '{"max_tokens": 32000}'
```

## `readanydoc extract`

```bash
readanydoc extract FILE --schema SCHEMA.json [--instructions TEXT] [--extract-model M] [--no-citations]
                        [--mode ...] [--model ...] [--effort ...] [--pages ...]
```

`SCHEMA.json` is a JSON Schema file. Prints the extracted data with citations as JSON.

## `readanydoc serve`

```bash
readanydoc serve [--host 127.0.0.1] [--port 8000]
```

Starts the Reducto-compatible server. Configure it with [environment variables](../deploy/configuration.md).

## `readanydoc cleanup`

```bash
readanydoc cleanup [--json]
```

Runs the server's clean-up pass by hand: expired uploads, results and caches.
