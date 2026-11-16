# Quickstart (server)

Run ReadAnyDoc as an HTTP server with Reducto's API, then call it with `curl`, any HTTP client, or Reducto's own SDK.

## 1. Start the server

=== "Python"

    ```bash
    pip install "readanydoc[engine]"
    export ANTHROPIC_API_KEY=sk-ant-...
    readanydoc serve --port 8000
    ```

=== "Docker"

    ```bash
    git clone https://github.com/arpitpatel25/ReadAnyDoc && cd ReadAnyDoc
    export ANTHROPIC_API_KEY=sk-ant-...
    docker compose up --build
    ```

Check it is up:

```bash
curl http://localhost:8000/version
# "readanydoc-0.1.7"
```

## 2. Upload and parse

```bash
# upload a file, get a file id
curl -s -F "file=@invoice.pdf" http://localhost:8000/upload
# {"file_id": "reducto://5d44c8....pdf", "presigned_url": null}

# parse it
curl -s -X POST http://localhost:8000/parse \
  -H "content-type: application/json" \
  -d '{"input": "reducto://5d44c8....pdf"}'
```

The response has Reducto's shape: `result.chunks[]` with `content` and `blocks[]` (type, bbox, content, confidence),
plus `usage` and `job_id`. A public URL works as `input` too.

## 3. Or use Reducto's SDK

```python
from pathlib import Path
from reducto import Reducto          # pip install reductoai

client = Reducto(api_key="anything", base_url="http://localhost:8000")

upload = client.upload(file=Path("invoice.pdf"))
result = client.parse.run(input=upload.file_id)
for chunk in result.result.chunks:
    print(chunk.content)
```

The SDK requires an `api_key` string. It is only checked when you set `READANYDOC_API_KEY` on the server.

## 4. Before you share the server

- Set `READANYDOC_API_KEY` so that every request needs `Authorization: Bearer <key>`.
- Read [Security](../deploy/security.md) and [Configuration](../deploy/configuration.md).

## What's next

- [Migrate from Reducto](../migrate/from-reducto.md)
- [HTTP API reference](../reference/http.md)
- [Async jobs and webhooks](../guides/jobs-webhooks.md)
