# Async jobs and webhooks

Every call can run in the background. Use the `*_async` routes, or add an `async` block to a normal call.

```bash
curl -s -X POST http://localhost:8000/parse_async -H "content-type: application/json" -d '{
  "input": "reducto://big.pdf",
  "async": {"webhook": {"mode": "direct", "url": "https://example.com/hooks/readanydoc"},
            "metadata": {"customer": "acme"}, "priority": true}
}'
# {"job_id": "a1b2..."}
```

## Polling

```bash
curl -s http://localhost:8000/job/a1b2...
```

`status` is `Pending`, `Completed`, `Failed` or `Idle`, as on Reducto. Set `READANYDOC_JOB_STATUS_DETAIL=1` to see
`InProgress`, `Completing` and `Cancelled` too.

| Route | Purpose |
|---|---|
| `GET /job/{id}` | status and, when done, the result |
| `GET /jobs?limit=&cursor=` | newest first, all statuses |
| `POST /cancel/{id}` | waiting jobs stop at once; a running job's result is discarded |
| `DELETE /job/{id}` | remove the job and its files (409 while running) |

Synchronous calls are stored as jobs too, so `jobid://<id>` works as `input` for a later extract, split or classify.

## Webhooks

When the job completes or fails, ReadAnyDoc sends one `POST`:

```json
{"job_id": "a1b2...", "status": "Completed", "metadata": {"customer": "acme"}}
```

Non-2xx answers are retried 3 times with backoff. `mode: "svix"` is accepted and sent to the default URL set with
`POST /configure_webhook?url=...` or `READANYDOC_WEBHOOK_URL`.

### Verifying the signature

With `READANYDOC_WEBHOOK_SECRET` set, each request has
`X-Readanydoc-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>`.

```python
from readanydoc.api.webhooks import verify

ok = verify(raw_body_bytes, request.headers["X-Readanydoc-Signature"], secret)  # rejects stamps older than 5 min
```

## Large results

Results above `READANYDOC_MAX_INLINE_BYTES` (6 MB) come back as `{"type": "url", "url": ..., "result_id": ...}`.
Results expire after `READANYDOC_RESULT_TTL_HOURS` (12) unless `settings.persist_results` is set.
