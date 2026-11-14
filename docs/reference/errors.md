# Errors

Errors use Reducto's documented body plus FastAPI's `detail`, so both styles of client work:

```json
{"error": {"message": "...", "type": "file_download_failure", "code": 400, "name": "PROCESSING_FAILED"},
 "detail": "..."}
```

400 bad URL / page range / config, 401 bad key, 403 bad file token, 404 unknown file or job, 409 job state,
410 deleted or expired job, 413 too large, 415 unsupported or damaged file, 422 validation
(`HTTPValidationError`), 442 password needed, 500 internal, 501 not implemented, 502 model provider error.
Failed jobs carry `error: {code, name, message, job_id}`.

## Common errors and fixes

| Status | Name | Fix |
|---|---|---|
| 400 | `INVALID_CONFIG` | a page range outside the document, an invalid URL or option |
| 401 | | missing or wrong `Authorization: Bearer` key |
| 413 | | file above `READANYDOC_MAX_UPLOAD_MB` |
| 415 | | unsupported or damaged file, or a converter (LibreOffice, HEIC decoder) is missing; the message names it |
| 442 | `DOCUMENT_PASSWORD_PROTECTED` | send `settings.document_password` |
| 502 | `LLM_PROVIDER_ERROR` | the model provider failed: check the key, rate limits and model name |
| 504 | `timeout` | the document took longer than `READANYDOC_DOCUMENT_TIMEOUT_SECONDS` |
