# HTTP API

The server implements Reducto's v3 API. Every request may carry `Authorization: Bearer <READANYDOC_API_KEY>`; it is
required when that variable is set. Bodies are JSON. An interactive schema is served at `/docs` on your server
(FastAPI's OpenAPI UI).

## Routes

| Method and path | Purpose | Guide |
|---|---|---|
| `POST /upload` | Upload a file (multipart `file`); returns `file_id` (`reducto://...`) | [Server quickstart](../get-started/server-quickstart.md) |
| `DELETE /upload/{file_id}` | Remove an upload | |
| `POST /parse`, `/parse_async` | Parse | [Parse](../guides/parse.md) |
| `POST /extract`, `/extract_async` | Extract | [Extract](../guides/extract.md) |
| `POST /split`, `/split_async` | Split | [Split](../guides/split.md) |
| `POST /classify`, `/classify_async` | Classify | [Classify](../guides/classify.md) |
| `POST /edit`, `/edit_async` | Fill PDF forms | [Edit](../guides/edit.md) |
| `GET /job/{job_id}` | Status and result | [Jobs](../guides/jobs-webhooks.md) |
| `GET /jobs` | List jobs, newest first | |
| `POST /cancel/{job_id}` | Cancel | |
| `DELETE /job/{job_id}` | Delete a job and its files | |
| `POST /configure_webhook` | Default webhook URL | |
| `GET /version` | `"readanydoc-<version>"` | |
| `GET /health` | Health check (no key needed) | |
| `POST /pipeline`, `/pipeline_async` | Deprecated by Reducto; returns 501 | |

## Inputs

`input` accepts a `reducto://` file id from `/upload`, the upload response object, an `http(s)` URL, `jobid://<id>`
to reuse an earlier parse, or a list of these (parsed in order into one response).

## Example

```bash
curl -s -X POST http://localhost:8000/extract \
  -H "Authorization: Bearer $READANYDOC_API_KEY" -H "content-type: application/json" \
  -d '{"input": "https://example.com/invoice.pdf",
       "instructions": {"schema": {"type": "object", "properties": {"total": {"type": "number"}}}},
       "settings": {"citations": {"enabled": true}}}'
```

Field-level detail and every difference from Reducto: [Compatibility](../migrate/compatibility.md).
Errors: [Errors](errors.md).
