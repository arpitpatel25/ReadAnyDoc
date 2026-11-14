# Migrate from Reducto

If you call Reducto today, you can move to ReadAnyDoc without rewriting your integration. ReadAnyDoc's server
speaks Reducto's v3 HTTP API, and Reducto's own SDKs work against it with one changed URL.

## Why teams switch

| | Reducto | ReadAnyDoc |
|---|---|---|
| Price per page | $0.010 (r-1) | about $0.003 to $0.005 (engine), $0.001 to $0.002 (model only), paid to your model provider |
| Where documents go | Reducto's cloud | your machine, and the model provider you choose under your account |
| Model | Reducto's own r-1 | Claude Haiku 5.5 by default; any Claude, OpenAI or self-hosted model |
| Source | closed | open source, Apache-2.0 |
| Accuracy | see [Benchmarks](../resources/benchmarks.md): ahead on layout, formulas, header removal and speed | ahead on text and multi-column pages, level on tables and forms |

## Step 1: run ReadAnyDoc

```bash
pip install "readanydoc[engine]"
export ANTHROPIC_API_KEY=sk-ant-...
export READANYDOC_API_KEY=choose-a-long-random-key     # so only your services can call it
readanydoc serve --host 0.0.0.0 --port 8000
```

Or use [Docker](../deploy/docker.md).

## Step 2: point your client at it

=== "Python SDK"

    ```python
    from reducto import Reducto

    client = Reducto(
        api_key=os.environ["READANYDOC_API_KEY"],
        base_url="https://readanydoc.internal.example.com",   # was Reducto's
    )
    # everything else stays the same
    result = client.parse.run(input=upload.file_id)
    ```

=== "Environment variable"

    ```bash
    # the Python SDK reads this when base_url is not given
    export REDUCTO_BASE_URL=https://readanydoc.internal.example.com
    export REDUCTO_API_KEY=$READANYDOC_API_KEY
    ```

=== "Plain HTTP"

    Replace Reducto's host with yours; keep paths, bodies and the `Authorization: Bearer` header.

## Step 3: map the model setting

| `settings.model` you send | What ReadAnyDoc runs |
|---|---|
| `r-1` (or nothing) | the configured profile, `engine` by default |
| `r-1-mini`, `legacy` | its cheaper tier: `engine` becomes `model-only` at medium effort |

`usage_breakdown.parse_model` reports the model you asked for, as Reducto does; `usage.model_usage` says what actually
ran and the tokens it used.

## Step 4: check the differences that matter to you

Read [Differences and limits](differences.md), then run a sample of your own documents through both and compare.
Most teams check:

- the fields they extract, against their own answer key;
- documents with forms, handwriting or complex layout, where Reducto is still stronger on layout;
- latency: ReadAnyDoc takes 20 to 50 seconds a page with the engine against about 5 on Reducto.

## What stays the same

Request and response shapes, `reducto://` file ids, `jobid://` chaining, sync and async routes, `GET /job`
statuses, webhooks, error bodies, citations in Reducto's shape, chunking rules. Route-by-route detail:
[Compatibility](compatibility.md).
